"""
DataGatherer - estimated positions for photos without GPS, from the location history

For every photo or video in the media index that has a real capture time (EXIF, or the
video's own creation time) but no GPS position, estimate where it was taken from the
location history around that moment (location_store.estimate: a visit spanning it, or
interpolation between two points at most max_gap apart). The estimate goes ONLY into the
database (media_location_inferred, source 'tijdlijn'): no image file is ever written.

Time zones: the EXIF capture time is the camera's local wall-clock time; location history
is in UTC. In this order:
  1. a video's creation time (QuickTime) is already UTC                       tz_source 'utc'
  2. the photo's EXIF OffsetTimeOriginal (+02:00), when the camera wrote it    'exif'
  3. the time zone at the place: a first guess with DEFAULT_ZONE gives the rough
     position, timezones.zone_at() its zone, and the time is converted again    'locatie'
  4. nothing found: Europe/Brussels with daylight saving time is assumed        'aangenomen'
media.taken holds the EXIF wall time read as this Mac's local time (media_dates), so
datetime.fromtimestamp(taken) gives the wall time back.
"""
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.datagatherer import timezones

DEFAULT_MAX_GAP_MINUTES = 30
DEFAULT_MAX_ACCURACY_M = 500
MIN_CONFIDENCE = 0.2
SETTINGS_KEY = "location_geotag"       # app_settings: {max_gap_minutes, max_accuracy_m}


def settings(db) -> Dict[str, Any]:
    stored = db.get_setting(SETTINGS_KEY, None) or {}
    gap = stored.get("max_gap_minutes", DEFAULT_MAX_GAP_MINUTES)
    acc = stored.get("max_accuracy_m", DEFAULT_MAX_ACCURACY_M)
    return {"max_gap_minutes": gap if isinstance(gap, (int, float)) and 1 <= gap <= 360 else DEFAULT_MAX_GAP_MINUTES,
            "max_accuracy_m": acc if isinstance(acc, (int, float)) and 10 <= acc <= 10000 else DEFAULT_MAX_ACCURACY_M}


def exif_offset(path: str) -> Optional[timedelta]:
    """EXIF OffsetTimeOriginal (0x9011, or OffsetTime 0x9010) of a photo, read-only; None when absent."""
    try:
        from backend import thumbnail
        with thumbnail.open_unchecked(Path(path)) as img:
            exif = img.getexif()
            sub = exif.get_ifd(0x8769)
            value = sub.get(0x9011) or sub.get(0x9010) or exif.get(0x9011)
    except Exception:
        return None
    return parse_offset(value)


def parse_offset(value: Any) -> Optional[timedelta]:
    if not isinstance(value, str):
        return None
    text = value.strip("\x00 ")
    if len(text) != 6 or text[0] not in "+-" or text[3] != ":":
        return None
    try:
        hours, minutes = int(text[1:3]), int(text[4:6])
    except ValueError:
        return None
    if hours > 14 or minutes > 59:
        return None
    delta = timedelta(hours=hours, minutes=minutes)
    return -delta if text[0] == "-" else delta


def wall_time(taken: float) -> datetime:
    """The naive wall-clock time media_dates stored as a local-time epoch."""
    return datetime.fromtimestamp(taken)


def to_utc_ms(wall: datetime, zone: str) -> int:
    return int(wall.replace(tzinfo=ZoneInfo(zone)).timestamp() * 1000)


def capture_utc(conn, row: Dict[str, Any], opts: Dict[str, Any],
                read_offset: Callable[[str], Optional[timedelta]] = exif_offset) -> Tuple[int, Optional[str], str]:
    """(UTC ms, zone or offset text, tz_source) of one media row {path, kind, taken, taken_source}."""
    from backend.datagatherer import location_store
    if row["taken_source"] == "video":
        return int(row["taken"] * 1000), "UTC", "utc"
    wall = wall_time(row["taken"])
    offset = read_offset(row["path"]) if row["kind"] == "photo" else None
    if offset is not None:
        utc = wall.replace(tzinfo=timezone(offset))
        sign = "-" if offset < timedelta(0) else "+"
        minutes = abs(int(offset.total_seconds())) // 60
        return int(utc.timestamp() * 1000), f"{sign}{minutes // 60:02d}:{minutes % 60:02d}", "exif"
    guess = to_utc_ms(wall, timezones.DEFAULT_ZONE)
    # Where roughly? A wide window: the zone only needs the country
    rough = location_store.estimate(conn, guess, 12 * 3600, None)
    if rough is not None:
        zone, how = timezones.zone_at(rough["lat"], rough["lon"])
        if zone:
            return to_utc_ms(wall, zone), zone, "locatie"
    return guess, timezones.DEFAULT_ZONE, "aangenomen"


def candidates_sql(full: bool) -> str:
    """Media with a capture time and no GPS (and, unless full, no estimate yet), within the history."""
    return ("SELECT m.id, m.path, m.kind, m.taken, m.taken_source FROM media m "
            + ("" if full else "LEFT JOIN media_location_inferred i ON i.media_id = m.id ")
            + "WHERE m.missing = 0 AND m.lat IS NULL AND m.kind IN ('photo', 'video') "
              "AND m.taken_source IN ('exif', 'video') AND m.taken BETWEEN ? AND ? "
            + ("" if full else "AND i.media_id IS NULL ")
            + "AND m.id > ? ORDER BY m.id LIMIT ?")


def run(db, check: Callable[[], None] = lambda: None, progress: Callable[..., None] = lambda *a, **k: None,
        full: bool = False, read_offset: Callable[[str], Optional[timedelta]] = exif_offset) -> Dict[str, Any]:
    """
    Estimate positions for every candidate (full: also the ones estimated before, for
    example after new location history came in). {checked, estimated, skipped}.
    """
    from backend.datagatherer import location_store
    opts = settings(db)
    max_gap_s = opts["max_gap_minutes"] * 60
    with db._connect() as conn:
        first, last = conn.execute("SELECT MIN(ts_ms), MAX(ts_ms) FROM location_points").fetchone()
    stats = {"checked": 0, "estimated": 0, "skipped": 0}
    if first is None:
        return stats
    # A capture time up to 14 hours of time-zone difference plus the gap outside the history can still match
    lo = first / 1000 - 14 * 3600 - max_gap_s
    hi = last / 1000 + 14 * 3600 + max_gap_s
    sql = candidates_sql(full)
    after = 0
    while True:
        check()
        with db._connect() as conn:
            rows = conn.execute(sql, (lo, hi, after, 500)).fetchall()
        if not rows:
            break
        out = []
        with db._connect() as conn:
            for mid, path, kind, taken, source in rows:
                after = mid
                stats["checked"] += 1
                row = {"path": path, "kind": kind, "taken": taken, "taken_source": source}
                try:
                    utc_ms, tz, tz_source = capture_utc(conn, row, opts, read_offset)
                except (OverflowError, OSError, ValueError):
                    stats["skipped"] += 1
                    continue
                est = location_store.estimate(conn, utc_ms, max_gap_s, opts["max_accuracy_m"])
                if est is None or est["confidence"] < MIN_CONFIDENCE:
                    stats["skipped"] += 1
                    continue
                out.append((mid, est["lat"], est["lon"], est["confidence"], est["accuracy_m"], est["gap_s"],
                            est["method"], utc_ms, tz, tz_source, time.time()))
        if out:
            with db._connect() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO media_location_inferred (media_id, lat, lon, confidence, accuracy_m, gap_s, "
                    "method, source, taken_utc_ms, tz, tz_source, computed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'tijdlijn', ?, ?, ?, ?)", out)
            stats["estimated"] += len(out)
        progress(stats["checked"], None, f"Locaties schatten: {stats['estimated']} van {stats['checked']} foto's")
    return stats


def inferred(db, media_id: int) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        row = conn.execute("SELECT lat, lon, confidence, accuracy_m, gap_s, method, tz, tz_source FROM "
                           "media_location_inferred WHERE media_id = ?", (media_id,)).fetchone()
    if row is None:
        return None
    return {"lat": row[0], "lon": row[1], "confidence": row[2], "accuracy_m": row[3], "gap_s": row[4],
            "method": row[5], "tz": row[6], "tz_source": row[7], "source": "tijdlijn"}


def count(db) -> int:
    with db._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM media_location_inferred i JOIN media m ON m.id = i.media_id "
                            "WHERE m.missing = 0 AND m.lat IS NULL").fetchone()[0]
