"""
DataVault Dashboard - Capture dates of photos and videos

One rule for every part of the app that orders or finds media by date (timeline, on
this day, photos by date), LIVE-PROBE-15/MEDIA-THUMBS-TIMELINE-15:
  1. the date stored in the file itself: EXIF DateTimeOriginal (then DateTimeDigitized,
     then DateTime) for a photo, the QuickTime creation time for an mp4/mov/m4v/3gp;
  2. else the file's creation date (birthtime);
  3. else its modification date (mtime).
EXIF dates are local time (cameras store no zone), QuickTime dates are UTC.

Reading a date costs a file open, so dates are cached in SQLite (media_dates,
migration 9) with the size and mtime_ns they were read at: a changed file is read
again, an unchanged one never. A full index of the archive comes later; this module
is the reusable piece it can build on.
"""
import os
import struct
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from backend.config import PHOTO_EXTENSIONS, VIDEO_EXTENSIONS

# Short codes keep the timeline payload small; the frontend maps them back
SOURCES = {"exif": "e", "video": "q", "birthtime": "b", "mtime": "m"}

QUICKTIME_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.3gp'}
_QT_EPOCH_OFFSET = 2082844800  # seconds from 1904-01-01 (QuickTime) to 1970-01-01

READ_WORKERS = 4

# A date before this (or in the future) is a camera without a clock, not a capture date
_EARLIEST = datetime(1971, 1, 1).timestamp()


def _plausible(ts: Optional[float]) -> Optional[float]:
    if ts is None or ts < _EARLIEST or ts > time.time() + 86400:
        return None
    return ts


def exif_date(path: Path) -> Optional[float]:
    """EXIF DateTimeOriginal (or DateTimeDigitized, DateTime) as a local-time epoch, or None."""
    try:
        from PIL import Image
        from backend import thumbnail  # noqa: F401  (registers the HEIC opener, raises the pixel limit)
        with Image.open(path) as img:
            exif = img.getexif()
            sub = exif.get_ifd(0x8769)
            values = (sub.get(0x9003), sub.get(0x9004), exif.get(0x0132))
    except Exception:
        return None
    for value in values:
        if isinstance(value, bytes):
            value = value.decode("ascii", "ignore")
        if not value:
            continue
        try:
            ts = datetime.strptime(str(value).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S").timestamp()
        except (ValueError, OverflowError, OSError):
            continue
        if _plausible(ts):
            return ts
    return None


def _box_header(f, end: int) -> Optional[Tuple[bytes, int, int]]:
    """(type, header size, box size) of the box at the current position, or None."""
    start = f.tell()
    head = f.read(8)
    if len(head) < 8:
        return None
    size, kind = struct.unpack(">I4s", head)
    header = 8
    if size == 1:
        large = f.read(8)
        if len(large) < 8:
            return None
        size = struct.unpack(">Q", large)[0]
        header = 16
    elif size == 0:
        size = end - start
    if size < header or start + size > end:
        return None
    return kind, header, size


def quicktime_date(path: Path) -> Optional[float]:
    """
    The creation time in the movie header (moov/mvhd) of an mp4/mov, as a UTC epoch, or
    None. Reads only box headers (a few small reads, wherever moov is in the file).
    """
    try:
        with open(path, "rb") as f:
            end = os.fstat(f.fileno()).st_size
            pos = 0
            for _ in range(10000):
                if pos + 8 > end:
                    return None
                f.seek(pos)
                box = _box_header(f, end)
                if box is None:
                    return None
                kind, header, size = box
                if kind == b"moov":
                    child, moov_end = pos + header, pos + size
                    for _ in range(10000):
                        if child + 8 > moov_end:
                            return None
                        f.seek(child)
                        inner = _box_header(f, moov_end)
                        if inner is None:
                            return None
                        ckind, cheader, csize = inner
                        if ckind == b"mvhd":
                            version = f.read(4)[:1]
                            if not version:
                                return None
                            raw = f.read(8) if version[0] == 1 else f.read(4)
                            if len(raw) not in (4, 8):
                                return None
                            created = struct.unpack(">Q" if len(raw) == 8 else ">I", raw)[0]
                            return _plausible(float(created - _QT_EPOCH_OFFSET)) if created else None
                        child += csize
                    return None
                pos += size
    except (OSError, struct.error):
        return None
    return None


def embedded_date(path: Path) -> Tuple[Optional[float], Optional[str]]:
    """The date stored in the file itself and its source ('exif' or 'video'), or (None, None)."""
    ext = path.suffix.lower()
    if ext in PHOTO_EXTENSIONS:
        ts = exif_date(path)
        return (ts, "exif") if ts is not None else (None, None)
    if ext in QUICKTIME_EXTENSIONS:
        ts = quicktime_date(path)
        return (ts, "video") if ts is not None else (None, None)
    return None, None


def file_date(st: os.stat_result) -> Tuple[float, str]:
    """The file's own date: birthtime when the filesystem has one, else mtime."""
    birthtime = getattr(st, "st_birthtime", None)
    if birthtime and birthtime > 0:
        return birthtime, "birthtime"
    return st.st_mtime, "mtime"


def capture_date(path: Path, st: os.stat_result) -> Tuple[float, str]:
    """(timestamp, source) by the rule at the top of this module, without the cache."""
    ts, source = embedded_date(path)
    if ts is not None:
        return ts, source
    return file_date(st)


def capture_dates(entries: Iterable[Tuple[str, os.stat_result]], db=None,
                  workers: int = READ_WORKERS) -> Dict[str, Tuple[float, str]]:
    """
    {path: (timestamp, source)} for [(path, stat)], from the cache where the file is
    unchanged and read (in parallel) where it is not; what was read is stored. Works
    without a database (db None or unreachable): then every date is read.
    """
    entries = list(entries)
    cached: Dict[str, Tuple[int, int, float, str]] = {}
    if db is not None and entries:
        try:
            cached = db.get_media_dates([path for path, _ in entries])
        except Exception:
            db = None  # vault (and with it the database) just went away: read without it
    result: Dict[str, Tuple[float, str]] = {}
    todo: List[Tuple[str, os.stat_result]] = []
    for path, st in entries:
        row = cached.get(path)
        if row is not None and row[0] == st.st_size and row[1] == st.st_mtime_ns:
            result[path] = (row[2], row[3])
        else:
            todo.append((path, st))
    if not todo:
        return result

    read: List[Tuple[str, int, int, float, str]] = []

    def one(item):
        path, st = item
        return capture_date(Path(path), st)

    if len(todo) == 1 or workers <= 1:
        outcomes = [(item, one(item), None) for item in todo]
    else:
        from backend.jobs import bounded_map
        outcomes = bounded_map(one, todo, workers=workers)
    for (path, st), value, error in outcomes:
        if error is not None:
            value = file_date(st)
        result[path] = value
        read.append((path, st.st_size, st.st_mtime_ns, value[0], value[1]))
    if db is not None:
        try:
            db.store_media_dates(read)
        except Exception:
            pass  # a cache: the dates are right either way
    return result
