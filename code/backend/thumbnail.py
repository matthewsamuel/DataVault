"""
DataVault Dashboard - Thumbnail Generator
Generate and cache thumbnails for photos and videos

Cache entries are named after the file's path, size, modification time and inode plus
a version stamp (THUMB_VERSION), so a different file at the same path (a restore, an
undo, another IMG_0001.jpg moved in) never shows the old picture, and thumbnails from
before a change in how they are made (EXIF rotation) are made again. A failed thumbnail
is never cached: the caller serves a placeholder that is not stored, and the file is
tried again after RETRY_SECONDS (or at once for a read error, e.g. a disk that was
briefly gone).
"""
import functools
import hashlib
import io
import os
import re
import shutil
import threading
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional
from PIL import Image, ImageOps
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False

from backend.config import THUMB_SIZE, THUMB_QUALITY, RAW_PHOTO_EXTENSIONS

# Bump when thumbnails must be made again (2: EXIF orientation applied, new cache key)
THUMB_VERSION = 2

# Pillow refuses images above 2x MAX_IMAGE_PIXELS (default ~179 MP) as a 'decompression
# bomb'. These are the user's own photos (panoramas, scans), and a JPEG is decoded at
# reduced size (draft), so allow up to 2 x 300 MP; very large non-JPEG images are
# decoded one at a time (_BIG_DECODE) so memory stays bounded.
Image.MAX_IMAGE_PIXELS = 300_000_000
warnings.simplefilter("ignore", Image.DecompressionBombWarning)
BIG_IMAGE_PIXELS = 50_000_000
_BIG_DECODE = threading.Lock()

# At most this many thumbnails are made at once (requests and the thumbnail job together);
# each holds a decoded image in memory
_RENDER_SLOTS = threading.BoundedSemaphore(4)

# Two requests for the same thumbnail: the second waits for the first instead of making it again
_KEY_LOCKS = [threading.Lock() for _ in range(64)]

# How long a file that could not be decoded is not tried again (in this process)
RETRY_SECONDS = 300.0
_FAILED: Dict[str, float] = {}
_FAILED_LOCK = threading.Lock()


def _file_key(file_path: Path) -> str:
    try:
        st = os.stat(file_path)
        identity = f"{st.st_size}|{st.st_mtime_ns}|{st.st_ino}"
    except OSError:
        identity = "?"
    return hashlib.md5(f"{file_path}|{identity}".encode("utf-8", "surrogateescape")).hexdigest()


def get_thumbnail_cache_path(file_path: Path, thumb_cache_dir: Path) -> Path:
    """
    Cache path for a file's thumbnail (always JPEG): a hash of its path, size,
    modification time and inode, prefixed with THUMB_VERSION.
    """
    return thumb_cache_dir / f"v{THUMB_VERSION}_{_file_key(file_path)}.jpg"


_CURRENT_NAME = re.compile(rf"^v{THUMB_VERSION}_[0-9a-f]{{32}}\.jpg$")


def prune_thumbnail_cache(thumb_cache_dir: Path, max_tmp_age: float = 3600.0) -> int:
    """
    Remove what an older version of the app left in the thumbnail cache: thumbnails
    named the old way (path-only key, made before EXIF rotation), the per-file and shared
    placeholders it stored, and temporary files older than max_tmp_age. Only ever touches
    files directly in the app's own thumbnail folder. Returns how many were removed.
    """
    removed = 0
    now = time.time()
    try:
        entries = list(os.scandir(thumb_cache_dir))
    except OSError:
        return 0
    for entry in entries:
        name = entry.name
        try:
            if not entry.is_file(follow_symlinks=False) or _CURRENT_NAME.match(name):
                continue
            if name.endswith(".tmp.jpg"):
                if now - entry.stat(follow_symlinks=False).st_mtime < max_tmp_age:
                    continue
            elif not (re.match(r"^(v\d+_)?[0-9a-f]{32}\.jpg$", name) or name.startswith("_placeholder_")):
                continue
            os.unlink(entry.path)  # the app's own cache file, never an archive file
            removed += 1
        except OSError:
            continue
    return removed


def carry_over(old_path: Path, new_path: Path, thumb_cache_dir: Path) -> None:
    """
    After a move (same file: same size, mtime and inode), rename its cached thumbnail to
    the new path's key, so the moved file keeps its thumbnail and no orphan is left.
    Best effort; only touches the app's own cache.
    """
    try:
        st = os.stat(new_path)
        identity = f"{st.st_size}|{st.st_mtime_ns}|{st.st_ino}"
        old_key = hashlib.md5(f"{old_path}|{identity}".encode("utf-8", "surrogateescape")).hexdigest()
        old = thumb_cache_dir / f"v{THUMB_VERSION}_{old_key}.jpg"
        if old.is_file():
            os.replace(old, get_thumbnail_cache_path(new_path, thumb_cache_dir))
    except OSError:
        pass


@contextmanager
def _atomic_output(output_path: Path) -> Iterator[Path]:
    """
    Write a thumbnail under a temporary name in the same (cache) folder and rename it
    into place only when complete. A request and the thumbnail job that make the same
    thumbnail at once, or a server stopped halfway, never leave a half-written JPEG.
    Only ever touches the app's own thumbnail cache.

    Only the thumbnail folder itself is made when it is missing, never the folders
    above it (R23-1): with the cache inside the vault, a render that finishes after
    the vault was renamed or unplugged must fail here (FileNotFoundError, which a
    job reads as 'vault gone') instead of building an empty vault at the old path.
    """
    output_path.parent.mkdir(exist_ok=True)
    tmp = output_path.with_name(f".{output_path.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
    try:
        yield tmp
        os.replace(tmp, output_path)
    finally:
        try:
            os.unlink(tmp)  # only left over when writing failed
        except FileNotFoundError:
            pass


class Undecodable(Exception):
    """
    The file reads fine, but no picture can be made from it: a corrupt or empty
    photo, a format Pillow does not know, a video ffmpeg gets no frame from. Unlike
    a read error (no permission, I/O error, disk gone) trying again gives the same
    answer, so the thumbnail job counts these apart instead of as failures.
    """


class _NoFrame(Undecodable):
    """ffmpeg wrote no frame: nothing is put in place."""


def _is_read_error(e: BaseException, file_path: Path) -> bool:
    """An OS error with an errno, or a file that cannot be read now: not the file's content."""
    return (isinstance(e, OSError) and bool(e.errno)) or not _readable(file_path)


def is_thumbnail_valid(thumb_path: Path, original_path: Path) -> bool:
    """
    A cached thumbnail is valid when it exists and is not empty: its name already holds
    the original's path, size, modification time and inode (see get_thumbnail_cache_path).
    """
    try:
        return thumb_path.stat().st_size > 0
    except OSError:
        return False


def _orientation_fixed(img: Image.Image) -> Image.Image:
    """Turn the (already small) image upright according to its EXIF orientation."""
    return ImageOps.exif_transpose(img)


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ('RGBA', 'LA', 'P', 'PA'):
        if img.mode in ('P', 'PA'):
            img = img.convert('RGBA')
        background = Image.new('RGB', img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        return background
    if img.mode != 'RGB':
        return img.convert('RGB')
    return img


def render_photo_thumbnail(file_path: Path, output_path: Path) -> None:
    """
    Make the thumbnail of a photo, or raise: an unreadable photo is an OSError, a
    photo that reads fine but cannot be decoded is Undecodable. Never puts a
    placeholder in its place (see generate_photo_thumbnail).

    Small first, then upright (MEDIA-THUMBS-TIMELINE-1/PERF-8): a JPEG is decoded at
    1/2 to 1/8 of its size (draft), thumbnail() reduces it, and only the small image is
    turned according to its EXIF orientation. A 48 MP photo then needs about 25 MB
    instead of 400 MB. A RAW photo Pillow cannot decode goes through macOS's sips.
    """
    with _RENDER_SLOTS:
        try:
            img = _decode_small(file_path)
        except Exception as e:
            if _is_read_error(e, file_path):
                raise
            if file_path.suffix.lower() in RAW_PHOTO_EXTENSIONS | {'.heic', '.heif', '.hif'} and _have_sips():
                _render_with_sips(file_path, output_path)
                return
            raise Undecodable(f"geen beeld te maken van dit bestand ({e})") from e

        # Save (errors here are about the cache, never about the photo)
        with _atomic_output(output_path) as tmp:
            img.save(tmp, 'JPEG', quality=THUMB_QUALITY, optimize=True)


def _decode_small(file_path: Path) -> Image.Image:
    with Image.open(file_path) as img:
        img.draft('RGB', (THUMB_SIZE[0] * 2, THUMB_SIZE[1] * 2))  # JPEG only; a no-op otherwise
        big = img.size[0] * img.size[1] > BIG_IMAGE_PIXELS
        with (_BIG_DECODE if big else _no_lock()):
            # In place, so the EXIF orientation stays with it; a loaded copy that
            # outlives the opened file
            img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS, reducing_gap=3.0)
        return _to_rgb(_orientation_fixed(img))


@contextmanager
def _no_lock():
    yield


def _have_sips() -> bool:
    return shutil.which('sips') is not None


def _render_with_sips(file_path: Path, output_path: Path) -> None:
    """macOS's own image converter: reads RAW formats (and HEIC) that Pillow cannot."""
    import subprocess
    with _atomic_output(output_path) as tmp:
        try:
            result = subprocess.run(
                ['sips', '-s', 'format', 'jpeg', '-s', 'formatOptions', str(THUMB_QUALITY),
                 '-Z', str(max(THUMB_SIZE)), str(file_path), '--out', str(tmp)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise OSError(0, "sips deed er te lang over")
        try:
            ok = result.returncode == 0 and tmp.stat().st_size > 0
        except FileNotFoundError:
            ok = False
        if not ok:
            if not _readable(file_path):
                raise OSError(0, "de foto kon niet gelezen worden")
            raise Undecodable("geen beeld te maken van dit bestand (ook niet met sips)")


def generate_photo_thumbnail(file_path: Path, output_path: Path) -> bool:
    """
    Generate thumbnail for photo.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        render_photo_thumbnail(file_path, output_path)
        return True
    except Exception as e:
        print(f"Error generating photo thumbnail for {file_path}: {e}")
        return False

class NoFfmpeg(OSError):
    """ffmpeg is not installed: videos get the grey placeholder."""

    def __init__(self):
        super().__init__(0, "ffmpeg ontbreekt")


def have_ffmpeg() -> bool:
    return shutil.which('ffmpeg') is not None


def render_video_thumbnail(file_path: Path, output_path: Path) -> None:
    """
    Make the thumbnail of a video from its frame at 1 s, or from its first frame
    when there is none at 1 s (a clip shorter than a second), or raise: NoFfmpeg
    without ffmpeg, OSError when the video cannot be read (or ffmpeg takes too
    long), Undecodable when the video reads fine but ffmpeg gets no frame from it.
    Never puts a placeholder in place.

    -ss comes before -i (seek in the input: no decoding from the start), and -nostdin
    keeps ffmpeg from reading the Terminal the server runs in.
    """
    import subprocess

    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        raise NoFfmpeg()
    with open(file_path, 'rb') as f:  # a clear error for a video that cannot be read
        f.read(1)

    with _RENDER_SLOTS, _atomic_output(output_path) as tmp:
        # The frame at 1 second; a clip shorter than that has none, so then the first frame
        for seek in (['-ss', '1'], []):
            cmd = [
                ffmpeg_path, '-nostdin', '-v', 'error',
                *seek,
                '-i', str(file_path),
                '-frames:v', '1',
                '-vf', f'scale={THUMB_SIZE[0]}:{THUMB_SIZE[1]}:force_original_aspect_ratio=decrease',
                '-y',  # Overwrite (the temporary file only)
                str(tmp)
            ]
            try:
                result = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=20
                )
            except subprocess.TimeoutExpired:
                raise OSError(0, "ffmpeg deed er te lang over")

            # Only a frame that ffmpeg really wrote is put in place
            try:
                if result.returncode == 0 and tmp.stat().st_size > 0:
                    return
            except FileNotFoundError:
                pass
        if not _readable(file_path):
            raise OSError(0, "de video kon niet gelezen worden")
        raise _NoFrame("ffmpeg kon geen beeld uit de video halen")


def generate_video_thumbnail(file_path: Path, output_path: Path) -> bool:
    """
    Generate thumbnail for video using ffmpeg if available.
    
    Returns:
        True if successful, False if ffmpeg not available
    """
    try:
        render_video_thumbnail(file_path, output_path)
        return output_path.exists()
    except (NoFfmpeg, _NoFrame):
        return False
    except Exception as e:
        print(f"Error generating video thumbnail for {file_path}: {e}")
        return False

@functools.lru_cache(maxsize=2)
def _placeholder_bytes(is_video: bool) -> bytes:
    """The grey (photo) or blue-grey (video) placeholder JPEG: always the same bytes."""
    color = (100, 100, 150) if is_video else (150, 150, 150)
    buffer = io.BytesIO()
    Image.new('RGB', THUMB_SIZE, color).save(buffer, 'JPEG', quality=THUMB_QUALITY)
    return buffer.getvalue()


def is_placeholder(thumb_path: Path) -> bool:
    """
    True when the cached thumbnail is a placeholder rather than the picture itself
    (one stat; the bytes are only read when the size matches a placeholder's).
    """
    try:
        size = thumb_path.stat().st_size
        for is_video in (False, True):
            data = _placeholder_bytes(is_video)
            if size == len(data):
                with open(thumb_path, 'rb') as f:
                    if f.read() == data:
                        return True
    except OSError:
        return False
    return False


def _readable(file_path: Path) -> bool:
    try:
        with open(file_path, 'rb') as f:
            f.read(1)
        return True
    except OSError:
        return False


def create_placeholder_thumbnail(output_path: Path, is_video: bool = False) -> bool:
    """
    Create a placeholder thumbnail.
    """
    try:
        with _atomic_output(output_path) as tmp:
            tmp.write_bytes(_placeholder_bytes(is_video))
        return True
    except Exception as e:
        print(f"Error creating placeholder: {e}")
        return False

def _remember_failure(key: str) -> None:
    with _FAILED_LOCK:
        if len(_FAILED) > 10000:
            _FAILED.clear()
        _FAILED[key] = time.monotonic() + RETRY_SECONDS


def _failed_recently(key: str) -> bool:
    with _FAILED_LOCK:
        until = _FAILED.get(key)
        if until is None:
            return False
        if until < time.monotonic():
            del _FAILED[key]
            return False
        return True


def get_or_create_thumbnail(
    file_path: Path,
    thumb_cache_dir: Path,
    is_video: bool = False,
    is_audio: bool = False
) -> Optional[Path]:
    """
    The cached thumbnail of a photo or video, made when it is missing.

    Returns None when there is no thumbnail to show: audio, a video without ffmpeg, a
    file that cannot be read or decoded. The caller then serves placeholder_bytes()
    without storing it, so nothing stands in for the file for good (MEDIA-THUMBS-
    TIMELINE-7, GAP-1): a read error is tried again on the next request, a file that
    cannot be decoded after RETRY_SECONDS.
    """
    if is_audio:
        return None
    cache_path = get_thumbnail_cache_path(file_path, thumb_cache_dir)
    if is_thumbnail_valid(cache_path, file_path):
        return cache_path
    if _failed_recently(cache_path.name):
        return None

    with _KEY_LOCKS[hash(cache_path.name) % len(_KEY_LOCKS)]:
        # Made by another request while this one waited
        if is_thumbnail_valid(cache_path, file_path):
            return cache_path
        try:
            if is_video:
                render_video_thumbnail(file_path, cache_path)
            else:
                render_photo_thumbnail(file_path, cache_path)
        except NoFfmpeg:
            return None  # cheap to check again: works as soon as ffmpeg is installed
        except Undecodable as e:
            print(f"Geen miniatuur voor {file_path}: {e}")
            _remember_failure(cache_path.name)
            return None
        except Exception as e:
            # A read error (no permission, disk just unplugged) or a cache write error:
            # tried again on the next request
            print(f"Error generating thumbnail for {file_path}: {e}")
            return None
    return cache_path if is_thumbnail_valid(cache_path, file_path) else None


def placeholder_bytes(kind: str) -> bytes:
    """The placeholder JPEG for 'photo', 'video' or 'audio'; served, never stored."""
    return _placeholder_bytes(kind == 'video')
