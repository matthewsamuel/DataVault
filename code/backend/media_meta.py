"""
DataVault Dashboard - What the media index knows about one file

read_metadata(path, st) gives the columns of a media row (backend/media_index.py)
for one photo, video or audio file, as cheaply as possible:
- photos (JPEG, PNG, HEIC, TIFF, and RAW formats Pillow can open as TIFF): Pillow
  reads only the header and EXIF, never the pixels: dimensions, orientation, camera
  make and model, lens, GPS position and the capture date. A RAW file Pillow cannot
  open gets its size and file date only.
- QuickTime/MP4 videos and m4a audio: a few small reads of the box headers (moov):
  the creation time, duration, dimensions and rotation, and the GPS position and
  camera an iPhone writes. No ffprobe per file.
- other videos and audio: size and file date only.

The capture date follows the one rule of backend/media_dates.py (EXIF or the video's
own date, else birthtime, else mtime), so the index and the timeline walk agree.
A file that could not be read (an OSError with an errno) raises, so the caller can
tell 'no metadata' apart from 'try again later'.
"""
import math
import os
import re
import struct
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend import media_dates
from backend.config import RAW_PHOTO_EXTENSIONS, media_type

QUICKTIME_INFO_EXTENSIONS = media_dates.QUICKTIME_EXTENSIONS | {'.m4a'}

# Outcome of reading a file's own metadata, stored in media.meta_status
META_OK = "ok"          # read (whatever the file had)
META_NONE = "none"      # nothing to read: a format without metadata, or a file Pillow cannot open
META_ERROR = "error"    # could not be read (I/O, permission): read again next pass

_MAX_BOXES = 10000
_MAX_SMALL_BOX = 1 << 20   # udta/meta/keys/ilst payloads read whole only up to 1 MB


def _clean_text(value: Any) -> Optional[str]:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    if not isinstance(value, str):
        return None
    value = value.strip("\x00 \t\r\n")
    return value[:200] or None


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def _dms(value: Any) -> Optional[float]:
    """Degrees from an EXIF (degrees, minutes, seconds) triple of rationals."""
    try:
        d, m, s = (float(v) for v in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    total = d + m / 60.0 + s / 3600.0
    return total if math.isfinite(total) else None


def _position(lat: Optional[float], lon: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """A usable GPS position, or (None, None): out of range, or exactly 0,0 (no fix)."""
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
        return None, None
    return round(lat, 7), round(lon, 7)


def _exif_gps(exif) -> Dict[str, Optional[float]]:
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:
        return {}
    if not gps:
        return {}
    lat, lon = _dms(gps.get(2)), _dms(gps.get(4))
    if lat is not None and str(gps.get(1, "N")).upper().startswith("S"):
        lat = -lat
    if lon is not None and str(gps.get(3, "E")).upper().startswith("W"):
        lon = -lon
    lat, lon = _position(lat, lon)
    if lat is None:
        return {}
    out: Dict[str, Optional[float]] = {"lat": lat, "lon": lon}
    altitude = _number(gps.get(6))
    if altitude is not None:
        ref = gps.get(5)
        if ref in (1, b"\x01"):
            altitude = -altitude
        out["altitude"] = round(altitude, 2)
    return out


def image_metadata(path: Path) -> Tuple[Dict[str, Any], str]:
    """
    (fields, meta_status) of a photo: width, height (as shown, after the EXIF rotation),
    orientation, camera_make, camera_model, lens, lat, lon, altitude, exif_taken.
    Raises OSError when the file could not be read.
    """
    from backend import thumbnail  # registers the HEIC opener; open_unchecked skips the size check
    raw = path.suffix.lower() in RAW_PHOTO_EXTENSIONS
    fields: Dict[str, Any] = {}
    try:
        with thumbnail.open_unchecked(path) as img:
            width, height = img.size
            # Pillow decodes a whole PNG to look for EXIF after the pixel data: only an early eXIf chunk
            exif = None if img.format == "PNG" and "exif" not in img.info else img.getexif()
    except Exception as e:
        if media_dates._read_error(e):
            raise
        return fields, META_NONE
    if exif is not None:
        try:
            sub = exif.get_ifd(0x8769)
        except Exception:
            sub = {}
        orientation = exif.get(0x0112)
        if isinstance(orientation, int) and 1 <= orientation <= 8:
            fields["orientation"] = orientation
        fields["camera_make"] = _clean_text(exif.get(0x010F))
        fields["camera_model"] = _clean_text(exif.get(0x0110))
        fields["lens"] = _clean_text(sub.get(0xA434))
        fields.update(_exif_gps(exif))
        try:
            fields["exif_taken"] = media_dates.exif_timestamp(exif)
        except Exception:
            fields["exif_taken"] = None
        if raw:
            # A RAW file opened as TIFF shows its small preview: the real size is in the Exif IFD
            width, height = sub.get(0xA002), sub.get(0xA003)
    elif raw:
        width = height = None
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        if fields.get("orientation") in (5, 6, 7, 8):
            width, height = height, width
        fields["width"], fields["height"] = width, height
    return fields, META_OK


# ---------- QuickTime / MP4 ----------

def _iso6709(text: Optional[str]) -> Dict[str, float]:
    """'+52.3702+004.8952+001.000/' -> {lat, lon, altitude}."""
    if not text:
        return {}
    match = re.match(r"\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?", text)
    if not match:
        return {}
    lat, lon = _position(_number(match.group(1)), _number(match.group(2)))
    if lat is None:
        return {}
    out = {"lat": lat, "lon": lon}
    altitude = _number(match.group(3)) if match.group(3) else None
    if altitude is not None:
        out["altitude"] = round(altitude, 2)
    return out


def _children(f, start: int, end: int):
    """(type, payload start, box end) of each box between start and end."""
    pos = start
    for _ in range(_MAX_BOXES):
        if pos + 8 > end:
            return
        f.seek(pos)
        box = media_dates._box_header(f, end)
        if box is None:
            return
        kind, header, size = box
        yield kind, pos + header, pos + size
        pos += size


def _read_payload(f, start: int, end: int) -> bytes:
    if end - start > _MAX_SMALL_BOX or end < start:
        return b""
    f.seek(start)
    return f.read(end - start)


def _tkhd(data: bytes) -> Optional[Tuple[int, int, int]]:
    """(width, height, rotation in degrees) of a track header, or None."""
    if not data:
        return None
    version = data[0]
    offset = 4 + (32 if version == 1 else 20) + 8 + 8   # times, track id, duration; reserved; layer..volume
    matrix_end = offset + 36
    if len(data) < matrix_end + 8:
        return None
    a, b, _u, c, d = struct.unpack(">iiiii", data[offset:offset + 20])
    width, height = struct.unpack(">II", data[matrix_end:matrix_end + 8])
    width, height = width >> 16, height >> 16
    rotation = int(round(math.degrees(math.atan2(b, a)))) % 360 if (a or b) else 0
    return width, height, rotation


def _meta_items(f, start: int, end: int) -> Dict[str, Any]:
    """The mdta key/value items (com.apple.quicktime.*) of a QuickTime 'meta' box."""
    # In an MP4 'meta' is a full box (4 bytes version/flags before its children)
    f.seek(start)
    head = f.read(8)
    if len(head) == 8 and head[:4] == b"\x00\x00\x00\x00" and head[4:8] != b"hdlr":
        start += 4
    keys = []
    values: Dict[int, bytes] = {}
    for kind, payload, box_end in _children(f, start, end):
        if kind == b"keys":
            data = _read_payload(f, payload, box_end)
            if len(data) < 8:
                continue
            count = struct.unpack(">I", data[4:8])[0]
            pos = 8
            for _ in range(min(count, 1000)):
                if pos + 8 > len(data):
                    break
                size = struct.unpack(">I", data[pos:pos + 4])[0]
                if size < 8 or pos + size > len(data):
                    break
                keys.append(data[pos + 8:pos + size].decode("utf-8", "ignore"))
                pos += size
        elif kind == b"ilst":
            for item, item_start, item_end in _children(f, payload, box_end):
                index = struct.unpack(">I", item)[0]
                for dkind, dstart, dend in _children(f, item_start, item_end):
                    if dkind == b"data":
                        data = _read_payload(f, dstart, dend)
                        if len(data) > 8:
                            values[index] = data[8:]
                        break
    items: Dict[str, Any] = {}
    for index, value in values.items():
        if 1 <= index <= len(keys):
            items[keys[index - 1]] = value.decode("utf-8", "ignore")
    return items


def quicktime_info(path: Path) -> Dict[str, Any]:
    """
    created (UTC epoch, plausible only), duration (s), width, height (as shown, after the
    track rotation), lat, lon, altitude, camera_make, camera_model of an MP4/MOV/M4A.
    Reads only box headers and a few small boxes. Raises OSError when the file could
    not be read; a damaged or unknown structure just gives fewer fields.
    """
    info: Dict[str, Any] = {}
    try:
        with open(path, "rb") as f:
            end = os.fstat(f.fileno()).st_size
            for kind, payload, box_end in _children(f, 0, end):
                if kind != b"moov":
                    continue
                for ckind, cstart, cend in _children(f, payload, box_end):
                    if ckind == b"mvhd":
                        f.seek(cstart)
                        data = f.read(32)
                        if len(data) < 20:
                            continue
                        if data[0] == 1 and len(data) >= 32:
                            created, _mod, scale, duration = struct.unpack(">QQIQ", data[4:32])
                        else:
                            created, _mod, scale, duration = struct.unpack(">IIII", data[4:20])
                        if created:
                            ts = media_dates._plausible(float(created - media_dates._QT_EPOCH_OFFSET))
                            if ts is not None:
                                info["created"] = ts
                        if scale and duration and duration not in (0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
                            info["duration"] = round(duration / scale, 3)
                    elif ckind == b"trak" and "width" not in info:
                        for tkind, tstart, tend in _children(f, cstart, cend):
                            if tkind == b"tkhd":
                                f.seek(tstart)
                                header = _tkhd(f.read(min(tend - tstart, 128)))
                                if header and header[0] and header[1]:
                                    width, height, rotation = header
                                    if rotation in (90, 270):
                                        width, height = height, width
                                    info["width"], info["height"] = width, height
                                    info["rotation"] = rotation
                                break
                    elif ckind == b"udta":
                        for ukind, ustart, uend in _children(f, cstart, cend):
                            # QuickTime user data text: 2 bytes length, 2 bytes language, the text
                            if ukind == b"\xa9xyz" and "lat" not in info:
                                data = _read_payload(f, ustart, uend)
                                if len(data) > 4:
                                    info.update(_iso6709(data[4:].decode("utf-8", "ignore")))
                            elif ukind in _UDTA_TEXT and not info.get(_UDTA_TEXT[ukind]):
                                data = _read_payload(f, ustart, uend)
                                if len(data) > 4:
                                    info[_UDTA_TEXT[ukind]] = _clean_text(data[4:])
                            elif ukind == b"meta":
                                _apply_meta(info, _meta_items(f, ustart, uend))
                    elif ckind == b"meta":
                        _apply_meta(info, _meta_items(f, cstart, cend))
                break
    except (struct.error, UnicodeDecodeError):
        pass
    return info


_UDTA_TEXT = {b"\xa9mak": "camera_make", b"\xa9mod": "camera_model"}


def _apply_meta(info: Dict[str, Any], items: Dict[str, Any]) -> None:
    if "lat" not in info:
        info.update(_iso6709(items.get("com.apple.quicktime.location.ISO6709")))
    for key, field in (("com.apple.quicktime.make", "camera_make"), ("com.apple.quicktime.model", "camera_model")):
        if items.get(key) and not info.get(field):
            info[field] = _clean_text(items[key])


# ---------- one file ----------

def read_metadata(path: Path, st: os.stat_result) -> Dict[str, Any]:
    """
    The media row fields of one file (without path, folder and the index's own times):
    kind, ext, size, mtime_ns, inode, birthtime, taken, taken_source, width, height,
    orientation, camera_make, camera_model, lens, lat, lon, altitude, duration, meta_status.
    Raises OSError (with an errno) when the file could not be read.
    """
    path = Path(path)
    ext = path.suffix.lower()
    kind = media_type(path)
    birthtime = getattr(st, "st_birthtime", None)
    row: Dict[str, Any] = {
        "kind": kind, "ext": ext, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "inode": st.st_ino,
        "birthtime": birthtime if birthtime and birthtime > 0 else None,
        "taken": None, "taken_source": None, "width": None, "height": None, "orientation": None,
        "camera_make": None, "camera_model": None, "lens": None, "lat": None, "lon": None,
        "altitude": None, "duration": None, "meta_status": META_NONE,
    }
    embedded = None
    if kind == "photo":
        fields, row["meta_status"] = image_metadata(path)
        embedded = fields.pop("exif_taken", None)
        if embedded is not None:
            row["taken"], row["taken_source"] = embedded, "exif"
        row.update({k: v for k, v in fields.items() if k in row})
    elif ext in QUICKTIME_INFO_EXTENSIONS:
        info = quicktime_info(path)
        row["meta_status"] = META_OK
        if info.get("created") is not None and ext in media_dates.QUICKTIME_EXTENSIONS:
            embedded = info["created"]
            row["taken"], row["taken_source"] = embedded, "video"
        for key in ("duration", "width", "height", "lat", "lon", "altitude", "camera_make", "camera_model"):
            if info.get(key) is not None:
                row[key] = info[key]
        if info.get("rotation") in (90, 270):
            row["orientation"] = 6 if info["rotation"] == 90 else 8
    if row["taken"] is None:
        row["taken"], row["taken_source"] = media_dates.file_date(st)
    return row
