import json
from fastapi import APIRouter, Query
from collections import defaultdict
from datetime import datetime
from typing import Optional
from backend.config import Config

router = APIRouter()
config: Config = None

def init_router(cfg: Config):
    global config
    config = cfg

def get_spotify_files():
    """Find all Spotify streaming history files recursively"""
    if not config:
        return []
        
    spotify_data_root = config.VAULT_ROOT / 'data'
    if not spotify_data_root.exists():
        return []
    
    # Look for both "Streaming_History_Audio_*.json" (Extended) and "StreamingHistory*.json" (Standard)
    files = []
    for path in spotify_data_root.rglob('*.json'):
        if 'Streaming_History_Audio' in path.name or 'StreamingHistory' in path.name:
            files.append(path)
    return files

def parse_spotify_data(year_filter: Optional[int] = None, date_filter: Optional[tuple] = None):
    """
    Parse Spotify JSON files and aggregate stats.
    year_filter: int year (e.g. 2023) or None for all time
    date_filter: tuple (month, day) for "On This Day" or None
    """
    files = get_spotify_files()
    
    stats = {
        'total_ms': 0,
        'artists': defaultdict(lambda: {'ms': 0, 'count': 0, 'tracks': set(), 'first_seen': None}),
        'tracks': defaultdict(lambda: {'ms': 0, 'count': 0, 'artist': '', 'name': ''}),
        'hourly_activity': defaultdict(int), # 0-23
        'monthly_activity': defaultdict(int), # 1-12
        'platform_usage': defaultdict(int),
        'total_skips': 0, # < 30s
        'total_completions': 0, # > 30s as proxy
        'on_this_day': [], # List of tracks played on this day
        'daily_ms': defaultdict(int) # "YYYY-MM-DD" -> ms for record day
    }
    
    available_years = set()
    
    # Context state for binge tracking
    prev_track = None
    current_binge_count = 0
    current_binge_track = None
    current_binge_artist = None
    
    max_binge_count = 0
    max_binge_track = None
    max_binge_artist = None
    for file_path in files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    continue
                    
                if not isinstance(data, list):
                    continue

                for play in data:
                    # Handle different formats (Extended vs Standard)
                    
                    # Extended history fields
                    ts_str = play.get('ts') # "2023-01-23T08:52:56Z"
                    ms_played = play.get('ms_played', 0)
                    track_name = play.get('master_metadata_track_name')
                    artist_name = play.get('master_metadata_album_artist_name')
                    platform = play.get('platform')
                    
                    # Standard history fields fallback (legacy format)
                    if not ts_str:
                        ts_str = play.get('endTime') # "2023-01-23 08:52"
                    if not ms_played:
                        ms_played = play.get('msPlayed', 0)
                    if not track_name:
                        track_name = play.get('trackName')
                    if not artist_name:
                        artist_name = play.get('artistName')
                    
                    if ms_played < 1000: # Skip < 1s glitches
                        continue
                        
                    if not track_name or not artist_name or not ts_str:
                        continue

                    # Parse timestamp logic
                    dt = None
                    try:
                        # 1. Extended Format: "2023-01-23T08:52:56Z"
                        if ts_str and 'T' in ts_str:
                             dt_utc = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                             # Convert to local timezone
                             dt = dt_utc.astimezone()
                        # 2. Legacy Format: "2023-01-23 08:52" (Assumed to be local or UTC? Usually UTC in Spotify exports, but treating as local for safety if no Z)
                        elif ts_str:
                             # Legacy might be local or UTC, we assume it's UTC and convert, or just parse.
                             # If we assume it's UTC:
                             dt_naive = datetime.strptime(ts_str, "%Y-%m-%d %H:%M")
                             from datetime import timezone
                             dt_utc = dt_naive.replace(tzinfo=timezone.utc)
                             dt = dt_utc.astimezone()
                    except ValueError:
                        pass
                        
                    if not dt:
                        continue
                        
                    # Remove tzinfo so the rest of the logic works with naive local datetimes
                    dt = dt.replace(tzinfo=None)
                            
                    play_year = dt.year
                    available_years.add(play_year)
                    
                    # Track absolute first seen BEFORE any year filtering
                    if not stats['artists'][artist_name]['first_seen'] or dt < stats['artists'][artist_name]['first_seen']:
                          stats['artists'][artist_name]['first_seen'] = dt
                    
                    # On This Day Filter
                    if date_filter:
                        target_month, target_day = date_filter
                        current_year = datetime.now().year
                        if dt.month == target_month and dt.day == target_day:
                            if year_filter:
                                # Per-jaar: toon enkel tracks van dat specifieke jaar
                                if play_year == year_filter:
                                    stats['on_this_day'].append({
                                        'track': track_name,
                                        'artist': artist_name,
                                        'year': play_year,
                                        'time': dt.strftime("%H:%M"),
                                        'ms': ms_played
                                    })
                            else:
                                # All-time: toon alle jaren behalve het huidige jaar
                                if play_year != current_year:
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
                    if platform:
                        # Clean up platform names if needed
                        stats['platform_usage'][platform] += 1
                        
                    # Skip Rate proxy (assuming < 30s is a skip for most songs)
                    if ms_played < 30000:
                        stats['total_skips'] += 1
                    else:
                        stats['total_completions'] += 1

        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            continue

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
        [{'name': k, 'ms': v['ms'], 'count': v['count'], 'unique_tracks': len(v['tracks']), 'first_seen': v['first_seen']} 
         for k, v in stats['artists'].items()],
        key=lambda x: x['ms'], 
        reverse=True
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
        key=lambda x: x['count'], # Sort tracks by play count usually
        reverse=True
    )[:50]
    
    total_actions = stats['total_skips'] + stats['total_completions']
    skip_rate = (stats['total_skips'] / total_actions * 100) if total_actions > 0 else 0
    
    return {
        'year': year_filter if year_filter else 'all',
        'available_years': sorted(list(available_years)),
        'total_minutes': int(stats['total_ms'] / 60000),
        'total_hours': int(stats['total_ms'] / 3600000),
        'total_plays': sum(a['count'] for a in top_artists), # Approx
        'top_artists': top_artists[:10], # Return top 10 for UI
        'top_tracks': top_tracks[:10],
        'hourly_activity': [stats['hourly_activity'].get(h, 0) for h in range(24)],
        'monthly_activity': [stats['monthly_activity'].get(m, 0) for m in range(1, 13)],
        'platform_usage': stats['platform_usage'],
        'listening_dna': listening_dna,
        'skip_rate': round(skip_rate, 1),
        'on_this_day': (lambda entries: sorted(
            {(e['year'], e['time']): e for e in entries}.values(),
            key=lambda x: x['time']  # Sorteer op tijdstip (HH:MM), niet op jaar
        ))(stats['on_this_day']),
        'new_insights': {
             'mainstream_score': mainstream_score,
             'mainstream_percent': mainstream_percent,
             'record_day': record_day,
             'seasonal_mood': season_mood,
             'top_binge': {'track': max_binge_track, 'artist': max_binge_artist, 'count': max_binge_count} if max_binge_count > 1 else None,
             'discoveries': [{'name': d['name'], 'ms': d['ms']} for d in discoveries]
        }
    }

@router.get("/years")
async def get_years():
    data = parse_spotify_data()
    return {'years': data['available_years']}

@router.get("/stats")
async def get_stats(year: Optional[str] = None):
    # Determine current date for "On This Day"
    now = datetime.now()
    date_filter = (now.month, now.day)
    
    year_int = None
    if year and year.lower() != 'all':
        try:
            year_int = int(year)
        except ValueError:
            pass
            
    data = parse_spotify_data(year_int, date_filter)
    return data
