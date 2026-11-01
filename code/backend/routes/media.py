"""
DataVault Dashboard - Media Routes
"""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pathlib import Path
from typing import Dict, Any, List, Optional
import sqlite3

from backend import recovery
from backend.config import Config, content_type, is_video, media_type
from backend.paths import resolve_in_vault, is_in_vault, ArchiveFilter
from backend.file_ops import (
    move_no_overwrite, same_folder, is_inside, ARCHIVE_LOCK,
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

# Types a browser runs as a page (scripts included) even when they come from an <img>
# URL opened on its own: never shown inline from the archive
ACTIVE_CONTENT_TYPES = {'text/html', 'application/xhtml+xml', 'image/svg+xml', 'text/xml', 'application/xml'}


@router.get("/api/file")
def get_file_endpoint(path: str = Query(...)):
    """
    Stream a file from the archive (never the app's own cache or database).

    Photos, videos and audio are served inline with their real content type, so the
    browser can show and stream them; a RAW photo is never text/plain (see
    config.content_type). Anything else (a saved web page, an SVG, a PDF, ...) is a
    download (application/octet-stream, attachment): a page from the archive must never
    run on the app's own origin, where /api/session hands out the session token (R25-8).
    nosniff and a sandbox policy keep the browser from guessing its way around that.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    file_path = resolve_in_vault(path, config)
    
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}
    kind = content_type(file_path)
    if media_type(file_path) == 'other' or kind in ACTIVE_CONTENT_TYPES:
        return FileResponse(path=str(file_path), media_type='application/octet-stream', headers=headers,
                            filename=file_path.name, content_disposition_type='attachment')
    return FileResponse(path=str(file_path), media_type=kind, headers=headers)

@router.get("/api/thumb")
def get_thumbnail_endpoint(path: str = Query(...)):
    """Get thumbnail for media file"""
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    file_path = resolve_in_vault(path, config)
    
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Generate or get cached thumbnail
    from backend import thumbnail
    from backend.config import media_type

    kind = media_type(file_path)
    thumb_path = None
    if kind in ('photo', 'video'):
        thumb_path = thumbnail.get_or_create_thumbnail(
            file_path, config.THUMB_CACHE_PATH, is_video=(kind == 'video'))

    if thumb_path is None:
        # Nothing to show (audio, a document, no ffmpeg, a file that cannot be read or
        # decoded): a placeholder that is never stored, here or in the browser, so the
        # real thumbnail appears as soon as it can be made
        return Response(
            content=thumbnail.placeholder_bytes(kind),
            media_type='image/jpeg',
            headers={'Cache-Control': 'no-store', 'X-DataVault-Placeholder': kind},
        )

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
                raise HTTPException(status_code=404, detail="Bestand niet gevonden")
            
            if destination_folder.exists() and not destination_folder.is_dir():
                raise HTTPException(status_code=400, detail="De bestemming is geen map")
            
            if same_folder(destination_folder, source_path.parent):
                raise HTTPException(status_code=400, detail="Het bestand staat al in deze map")
            
            # Atomic, metadata-preserving, never overwrites (see backend/file_ops.py)
            destination_path = move_no_overwrite(source_path, destination_folder / source_path.name)
            # Recorded for the recovery center (Herstelcentrum), which can move it back;
            # if recording fails the file goes straight back and the move is refused
            try:
                recovery.ActionRecorder(
                    db, "viewer_move", f"Verplaatst naar {destination_folder.name or 'het archief'}",
                    folder=str(source_path.parent),
                ).add(source_path, destination_path)
            except sqlite3.OperationalError:
                raise   # database gone with the disk: 503 (errors.py)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Verplaatsen niet vastgelegd, het bestand is teruggezet: {e}")
    except ProtectedPathError:
        raise HTTPException(status_code=403, detail="Toegang geweigerd: cache-map van de app")
    except CrossDeviceError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DestinationFolderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileOperationError as e:
        raise HTTPException(status_code=500, detail=f"Verplaatsen mislukt: {e}")
    
    return {
        "success": True,
        "new_path": str(destination_path),
        "message": f"Verplaatst naar {destination_folder.name}/{destination_path.name}"
    }


# ==================== SELECTION: MOVE OR TRASH MANY FILES AT ONCE ====================
# The selection bar (export.js) in the Tijdlijn and Albums. One request is one action in
# the recovery center (Herstelcentrum), which moves every file back in one go. The trash is
# a folder like any other: nothing is deleted.

MAX_SELECTION = 20000   # as many as the selection bar selects at once (MAX_ALL in export.js)

recovery.register_kind("selection_move", "Selectie verplaatst")
recovery.register_kind("selection_trash", "Naar de prullenmand")


def _selection_paths(body: Any) -> List[str]:
    paths = body.get("paths") if isinstance(body, dict) else None
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        raise HTTPException(status_code=400, detail="Ontbrekende of ongeldige 'paths'")
    if len(paths) > MAX_SELECTION:
        raise HTTPException(status_code=400, detail=f"Hoogstens {MAX_SELECTION} bestanden tegelijk")
    return list(dict.fromkeys(paths))


def _move_selection(paths: List[str], destination: Path, kind: str, title: str,
                    already_there: str = "Staat al in deze map") -> Dict[str, Any]:
    """
    Move each file into destination, under one lock and one recovery action. A file that
    cannot go (gone, outside the vault, already there) is skipped with its reason; the rest
    still moves. If recording fails the file goes straight back and the batch stops there.
    """
    moved: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    error: Optional[str] = None
    with ARCHIVE_LOCK:
        recorder = recovery.ActionRecorder(db, kind, title, folder=str(destination))
        for path_str in paths:
            try:
                source = resolve_in_vault(path_str, config)
            except HTTPException as e:
                skipped.append({"path": path_str, "reason": str(e.detail)})
                continue
            if source.is_symlink() or not source.is_file():
                skipped.append({"path": path_str, "reason": "Bestand niet gevonden"})
                continue
            if same_folder(destination, source.parent) or (kind == "selection_trash" and is_inside(source, destination)):
                skipped.append({"path": path_str, "reason": already_there})
                continue
            try:
                target = move_no_overwrite(source, destination / source.name)
            except ProtectedPathError:
                skipped.append({"path": path_str, "reason": "Toegang geweigerd: cache-map van de app"})
                continue
            except DestinationFolderError as e:
                error = str(e)
                break
            except (CrossDeviceError, FileOperationError) as e:
                skipped.append({"path": path_str, "reason": str(e)})
                continue
            try:
                recorder.add(source, target)
            except sqlite3.OperationalError:
                raise   # database gone with the disk: 503 (errors.py)
            except Exception as e:
                error = f"Verplaatsen niet vastgelegd, gestopt na {len(moved)} bestanden: {e}"
                break
            moved.append({"path": path_str, "new_path": str(target)})
    return {
        "success": error is None,
        "moved": moved,
        "skipped": skipped,
        "action_id": recorder.action_id,
        "error": error,
    }


@router.post("/api/move-files")
def move_files_endpoint(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Move a selection into one folder (never overwrites; a name clash gets _DUP_001).

    Body: { "paths": ["/vault/a.jpg", ...], "destination_folder": "/vault/library/X" }
    Returns: { "moved": [{path, new_path}], "skipped": [{path, reason}], "action_id", "error" }
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    paths = _selection_paths(body)
    destination_str = body.get("destination_folder")
    if not isinstance(destination_str, str) or not destination_str:
        raise HTTPException(status_code=400, detail="Ontbrekende of ongeldige 'destination_folder'")
    destination = resolve_in_vault(destination_str, config, folder=True)
    if destination.exists() and not destination.is_dir():
        raise HTTPException(status_code=400, detail="De bestemming is geen map")
    return _move_selection(paths, destination, "selection_move", f"Verplaatst naar {destination.name or 'het archief'}")


@router.post("/api/trash-files")
def trash_files_endpoint(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Move a selection to the trash folder (TRASH): the app's 'delete'. Undo in the recovery center.

    Body: { "paths": ["/vault/a.jpg", ...] }
    Returns: the same as /api/move-files
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    paths = _selection_paths(body)
    trash = config.TRASH_FOLDER
    # The recovery center only moves files back from inside the vault
    if not is_in_vault(trash, config, allow_internal=True):
        raise HTTPException(status_code=409, detail="De prullenmand staat buiten het archief; verwijderen kan hier niet")
    return _move_selection(paths, trash, "selection_trash", "Naar de prullenmand", "Staat al in de prullenmand")
