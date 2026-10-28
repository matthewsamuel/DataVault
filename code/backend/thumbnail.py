"""
DataVault Dashboard - Thumbnail Generator
Generate and cache thumbnails for photos and videos
"""
import functools
import hashlib
import io
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional
from PIL import Image, ImageOps
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False

from backend.config import THUMB_SIZE, THUMB_QUALITY

def get_thumbnail_cache_path(file_path: Path, thumb_cache_dir: Path) -> Path:
    """
    Get cache path for thumbnail.
    Uses hash of file path to avoid name conflicts.
    All thumbnails are saved as JPEG.
    """
    # Create hash of file path
    path_hash = hashlib.md5(str(file_path).encode()).hexdigest()
    
    # ALL thumbnails are JPEG
    cache_filename = f"{path_hash}.jpg"
    return thumb_cache_dir / cache_filename

@contextmanager
def _atomic_output(output_path: Path) -> Iterator[Path]:
    """
    Write a thumbnail under a temporary name in the same (cache) folder and rename it
    into place only when complete. A request and the thumbnail job that make the same
    thumbnail at once, or a server stopped halfway, never leave a half-written JPEG.
    Only ever touches the app's own thumbnail cache.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_name(f".{output_path.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
    try:
        yield tmp
        os.replace(tmp, output_path)
    finally:
        try:
            os.unlink(tmp)  # only left over when writing failed
        except FileNotFoundError:
            pass


class _NoFrame(OSError):
    """ffmpeg wrote no frame: nothing is put in place."""


def is_thumbnail_valid(thumb_path: Path, original_path: Path) -> bool:
    """
    Check if cached thumbnail is still valid.
    Valid if it exists and is newer than original file.
    """
    if not thumb_path.exists():
        return False
    
    if not original_path.exists():
        return False
    
    thumb_mtime = thumb_path.stat().st_mtime
    original_mtime = original_path.stat().st_mtime
    
    return thumb_mtime >= original_mtime

def render_photo_thumbnail(file_path: Path, output_path: Path) -> None:
    """
    Make the thumbnail of a photo, or raise: an unreadable or undecodable photo
    is an error here, never a placeholder in its place (see generate_photo_thumbnail).
    """
    with Image.open(file_path) as img:
        # Roteer volgens EXIF-oriëntatie zodat thumbnails altijd rechtop staan
        img = ImageOps.exif_transpose(img)

        # Convert RGBA to RGB if needed
        if img.mode in ('RGBA', 'LA', 'P'):
            # Create white background
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Create thumbnail
        img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)

        # Save
        with _atomic_output(output_path) as tmp:
            img.save(tmp, 'JPEG', quality=THUMB_QUALITY, optimize=True)


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
    import shutil
    return shutil.which('ffmpeg') is not None


def render_video_thumbnail(file_path: Path, output_path: Path) -> None:
    """
    Make the thumbnail of a video from a frame at 1 s (or the first frame), or
    raise: NoFfmpeg without ffmpeg, OSError when the video cannot be read or
    ffmpeg writes no frame. Never puts a placeholder in place.
    """
    import subprocess
    import shutil

    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        raise NoFfmpeg()
    with open(file_path, 'rb') as f:  # a clear error for a video that cannot be read
        f.read(1)

    with _atomic_output(output_path) as tmp:
        # Extract frame at 1 second (or first frame if shorter)
        cmd = [
            ffmpeg_path,
            '-i', str(file_path),
            '-ss', '00:00:01',
            '-vframes', '1',
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
                timeout=10
            )
        except subprocess.TimeoutExpired:
            raise OSError(0, "ffmpeg deed er te lang over")

        # Only a frame that ffmpeg really wrote is put in place
        if result.returncode != 0 or not tmp.exists():
            raise _NoFrame(0, "ffmpeg kon geen beeld uit de video halen")


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

def get_or_create_thumbnail(
    file_path: Path,
    thumb_cache_dir: Path,
    is_video: bool = False,
    is_audio: bool = False
) -> Optional[Path]:
    """
    Get cached thumbnail or create new one.
    
    Args:
        file_path: Path to original media file
        thumb_cache_dir: Directory for thumbnail cache
        is_video: True if file is a video
        is_audio: True if file is audio
    
    Returns:
        Path to thumbnail file, or None if failed
    """
    # Get cache path
    cache_path = get_thumbnail_cache_path(file_path, thumb_cache_dir)
    
    # Check if valid cached version exists
    if is_thumbnail_valid(cache_path, file_path):
        return cache_path
    
    # Generate new thumbnail
    success = False
    
    if is_audio:
        # Instantly create a placeholder for audio files
        success = create_placeholder_thumbnail(cache_path, is_video=False)
    else:
        if is_video:
            success = generate_video_thumbnail(file_path, cache_path)
        else:
            success = generate_photo_thumbnail(file_path, cache_path)

        # Fallback to placeholder. A file that cannot be read right now (disk just
        # unplugged, no permission) gets a shared one: a placeholder under its own
        # name would be newer than the photo and stand in for it for good.
        if not success:
            if not _readable(file_path):
                shared = thumb_cache_dir / ('_placeholder_video.jpg' if is_video else '_placeholder_photo.jpg')
                if shared.exists() or create_placeholder_thumbnail(shared, is_video=is_video):
                    return shared
                return None
            success = create_placeholder_thumbnail(cache_path, is_video=is_video)
    
    if success and cache_path.exists():
        return cache_path
    
    return None
