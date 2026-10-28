"""
DataVault Dashboard - Duplicates Routes
"""
from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List, Dict, Any, Optional
import os
import stat

from backend.config import Config, validate_path_in_vault
from backend.file_ops import (
    safe_move_with_structure, move_no_overwrite, files_identical, is_inside, ARCHIVE_LOCK,
    FileOperationError, CrossDeviceError, ProtectedPathError,
)

router = APIRouter()

# Global state
config: Config = None
db = None
last_scan_results: List[Dict[str, Any]] = []

def init_router(cfg: Config, database):
    """Initialize router with config and database"""
    global config, db
    config = cfg
    db = database


def _protected_folders() -> List[Path]:
    """
    Folders the duplicates tool never takes files out of (nor accepts a kept original from).
    Checked with is_inside, so other spellings (raw_duplicates, .DATAVAULT_CACHE) count too.
    """
    return [config.DUPLICATES_FOLDER, config.TRASH_FOLDER, config.CACHE_FOLDER, config.DB_PATH]


def _in_protected_folder(path: Path) -> Optional[Path]:
    for folder in _protected_folders():
        if is_inside(path, folder):
            return folder
    return None

@router.get("/api/raw_duplicates/progress")
async def get_scan_progress_endpoint():
    """Get current scan progress"""
    from backend.progress import get_scan_progress
    
    progress = get_scan_progress()
    if progress:
        return progress.to_dict()
    return {
        "status": "idle",
        "phase": "No scan in progress",
        "total_files": 0,
        "processed_files": 0,
        "duplicates_found": 0,
        "progress_percent": 0
    }

@router.post("/api/raw_duplicates/cancel")
async def cancel_scan_endpoint():
    """Cancel the current scan"""
    from backend.progress import cancel_scan
    
    cancel_scan()
    return {"status": "cancelled"}


@router.get("/api/raw_duplicates")
async def get_duplicates_endpoint() -> Dict[str, Any]:
    """Get last scan results"""
    return {
        "duplicates_found": len(last_scan_results),
        "duplicates": last_scan_results
    }

def _paths_allowed(op: Dict[str, Any]) -> bool:
    """Re-validate what the database says before moving anything."""
    return (
        is_inside(Path(op["dst_path"]), config.DUPLICATES_FOLDER)
        and validate_path_in_vault(Path(op["src_path"]), config.VAULT_ROOT)
        and _in_protected_folder(Path(op["src_path"])) is None
    )


def _still_there(op: Dict[str, Any]) -> bool:
    """The moved file is still a regular file at its RAW_DUPLICATES location."""
    try:
        return stat.S_ISREG(os.lstat(op["dst_path"]).st_mode)
    except OSError:
        return False


def _restorable(op: Dict[str, Any]) -> bool:
    return _paths_allowed(op) and _still_there(op)


@router.post("/api/raw_duplicates/undo_last")
def undo_last_delete_endpoint() -> Dict[str, Any]:
    """
    Undo the most recent delete run that still has something to restore.
    Moves files back from RAW_DUPLICATES to where they came from.
    
    A run whose files are all gone from RAW_DUPLICATES (e.g. moved by hand) is
    passed over instead of blocking every older run; its records are kept, so it
    becomes restorable again if the files come back. Restored files are removed
    from the run; files that failed stay recorded for a later attempt.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    with ARCHIVE_LOCK:
        runs = db.get_delete_runs()
        if not runs:
            raise HTTPException(status_code=404, detail="No delete operations to undo")
        
        run_id = None
        operations = []
        for run in runs:
            ops = db.get_delete_operations(run["run_id"])
            if any(_restorable(op) for op in ops):
                run_id, operations = run["run_id"], ops
                break
        
        if run_id is None:
            raise HTTPException(
                status_code=404,
                detail="Nothing left to restore: the moved files are no longer in RAW_DUPLICATES"
            )
        
        restored = 0
        skipped = 0
        errors = []
        
        for op in operations:
            # src_path is the ORIGINAL location, dst_path is where it went (in RAW_DUPLICATES).
            # For undo, we move FROM dst_path BACK TO src_path
            dst_path = Path(op["dst_path"])  # Current location
            src_path = Path(op["src_path"])  # Original location
            
            if not _paths_allowed(op):
                errors.append(f"Refused to restore {dst_path}: path outside RAW_DUPLICATES or vault")
                skipped += 1
                continue
            
            # Skip if file no longer exists at destination
            if not _still_there(op):
                skipped += 1
                continue
            
            try:
                # Move back; if the original location is taken, use name_RESTORED_001.ext
                restored_path = move_no_overwrite(dst_path, src_path, suffix_prefix="RESTORED")
            
            except CrossDeviceError as e:
                errors.append(f"CRITICAL: Cross-device move blocked for {dst_path}: {str(e)}")
                skipped += 1
                continue
            
            except FileOperationError as e:
                errors.append(f"Failed to restore {dst_path}: {str(e)}")
                skipped += 1
                continue
            
            except Exception as e:
                errors.append(f"Unexpected error restoring {dst_path}: {str(e)}")
                skipped += 1
                continue
            
            db.delete_delete_operation(op["id"])
            
            # Invalidate hash cache
            db.invalidate_hash(dst_path)
            db.invalidate_hash(restored_path)
            
            restored += 1
    
    return {
        "run_id": run_id,
        "restored": restored,
        "skipped": skipped,
        "errors": errors
    }

@router.post("/api/duplicates/scan_folder")
async def scan_folder_for_duplicates(folder_path: str) -> Dict[str, Any]:
    """
    Scan a specific folder for duplicates within that folder only.
    """
    global last_scan_results
    
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    folder = Path(folder_path)
    
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")
    
    # Validate folder is in vault
    if not validate_path_in_vault(folder, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="Folder must be within vault")
    
    try:
        from backend.scanner import find_duplicates_in_folder
        
        result = find_duplicates_in_folder(folder, config, db)
        duplicates = result["duplicates"]
        total_files = result["total_files"]
        
        last_scan_results = duplicates
        
        return {
            "folder": str(folder),
            "total_scanned": total_files,  # Actual count of all files scanned
            "duplicates_found": len(duplicates),
            "duplicates": duplicates
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scan failed: {str(e)}")

def _find_keeper(file_path: Path) -> Optional[str]:
    """The original the last folder scan paired with this duplicate."""
    for dup in last_scan_results:
        if dup.get("path") == str(file_path):
            return dup.get("duplicate_of")
    return None


@router.post("/api/duplicates/delete_single")
def delete_single_duplicate(file_path: str, duplicate_of: Optional[str] = None) -> Dict[str, Any]:
    """
    Move a single duplicate file to RAW_DUPLICATES.
    
    Only after checking, right before the move, that the kept original
    (duplicate_of, or the one from the last scan) still exists and is
    byte-for-byte identical, so the last copy of a photo can never be moved away.
    The checks and the move run under the app-wide ARCHIVE_LOCK, so a second
    request (another tab, or the Keurer) cannot move the original away in between.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    src_path = Path(file_path)
    
    # Validate file is in vault and is a plain file (no folders, no symlinks)
    if not validate_path_in_vault(src_path, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="File must be within vault")
    
    keeper_str = duplicate_of or _find_keeper(src_path)
    keeper = Path(keeper_str) if keeper_str else None
    if keeper is not None and not validate_path_in_vault(keeper, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="Original must be within vault")
    
    with ARCHIVE_LOCK:
        if not os.path.lexists(src_path):
            raise HTTPException(status_code=404, detail="File not found")
        
        if src_path.is_symlink() or not src_path.is_file():
            raise HTTPException(status_code=400, detail="Only regular files can be moved")
        
        protected = _in_protected_folder(src_path)
        if protected is not None:
            raise HTTPException(status_code=400, detail=f"Refusing files inside {protected.name}")
        
        if keeper is None:
            raise HTTPException(
                status_code=409,
                detail="Unknown original for this file; scan the folder again"
            )
        
        if keeper.is_symlink() or not keeper.is_file() or _in_protected_folder(keeper) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Kept original no longer exists: {keeper}. Nothing was moved; scan again"
            )
        
        if os.path.samefile(src_path, keeper):
            raise HTTPException(status_code=409, detail="File and original are the same file")
        
        if not files_identical(src_path, keeper):
            raise HTTPException(
                status_code=409,
                detail=f"File is no longer identical to {keeper.name}. Nothing was moved; scan again"
            )
        
        # Ensure duplicates folder exists
        config.ensure_duplicates_folder()
        
        try:
            # Compare real locations (resolves /var -> /private/var style aliases);
            # the file itself is not a symlink, so only its folders are resolved
            real_src = src_path.parent.resolve() / src_path.name
            
            # Determine which folder to use as base
            # If file is in RAW, use RAW as base, otherwise use vault root
            if real_src.is_relative_to(config.RAW_FOLDER.resolve()):
                base_folder = config.RAW_FOLDER.resolve()
            else:
                base_folder = config.VAULT_ROOT.resolve()
            
            # Move file preserving structure (never overwrites, see file_ops)
            dst_path, was_conflict = safe_move_with_structure(
                real_src,
                base_folder,
                config.DUPLICATES_FOLDER.resolve(),
                conflict_suffix="DUP"
            )
        
        except ProtectedPathError as e:
            raise HTTPException(status_code=400, detail=f"Refused: {str(e)}")
        
        except CrossDeviceError as e:
            raise HTTPException(status_code=409, detail=f"CRITICAL: Cross-device move blocked for {src_path}: {str(e)}")

        except FileOperationError as e:
            raise HTTPException(status_code=500, detail=f"Failed to move {src_path}: {str(e)}")

        # Record operation for undo
        run_id = db.create_delete_run()
        db.record_delete_operation(run_id, src_path, dst_path)
    
    # Invalidate hash cache for both paths
    db.invalidate_hash(src_path)
    db.invalidate_hash(dst_path)
    
    return {
        "run_id": run_id,
        "moved": True,
        "src_path": str(src_path),
        "dst_path": str(dst_path),
        "duplicate_of": str(keeper),
        "was_conflict": was_conflict
    }
