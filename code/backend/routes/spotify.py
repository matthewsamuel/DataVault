"""
DataVault Dashboard - Spotify statistics

The streaming history export(s) in <vault>/data are parsed once into a sorted list of
plays and kept in memory until an export file is added, removed or changed (its size or
mtime), SPOTIFY-CHAT-3/PERF-9. Every statistic (per year, on this day, the chat's
summary) is computed from that list, and the last few answers are cached too.

Both export formats may be present (Account Data 'StreamingHistory*.json' and Extended
'Streaming_History_Audio_*.json'): a legacy play that the extended export also has
(same minute, track and artist) is counted once (SPOTIFY-CHAT-4).

Once DataGatherer has read the exports into the database (backend/datagatherer/), the plays
come from the merged view spotify_plays_merged instead: the exports, the Web API and
Last.fm, each play once (spotify_store.py), cached until that data changes (data_version).
The files are only the fallback while nothing was read yet. /coverage says up to when
the data goes and since when listening time is an estimate (API and Last.fm plays).
The chat never gets the Web API plays (Spotify's developer terms): for_assistant.
"""
import sqlite3
import json
import threading
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from fastapi import APIRouter, HTTPException
from backend.config import Config

router = APIRouter()
config: Config = None
db = None

_lock = threading.Lock()
_plays_cache: dict = {}         # mode ('dashboard' | 'assistant') -> (key, plays)
_stats_cache: "OrderedDict[tuple, dict]" = OrderedDict()
STATS_CACHE_SIZE = 32


def init_router(cfg: Config, database=None):
    global config, db
    config = cfg
    db = database
    with _lock:
        _plays_cache.clear()
        _stats_cache.clear()


class Play(NamedTuple):
    utc: datetime        # when the play ended, UTC
    local: datetime      # the same moment in local time, without tzinfo
    track: str
    artist: str
    ms: int
    platform: Optional[str]


def get_spotify_files() -> List[Path]:
    """All Spotify streaming history files below <vault>/data, sorted by path"""
    if not config:
        return []
        
    spotify_data_root = config.VAULT_ROOT / 'data'
    if not spotify_data_root.exists():
        return []
    
    # Look for both "Streaming_History_Audio_*.json" (Extended) and "StreamingHistory*.json" (Standard)
    # Same exclusions as every other walk (no hidden or '._' files, no symlinks)
    from backend.paths import walk_archive
    files = []
    for root, _, names in walk_archive(spotify_data_root, config):
        for name in names:
            if name.endswith('.json') and ('Streaming_History_Audio' in name or 'StreamingHistory' in name):
                files.append(Path(root) / name)
    return sorted(files)


def _files_key(files: List[Path]) -> tuple:
    key = []
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        key.append((str(f), st.st_size, st.st_mtime_ns))
    return tuple(key)


def _parse_file(file_path: Path):
    """Yield (utc datetime, track, artist, ms, platform, extended?) for every usable play."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"Error reading {file_path}: {e}")
        return
    if not isinstance(data, list):
        return
    for play in data:
        if not isinstance(play, dict):
            continue
        # Extended history fields, with the standard (legacy) names as fallback
        ts_str = play.get('ts') or play.get('endTime')
        ms_played = play.get('ms_played') or play.get('msPlayed') or 0
        track_name = play.get('master_metadata_track_name') or play.get('trackName')
        artist_name = play.get('master_metadata_album_artist_name') or play.get('artistName')
        if not isinstance(ms_played, (int, float)) or ms_played < 1000:  # Skip < 1s glitches
            continue
        if not isinstance(track_name, str) or not isinstance(artist_name, str) or not isinstance(ts_str, str):
            continue
        try:
            if 'T' in ts_str:
                # Extended: "2023-01-23T08:52:56Z"
                utc = datetime.fromisoformat(ts_str.replace('Z', '+00:00')).astimezone(timezone.utc)
                extended = True
            else:
                # Legacy: "2023-01-23 08:52", UTC in Spotify's exports
                utc = datetime.strptime(ts_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                extended = False
        except (ValueError, OverflowError):
            continue
        platform = play.get('platform')
        yield utc, track_name, artist_name, int(ms_played), platform if isinstance(platform, str) else None, extended


def _same_track(name: str) -> str:
    """A track name as both export formats can be compared on."""
    return unicodedata.normalize("NFC", name or "").strip().casefold()


def _from_database() -> bool:
    """True once DataGatherer has read Spotify plays into the database."""
    if db is None or not db.ready:
        return False
    from backend.datagatherer import spotify_store
    try:
        return spotify_store.has_plays(db)
    except sqlite3.Error:
        return False


def _db_plays(for_assistant: bool) -> Tuple[tuple, List[Play]]:
    from backend.datagatherer import spotify_store
    mode = "assistant" if for_assistant else "dashboard"
    key = ("db", mode, spotify_store.data_version(db))
    with _lock:
        cached = _plays_cache.get(mode)
        if cached and cached[0] == key:
            return cached
    rows = []
    for ts_ms, track, artist, ms, platform in spotify_store.dashboard_plays(db, include_api=not for_assistant):
        utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        rows.append((utc, track, artist, int(ms), platform))
    rows.sort(key=lambda p: (p[0], p[1], p[2]))
    plays = [Play(utc, utc.astimezone().replace(tzinfo=None), track, artist, ms, platform)
             for utc, track, artist, ms, platform in rows]
    with _lock:
        _plays_cache[mode] = (key, plays)
    return key, plays


def load_plays(for_assistant: bool = False) -> Tuple[tuple, List[Play]]:
    """
    Every play, oldest first (ties by track and artist, so the order never depends on the
    order of the files), each counted once: from the database once DataGatherer read the
    exports, else from the export files. Cached until the data or a file changes.
    """
    if _from_database():
        return _db_plays(for_assistant)
    files = get_spotify_files()
    key = _files_key(files)
    with _lock:
        cached = _plays_cache.get("files")
        if cached and cached[0] == key:
            return cached

    extended, legacy = [], []
    seen = set()
    for file_path in files:
        for utc, track, artist, ms, platform, is_extended in _parse_file(file_path):
            exact = (utc, track, artist, ms)
            if exact in seen:
                continue  # the same export twice (a copy in another folder)
            seen.add(exact)
            (extended if is_extended else legacy).append((utc, track, artist, ms, platform))

    # A legacy play the extended export also has: the same track, ending within a minute.
    # Not the artist: legacy has the track's artist, extended the album artist ('Various
    # Artists' on a compilation), so they differ for the same play. Each extended play
    # covers at most one legacy play, so a track played twice in a row stays two plays.
    available = Counter((int(utc.timestamp() // 60), _same_track(track)) for utc, track, _, _, _ in extended)
    for utc, track, artist, ms, platform in legacy:
        minute, name = int(utc.timestamp() // 60), _same_track(track)
        match = next(((minute + d, name) for d in (0, -1, 1) if available[(minute + d, name)] > 0), None)
        if match is not None:
            available[match] -= 1
            continue
        extended.append((utc, track, artist, ms, platform))

    extended.sort(key=lambda p: (p[0], p[1], p[2]))
    plays = [Play(utc, utc.astimezone().replace(tzinfo=None), track, artist, ms, platform)
             for utc, track, artist, ms, platform in extended]
    with _lock:
        _plays_cache["files"] = (key, plays)
    return key, plays


def parse_spotify_data(year_filter: Optional[int] = None, date_filter: Optional[tuple] = None,
                       for_assistant: bool = False):
    """
    Spotify stats from the cached plays.
    year_filter: int year (e.g. 2023) or None for all time
    date_filter: tuple (month, day) for "On This Day" or None
    for_assistant: for the chat (Pasha): without the Web API plays
    """
    key, plays = load_plays(for_assistant)
    today = datetime.now().date()
    cache_key = (key, year_filter, date_filter, today)
    with _lock:
        cached = _stats_cache.get(cache_key)
        if cached is not None:
            _stats_cache.move_to_end(cache_key)
            return cached
    result = _aggregate(plays, year_filter, date_filter, today.year)
    with _lock:
        _stats_cache[cache_key] = result
        while len(_stats_cache) > STATS_CACHE_SIZE:
            _stats_cache.popitem(last=False)
    return result


def _aggregate(plays: List[Play], year_filter: Optional[int], date_filter: Optional[tuple], current_year: int):
    stats = {
        'total_ms': 0,
        'artists': defaultdict(lambda: {'ms': 0, 'count': 0, 'tracks': set()}),
        'tracks': defaultdict(lambda: {'ms': 0, 'count': 0, 'artist': '', 'name': ''}),
        'hourly_activity': defaultdict(int), # 0-23
        'monthly_activity': defaultdict(int), # 1-12
        'platform_usage': defaultdict(int),
        'total_skips': 0, # < 30s
        'total_completions': 0, # > 30s as proxy
        'on_this_day': [], # List of tracks played on this day
        'daily_ms': defaultdict(int) # "YYYY-MM-DD" -> ms for record day
    }
    # First play per artist over ALL years (for 'discoveries'); kept apart, so an artist
    # without plays in the chosen year never shows up with 0 plays (LIVE-PROBE-25)
    first_seen = {}
    available_years = set()
    total_plays = 0
    
    # Context state for binge tracking (plays are in time order, see load_plays)
    prev_track = None
    current_binge_count = 0
    current_binge_track = None
    current_binge_artist = None
    
    max_binge_count = 0
    max_binge_track = None
    max_binge_artist = None
    for play in plays:
        dt = play.local
        track_name, artist_name, ms_played = play.track, play.artist, play.ms
        play_year = dt.year
        available_years.add(play_year)
        if artist_name not in first_seen:
            first_seen[artist_name] = dt

        # On This Day Filter
        if date_filter:
            target_month, target_day = date_filter
            if dt.month == target_month and dt.day == target_day:
                # Per-jaar: alleen dat jaar; all-time: alle jaren behalve het huidige
                if (play_year == year_filter) if year_filter else (play_year != current_year):
                    stats['on_this_day'].append({
                        'track': track_name,
                        'artist': artist_name,
                        'year': play_year,
                        'time': dt.strftime("%H:%M"),
                        'ms': ms_played
                    })

        # Filter by year if requested
        if year_filter and play_year != year_filter:
            continue

        # Aggregate
        total_plays += 1
        stats['total_ms'] += ms_played
        stats['daily_ms'][dt.strftime("%Y-%m-%d")] += ms_played

        stats['artists'][artist_name]['ms'] += ms_played
        stats['artists'][artist_name]['count'] += 1
        stats['artists'][artist_name]['tracks'].add(track_name)

        track_key = f"{track_name} - {artist_name}"
        stats['tracks'][track_key]['ms'] += ms_played
        stats['tracks'][track_key]['count'] += 1
        stats['tracks'][track_key]['artist'] = artist_name
        stats['tracks'][track_key]['name'] = track_name

        # Binge Logic
        if track_key == prev_track:
            current_binge_count += 1
        else:
            if current_binge_count > max_binge_count and current_binge_count > 1:
                max_binge_count = current_binge_count
                max_binge_track = current_binge_track
                max_binge_artist = current_binge_artist
            current_binge_count = 1
            current_binge_track = track_name
            current_binge_artist = artist_name
            prev_track = track_key

        stats['hourly_activity'][dt.hour] += 1
        stats['monthly_activity'][dt.month] += 1
        if play.platform:
            stats['platform_usage'][play.platform] += 1

        # Skip Rate proxy (assuming < 30s is a skip for most songs)
        if ms_played < 30000:
            stats['total_skips'] += 1
        else:
            stats['total_completions'] += 1

    # Post-process for JSON response
    
    # Listening DNA
    morning_plays = sum(stats['hourly_activity'][h] for h in range(5, 12))
    afternoon_plays = sum(stats['hourly_activity'][h] for h in range(12, 18))
    evening_plays = sum(stats['hourly_activity'][h] for h in range(18, 23))
    night_plays = sum(stats['hourly_activity'][h] for h in list(range(23, 24)) + list(range(0, 5)))
    
    listening_dna = 'Balanced Listener'
    max_val = max(morning_plays, afternoon_plays, evening_plays, night_plays)
    if max_val > 0:
        if morning_plays == max_val: listening_dna = 'Early Bird 🐦'
        elif afternoon_plays == max_val: listening_dna = 'Daydreamer ☀️'
        elif evening_plays == max_val: listening_dna = 'Evening Chiller 🌇'
        elif night_plays == max_val: listening_dna = 'Night Owl 🦉'

    # Process final binge if it ended on file end
    if current_binge_count > max_binge_count and current_binge_count > 1:
        max_binge_count = current_binge_count
        max_binge_track = current_binge_track
        max_binge_artist = current_binge_artist
        
    # Top Artists
    top_artists = sorted(
        [{'name': k, 'ms': v['ms'], 'count': v['count'], 'unique_tracks': len(v['tracks']), 'first_seen': first_seen.get(k)}
         for k, v in stats['artists'].items()],
        key=lambda x: (-x['ms'], x['name'])
    )
    
    # 1. Mainstream vs Obscure (Top 5% artists listen share vs rest)
    mainstream_score = "Balanced"
    mainstream_percent = 50
    if top_artists and stats['total_ms'] > 0:
        top_5_percent_count = max(1, int(len(top_artists) * 0.05))
        top_5_percent_ms = sum(a['ms'] for a in top_artists[:top_5_percent_count])
        mainstream_percent = int((top_5_percent_ms / stats['total_ms']) * 100)
        
        if mainstream_percent > 75: mainstream_score = "Mainstream Repeater 🔁"
        elif mainstream_percent < 40: mainstream_score = "Broad Explorer 🌍"
        else: mainstream_score = "Balanced Listener ⚖️"
        
    # 2. Top Discoveries (Artists first seen in this year, sorted by MS)
    discoveries = []
    if year_filter:
        discoveries = [a for a in top_artists if a['first_seen'] and a['first_seen'].year == year_filter]
        discoveries = discoveries[:5]
    else:
        # For 'All Time', discoveries doesn't make as much sense as an annual concept, 
        # so we disable it for All Time by keeping the list empty.
        discoveries = []

    # 3. Record Day
    record_day = None
    if stats['daily_ms']:
         best_day_str, best_day_ms = max(stats['daily_ms'].items(), key=lambda item: item[1])
         best_hours = min(24.0, round(best_day_ms / 3600000, 1))
         record_day = {'date': best_day_str, 'hours': best_hours}
         
    # 4. Seasonal Mood Proxy
    season_mood = "Consistent"
    winter_ms = stats['monthly_activity'][12] + stats['monthly_activity'][1] + stats['monthly_activity'][2]
    summer_ms = stats['monthly_activity'][6] + stats['monthly_activity'][7] + stats['monthly_activity'][8]
    if winter_ms > summer_ms * 1.5: season_mood = "Winter Hibernator ❄️"
    elif summer_ms > winter_ms * 1.5: season_mood = "Summer Vibes 🏖️"

    # Top Tracks
    top_tracks = sorted(
        [{'name': v['name'], 'artist': v['artist'], 'ms': v['ms'], 'count': v['count']} 
         for k, v in stats['tracks'].items()],
        key=lambda x: (-x['count'], -x['ms'], x['name'], x['artist'])  # most played first
    )[:50]
    
    total_actions = stats['total_skips'] + stats['total_completions']
    skip_rate = (stats['total_skips'] / total_actions * 100) if total_actions > 0 else 0
    
    return {
        'year': year_filter if year_filter else 'all',
        'available_years': sorted(list(available_years)),
        'total_minutes': int(stats['total_ms'] / 60000),
        'total_hours': int(stats['total_ms'] / 3600000),
        'total_plays': total_plays,
        'top_artists': top_artists[:10], # Return top 10 for UI
        'top_tracks': top_tracks[:10],
        'hourly_activity': [stats['hourly_activity'].get(h, 0) for h in range(24)],
        'monthly_activity': [stats['monthly_activity'].get(m, 0) for m in range(1, 13)],
        'platform_usage': stats['platform_usage'],
        'listening_dna': listening_dna,
        'skip_rate': round(skip_rate, 1),
        # Each play once, also two different tracks in the same minute (SPOTIFY-CHAT-13);
        # sorted by time of day (HH:MM), then year, track and artist
        'on_this_day': sorted(
            {(e['year'], e['time'], e['track'], e['artist']): e for e in stats['on_this_day']}.values(),
            key=lambda x: (x['time'], x['year'], x['track'], x['artist'])
        ),
        'new_insights': {
             'mainstream_score': mainstream_score,
             'mainstream_percent': mainstream_percent,
             'record_day': record_day,
             'seasonal_mood': season_mood,
             'top_binge': {'track': max_binge_track, 'artist': max_binge_artist, 'count': max_binge_count} if max_binge_count > 1 else None,
             'discoveries': [{'name': d['name'], 'ms': d['ms']} for d in discoveries]
        }
    }

@router.get("/coverage")
def get_coverage():
    """
    Up to when the plays go and since when listening time is an estimate: the chip on the
    Spotify page ('Bijgewerkt t/m 27 okt · luistertijd na 14 jul is een schatting').
    """
    if _from_database():
        from backend.datagatherer import spotify_store
        c = spotify_store.coverage(db)
        return {"mode": "database", "last": c["last"], "first": c["first"], "estimated_since": c["estimated_since"],
                "export_until": c["export_until"], "plays": c["plays"],
                "sources": {k: v["plays"] for k, v in c["by_source"].items()}}
    _, plays = load_plays()
    return {"mode": "files", "last": plays[-1].utc.timestamp() if plays else None,
            "first": plays[0].utc.timestamp() if plays else None, "estimated_since": None,
            "export_until": plays[-1].utc.timestamp() if plays else None, "plays": len(plays),
            "sources": {"export": len(plays)} if plays else {}}


@router.get("/years")
def get_years():
    data = parse_spotify_data()
    return {'years': data['available_years']}

@router.get("/stats")
def get_stats(year: Optional[str] = None):
    # Determine current date for "On This Day"
    now = datetime.now()
    date_filter = (now.month, now.day)
    
    year_int = None
    if year and year.lower() != 'all':
        try:
            year_int = int(year)
        except ValueError:
            raise HTTPException(status_code=400, detail="Ongeldig jaar: gebruik een jaartal of 'all'")
        if not 1900 <= year_int <= 9999:
            raise HTTPException(status_code=400, detail="Ongeldig jaar: gebruik een jaartal of 'all'")
            
    return parse_spotify_data(year_int, date_filter)
