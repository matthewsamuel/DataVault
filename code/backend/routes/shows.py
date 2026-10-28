from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List, Dict, Any, Optional
import heapq
import os
import random
import stat
from backend.config import Config, is_photo, is_video
from backend.paths import resolve_in_vault, vault_folder_or_none, walk_archive

router = APIRouter()

# Global state
config: Config = None
db = None

def init_router(cfg: Config, database):
    """Initialize router with config and database"""
    global config, db
    config = cfg
    db = database

def get_disabled_folders() -> List[str]:
    """Get list of DISABLED folder paths from DB"""
    if not db:
        return []
    
    return db.get_disabled_show_folders()

def is_folder_enabled(path: Path, disabled_folders: List[str]) -> bool:
    """
    Check if folder is allowed.
    A folder is disabled if it matches a disabled path OR is a child of a disabled path.
    """
    path_str = str(path)
    for disabled in disabled_folders:
        if path_str == disabled or path_str.startswith(disabled + '/'):
            return False
    return True

# --- Which files a show plays ------------------------------------------------
#
# A show plays photos and videos (never audio), drawn from EVERY file below its folder,
# in a rating-weighted random order WITHOUT repeats: each file appears at most once,
# a higher rating makes a file come earlier. The walk only lists names; a file is
# stat'ed once it has been picked.

SHOW_MAX_FILES = 2000      # a folder show plays at most this many (picked from all files)
FEATURED_SIZE = 50         # Feeling Lucky


def is_show_media(path) -> bool:
    """Photos and videos; audio has no picture to show."""
    return is_photo(Path(path)) or is_video(Path(path))


def _log_walk_error(path, error):
    # One unreadable folder skips that folder only, never the rest of the walk
    print(f"Shows: kon {path} niet lezen: {error}")


def collect_show_files(root_path: Path, disabled_folders: List[str]) -> List[str]:
    """All photo and video paths below root_path, skipping disabled folders (and what
    walk_archive skips: the app's own folders, TRASH, RAW_DUPLICATES, hidden files and
    symlinks)."""
    paths = []
    for root, dirs, filenames in walk_archive(root_path, config, onerror=_log_walk_error):
        if not is_folder_enabled(Path(root), disabled_folders):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if is_folder_enabled(Path(root) / d, disabled_folders)]
        paths.extend(os.path.join(root, name) for name in filenames if is_show_media(name))
    return paths


def show_weight(rating: int) -> float:
    """rating ^ 1.5: a liked photo comes clearly more often, a disliked one still can."""
    return pow(max(1, rating), 1.5)


def weighted_order(paths: List[str], ratings: Dict[str, int], k: Optional[int] = None) -> List[str]:
    """
    A weighted random sample WITHOUT replacement (Efraimidis-Spirakis): every path gets
    the key random() ** (1 / weight) and the k largest keys win, in that order. Each
    path appears at most once; a higher rating makes it likely to come earlier.
    """
    k = len(paths) if k is None else min(k, len(paths))
    if k <= 0:
        return []
    keyed = ((random.random() ** (1.0 / show_weight(ratings.get(p, 1000))), p) for p in paths)
    return [p for _, p in heapq.nlargest(k, keyed)]


def describe_show_file(path: str, ratings: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """The file dict the slideshow needs, or None when the file is gone or unreadable."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    p = Path(path)
    return {
        "path": path,
        "name": p.name,
        "folder": p.parent.name,
        "size": st.st_size,
        "modified": st.st_mtime,
        "type": "video" if is_video(p) else "photo",
        "rating": ratings.get(path, 1000),
    }


def pick_show_files(paths: List[str], ratings: Dict[str, int], k: int) -> List[Dict[str, Any]]:
    """Weighted order without repeats, described; files that vanished are skipped."""
    files = []
    for path in weighted_order(paths, ratings):
        info = describe_show_file(path, ratings)
        if info:
            files.append(info)
            if len(files) >= k:
                break
    return files


@router.get("/stories")
def get_stories() -> Dict[str, Any]:
    """
    Shows from the library folder ONLY:
    - shows: every folder below library (not library itself) with photos or videos
      somewhere inside; the preview is a random one of its own files, or of its
      subfolders' files when it has none itself
    - featured: Feeling Lucky, a rating-weighted sample (no repeats) from ALL library files
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    if not config.VAULT_ROOT.exists():
        raise HTTPException(status_code=503, detail="Vault not available")
    
    # Shows come ONLY from the library folder, in the resolved spelling that settings and
    # votes are stored in (and never a 'library' symlink that points out of the vault)
    library_root = vault_folder_or_none("library", config)
    if library_root is None or not library_root.is_dir():
        return {"featured": None, "shows": []}
    
    disabled_folders = get_disabled_folders()
    root = str(library_root)

    # One walk: every show-media path, and per folder a random preview from its own files
    # or (reservoir sampling) from the files of its subfolders
    all_paths: List[str] = []
    order: List[str] = []                  # folders in walk order (parents before children)
    preview: Dict[str, str] = {}
    seen: Dict[str, int] = {}              # media counted per folder for the reservoir
    own_media: set = set()                 # folders with photos or videos of their own

    for folder, dirs, filenames in walk_archive(library_root, config, onerror=_log_walk_error):
        dirs[:] = sorted(d for d in dirs if is_folder_enabled(Path(folder) / d, disabled_folders))
        if not is_folder_enabled(Path(folder), disabled_folders):
            dirs[:] = []
            continue
        if folder != root:
            order.append(folder)
        media = [os.path.join(folder, n) for n in filenames if is_show_media(n)]
        all_paths.extend(media)
        if not media:
            continue
        if folder != root:
            own_media.add(folder)
            preview[folder] = random.choice(media)
        # Ancestors without media of their own take a random file from below them
        ancestor = os.path.dirname(folder)
        while len(ancestor) > len(root) and ancestor.startswith(root + os.sep):
            if ancestor not in own_media:
                for path in media:
                    seen[ancestor] = seen.get(ancestor, 0) + 1
                    if random.randrange(seen[ancestor]) == 0:
                        preview[ancestor] = path
            ancestor = os.path.dirname(ancestor)

    folder_shows = [
        {
            "title": Path(folder).name,
            "path": folder,
            "preview": {"path": preview[folder], "name": Path(preview[folder]).name},
            "type": "folder_show",
        }
        for folder in sorted(order, key=str.lower) if folder in preview
    ]

    # Featured: weighted by the Shows votes, drawn from every file in the library
    featured_show = None
    ratings = db.get_all_ratings() if db else {}
    featured_files = pick_show_files(all_paths, ratings, FEATURED_SIZE)
    if featured_files:
        featured_show = {
            "title": "Feeling Lucky",
            "subtitle": f"{len(featured_files)} random photos from your library",
            "files": featured_files,
            "type": "featured"
        }
    
    return {
        "featured": featured_show,
        "shows": folder_shows
    }

@router.post("/settings")
def update_settings(payload: Dict[str, Any]):
    """
    Update settings (enable/disable folders).
    Receives: { "path": "/path/to/folder", "enabled": true/false }
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")

    path = payload.get("path")
    enabled = payload.get("enabled")
    
    if path is None or enabled is None:
        raise HTTPException(status_code=400, detail="Missing path or enabled status")
    if not isinstance(path, str) or not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="Invalid path or enabled status")
    
    # Only folders inside the vault can be switched off (stored in one spelling)
    path = str(resolve_in_vault(path, config, folder=True))
    
    db.set_show_folder_disabled(path, not enabled)
    return {"status": "success", "message": f"Folder {'enabled' if enabled else 'disabled'}"}

@router.get("/settings")
def get_settings():
    """Get current settings"""
    return {"disabled_folders": get_disabled_folders()}

@router.post("/vote")
def vote_file(payload: Dict[str, Any]):
    """
    Save a vote (up/down) for a media file and update its ELO rating.
    Receives: { "path": "/path/to/file", "vote": "up"|"down" }
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")

    path = payload.get("path")
    vote = payload.get("vote")

    if not path or not isinstance(path, str) or vote not in ("up", "down"):
        raise HTTPException(status_code=400, detail="Missing or invalid path/vote")

    target = resolve_in_vault(path, config)
    # Only an existing regular file gets a rating (no rows for typos, folders or symlinks)
    try:
        is_file = stat.S_ISREG(os.lstat(target).st_mode)
    except OSError:
        is_file = False
    if not is_file:
        raise HTTPException(status_code=404, detail="Bestand niet gevonden")
    path = str(target)

    current_rating = db.get_rating(path)
    change = 50 if vote == "up" else -50
    new_rating = max(100, current_rating + change)
    db.update_rating(path, new_rating)

    return {"status": "ok", "new_rating": new_rating}

@router.get("/folder-content")
def get_folder_content(path: str):
    """
    Photos and videos below a folder, for a "Folder Show": every file is a candidate,
    each appears at most once, in a rating-weighted random order (higher-rated files
    tend to come first). At most SHOW_MAX_FILES are returned.
    """
    # Inside the vault first, existence second: the same 403 for anything outside
    target_path = resolve_in_vault(path, config, folder=True)
         
    if not target_path.is_dir():
         raise HTTPException(status_code=404, detail="Folder not found")
    
    ratings = db.get_all_ratings() if db else {}
    paths = collect_show_files(target_path, get_disabled_folders())
    return pick_show_files(paths, ratings, SHOW_MAX_FILES)
