"""
DataGatherer - the activity store (table activity_events, migration 110)

Everything a source recorded that the user did, as one kind of row: a YouTube video
watched, a Google search, a page visited, a calendar event, a Keep note, a Netflix title,
a TikTok video, a workout, ... Each row has:

    source    the connector id (google_takeout, chrome_history, netflix, ...)
    kind      what it is (KINDS has the Dutch label of every kind)
    ts_ms     when, in UTC milliseconds (end_ms for things that last)
    title     one line to show; url when there is one; details a small JSON object
    event_key stable per record, so reading the same export twice adds nothing

The connectors turn their exports into Event dicts and call insert(); the Activiteit view
(routes/history.py) and the assistant read through days(), events() and facets().
"""
import hashlib
import json
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.datagatherer import state
from backend.datagatherer.base import Coverage

BATCH = 2000

# kind -> (Dutch label, Font Awesome icon); a kind not listed here shows as 'Activiteit'
KINDS: Dict[str, Tuple[str, str]] = {
    "youtube_watch": ("YouTube bekeken", "fa-circle-play"),
    "youtube_search": ("YouTube gezocht", "fa-magnifying-glass"),
    "search": ("Gezocht", "fa-magnifying-glass"),
    "maps": ("Maps", "fa-map-location-dot"),
    "web_visit": ("Website bezocht", "fa-globe"),
    "calendar": ("Agenda", "fa-calendar-day"),
    "note": ("Notitie", "fa-note-sticky"),
    "activity": ("Activiteit", "fa-clock-rotate-left"),
    "video_watch": ("Gekeken", "fa-tv"),
    "like": ("Leuk gevonden", "fa-heart"),
    "memory": ("Herinnering", "fa-camera"),
    "chat": ("Chatbericht", "fa-comment"),
    "music": ("Muziek", "fa-music"),
    "steps": ("Stappen", "fa-shoe-prints"),
    "workout": ("Training", "fa-person-running"),
}


def key(*parts: Any) -> str:
    """A stable record key from the parts that identify it (sha1, shortened)."""
    text = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(text.encode("utf-8", "surrogatepass")).hexdigest()[:24]


def label(kind: str) -> str:
    return KINDS.get(kind, KINDS["activity"])[0]


def event(kind: str, ts_ms: int, event_key: str, title: Optional[str] = None, url: Optional[str] = None,
          details: Optional[Dict[str, Any]] = None, end_ms: Optional[int] = None) -> Dict[str, Any]:
    return {"kind": kind, "ts_ms": int(ts_ms), "end_ms": None if end_ms is None else int(end_ms),
            "title": (title or "")[:500] or None, "url": (url or "")[:2000] or None,
            "details": details or None, "key": event_key}


def insert(db, source: str, raw_import_id: Optional[int], events: Iterable[Dict[str, Any]],
           check=None) -> Dict[str, Any]:
    """
    Add the events of one export (INSERT OR IGNORE on (source, event_key)): {added, seen,
    covers_from, covers_until} with the covered span in epoch seconds. check() is called
    between batches (a job's stop check).
    """
    added = seen = 0
    first = last = None
    batch: List[tuple] = []
    now = time.time()

    def flush():
        nonlocal added
        if not batch:
            return
        with db._connect() as conn:
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO activity_events (source, kind, ts_ms, end_ms, title, url, details, event_key, "
                "raw_import_id, first_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
            added += conn.total_changes - before
            if conn.total_changes != before:
                state.bump_version(conn, source)
        batch.clear()
        if check is not None:
            check()

    for ev in events:
        seen += 1
        ts = ev["ts_ms"]
        first = ts if first is None or ts < first else first
        last = ts if last is None or ts > last else last
        details = json.dumps(ev["details"], ensure_ascii=False, separators=(",", ":")) if ev.get("details") else None
        batch.append((source, ev["kind"], ts, ev.get("end_ms"), ev.get("title"), ev.get("url"), details, ev["key"],
                      raw_import_id, now))
        if len(batch) >= BATCH:
            flush()
    flush()
    return {"added": added, "seen": seen,
            "covers_from": first / 1000 if first is not None else None,
            "covers_until": last / 1000 if last is not None else None}


def coverage(db, source: str, kinds: Optional[Iterable[str]] = None) -> Coverage:
    where, args = "source = ?", [source]
    if kinds:
        kinds = list(kinds)
        where += f" AND kind IN ({', '.join('?' * len(kinds))})"
        args += kinds
    with db._connect() as conn:
        count, first, last = conn.execute(
            f"SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM activity_events WHERE {where}", args).fetchone()
    if not count:
        return Coverage()
    return Coverage(records=count, first=first / 1000, last=last / 1000,
                    segments=[(first / 1000, last / 1000, False)])


# ---------- reading (the Activiteit view, the assistant) ----------

def _filters(sources: Optional[List[str]], kinds: Optional[List[str]], q: Optional[str] = None) -> Tuple[str, list]:
    where, args = ["1 = 1"], []
    if sources:
        where.append(f"source IN ({', '.join('?' * len(sources))})")
        args += sources
    if kinds:
        where.append(f"kind IN ({', '.join('?' * len(kinds))})")
        args += kinds
    if q:
        where.append("(title LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\')")
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like]
    return " AND ".join(where), args


def local_day_bounds(day: str) -> Tuple[int, int]:
    """[start, end) of a local calendar day 'YYYY-MM-DD' in UTC ms (this Mac's time zone)."""
    start = datetime.strptime(day, "%Y-%m-%d")
    end = datetime.fromordinal(start.toordinal() + 1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def days(db, sources=None, kinds=None, q=None, date_from: Optional[str] = None,
         date_to: Optional[str] = None, limit: int = 400) -> List[Dict[str, Any]]:
    """[{day, count, kinds: {kind: n}}], newest day first, local calendar days."""
    where, args = _filters(sources, kinds, q)
    if date_from:
        where += " AND ts_ms >= ?"
        args.append(local_day_bounds(date_from)[0])
    if date_to:
        where += " AND ts_ms < ?"
        args.append(local_day_bounds(date_to)[1])
    with db._connect() as conn:
        rows = conn.execute(
            f"SELECT date(ts_ms / 1000, 'unixepoch', 'localtime') AS day, kind, COUNT(*) FROM activity_events "
            f"WHERE {where} GROUP BY day, kind ORDER BY day DESC", args).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for day, kind, n in rows:
        entry = out.setdefault(day, {"day": day, "count": 0, "kinds": {}})
        entry["count"] += n
        entry["kinds"][kind] = n
    return list(out.values())[:limit]


def events(db, day: str, sources=None, kinds=None, q=None, limit: int = 500) -> List[Dict[str, Any]]:
    """The events of one local day, oldest first."""
    start, end = local_day_bounds(day)
    where, args = _filters(sources, kinds, q)
    with db._connect() as conn:
        rows = conn.execute(
            f"SELECT id, source, kind, ts_ms, end_ms, title, url, details FROM activity_events "
            f"WHERE {where} AND ts_ms >= ? AND ts_ms < ? ORDER BY ts_ms, id LIMIT ?",
            (*args, start, end, limit)).fetchall()
    return [_row(r) for r in rows]


def _row(r) -> Dict[str, Any]:
    try:
        details = json.loads(r[7]) if r[7] else None
    except ValueError:
        details = None
    return {"id": r[0], "source": r[1], "kind": r[2], "kind_label": label(r[2]), "ts": r[3] / 1000,
            "end": r[4] / 1000 if r[4] is not None else None, "title": r[5], "url": r[6], "details": details}


def facets(db) -> Dict[str, Any]:
    """{sources: [{id, count}], kinds: [{id, label, icon, count}], first, last}."""
    with db._connect() as conn:
        by_source = conn.execute("SELECT source, COUNT(*) FROM activity_events GROUP BY source ORDER BY 2 DESC").fetchall()
        by_kind = conn.execute("SELECT kind, COUNT(*) FROM activity_events GROUP BY kind ORDER BY 2 DESC").fetchall()
        first, last = conn.execute("SELECT MIN(ts_ms), MAX(ts_ms) FROM activity_events").fetchone()
    return {
        "sources": [{"id": s, "count": n} for s, n in by_source],
        "kinds": [{"id": k, "label": label(k), "icon": KINDS.get(k, KINDS["activity"])[1], "count": n} for k, n in by_kind],
        "first": first / 1000 if first is not None else None,
        "last": last / 1000 if last is not None else None,
    }
