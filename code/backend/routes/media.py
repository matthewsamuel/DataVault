"""
DataVault Dashboard - Media Routes
"""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from typing import Dict, Any
import os
import shutil
from datetime import datetime

from backend.config import Config, validate_path_in_vault, is_video

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
    
    if not folder or folder.strip() == "":
        folder_path = config.VAULT_ROOT
    else:
        folder_path = Path(folder)
    
    # Validate path is in vault
    if not validate_path_in_vault(folder_path, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="Access denied: path outside vault")
    
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    
    from backend.scanner import get_media_files
    
    media_files = get_media_files(folder_path, config.VAULT_ROOT)
    
    return {
        "folder": str(folder_path),
        "files": media_files
    }

@router.get("/api/file")
def get_file_endpoint(path: str = Query(...)):
    """Stream full media file"""
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    file_path = Path(path)
    
    # Validate path
    if not validate_path_in_vault(file_path, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="Access denied: path outside vault")
    
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Return file with appropriate content type so browsers can stream it natively
    return FileResponse(path=str(file_path))

@router.get("/api/thumb")
def get_thumbnail_endpoint(path: str = Query(...)):
    """Get thumbnail for media file"""
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    file_path = Path(path)
    
    # Validate path
    if not validate_path_in_vault(file_path, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="Access denied: path outside vault")
    
    if not file_path.exists() or not file_path.is_file():
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
    if not file_path_str:
        raise HTTPException(status_code=400, detail="Missing 'path' in request body")
        
    file_path = Path(file_path_str)
    
    # Validate file path is within vault
    if not validate_path_in_vault(file_path, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="Access denied: path outside vault")
    
    # Check file exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        import subprocess
        subprocess.run(['open', '-R', str(file_path)], check=True)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open in Finder: {str(e)}")

@router.post("/api/delete-to-trash")
def delete_to_trash_endpoint(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Move a file to the trash folder.
    
    Body: { "path": "/full/path/to/file.jpg" }
    Returns: { "success": true, "trash_path": "..." }
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    try:
        file_path_str = body.get("path")
        
        if not file_path_str:
            raise HTTPException(status_code=400, detail="Missing 'path' in request body")
        
        file_path = Path(file_path_str)
        
        # Validate file path is within vault
        if not validate_path_in_vault(file_path, config.VAULT_ROOT):
            raise HTTPException(status_code=403, detail="Access denied: path outside vault")
        
        # Check file exists
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        
        # Ensure trash folder exists
        config.TRASH_FOLDER.mkdir(parents=True, exist_ok=True)
        
        # Determine trash path with collision handling
        trash_filename = file_path.name
        trash_path = config.TRASH_FOLDER / trash_filename
        
        # If file already exists in trash, append timestamp
        if trash_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name_parts = file_path.stem
            extension = file_path.suffix
            trash_filename = f"{name_parts}_{timestamp}{extension}"
            trash_path = config.TRASH_FOLDER / trash_filename
        
        # Move file to trash
        shutil.move(str(file_path), str(trash_path))
        
        return {
            "success": True,
            "trash_path": str(trash_path),
            "message": f"File moved to trash: {trash_filename}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to move file to trash: {str(e)}")

@router.post("/api/move-file")
def move_file_endpoint(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Move a file to a different folder.
    
    Body: { 
        "source_path": "/full/path/to/file.jpg",
        "destination_folder": "/full/path/to/destination/folder"
    }
    Returns: { "success": true, "new_path": "...", "message": "..." }
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    try:
        source_path_str = body.get("source_path")
        destination_folder_str = body.get("destination_folder")
        
        if not source_path_str or not destination_folder_str:
            raise HTTPException(
                status_code=400, 
                detail="Missing 'source_path' or 'destination_folder' in request body"
            )
        
        source_path = Path(source_path_str)
        destination_folder = Path(destination_folder_str)
        
        # Validate both paths are within vault
        if not validate_path_in_vault(source_path, config.VAULT_ROOT):
            raise HTTPException(status_code=403, detail="Access denied: source path outside vault")
        
        if not validate_path_in_vault(destination_folder, config.VAULT_ROOT):
            raise HTTPException(status_code=403, detail="Access denied: destination path outside vault")
        
        # Check source file exists
        if not source_path.exists() or not source_path.is_file():
            raise HTTPException(status_code=404, detail="Source file not found")
        
        # Ensure destination folder exists
        destination_folder.mkdir(parents=True, exist_ok=True)
        
        # Determine destination path with collision handling
        destination_filename = source_path.name
        destination_path = destination_folder / destination_filename
        
        # If file already exists in destination, append timestamp
        if destination_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            stem = source_path.stem
            suffix = source_path.suffix
            destination_filename = f"{stem}_{timestamp}{suffix}"
            destination_path = destination_folder / destination_filename
        
        # Move file preserving ALL metadata (creation date, EXIF, xattrs, etc.)
        _safe_move_file(source_path, destination_path)
        
        return {
            "success": True,
            "new_path": str(destination_path),
            "message": f"File moved to {destination_folder.name}/{destination_filename}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to move file: {str(e)}")


def _safe_move_file(source: Path, destination: Path):
    """
    Move a file while preserving ALL metadata (creation date, modification date,
    EXIF, extended attributes, resource forks).
    
    Handles macOS 'uchg' (user immutable / locked) files by temporarily
    clearing the flag, then restoring it on the destination.
    
    Strategy:
    1. Clear any immutable flags on the source file
    2. Try os.rename() — atomic, preserves everything (works on same filesystem)
    3. Fallback: use macOS-native 'ditto' which preserves all HFS+/APFS metadata,
       then delete the original.
    4. Restore original flags on the destination
    """
    import subprocess, stat
    
    # Save and clear immutable flags (uchg, schg, etc.)
    original_stat = os.stat(str(source))
    original_flags = original_stat.st_flags
    has_flags = original_flags != 0
    
    if has_flags:
        try:
            os.chflags(str(source), 0)
        except OSError as e:
            raise RuntimeError(f"Cannot clear file flags on source: {e}")
    
    try:
        # Strategy 1: os.rename (atomic, same filesystem, preserves everything)
        try:
            os.rename(str(source), str(destination))
            # Restore flags on destination
            if has_flags:
                os.chflags(str(destination), original_flags)
            return
        except OSError:
            pass
        
        # Strategy 2: macOS ditto (preserves ALL metadata including creation date)
        result = subprocess.run(
            ['/usr/bin/ditto', str(source), str(destination)],
            capture_output=True, text=True
        )
        
        # ditto may print warnings but still succeed
        if not destination.exists():
            raise RuntimeError(f"ditto failed: {result.stderr.strip()}")
        
        if destination.stat().st_size != original_stat.st_size:
            # Size mismatch — don't delete original, restore flags
            destination.unlink()
            raise RuntimeError("File size mismatch after copy, aborting move")
        
        # Safe to delete original now (flags already cleared above)
        os.remove(str(source))
        
        # Restore flags on destination
        if has_flags:
            os.chflags(str(destination), original_flags)
    
    except Exception:
        # If anything goes wrong, try to restore flags on source
        if has_flags and source.exists():
            try:
                os.chflags(str(source), original_flags)
            except OSError:
                pass
        raise

