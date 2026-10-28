from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List, Dict, Any
import os
import random
import stat
from backend.config import Config, is_media_file, is_video
from backend.paths import ArchiveFilter, resolve_in_vault, scan_dir, vault_folder_or_none, walk_archive

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
    
    conn = db._get_connection()
    cursor = conn.cursor()
    # 0 = disabled
    cursor.execute("SELECT path FROM show_settings WHERE enabled = 0")
    results = cursor.fetchall()
    conn.close()
    
    return [row[0] for row in results]

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

# Updated signature to use disabled_folders list AND ratings dict
def get_all_media_recursive(root_path: Path, _vault_root: Path, ratings: Dict[str, int], limit: int = 1000) -> List[Dict[str, Any]]:
    """Recursively find media files with a limit to avoid performance issues."""
    import os
    
    files = []
    count = 0
    
    # Get list of disabled folders (more efficient to check against)
    disabled_folders = get_disabled_folders()
    
    # walk_archive skips the app's own folders, TRASH, RAW_DUPLICATES, hidden files and symlinks
    for root, dirs, filenames in walk_archive(root_path, config):
        # Check if current folder is enabled (or child of enabled)
        current_path = Path(root)
        if not is_folder_enabled(current_path, disabled_folders):
            # If directory is disabled, we can modify dirs in-place to stop recursion
            dirs[:] = [] 
            continue
        
        for filename in filenames:
            file_path = Path(root) / filename
            if is_media_file(file_path):
                try:
                    stat = file_path.stat()
                    # Get rating from bulk dict (default 1000)
                    rating = ratings.get(str(file_path), 1000)
                    
                    files.append({
                        "path": str(file_path),
                        "name": file_path.name,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "type": "video" if is_video(file_path) else "photo",
                        "rating": rating
                    })
                    count += 1
                except:
                    continue
            
            if count >= limit:
                return files
                
    return files

def weighted_sample(files: List[Dict], k: int) -> List[Dict]:
    """Sample files based on ELO rating weights"""
    if not files:
        return []
        
    k = min(k, len(files))
    
    # Calculate weights: rating ^ 1.5 to favor higher rated items more
    weights = [pow(f['rating'], 1.5) for f in files]
    
    return random.choices(files, weights=weights, k=k)

@router.get("/stories")
async def get_stories() -> Dict[str, Any]:
    """
    Generate "Shows" based on sub-folders within the library folder ONLY.
    Structure:
    - shows: List of sub-folders within library (each acts as a show)
    - featured: One ELO-weighted random file from library
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
    
    # 1. Get Sub-folder Shows (recursive within library only)
    folder_shows = []
    
    try:
        import os
        
        archive_filter = ArchiveFilter(config)
        for root, dirs, files in walk_archive(library_root, config, archive_filter):
            current_path = Path(root)
            
            # Prune disabled dirs (hidden dirs and symlinks are never listed)
            dirs[:] = [
                d for d in dirs
                if is_folder_enabled(current_path / d, disabled_folders)
            ]
            
            # Check for media in this folder (shallow first)
            has_media = False
            preview_file = None
            
            for name in files:
                if is_media_file(Path(name)):
                    has_media = True
                    preview_file = current_path / name
                    break
            
            # Deep search for preview if no direct media
            if not has_media:
                preview_file = get_random_media_file(current_path)
                if preview_file:
                    has_media = True
            
            if has_media and preview_file:
                title = current_path.name
                
                folder_shows.append({
                    "title": title,
                    "path": str(current_path),
                    "preview": {
                        "path": str(preview_file),
                        "name": preview_file.name
                    },
                    "type": "folder_show"
                })
    
    except Exception as e:
        pass
    
    # 2. Featured Show — random files from library (full slideshow)
    featured_show = None
    ratings = db.get_all_ratings() if db else {}
    all_media = get_all_media_recursive(library_root, config.VAULT_ROOT, ratings, limit=500)
    
    if all_media:
        import random
        # Pick up to 50 random files for a proper slideshow
        sample_size = min(50, len(all_media))
        featured_files = random.sample(all_media, sample_size)
        
        # Add folder name to each file for display
        for f in featured_files:
            f["folder"] = Path(f["path"]).parent.name
        
        # Use first file info for the hero card subtitle
        featured_show = {
            "title": "Feeling Lucky",
            "subtitle": f"{sample_size} random photos from your library",
            "files": featured_files,
            "type": "featured"
        }
    
    return {
        "featured": featured_show,
        "shows": folder_shows
    }

def get_random_media_file(folder_path: Path) -> Path:
    """Find a random media file in a folder (recursive)"""
    # Try shallow first for speed
    import os
    import random
    
    media_files = []
    try:
        # Shallow check
        for entry in scan_dir(folder_path, ArchiveFilter(config)):
            if entry.is_file(follow_symlinks=False) and is_media_file(Path(entry.path)):
                media_files.append(Path(entry.path))
        
        if media_files:
            return random.choice(media_files)
            
        # If no files in root, try one level deeper
        for root, dirs, files in walk_archive(folder_path, config):
            for f in files:
                p = Path(root) / f
                if is_media_file(p):
                    return p
            # Just panic return first found if recursive walk gets heavy, 
            # but iterate safely.
            # Actually os.walk is fine, just break after first match if we want SPEED
            if files: 
                 break
    except:
        pass
    
    return None

@router.post("/settings")
async def update_settings(payload: Dict[str, Any]):
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
    
    conn = db._get_connection()
    cursor = conn.cursor()
    
    try:
        if enabled:
            # Enable: Remove from disabled list
            cursor.execute("DELETE FROM show_settings WHERE path = ? AND enabled = 0", (path,))
        else:
            # Disable: Add to disabled list (0 = disabled)
            cursor.execute(
                "INSERT OR REPLACE INTO show_settings (path, enabled) VALUES (?, 0)",
                (path,)
            )
            
        conn.commit()
        return {"status": "success", "message": f"Folder {'enabled' if enabled else 'disabled'}"}
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@router.get("/settings")
async def get_settings():
    """Get current settings"""
    return {"disabled_folders": get_disabled_folders()}

@router.post("/vote")
async def vote_file(payload: Dict[str, Any]):
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
async def get_folder_content(path: str):
    """
    Get all media files in a specific folder (flat list).
    Used for playing a "Folder Show".
    Files are returned in ELO-weighted order: higher-rated photos appear first.
    """
    # Inside the vault first, existence second: the same 403 for anything outside
    target_path = resolve_in_vault(path, config, folder=True)
         
    if not target_path.is_dir():
         raise HTTPException(status_code=404, detail="Folder not found")
    
    # Fetch all ratings for ELO weighting
    ratings = db.get_all_ratings() if db else {}
    
    # Get all media files recursively
    all_files = get_all_media_recursive(target_path, config.VAULT_ROOT, ratings, limit=500)
    
    if not all_files:
        return []
    
    # Return files in ELO-weighted shuffled order
    # Higher-rated files appear earlier in the list
    weighted_files = weighted_sample(all_files, len(all_files))
    
    return weighted_files
