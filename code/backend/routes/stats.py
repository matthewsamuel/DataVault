from fastapi import APIRouter, HTTPException, Query
from pathlib import Path
import shutil
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import os
import random
import sqlite3
import stat as stat_module
import subprocess
import time
import unicodedata

from backend import file_ops, media_dates
from backend.config import Config, is_photo, media_type
from backend.paths import ArchiveFilter, excluded_folders, resolve_in_vault, scan_dir, walk_archive
from backend.routes.spotify import parse_spotify_data

router = APIRouter()
config: Config = None
db = None

def init_router(cfg: Config, database=None):
    global config, db
    config = cfg
    db = database

@router.get("/storage")
def get_storage_stats() -> Dict[str, Any]:
    # A missing vault is a 503 before this runs (errors.require_vault)
    total, used, free = shutil.disk_usage(config.VAULT_ROOT)
    
    return {
        "total_gb": round(total / (2**30), 2),
        "used_gb": round(used / (2**30), 2),
        "free_gb": round(free / (2**30), 2),
        "percent_used": round((used / total) * 100, 1)
    }
def _du_breakdown(target_path: Path, names: List[str]) -> Dict[str, float]:
    """
    Size in GB of each named subfolder of target_path, measured with `du -sm`.
    '--' ends du's options, so a folder named '-h' is measured instead of read as a flag;
    lines du could not measure (no leading number) are skipped instead of failing everything.
    """
    breakdown = {}
    if not names:
        return breakdown
    result = subprocess.run(
        ["du", "-sm", "--", *names],
        cwd=str(target_path),
        capture_output=True,
        text=True,
        check=False,
    )
    # 0 = all measured, 1 = some entries unreadable (still report the rest)
    if result.returncode not in (0, 1):
        return breakdown
    for line in result.stdout.splitlines():
        size_str, sep, folder_name = line.partition('\t')
        if not sep:
            continue
        try:
            size_mb = int(size_str)
        except ValueError:
            continue
        gb_size = round(size_mb / 1024, 2)
        if gb_size > 0:
            breakdown[folder_name] = gb_size
    # Sort by size descending
    return dict(sorted(breakdown.items(), key=lambda item: item[1], reverse=True))


def _subfolder_names(target_path: Path) -> List[str]:
    """Visible real subfolders (no hidden folders, no symlinks)."""
    return [
        entry.name for entry in os.scandir(target_path)
        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.')
    ]


def _stats_folder(folder_path: Optional[str]) -> Path:
    """
    The folder a stats request is about: the vault root, or folder_path (relative to the
    vault, or absolute) checked by resolve_in_vault. Outside the vault -> 403, whether it
    exists or not; inside but missing -> 404.
    """
    if not folder_path:
        return config.VAULT_ROOT.resolve()
    target_path = resolve_in_vault(folder_path, config, folder=True)
    if not target_path.is_dir():
        raise HTTPException(status_code=404, detail="Map niet gevonden")
    return target_path


@router.get("/storage-breakdown")
def get_storage_breakdown() -> Dict[str, Any]:
    # Calculate sizes for immediate subdirectories using `du` for speed
    vault_path = config.VAULT_ROOT
    return {"breakdown": _du_breakdown(vault_path, _subfolder_names(vault_path))}

@router.get("/storage-breakdown-nested")
def get_storage_breakdown_nested(folder_path: str = None) -> Dict[str, Any]:
    """Get storage breakdown for a specific folder and its subfolders."""
    target_path = _stats_folder(folder_path)
    breakdown = _du_breakdown(target_path, _subfolder_names(target_path))
    return {"breakdown": breakdown, "path": folder_path or "/"}

@router.get("/largest-files")
def get_largest_files(folder_path: str = None, limit: int = 20) -> Dict[str, Any]:
    """Get largest files in a specific folder."""
    vault_path = config.VAULT_ROOT
    target_path = _stats_folder(folder_path)
    limit = max(1, min(limit, 200))

    files_with_sizes = []
    for root, _, files in walk_archive(target_path, config):
        for filename in files:
            file_path = Path(root) / filename
            try:
                size_bytes = file_path.stat().st_size
            except OSError:
                continue
            size_mb = round(size_bytes / (1024 * 1024), 2)
            if size_mb > 0:
                files_with_sizes.append({
                    "name": filename,
                    "path": str(file_path.relative_to(vault_path)),
                    "size_mb": size_mb,
                    "size_gb": round(size_mb / 1024, 2)
                })

    sorted_files = sorted(files_with_sizes, key=lambda x: x['size_mb'], reverse=True)[:limit]
    return {"files": sorted_files, "path": folder_path or "/"}

# "On this day" and photos-by-date use the capture date (backend/media_dates.py) and a
# bounded amount of work per request (MEDIA-THUMBS-TIMELINE-10, PERF-14): first what the
# date cache already knows (checked against the disk), then a walk in random order that
# stops at enough hits, a number of folders or a time budget. Dates read on the way are
# cached, so the answers get fuller and faster with every visit.
OTD_LIMIT = 10
OTD_MAX_FOLDERS = 300
OTD_SECONDS = 2.0
BY_DATE_LIMIT = 20
BY_DATE_MAX_FOLDERS = 2000
BY_DATE_SECONDS = 4.0
SEARCH_MAX_FOLDERS = 5000   # a name search that finds nothing stops after this many folders
SEARCH_SECONDS = 4.0        # ... or after this long, and then answers complete: false


def _roots(folders: Optional[List[str]]) -> List[Path]:
    """The chosen folders inside the vault (nested ones once), or the whole vault."""
    if not folders:
        return [config.VAULT_ROOT]
    from backend.routes.timeline import selected_roots
    roots, _ = selected_roots(folders)
    return roots


def _shown_kind(path: Path, roots: List[Path], excluded: List[Path]) -> Optional[str]:
    """'photo' or 'video' for a cached path that may still be shown, else None."""
    kind = media_type(path)
    if kind not in ("photo", "video"):
        return None
    if not any(path.is_relative_to(root) for root in roots):
        return None
    try:
        below = path.relative_to(config.VAULT_ROOT).parts
    except ValueError:
        return None
    if any(part.startswith(".") for part in below):
        return None
    if any(file_ops.is_inside(path, folder) for folder in excluded):
        return None
    return kind


def _known_hits(rows, roots: List[Path], limit: int, found: Dict[str, Tuple[float, str]]) -> None:
    """Rows from the date cache whose file is still there, unchanged and visible."""
    excluded = excluded_folders(config)
    for path, size, mtime_ns, taken, source in rows:
        if len(found) >= limit:
            return
        p = Path(path)
        if path in found or _shown_kind(p, roots, excluded) is None:
            continue
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if stat_module.S_ISREG(st.st_mode) and st.st_size == size and st.st_mtime_ns == mtime_ns:
            found[path] = (taken, source)


def _walk_for_dates(roots: List[Path], matches, limit: int, max_folders: int, seconds: float,
                    found: Dict[str, Tuple[float, str]]) -> bool:
    """
    Walk the folders below roots in random order (another part of the archive on every
    visit) and add photos and videos whose capture date matches. Returns True when every
    folder was walked, False when the budget ran out first.
    """
    archive_filter = ArchiveFilter(config)
    stack = [str(root) for root in roots]
    random.shuffle(stack)
    deadline = time.monotonic() + seconds
    walked = 0
    while stack:
        if len(found) >= limit or walked >= max_folders or time.monotonic() > deadline:
            return False
        folder = stack.pop()
        walked += 1
        try:
            entries = scan_dir(Path(folder), archive_filter)
        except OSError:
            continue
        subdirs, media = [], []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append(entry.path)
                elif entry.is_file(follow_symlinks=False) and media_type(Path(entry.name)) in ("photo", "video"):
                    media.append((entry.path, entry.stat(follow_symlinks=False)))
            except OSError:
                continue
        random.shuffle(subdirs)
        stack.extend(subdirs)
        if not media:
            continue
        for path, (ts, source) in media_dates.capture_dates(media, db, vault_root=config.VAULT_ROOT).items():
            if path not in found and matches(ts):
                found[path] = (ts, source)
    return True


def _dated_item(path: str, ts: float, source: str) -> Dict[str, Any]:
    p = Path(path)
    return {
        "path": path,
        "name": p.name,
        "type": media_type(p),
        "timestamp": ts,
        "year": datetime.fromtimestamp(ts).year,
        "date_source": source,
    }


def _cache_rows(query, *args):
    """Rows from the date cache, or none when the database is not reachable."""
    if db is None:
        return []
    try:
        rows = query(*args)
    except sqlite3.Error:
        return []
    rows = list(rows)
    random.shuffle(rows)
    return rows


@router.get("/photo-on-this-day")
def get_photo_on_this_day(allowed_folders: Optional[List[str]] = Query(None)) -> Dict[str, Any]:
    """
    Up to 10 photos and videos taken on today's date in earlier years, oldest year first.
    allowed_folders: one parameter per folder (a name may contain a comma); none = the
    whole vault. 'complete' is false when the work budget ran out before every folder
    was looked at.
    """
    now = datetime.now()
    roots = _roots(allowed_folders)
    if not roots:
        return {"photos": [], "complete": True}

    def matches(ts: float) -> bool:
        dt = datetime.fromtimestamp(ts)
        return dt.month == now.month and dt.day == now.day and dt.year != now.year

    found: Dict[str, Tuple[float, str]] = {}
    rows = [r for r in _cache_rows(getattr(db, "media_dates_on_day", None), now.month, now.day)
            if datetime.fromtimestamp(r[3]).year != now.year]
    _known_hits(rows, roots, OTD_LIMIT, found)
    complete = _walk_for_dates(roots, matches, OTD_LIMIT, OTD_MAX_FOLDERS, OTD_SECONDS, found)
    photos = sorted((_dated_item(p, ts, src) for p, (ts, src) in found.items()),
                    key=lambda item: (item["year"], item["timestamp"]))[:OTD_LIMIT]
    return {"photos": photos, "complete": complete or len(found) >= OTD_LIMIT}


@router.get("/spotify-on-this-day")
def get_spotify_on_this_day() -> Dict[str, Any]:
    now = datetime.now()
    date_filter = (now.month, now.day)
    
    data = parse_spotify_data(None, date_filter)
    tracks = data.get('on_this_day', [])
    if not tracks:
        return {"track": None}
    now_minutes = now.hour * 60 + now.minute

    def time_to_minutes(t):
        h, m = map(int, t['time'].split(':'))
        return h * 60 + m

    closest = min(tracks, key=lambda t: (abs(time_to_minutes(t) - now_minutes), t['year'], t['track']))
    return {"track": closest}

@router.get("/photos-by-date")
def get_photos_by_date(date: str) -> Dict[str, Any]:
    """
    Up to 20 photos and videos taken on a date (YYYY-MM-DD), by capture date, oldest first.
    """
    try:
        target = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Ongeldige datum, verwacht JJJJ-MM-DD")
    start = target.timestamp()
    end = (target + timedelta(days=1)).timestamp()
    roots = [config.VAULT_ROOT]

    found: Dict[str, Tuple[float, str]] = {}
    _known_hits(_cache_rows(getattr(db, "media_dates_between", None), start, end), roots, BY_DATE_LIMIT, found)
    complete = _walk_for_dates(roots, lambda ts: start <= ts < end, BY_DATE_LIMIT,
                               BY_DATE_MAX_FOLDERS, BY_DATE_SECONDS, found)
    photos = sorted((_dated_item(p, ts, src) for p, (ts, src) in found.items()),
                    key=lambda item: item["timestamp"])[:BY_DATE_LIMIT]
    return {"photos": photos, "complete": complete or len(found) >= BY_DATE_LIMIT}


@router.get("/search-photos")
def search_photos(query: str, limit: int = 5) -> Dict[str, Any]:
    """
    Zoek foto's op bestandsnaam die de query bevat. Hoofdletters en de Unicode-vorm tellen
    niet mee (GAP-2): een naam die Finder als 'e' + accentteken (NFD) opsloeg, wordt ook
    gevonden met een 'é' van het toetsenbord (NFC).
    """
    wanted = _search_key(query)
    if not wanted:
        return {"photos": [], "complete": True}
    limit = max(1, min(limit, 50))
    results = []
    # A name that matches nothing would otherwise walk the whole archive (minutes on a big
    # disk); like on-this-day there is a budget, and the answer says when it ran out
    deadline = time.monotonic() + SEARCH_SECONDS
    walked = 0

    for root, dirs, files in walk_archive(config.VAULT_ROOT, config):
        walked += 1
        if walked > SEARCH_MAX_FOLDERS or time.monotonic() > deadline:
            return {"photos": results, "complete": False}
        dirs.sort()
        for filename in sorted(files):
            if is_photo(Path(filename)) and wanted in _search_key(filename):
                results.append({
                    "path": str(Path(root) / filename),
                    "name": filename,
                    "type": "photo",
                })
                if len(results) >= limit:
                    return {"photos": results, "complete": True}

    return {"photos": results, "complete": True}


def _search_key(text: str) -> str:
    """Text for a name search: NFC and casefold, so 'É', 'é' and 'e'+accent all match."""
    return unicodedata.normalize("NFC", text or "").casefold().strip()
