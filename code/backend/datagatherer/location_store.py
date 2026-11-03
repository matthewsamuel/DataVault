"""
DataGatherer - location history (tables location_points and location_visits, migration 110)

Google has written the user's location history in four formats over the years; parse()
recognises each one by its CONTENT (the file names differ per language, phone and year):

    records     the old Takeout 'Locatiegeschiedenis'/'Location History' Records.json:
                {"locations": [{latitudeE7, longitudeE7, accuracy, timestamp|timestampMs}]}
    semantic    the old Takeout Semantic Location History (one file per month, for example
                2019_JULI.json): {"timelineObjects": [{placeVisit}|{activitySegment}]}
    device      the new on-device Timeline export from Android (Timeline.json):
                {"semanticSegments": [...], "rawSignals": [...]}, coordinates as '50.85°, 4.35°'
    ios         the on-device Timeline export from the iPhone's Google Maps app
                (location-history.json): a list of {startTime, endTime, visit|activity|timelinePath},
                coordinates as 'geo:50.85,4.35'

Everything becomes points (time in UTC ms, lat, lon, accuracy in metres when known) and
visits (a span of time at one place). Coordinates are rounded to 6 decimals (about 10 cm),
so the same point from two overlapping exports is one row (UNIQUE(ts_ms, lat, lon)).
"""
import io
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from backend.datagatherer import state
from backend.datagatherer.base import Coverage

BATCH = 5000
DEFAULT_ACCURACY = None

_GEO = re.compile(r"^\s*(?:geo:)?\s*(-?\d+(?:\.\d+)?)\s*°?\s*,\s*(-?\d+(?:\.\d+)?)\s*°?\s*$")


class NotLocationHistory(ValueError):
    pass


# ---------- parsing ----------

def parse_time(value: Any) -> Optional[int]:
    """ISO 8601 (with Z or an offset) or epoch milliseconds (as text or a number) -> UTC ms."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def parse_coord(value: Any) -> Optional[Tuple[float, float]]:
    """'geo:50.85,4.35', '50.85°, 4.35°' or {'latLng': ...} -> (lat, lon), None when not valid."""
    if isinstance(value, dict):
        value = value.get("latLng") or value.get("LatLng") or value.get("point")
    if not isinstance(value, str):
        return None
    m = _GEO.match(value)
    if not m:
        return None
    return _valid(float(m.group(1)), float(m.group(2)))


def _valid(lat: Any, lon: Any) -> Optional[Tuple[float, float]]:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:
        return None
    return round(lat, 6), round(lon, 6)


def _e7(obj: Dict[str, Any], lat_key="latitudeE7", lon_key="longitudeE7") -> Optional[Tuple[float, float]]:
    lat = obj.get(lat_key, obj.get("latE7"))
    lon = obj.get(lon_key, obj.get("lngE7"))
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    # Old Records.json has a known overflow: values beyond 180e7 are negative numbers wrapped round
    if lat > 900000000:
        lat -= 4294967296
    if lon > 1800000000:
        lon -= 4294967296
    return _valid(lat / 1e7, lon / 1e7)


def _num(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v >= 0 else None


def detect_format(data: Any) -> Optional[str]:
    if isinstance(data, dict):
        if isinstance(data.get("locations"), list):
            return "records"
        if isinstance(data.get("timelineObjects"), list):
            return "semantic"
        if isinstance(data.get("semanticSegments"), list) or isinstance(data.get("rawSignals"), list):
            return "device"
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data[:20]):
        if any("startTime" in x and ("visit" in x or "activity" in x or "timelinePath" in x) for x in data[:50]):
            return "ios"
    return None


def looks_like_location_json(head: bytes) -> bool:
    """Cheap check on the first bytes of a JSON file (the Downloads watcher)."""
    text = head[:4096].decode("utf-8", "ignore")
    return any(k in text for k in ('"locations"', '"timelineObjects"', '"semanticSegments"', '"rawSignals"')) or (
        text.lstrip().startswith("[") and '"startTime"' in text and
        any(k in text for k in ('"visit"', '"activity"', '"timelinePath"')))


def parse(data: Any) -> Tuple[str, List[tuple], List[dict]]:
    """(format, points [(ts_ms, lat, lon, accuracy)], visits [{start, end, lat, lon, ...}])."""
    fmt = detect_format(data)
    if fmt is None:
        raise NotLocationHistory("Dit is geen locatiegeschiedenis van Google")
    points: List[tuple] = []
    visits: List[dict] = []
    {"records": _records, "semantic": _semantic, "device": _device, "ios": _ios}[fmt](data, points, visits)
    return fmt, points, visits


def _record_point(loc: Any) -> Optional[tuple]:
    if not isinstance(loc, dict):
        return None
    ts = parse_time(loc.get("timestamp") or loc.get("timestampMs"))
    pos = _e7(loc)
    return (ts, pos[0], pos[1], _num(loc.get("accuracy"))) if ts is not None and pos else None


def _records(data, points, visits) -> None:
    for loc in data["locations"]:
        point = _record_point(loc)
        if point:
            points.append(point)


def _duration(obj: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    d = obj.get("duration") or {}
    return (parse_time(d.get("startTimestamp") or d.get("startTimestampMs")),
            parse_time(d.get("endTimestamp") or d.get("endTimestampMs")))


def _semantic(data, points, visits) -> None:
    for item in data["timelineObjects"]:
        if not isinstance(item, dict):
            continue
        visit = item.get("placeVisit")
        if isinstance(visit, dict):
            loc = visit.get("location") or {}
            start, end = _duration(visit)
            pos = _e7(loc)
            if start is not None and end is not None and pos:
                visits.append({"start": start, "end": end, "lat": pos[0], "lon": pos[1],
                               "place_id": loc.get("placeId"), "name": loc.get("name"), "address": loc.get("address"),
                               "semantic_type": loc.get("semanticType"),
                               "probability": _num(visit.get("visitConfidence")) / 100 if _num(visit.get("visitConfidence")) else None})
                points.append((start, pos[0], pos[1], None))
                points.append((end, pos[0], pos[1], None))
        seg = item.get("activitySegment")
        if isinstance(seg, dict):
            start, end = _duration(seg)
            a, b = _e7(seg.get("startLocation") or {}), _e7(seg.get("endLocation") or {})
            if start is not None and a:
                points.append((start, a[0], a[1], None))
            if end is not None and b:
                points.append((end, b[0], b[1], None))
            for p in ((seg.get("simplifiedRawPath") or {}).get("points") or []):
                ts = parse_time(p.get("timestamp") or p.get("timestampMs"))
                pos = _e7(p)
                if ts is not None and pos:
                    points.append((ts, pos[0], pos[1], _num(p.get("accuracyMeters"))))


def _device(data, points, visits) -> None:
    for seg in data.get("semanticSegments") or []:
        if not isinstance(seg, dict):
            continue
        start, end = parse_time(seg.get("startTime")), parse_time(seg.get("endTime"))
        _segment(seg, start, end, points, visits, path_key="timelinePath")
    for sig in data.get("rawSignals") or []:
        pos = (sig or {}).get("position") if isinstance(sig, dict) else None
        if isinstance(pos, dict):
            ts = parse_time(pos.get("timestamp"))
            coord = parse_coord(pos.get("LatLng") or pos.get("latLng"))
            if ts is not None and coord:
                points.append((ts, coord[0], coord[1], _num(pos.get("accuracyMeters"))))


def _ios(data, points, visits) -> None:
    for seg in data:
        if not isinstance(seg, dict):
            continue
        start, end = parse_time(seg.get("startTime")), parse_time(seg.get("endTime"))
        _segment(seg, start, end, points, visits, path_key="timelinePath")


def _segment(seg, start, end, points, visits, path_key) -> None:
    """One segment of the device and iPhone formats: a visit, an activity or a path."""
    visit = seg.get("visit")
    if isinstance(visit, dict) and start is not None and end is not None:
        top = visit.get("topCandidate") or {}
        pos = parse_coord(top.get("placeLocation"))
        if pos:
            prob = _num(top.get("probability"))
            visits.append({"start": start, "end": end, "lat": pos[0], "lon": pos[1],
                           "place_id": top.get("placeId") or top.get("placeID"), "name": None, "address": None,
                           "semantic_type": top.get("semanticType"), "probability": prob})
            points.append((start, pos[0], pos[1], None))
            points.append((end, pos[0], pos[1], None))
    activity = seg.get("activity")
    if isinstance(activity, dict):
        a, b = parse_coord(activity.get("start")), parse_coord(activity.get("end"))
        if start is not None and a:
            points.append((start, a[0], a[1], None))
        if end is not None and b:
            points.append((end, b[0], b[1], None))
    for p in seg.get(path_key) or []:
        if not isinstance(p, dict):
            continue
        pos = parse_coord(p.get("point"))
        ts = parse_time(p.get("time"))
        if ts is None and start is not None:
            offset = _num(p.get("durationMinutesOffsetFromStartTime"))
            ts = start + int(offset * 60000) if offset is not None else None
        if ts is not None and pos:
            points.append((ts, pos[0], pos[1], None))


def parse_bytes(blob: bytes) -> Tuple[str, List[tuple], List[dict]]:
    try:
        data = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise NotLocationHistory("Geen leesbare JSON") from e
    return parse(data)


# ---------- a large Records.json, read as it goes ----------

STREAM_FROM = 32 * 1024 * 1024          # smaller files are simply parsed whole
STREAM_CHUNK = 1024 * 1024               # characters per read
_RECORDS_HEAD = re.compile(r'\ufeff?\s*\{\s*"locations"\s*:\s*\[')
_BETWEEN = re.compile(r"[\s,]*")


def iter_records(opener, chunk: int = STREAM_CHUNK) -> Iterator[tuple]:
    """
    The points of a Records.json ({"locations": [...]}, Android's full location history,
    often 1-2 GB) one location object at a time: memory stays at one chunk plus one object,
    however long the history. opener() returns a binary stream.
    """
    decoder = json.JSONDecoder()
    with opener() as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig")
        buf, eof = "", False
        while len(buf) < 4096 and not eof:      # enough to see the opening '{"locations": ['
            more = text.read(chunk)
            buf, eof = buf + more, not more
        head = _RECORDS_HEAD.match(buf)
        if head is None:
            raise NotLocationHistory("Dit is geen Records.json van Google")
        pos = head.end()
        while True:
            pos = _BETWEEN.match(buf, pos).end()
            if pos >= len(buf) and not eof:
                more = text.read(chunk)
                buf, pos, eof = buf[pos:] + more, 0, not more
                continue
            if pos >= len(buf):
                raise NotLocationHistory("Records.json houdt halverwege op")
            if buf[pos] == "]":
                return
            try:
                obj, end = decoder.raw_decode(buf, pos)
            except ValueError:
                if eof:
                    raise NotLocationHistory("Records.json is beschadigd")
                more = text.read(chunk)       # the object runs on in the next chunk
                buf, pos, eof = buf[pos:] + more, 0, not more
                continue
            pos = end
            point = _record_point(obj)
            if point:
                yield point


def parse_export_file(read) -> Tuple[str, Iterable[tuple], List[dict]]:
    """
    parse_bytes for one file of an export (raw_store.ExportFile, or any read()): a large
    Records.json is streamed (its points come as an iterator), anything else is parsed whole.
    """
    opener = getattr(read, "open", None)
    if opener is not None and (getattr(read, "size", 0) or 0) > STREAM_FROM:
        with opener() as f:
            head = f.read(4096).decode("utf-8", "ignore")
        if _RECORDS_HEAD.match(head):
            return "records", iter_records(opener), []
    return parse_bytes(read())


# ---------- storing ----------

def insert(db, origin: str, raw_import_id: Optional[int], fmt: str, points: Iterable[tuple],
           visits: Iterable[dict], check=None) -> Dict[str, Any]:
    """Add points and visits (INSERT OR IGNORE); {added, seen, visits_added, covers_from, covers_until}."""
    added = seen = visits_added = 0
    first = last = None
    batch: List[tuple] = []

    def flush():
        nonlocal added
        if not batch:
            return
        with db._connect() as conn:
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO location_points (ts_ms, lat, lon, accuracy, source, origin, "
                             "raw_import_id) VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
            added += conn.total_changes - before
            if conn.total_changes != before:
                state.bump_version(conn, origin)
        batch.clear()
        if check is not None:
            check()

    for ts, lat, lon, acc in points:
        seen += 1
        first = ts if first is None or ts < first else first
        last = ts if last is None or ts > last else last
        batch.append((ts, lat, lon, acc, fmt, origin, raw_import_id))
        if len(batch) >= BATCH:
            flush()
    flush()
    rows = []
    for v in visits:
        place_key = v.get("place_id") or f"{v['lat']:.5f},{v['lon']:.5f}"
        rows.append((v["start"], v["end"], v["lat"], v["lon"], str(place_key)[:200], v.get("name"), v.get("address"),
                     v.get("semantic_type"), v.get("probability"), fmt, origin, raw_import_id))
    if rows:
        with db._connect() as conn:
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO location_visits (start_ms, end_ms, lat, lon, place_key, name, address, "
                             "semantic_type, probability, source, origin, raw_import_id) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            visits_added = conn.total_changes - before
            if visits_added:
                state.bump_version(conn, origin)
    return {"added": added, "seen": seen, "visits_added": visits_added, "format": fmt,
            "covers_from": first / 1000 if first is not None else None,
            "covers_until": last / 1000 if last is not None else None}


def coverage(db, origin: Optional[str] = None) -> Coverage:
    where, args = ("origin = ?", [origin]) if origin else ("1 = 1", [])
    with db._connect() as conn:
        count, first, last = conn.execute(
            f"SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM location_points WHERE {where}", args).fetchone()
    if not count:
        return Coverage()
    return Coverage(records=count, first=first / 1000, last=last / 1000, segments=[(first / 1000, last / 1000, False)])


def total_points(db) -> int:
    with db._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM location_points").fetchone()[0]


# ---------- reading ----------

def track(db, start_ms: int, end_ms: int, limit: int = 20000) -> Dict[str, Any]:
    """The points and visits in [start_ms, end_ms), oldest first."""
    with db._connect() as conn:
        pts = conn.execute("SELECT ts_ms, lat, lon, accuracy FROM location_points WHERE ts_ms >= ? AND ts_ms < ? "
                           "ORDER BY ts_ms LIMIT ?", (start_ms, end_ms, limit + 1)).fetchall()
        vis = conn.execute("SELECT start_ms, end_ms, lat, lon, name, address, semantic_type FROM location_visits "
                           "WHERE start_ms < ? AND end_ms >= ? ORDER BY start_ms LIMIT 500", (end_ms, start_ms)).fetchall()
    return {
        "points": [[r[0], r[1], r[2], r[3]] for r in pts[:limit]],
        "truncated": len(pts) > limit,
        "visits": [{"start": r[0], "end": r[1], "lat": r[2], "lon": r[3], "name": r[4], "address": r[5],
                    "semantic_type": r[6]} for r in vis],
    }


def days(db) -> List[Dict[str, Any]]:
    """[{day, count}] for every local day with location points, oldest first (cached until the data changes)."""
    return state.cached(db, "location_days", lambda: _days(db))


def _days(db) -> List[Dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute("SELECT date(ts_ms / 1000, 'unixepoch', 'localtime') AS day, COUNT(*) FROM location_points "
                            "GROUP BY day ORDER BY day").fetchall()
    return [{"day": d, "count": n} for d, n in rows]


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def neighbours(conn, ts_ms: int, max_accuracy: Optional[float]) -> Tuple[Optional[tuple], Optional[tuple]]:
    """The last point at or before ts_ms and the first after it (accuracy within max_accuracy)."""
    acc = "" if max_accuracy is None else " AND (accuracy IS NULL OR accuracy <= ?)"
    extra = () if max_accuracy is None else (max_accuracy,)
    before = conn.execute(f"SELECT ts_ms, lat, lon, accuracy FROM location_points WHERE ts_ms <= ?{acc} "
                          f"ORDER BY ts_ms DESC LIMIT 1", (ts_ms, *extra)).fetchone()
    after = conn.execute(f"SELECT ts_ms, lat, lon, accuracy FROM location_points WHERE ts_ms > ?{acc} "
                         f"ORDER BY ts_ms LIMIT 1", (ts_ms, *extra)).fetchone()
    return before, after


def visit_at(conn, ts_ms: int) -> Optional[tuple]:
    """A visit that spans ts_ms (the most probable one when several do)."""
    return conn.execute("SELECT lat, lon, name, address, probability, start_ms, end_ms FROM location_visits "
                        "WHERE start_ms <= ? AND start_ms >= ? AND end_ms >= ? "
                        "ORDER BY COALESCE(probability, 0.5) DESC, start_ms DESC LIMIT 1",
                        (ts_ms, ts_ms - 3 * 86400000, ts_ms)).fetchone()


def estimate(conn, ts_ms: int, max_gap_s: float, max_accuracy: Optional[float]) -> Optional[Dict[str, Any]]:
    """
    Where the user probably was at ts_ms (UTC ms), from the location history:
      bezoek        a visit (a place) spans the moment
      interpolatie  a point before and after, both within max_gap_s: linear in between
      dichtstbij    only one point within half of max_gap_s
    {lat, lon, confidence 0..1, accuracy_m, gap_s, method} or None when nothing is close enough.
    """
    visit = visit_at(conn, ts_ms)
    if visit is not None:
        prob = visit[4] if visit[4] is not None else 0.8
        return {"lat": visit[0], "lon": visit[1], "confidence": round(min(0.95, 0.6 + 0.35 * prob), 3),
                "accuracy_m": 100.0, "gap_s": 0.0, "method": "bezoek"}
    before, after = neighbours(conn, ts_ms, max_accuracy)
    max_gap_ms = max_gap_s * 1000
    gb = ts_ms - before[0] if before else None
    ga = after[0] - ts_ms if after else None
    if before and after and gb <= max_gap_ms and ga <= max_gap_ms:
        span = after[0] - before[0]
        f = gb / span if span else 0.0
        lat = before[1] + (after[1] - before[1]) * f
        lon = before[2] + (after[2] - before[2]) * f
        dist = km(before[1], before[2], after[1], after[2])
        hours = max(span / 3600000, 1e-6)
        acc = max(before[3] or 50.0, after[3] or 50.0) + dist * 1000 * min(f, 1 - f)
        gap = max(gb, ga) / 1000
        conf = 1.0 - 0.5 * (gap / max_gap_s) - min(0.4, acc / 5000)
        if dist / hours > 250:          # faster than a car: a flight or a jump in the data
            conf -= 0.3
        return {"lat": round(lat, 6), "lon": round(lon, 6), "confidence": round(max(0.05, min(0.95, conf)), 3),
                "accuracy_m": round(acc, 1), "gap_s": round(gap, 1), "method": "interpolatie"}
    near = min((p for p, g in ((before, gb), (after, ga)) if p is not None and g <= max_gap_ms / 2),
               key=lambda p: abs(p[0] - ts_ms), default=None)
    if near is not None:
        gap = abs(near[0] - ts_ms) / 1000
        acc = (near[3] or 50.0) + 500.0 * gap / max_gap_s
        conf = 0.55 * (1.0 - gap / max_gap_s) - min(0.2, acc / 10000)
        return {"lat": near[1], "lon": near[2], "confidence": round(max(0.05, conf), 3),
                "accuracy_m": round(acc, 1), "gap_s": round(gap, 1), "method": "dichtstbij"}
    return None
