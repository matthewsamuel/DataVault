"""
DataGatherer - Spotify album covers, kept on this Mac

Home's 'Spotify op deze dag' shows the cover of the track it picks. Home itself never goes
online: the covers are fetched while the Spotify connector syncs (a click or the schedule
the user switched on, through the network guard and its log) and kept as files in
<cache>/spotify_covers. Home and the Spotify tab only read those files.

Which covers: the tracks Home can pick in the coming days (played on that month and day in
an earlier year), today's closest to now first, at most MAX_LOOKUPS per sync. Covers of
the tracks in 'recently played' come free with that answer and are only remembered (the
URL); their image is fetched once Home needs it.

Per track: GET /v1/tracks/{id} when a play has its URI (the export and the API have it),
else a search on title and artist (old Account data plays have no URI). Since February 2026
a Development Mode app has no batch endpoint, so one request per track. The image comes
from i.scdn.co, Spotify's image host, in the size closest to COVER_PX, and is stored once
per album. A track Spotify does not know is marked 'none' and asked again after RETRY_DAYS.
A cover lookup never fails the sync: on a 429 or any error it stops and tries next time.
"""
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.datagatherer.spotify_store import norm

log = logging.getLogger(__name__)

TRACK_URL = "https://api.spotify.com/v1/tracks/{}"
SEARCH_URL = "https://api.spotify.com/v1/search"
IMAGE_HOST = "i.scdn.co"
COVER_PX = 300               # Spotify gives 640, 300 and 64 px
MAX_LOOKUPS = 24             # tracks looked up per sync (each one or two requests)
DAYS_AHEAD = 7               # today and the next six days
RETRY_DAYS = 30              # a track Spotify did not know is asked again after this
MAX_IMAGE_BYTES = 2_000_000
FILE_RE = re.compile(r"^[A-Za-z0-9]{1,64}\.jpg$")
_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")


def key_of(track: Optional[str], artist: Optional[str]) -> str:
    return f"{norm(track)}\x1f{norm(artist)}"


def folder(config) -> Path:
    return Path(config.CACHE_FOLDER) / "spotify_covers"


# ---------- reading (Home, no network) ----------

def cover_file(db, config, track: Optional[str], artist: Optional[str]) -> Optional[str]:
    """The stored cover's file name for a track, or None (no row, not fetched yet, or the file is gone)."""
    if db is None or config is None or not track:
        return None
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT file FROM spotify_covers WHERE key = ? AND file IS NOT NULL",
                               (key_of(track, artist),)).fetchone()
    except Exception:
        return None
    if not row or not FILE_RE.match(row[0] or "") or not (folder(config) / row[0]).is_file():
        return None
    return row[0]


# ---------- what to fetch ----------

def wanted(plays: Iterable, now: Optional[datetime] = None, days: int = DAYS_AHEAD) -> List[Tuple[str, str]]:
    """
    (track, artist) of every track Home can show in the coming days, best first: today's by
    how close the play's time of day is to now (Home picks the closest), then each next
    day's by time of day. Plays of the current year do not count (Home leaves them out).
    """
    now = now or datetime.now()
    targets = {}
    for offset in range(days):
        d = (now + timedelta(days=offset)).date()
        targets[(d.month, d.day)] = offset
    now_min = now.hour * 60 + now.minute
    best: Dict[str, Tuple[tuple, str, str]] = {}
    for play in plays:
        dt = play.local
        offset = targets.get((dt.month, dt.day))
        if offset is None or dt.year == now.year:
            continue
        minute = dt.hour * 60 + dt.minute
        rank = (offset, abs(minute - now_min) if offset == 0 else minute)
        key = key_of(play.track, play.artist)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, play.track, play.artist)
    return [(track, artist) for _, track, artist in sorted(best.values(), key=lambda v: v[0])]


def _rows(conn, keys: List[str]) -> Dict[str, tuple]:
    out = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        for row in conn.execute(f"SELECT key, file, image_url, album_id, status, checked_at FROM spotify_covers "
                                f"WHERE key IN ({','.join('?' * len(part))})", part):
            out[row[0]] = row[1:]
    return out


def _uris(conn, pairs: List[Tuple[str, str]]) -> Dict[str, str]:
    """A track URI per (track, artist) from the stored plays: one pass over the plays with these titles."""
    titles = sorted({t for t, _ in pairs})
    found: Dict[str, str] = {}
    for i in range(0, len(titles), 500):
        part = titles[i:i + 500]
        marks = ",".join("?" * len(part))
        for table in ("spotify_plays", "spotify_api_plays"):
            for track, artist, uri in conn.execute(
                    f"SELECT track, artist, track_uri FROM {table} WHERE track IN ({marks}) "
                    f"AND track_uri LIKE 'spotify:track:%'", part):
                found.setdefault(key_of(track, artist), uri)
    return found


def remember(db, items: Iterable[Dict[str, Any]]) -> None:
    """The album and cover URL of 'recently played' items (they come with that answer): no request."""
    rows = []
    now = time.time()
    for item in items:
        track = item.get("track") if isinstance(item, dict) else None
        if not isinstance(track, dict):
            continue
        artists = [a.get("name") for a in track.get("artists") or [] if isinstance(a, dict)]
        info = _album_info(track)
        if track.get("name") and artists and artists[0] and info:
            rows.append((key_of(track["name"], artists[0]), track.get("uri"), info[0], info[1], now))
    if not rows:
        return
    with db._connect() as conn:
        conn.executemany("INSERT INTO spotify_covers (key, track_uri, album_id, image_url, status, checked_at) "
                         "VALUES (?, ?, ?, ?, 'url', ?) ON CONFLICT(key) DO UPDATE SET "
                         "track_uri = excluded.track_uri, album_id = excluded.album_id, "
                         "image_url = excluded.image_url, checked_at = excluded.checked_at "
                         "WHERE spotify_covers.file IS NULL", rows)


# ---------- fetching (inside the connector's sync) ----------

class Stop(Exception):
    """Stop looking up covers for this sync (rate limit, an error); the next sync goes on."""


def _album_info(track: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(album id, image URL closest to COVER_PX) of a track object, or None."""
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    images = [i for i in album.get("images") or [] if isinstance(i, dict) and isinstance(i.get("url"), str)
              and i["url"].startswith(f"https://{IMAGE_HOST}/")]
    if not images:
        return None
    image = min(images, key=lambda i: (abs((i.get("width") or COVER_PX) - COVER_PX), -(i.get("width") or 0)))
    album_id = album.get("id") if isinstance(album.get("id"), str) and _ID_RE.match(album.get("id")) else None
    return album_id or hashlib.sha1(image["url"].encode()).hexdigest()[:32], image["url"]


def _same_artist(track: Dict[str, Any], artist: str) -> bool:
    return any(isinstance(a, dict) and norm(a.get("name")) == norm(artist) for a in track.get("artists") or [])


def _get(http, url: str, auth, params=None):
    response = http.get(url, params=params, headers=auth())
    if response.status_code == 429:
        raise Stop("Spotify vraagt om te wachten")
    return response


def _lookup(http, auth, track: str, artist: str, uri: Optional[str]) -> Optional[Tuple[str, str, Optional[str]]]:
    """(album id, image URL, track URI) from Spotify, or None when Spotify does not know the track."""
    if uri:
        r = _get(http, TRACK_URL.format(uri.rsplit(":", 1)[-1]), auth, params={"market": "from_token"})
        if r.status_code in (400, 404):
            return None
        if r.status_code != 200:
            raise Stop(f"fout {r.status_code}")
        body = r.json()
        info = _album_info(body) if isinstance(body, dict) else None
        return (*info, uri) if info else None
    r = _get(http, SEARCH_URL, auth, params={"q": f"track:{track} artist:{artist}", "type": "track", "limit": 5})
    if r.status_code != 200:
        raise Stop(f"fout {r.status_code}")
    body = r.json()
    items = [t for t in ((body.get("tracks") or {}).get("items") or []) if isinstance(t, dict)] \
        if isinstance(body, dict) else []
    items = [t for t in items if _same_artist(t, artist)]
    items.sort(key=lambda t: norm(t.get("name")) != norm(track))
    for item in items:
        info = _album_info(item)
        if info:
            return (*info, item.get("uri") if isinstance(item.get("uri"), str) else None)
    return None


def _download(http, url: str, target: Path) -> None:
    r = http.get(url)
    if r.status_code == 429:
        raise Stop("Spotify vraagt om te wachten")
    body = r.content
    if r.status_code != 200 or not body or len(body) > MAX_IMAGE_BYTES or not body.startswith(b"\xff\xd8"):
        raise Stop(f"geen afbeelding ({r.status_code})")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_bytes(body)
    os.replace(tmp, target)


def fetch(ctx, http, auth, plays: Iterable, now: Optional[datetime] = None, limit: int = MAX_LOOKUPS) -> int:
    """
    Fetch the covers Home needs soon (see wanted); returns how many new cover files there
    are. auth() gives the Authorization header (only api.spotify.com gets it).
    """
    pairs = wanted(plays, now)
    if not pairs:
        return 0
    target_dir = folder(ctx.config)
    retry_before = time.time() - RETRY_DAYS * 86400
    with ctx.db._connect() as conn:
        known = _rows(conn, [key_of(t, a) for t, a in pairs])
    todo = []
    for track, artist in pairs:
        row = known.get(key_of(track, artist))
        if row and row[0] and (target_dir / row[0]).is_file():
            continue
        if row and row[3] == "none" and (row[4] or 0) > retry_before:
            continue
        todo.append((track, artist, row))
        if len(todo) >= limit:
            break
    if not todo:
        return 0
    with ctx.db._connect() as conn:
        uris = _uris(conn, [(t, a) for t, a, _ in todo])
    added = 0
    for track, artist, row in todo:
        ctx.check()
        key = key_of(track, artist)
        try:
            if row and row[1] and row[2]:            # remembered from 'recently played'
                album_id, image_url, uri = row[2], row[1], None
            else:
                found = _lookup(http, auth, track, artist, uris.get(key))
                if found is None:
                    _save(ctx.db, key, None, None, None, None, "none")
                    continue
                album_id, image_url, uri = found
            name = f"{album_id}.jpg"
            if not (target_dir / name).is_file():
                _download(http, image_url, target_dir / name)
                added += 1
            _save(ctx.db, key, uri or uris.get(key), album_id, image_url, name, "ok")
        except Stop as e:
            log.info("Spotify covers: stopped for this sync (%s)", e)
            break
        except Exception as e:  # never fails the sync
            log.warning("Spotify covers: stopped for this sync (%s)", e)
            break
    return added


def _save(db, key, uri, album_id, image_url, file, status) -> None:
    with db._connect() as conn:
        conn.execute("INSERT INTO spotify_covers (key, track_uri, album_id, image_url, file, status, checked_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                     "track_uri = COALESCE(excluded.track_uri, spotify_covers.track_uri), "
                     "album_id = excluded.album_id, image_url = excluded.image_url, file = excluded.file, "
                     "status = excluded.status, checked_at = excluded.checked_at",
                     (key, uri, album_id, image_url, file, status, time.time()))
