"""
DataVault Dashboard - Media Routes
"""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from typing import Dict, Any

from backend.config import Config, is_video
from backend.paths import resolve_in_vault, ArchiveFilter
from backend.file_ops import (
    move_no_overwrite, same_folder, ARCHIVE_LOCK,
    FileOperationError, CrossDeviceError, DestinationFolderError, ProtectedPathError,
)

router = APIRouter()

# Global state
config: Config = None
db = None

def init_router(cfg: Config, database):
    """Initialize router with config and database"""
    global config, db
    config = cfg
    db = database

@router.get("/api/media")
def get_media_endpoint(folder: str = Query(...)) -> Dict[str, Any]:
    """Get media files in a folder"""
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    # Inside the vault first, existence second: the same 403 for anything outside
    folder_path = resolve_in_vault(folder if folder.strip() else str(config.VAULT_ROOT), config, folder=True)
    
    if not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")
    
    from backend.scanner import get_media_files
    
    media_files = get_media_files(folder_path, ArchiveFilter(config))

    # The Shows rating, only for files that were actually voted on (the viewer shows
    # nothing rather than a made-up default)
    ratings = db.get_ratings_in_folder(str(folder_path)) if db else {}
    for item in media_files:
        if item["path"] in ratings:
            item["rating"], item["votes"] = ratings[item["path"]]
    
    return {
        "folder": str(folder_path),
        "files": media_files
    }

@router.get("/api/file")
def get_file_endpoint(path: str = Query(...)):
    """Stream full media file (never the app's own cache or database)"""
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    file_path = resolve_in_vault(path, config)
    
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Return file with appropriate content type so browsers can stream it natively
    return FileResponse(path=str(file_path))

@router.get("/api/thumb")
def get_thumbnail_endpoint(path: str = Query(...)):
    """Get thumbnail for media file"""
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    file_path = resolve_in_vault(path, config)
    
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Generate or get cached thumbnail
    from backend.thumbnail import get_or_create_thumbnail
    from backend.config import is_audio
    
    thumb_path = get_or_create_thumbnail(
        file_path,
        config.THUMB_CACHE_PATH,
        is_video=is_video(file_path),
        is_audio=is_audio(file_path)
    )
    
    if not thumb_path or not thumb_path.exists():
        raise HTTPException(status_code=500, detail="Failed to generate thumbnail")
    
    return FileResponse(
        path=str(thumb_path),
        media_type='image/jpeg'
    )

@router.post("/api/open-in-finder")
def open_in_finder_endpoint(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Open a file in macOS Finder.
    
    Body: { "path": "/full/path/to/file.jpg" }
    Returns: { "success": true }
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    file_path_str = body.get("path")
    if not file_path_str or not isinstance(file_path_str, str):
        raise HTTPException(status_code=400, detail="Missing or invalid 'path' in request body")
        
    file_path = resolve_in_vault(file_path_str, config)
    
    # Check file exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        import subprocess
        subprocess.run(['open', '-R', str(file_path)], check=True)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open in Finder: {str(e)}")

@router.post("/api/move-file")
def move_file_endpoint(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Move a file to a different folder (never overwrites; a name clash gets _DUP_001).
    
    Body: { 
        "source_path": "/full/path/to/file.jpg",
        "destination_folder": "/full/path/to/destination/folder"
    }
    Returns: { "success": true, "new_path": "...", "message": "..." }
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    source_path_str = body.get("source_path")
    destination_folder_str = body.get("destination_folder")
    
    if not source_path_str or not destination_folder_str:
        raise HTTPException(
            status_code=400, 
            detail="Missing 'source_path' or 'destination_folder' in request body"
        )
    if not isinstance(source_path_str, str) or not isinstance(destination_folder_str, str):
        raise HTTPException(status_code=400, detail="'source_path' and 'destination_folder' must be strings")
    
    # Both inside the vault and outside the app's own folders (cache, database);
    # is_inside (via resolve_in_vault) also catches other spellings such as .DATAVAULT_CACHE
    source_path = resolve_in_vault(source_path_str, config)
    destination_folder = resolve_in_vault(destination_folder_str, config, folder=True)
    
    try:
        # One app-wide lock: no other move can slip in between these checks and the move
        with ARCHIVE_LOCK:
            # Only regular files, never folders or symlinks
            if source_path.is_symlink() or not source_path.is_file():
                raise HTTPException(status_code=404, detail="Source file not found")
            
            if destination_folder.exists() and not destination_folder.is_dir():
                raise HTTPException(status_code=400, detail="Destination is not a folder")
            
            if same_folder(destination_folder, source_path.parent):
                raise HTTPException(status_code=400, detail="File is already in this folder")
            
            # Atomic, metadata-preserving, never overwrites (see backend/file_ops.py)
            destination_path = move_no_overwrite(source_path, destination_folder / source_path.name)
    except ProtectedPathError:
        raise HTTPException(status_code=403, detail="Access denied: cache folder")
    except CrossDeviceError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DestinationFolderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileOperationError as e:
        raise HTTPException(status_code=500, detail=f"Failed to move file: {e}")
    
    return {
        "success": True,
        "new_path": str(destination_path),
        "message": f"File moved to {destination_folder.name}/{destination_path.name}"
    }
