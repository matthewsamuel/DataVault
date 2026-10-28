"""
DataGatherer - the merged Spotify plays (migration 61)

Four sources describe the same listening, each with its own stable key, so that importing
or fetching the same thing twice adds nothing:

    source            key                                         table
    export_extended   x|<end, epoch s>|<track/episode URI>|<ms>   spotify_plays
    export_account    l|<end minute>|<track>|<artist>|<ms>        spotify_plays
    api               a|<played_at ms>|<track URI>                spotify_api_plays
    lastfm            f|<start, epoch s>|<track>|<artist>         spotify_plays

Extended Streaming History is the richest (exact end time, real listening time, skips,
platform). The URI-based key means a track renamed between two exports is still one play
(the old file parser matched on names). Without a URI (local files, old rows) the names
take its place. IP address and user agent are left in the raw zip (data minimisation).

Precedence: export_extended > export_account > api > lastfm. reconcile() marks a play
that a richer source also has as superseded (with what superseded it); it never deletes:
- an Account-data play that an extended play covers: same track name, ending in the same
  minute or one either side, one-to-one (the rule of the old parser, SPOTIFY-CHAT-4);
- an API or Last.fm play inside the period an export covers (that export is complete
  for its period), or an API play matching an extended play on URI and time;
- a Last.fm play that matches an API play on artist and title within
  MATCH_SLACK_S of its start (Last.fm stamps the start, the API the end).
reconcile() recomputes every mark from the rows, so it gives the same answer however
often it runs and in whatever order the sources came in.

The dashboard (routes/spotify.py) reads the view spotify_plays_merged. API listening time
is the track length (Spotify gives no ms_played there) and Last.fm has none at all: those
plays are 'estimated' (ms_estimated = 1), which the coverage chip says.
"""
import bisect
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.datagatherer import state

SOURCE_ID = "spotify"
LASTFM_ID = "lastfm"
PRECEDENCE = {"export_extended": 4, "export_account": 3, "api": 2, "lastfm": 1}
EXPORT_SOURCES = ("export_extended", "export_account")
MATCH_SLACK_S = 120              # API vs extended (URI) and Last.fm vs API: slack around the track length
LASTFM_DEFAULT_MS = 210_000      # Last.fm has no duration: an estimated 3,5 minutes
MIN_MS = 1000                    # shorter plays are glitches (the dashboard's old rule)
BATCH = 2000

PLAY_COLUMNS = ("play_key", "source", "ts_ms", "start_ms", "ms_played", "ms_estimated", "content", "track", "artist",
                "album", "track_uri", "episode", "show", "episode_uri", "platform", "country", "reason_start",
                "reason_end", "shuffle", "skipped", "offline", "incognito", "raw_import_id", "first_seen_at")
API_COLUMNS = ("play_key", "ts_ms", "start_ms", "duration_ms", "track", "artist", "album", "track_uri", "context_uri",
               "fetched_at")


def norm(text: Optional[str]) -> str:
    """A name as two sources can be compared on (the old parser's _same_track)."""
    return unicodedata.normalize("NFC", text or "").strip().casefold()


def _str(value) -> Optional[str]:
    return value if isinstance(value, str) and value != "" else None


def _bool(value) -> Optional[int]:
    return None if value is None else int(bool(value))


def _iso_epoch(text: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except (ValueError, OverflowError, AttributeError, TypeError):
        return None


# ---------- rows from the sources ----------

def extended_row(play: Dict[str, Any], raw_id: Optional[int], now: float, video: bool = False) -> Optional[Dict[str, Any]]:
    """One element of Streaming_History_Audio_*.json / _Video_*.json, or None when it has no time."""
    if not isinstance(play, dict) or not isinstance(play.get("ts"), str):
        return None
    end = _iso_epoch(play["ts"])
    ms = play.get("ms_played")
    if end is None or not isinstance(ms, (int, float)) or isinstance(ms, bool) or ms < 0:
        return None
    ms = int(ms)
    track, artist = _str(play.get("master_metadata_track_name")), _str(play.get("master_metadata_album_artist_name"))
    episode, show = _str(play.get("episode_name")), _str(play.get("episode_show_name"))
    track_uri, episode_uri = _str(play.get("spotify_track_uri")), _str(play.get("spotify_episode_uri"))
    chapter_uri = _str(play.get("audiobook_chapter_uri"))
    if video:
        content = "video"
    elif track:
        content = "track"
    elif episode or episode_uri:
        content = "episode"
    elif chapter_uri or _str(play.get("audiobook_title")):
        content = "audiobook"
        episode, show, episode_uri = _str(play.get("audiobook_chapter_title")), _str(play.get("audiobook_title")), chapter_uri
    else:
        content = "unknown"
    ident = track_uri or episode_uri or chapter_uri or f"n:{track or episode or ''}|{artist or show or ''}"
    end_ms = int(round(end * 1000))
    return {
        "play_key": f"x|{int(end)}|{ident}|{ms}", "source": "export_extended", "ts_ms": end_ms,
        "start_ms": end_ms - ms, "ms_played": ms, "ms_estimated": 0, "content": content,
        "track": track, "artist": artist, "album": _str(play.get("master_metadata_album_album_name")),
        "track_uri": track_uri, "episode": episode, "show": show, "episode_uri": episode_uri,
        "platform": _str(play.get("platform")), "country": _str(play.get("conn_country")),
        "reason_start": _str(play.get("reason_start")), "reason_end": _str(play.get("reason_end")),
        "shuffle": _bool(play.get("shuffle")), "skipped": _bool(play.get("skipped")),
        "offline": _bool(play.get("offline")), "incognito": _bool(play.get("incognito_mode")),
        "raw_import_id": raw_id, "first_seen_at": now,
    }


def account_row(play: Dict[str, Any], raw_id: Optional[int], now: float) -> Optional[Dict[str, Any]]:
    """One element of Account data StreamingHistory*.json: end time to the minute, UTC."""
    if not isinstance(play, dict) or not isinstance(play.get("endTime"), str):
        return None
    try:
        end = datetime.strptime(play["endTime"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, OverflowError):
        return None
    ms = play.get("msPlayed")
    if not isinstance(ms, (int, float)) or isinstance(ms, bool) or ms < 0:
        return None
    ms = int(ms)
    track, artist = _str(play.get("trackName")), _str(play.get("artistName"))
    episode, show = _str(play.get("episodeName")), _str(play.get("podcastName"))
    content = "track" if track else ("episode" if episode else "unknown")
    end_ms = int(end * 1000)
    return {
        "play_key": f"l|{int(end // 60)}|{track or episode or ''}|{artist or show or ''}|{ms}",
        "source": "export_account", "ts_ms": end_ms, "start_ms": end_ms - ms, "ms_played": ms, "ms_estimated": 0,
        "content": content, "track": track, "artist": artist, "album": None, "track_uri": None,
        "episode": episode, "show": show, "episode_uri": None, "platform": None, "country": None,
        "reason_start": None, "reason_end": None, "shuffle": None, "skipped": None, "offline": None,
        "incognito": None, "raw_import_id": raw_id, "first_seen_at": now,
    }


def lastfm_row(scrobble: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
    """One track of user.getRecentTracks (format=json); None for 'now playing' (it has no date)."""
    if not isinstance(scrobble, dict) or (scrobble.get("@attr") or {}).get("nowplaying"):
        return None
    try:
        uts = int((scrobble.get("date") or {}).get("uts"))
    except (TypeError, ValueError):
        return None
    track = _str(scrobble.get("name"))
    artist_field = scrobble.get("artist")
    artist = _str(artist_field.get("#text") or artist_field.get("name")) if isinstance(artist_field, dict) else _str(artist_field)
    album_field = scrobble.get("album")
    album = _str(album_field.get("#text")) if isinstance(album_field, dict) else None
    if not track or not artist or uts <= 0:
        return None
    start_ms = uts * 1000
    return {
        "play_key": f"f|{uts}|{norm(track)}|{norm(artist)}", "source": "lastfm",
        "ts_ms": start_ms + LASTFM_DEFAULT_MS, "start_ms": start_ms, "ms_played": LASTFM_DEFAULT_MS, "ms_estimated": 1,
        "content": "track", "track": track, "artist": artist, "album": album, "track_uri": None, "episode": None,
        "show": None, "episode_uri": None, "platform": None, "country": None, "reason_start": None,
        "reason_end": None, "shuffle": None, "skipped": None, "offline": None, "incognito": None,
        "raw_import_id": None, "first_seen_at": now,
    }


def api_row(item: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
    """One item of GET /v1/me/player/recently-played. played_at is taken as the end of the play."""
    if not isinstance(item, dict):
        return None
    track = item.get("track") or {}
    played = _iso_epoch(item.get("played_at") or "")
    uri = _str(track.get("uri"))
    if played is None or not uri or not isinstance(track, dict):
        return None
    duration = track.get("duration_ms") if isinstance(track.get("duration_ms"), int) else None
    artists = [a.get("name") for a in track.get("artists") or [] if isinstance(a, dict) and _str(a.get("name"))]
    end_ms = int(round(played * 1000))
    return {
        "play_key": f"a|{end_ms}|{uri}", "ts_ms": end_ms, "start_ms": end_ms - (duration or 0), "duration_ms": duration,
        "track": _str(track.get("name")), "artist": artists[0] if artists else None,
        "album": _str((track.get("album") or {}).get("name")) if isinstance(track.get("album"), dict) else None,
        "track_uri": uri, "context_uri": _str((item.get("context") or {}).get("uri")) if isinstance(item.get("context"), dict) else None,
        "fetched_at": now,
    }


# ---------- writing ----------

def insert_plays(db, rows: Iterable[Dict[str, Any]], source_id: str = SOURCE_ID, check=None) -> Tuple[int, int]:
    """INSERT OR IGNORE in batches; returns (added, seen). Each batch is one transaction."""
    added = seen = 0
    batch: List[tuple] = []

    def flush():
        nonlocal added
        if not batch:
            return
        with db._connect() as conn:
            before = conn.total_changes
            conn.executemany(f"INSERT OR IGNORE INTO spotify_plays ({', '.join(PLAY_COLUMNS)}) "
                             f"VALUES ({', '.join('?' * len(PLAY_COLUMNS))})", batch)
            new = conn.total_changes - before
            if new:
                state.bump_version(conn, source_id)
        added += new
        batch.clear()

    for row in rows:
        seen += 1
        batch.append(tuple(row[c] for c in PLAY_COLUMNS))
        if len(batch) >= BATCH:
            flush()
            if check is not None:
                check()
    flush()
    return added, seen


def insert_api_plays(db, rows: List[Dict[str, Any]], cursor: Optional[Dict[str, Any]] = None) -> int:
    """API rows and the cursor in one transaction (the cursor never runs ahead of the rows)."""
    with db._connect() as conn:
        before = conn.total_changes
        conn.executemany(f"INSERT OR IGNORE INTO spotify_api_plays ({', '.join(API_COLUMNS)}) "
                         f"VALUES ({', '.join('?' * len(API_COLUMNS))})",
                         [tuple(r[c] for c in API_COLUMNS) for r in rows])
        added = conn.total_changes - before
        if added:
            state.bump_version(conn, SOURCE_ID)
        if cursor is not None:
            state.set_cursor(conn, SOURCE_ID, "recently_played", cursor)
    return added


# ---------- reconcile ----------

def export_windows(db) -> List[Tuple[int, int, int]]:
    """(from_ms, until_ms, raw id) of every imported Spotify export: complete for that period."""
    with db._connect() as conn:
        rows = conn.execute("SELECT id, covers_from, covers_until FROM dg_raw_imports WHERE source_id = ? "
                            "AND status = 'imported' AND covers_from IS NOT NULL AND covers_until IS NOT NULL "
                            "ORDER BY covers_from", (SOURCE_ID,)).fetchall()
    return [(int(f * 1000), int(u * 1000), i) for i, f, u in rows]


def _window_for(ts: int, windows: List[Tuple[int, int, int]]) -> Optional[int]:
    for start, end, raw_id in windows:
        if start <= ts <= end:
            return raw_id
    return None


def reconcile(db) -> Dict[str, int]:
    """Recompute every superseded mark (see the module docstring). Returns counts of what changed."""
    windows = export_windows(db)
    with db._connect() as conn:
        ext = conn.execute("SELECT id, ts_ms, track, track_uri FROM spotify_plays WHERE source = 'export_extended' "
                           "AND content = 'track' AND track IS NOT NULL AND artist IS NOT NULL AND ms_played >= ? "
                           "ORDER BY ts_ms, id", (MIN_MS,)).fetchall()
        legacy = conn.execute("SELECT id, ts_ms, track, artist IS NOT NULL AND ms_played >= ?, superseded, "
                              "superseded_by FROM spotify_plays WHERE source = 'export_account' ORDER BY ts_ms, id",
                              (MIN_MS,)).fetchall()
        lastfm = conn.execute("SELECT id, start_ms, track, artist, superseded, superseded_by FROM spotify_plays "
                              "WHERE source = 'lastfm' ORDER BY start_ms, id").fetchall()
        api = conn.execute("SELECT id, ts_ms, start_ms, duration_ms, track, artist, track_uri, superseded, "
                           "superseded_by FROM spotify_api_plays ORDER BY ts_ms, id").fetchall()

    # Account data vs extended: one extended play covers at most one legacy play
    available = defaultdict(deque)
    for pid, ts, track, _ in ext:
        available[(ts // 60000, norm(track))].append(pid)
    play_updates, api_updates = [], []
    for pid, ts, track, countable, sup, by in legacy:
        minute, name = ts // 60000, norm(track)
        want = (0, None)
        if track and countable:   # only plays the dashboard counts take part (as in the old parser)
            for d in (0, -1, 1):
                ids = available.get((minute + d, name))
                if ids:
                    want = (1, f"spotify_plays:{ids.popleft()}")
                    break
        if (sup, by) != want:
            play_updates.append((*want, pid))

    # API vs exports: inside an export's period, or the same URI near an extended play
    by_uri = defaultdict(list)
    for pid, ts, _, uri in ext:
        if uri:
            by_uri[uri].append((ts, pid))
    api_live = []
    for aid, ts, start, duration, track, artist, uri, sup, by in api:
        want = (0, None)
        raw_id = _window_for(ts, windows)
        if raw_id is not None:
            want = (1, f"export:{raw_id}")
        elif uri in by_uri:
            slack = (duration or 0) + MATCH_SLACK_S * 1000
            times = by_uri[uri]
            i = bisect.bisect_left(times, (ts - slack, -1))
            if i < len(times) and abs(times[i][0] - ts) <= slack:
                want = (1, f"spotify_plays:{times[i][1]}")
        if want[0] == 0:
            api_live.append((aid, start if start is not None else ts, duration, norm(track), norm(artist)))
        if (sup, by) != want:
            api_updates.append((*want, aid))

    # Last.fm vs exports and API: the export period first, then the nearest API play
    api_by_name = defaultdict(list)
    for aid, start, duration, track, artist in api_live:
        api_by_name[(track, artist)].append([start, duration, aid, False])
    for pid, start, track, artist, sup, by in lastfm:
        want = (0, None)
        raw_id = _window_for(start, windows)
        if raw_id is not None:
            want = (1, f"export:{raw_id}")
        else:
            best = None
            for cand in api_by_name.get((norm(track), norm(artist)), ()):
                slack = (cand[1] or LASTFM_DEFAULT_MS) + MATCH_SLACK_S * 1000
                gap = abs(cand[0] - start)
                if not cand[3] and gap <= slack and (best is None or gap < best[0]):
                    best = (gap, cand)
            if best is not None:
                best[1][3] = True
                want = (1, f"spotify_api_plays:{best[1][2]}")
        if (sup, by) != want:
            play_updates.append((*want, pid))

    if play_updates or api_updates:
        with db._connect() as conn:
            conn.executemany("UPDATE spotify_plays SET superseded = ?, superseded_by = ? WHERE id = ?", play_updates)
            conn.executemany("UPDATE spotify_api_plays SET superseded = ?, superseded_by = ? WHERE id = ?", api_updates)
            state.bump_version(conn, SOURCE_ID)
    return {"plays_changed": len(play_updates), "api_changed": len(api_updates)}


# ---------- reading ----------

def has_plays(db) -> bool:
    with db._connect() as conn:
        return (conn.execute("SELECT 1 FROM spotify_plays LIMIT 1").fetchone() is not None
                or conn.execute("SELECT 1 FROM spotify_api_plays LIMIT 1").fetchone() is not None)


def imported_exports(db) -> Dict[str, float]:
    """{path: imported_at} of every Spotify export (folder or file) that was read completely."""
    with db._connect() as conn:
        rows = conn.execute("SELECT path, imported_at FROM dg_raw_imports WHERE source_id = ? AND status = 'imported' "
                            "AND imported_at IS NOT NULL", (SOURCE_ID,)).fetchall()
    return {path: at for path, at in rows}


def data_version(db) -> tuple:
    return state.versions(db, (SOURCE_ID, LASTFM_ID))


def dashboard_plays(db, include_api: bool = True) -> List[Tuple[int, str, str, int, Optional[str]]]:
    """(end ms, track, artist, ms, platform) of every merged music play the dashboard counts."""
    where = "" if include_api else " AND source != 'api'"
    with db._connect() as conn:
        return conn.execute("SELECT ts_ms, track, artist, ms_played, platform FROM spotify_plays_merged "
                            "WHERE content = 'track' AND track IS NOT NULL AND artist IS NOT NULL "
                            f"AND ms_played >= ?{where}", (MIN_MS,)).fetchall()


def coverage(db) -> Dict[str, Any]:
    """What the merged plays cover, per source, and since when listening time is estimated."""
    with db._connect() as conn:
        rows = conn.execute("SELECT source, COUNT(*), MIN(ts_ms), MAX(ts_ms), SUM(ms_estimated) FROM spotify_plays_merged "
                            "WHERE content = 'track' GROUP BY source").fetchall()
        superseded = conn.execute("SELECT (SELECT COUNT(*) FROM spotify_plays WHERE superseded = 1) + "
                                  "(SELECT COUNT(*) FROM spotify_api_plays WHERE superseded = 1)").fetchone()[0]
    by_source = {s: {"plays": n, "first": lo / 1000, "last": hi / 1000} for s, n, lo, hi, _ in rows}
    if not by_source:
        return {"plays": 0, "first": None, "last": None, "export_until": None, "estimated_since": None,
                "by_source": {}, "superseded": superseded, "segments": []}
    first = min(v["first"] for v in by_source.values())
    last = max(v["last"] for v in by_source.values())
    exports = [by_source[s] for s in EXPORT_SOURCES if s in by_source]
    windows = export_windows(db)
    export_until = max([w[1] / 1000 for w in windows] + [e["last"] for e in exports]) if (windows or exports) else None
    estimated = [by_source[s] for s in ("api", "lastfm") if s in by_source]
    # The watermark: after the newest export, listening time comes from the API or Last.fm
    estimated_since = None
    if estimated:
        if export_until is None:
            estimated_since = min(e["first"] for e in estimated)
        elif max(e["last"] for e in estimated) > export_until:
            estimated_since = export_until
    segments = []
    if exports:
        segments.append({"from": min(e["first"] for e in exports), "until": export_until, "estimated": False})
    if estimated_since is not None:
        segments.append({"from": estimated_since, "until": last, "estimated": True})
    if not segments:
        segments.append({"from": first, "until": last, "estimated": bool(estimated)})
    return {"plays": sum(v["plays"] for v in by_source.values()), "first": first, "last": last,
            "export_until": export_until, "estimated_since": estimated_since, "by_source": by_source,
            "superseded": superseded, "segments": segments}


def last_api_play(db) -> Optional[int]:
    with db._connect() as conn:
        row = conn.execute("SELECT MAX(ts_ms) FROM spotify_api_plays").fetchone()
    return row[0] if row else None


def now() -> float:
    return time.time()
