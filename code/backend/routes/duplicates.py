"""
DataVault Dashboard - Duplicates Routes
"""
from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List, Dict, Any

from backend.config import Config
from backend.file_ops import safe_move_with_structure, move_no_overwrite, FileOperationError, CrossDeviceError

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


@router.post("/api/raw_duplicates/scan")
async def scan_duplicates_endpoint() -> Dict[str, Any]:
    """Scan RAW folder for duplicates"""
    global last_scan_results
    
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    if not config.RAW_FOLDER.exists():
        raise HTTPException(status_code=404, detail="RAW folder not found")
    
    try:
        from backend.scanner import find_duplicates
        
        duplicates = find_duplicates(config, db)
        last_scan_results = duplicates
        
        return {
            "total_scanned": len(duplicates),
            "duplicates_found": len(duplicates),
            "duplicates": duplicates
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scan failed: {str(e)}")

@router.get("/api/raw_duplicates")
async def get_duplicates_endpoint() -> Dict[str, Any]:
    """Get last scan results"""
    return {
        "duplicates_found": len(last_scan_results),
        "duplicates": last_scan_results
    }

@router.post("/api/raw_duplicates/delete")
async def delete_duplicates_endpoint() -> Dict[str, Any]:
    """
    Delete (move) duplicates from RAW to RAW_DUPLICATES.
    Records operation in database for undo.
    """
    global last_scan_results
    
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    if not last_scan_results:
        raise HTTPException(status_code=400, detail="No duplicates to delete. Run scan first.")
    
    # Ensure duplicates folder exists
    config.ensure_duplicates_folder()
    
    # Create delete run
    run_id = db.create_delete_run()
    
    moved = 0
    skipped = 0
    errors = []
    
    for dup in last_scan_results:
        src_path = Path(dup["raw_path"])
        
        # Skip if file no longer exists
        if not src_path.exists():
            skipped += 1
            continue
        
        try:
            # Move file preserving structure
            dst_path, was_conflict = safe_move_with_structure(
                src_path,
                config.RAW_FOLDER,
                config.DUPLICATES_FOLDER,
                conflict_suffix="DUP"
            )
            
            # Record operation for undo
            db.record_delete_operation(run_id, src_path, dst_path)
            
            # Invalidate hash cache for both paths
            db.invalidate_hash(src_path)
            db.invalidate_hash(dst_path)
            
            moved += 1
        
        except CrossDeviceError as e:
            errors.append(f"CRITICAL: Cross-device move blocked for {src_path}: {str(e)}")
            skipped += 1

        except FileOperationError as e:
            errors.append(f"Failed to move {src_path}: {str(e)}")
            skipped += 1

        except Exception as e:
            errors.append(f"Unexpected error moving {src_path}: {str(e)}")
            skipped += 1
    
    # Clear scan results after delete
    last_scan_results = []
    
    return {
        "run_id": run_id,
        "moved": moved,
        "skipped": skipped,
        "errors": errors
    }

@router.post("/api/raw_duplicates/undo_last")
async def undo_last_delete_endpoint() -> Dict[str, Any]:
    """
    Undo the most recent delete operation.
    Moves files back from RAW_DUPLICATES to RAW.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    # Get last delete run
    last_run = db.get_last_delete_run()
    
    if not last_run:
        raise HTTPException(status_code=404, detail="No delete operations to undo")
    
    run_id = last_run["run_id"]
    operations = db.get_delete_operations(run_id)
    
    if not operations:
        raise HTTPException(status_code=404, detail="No operations found for last run")
    
    restored = 0
    skipped = 0
    errors = []
    
    for op in operations:
        # Note: src_path is the ORIGINAL location (in RAW)
        # dst_path is where we moved it (in RAW_DUPLICATES)
        # For undo, we move FROM dst_path BACK TO src_path
        
        dst_path = Path(op["dst_path"])  # Current location
        src_path = Path(op["src_path"])  # Original location
        
        # Skip if file no longer exists at destination
        if not dst_path.exists():
            skipped += 1
            continue
        
        try:
            # Move back; if the original location is taken, use name_RESTORED_001.ext
            restored_path = move_no_overwrite(dst_path, src_path, suffix_prefix="RESTORED")
            
            # Invalidate hash cache
            db.invalidate_hash(dst_path)
            db.invalidate_hash(restored_path)
            
            restored += 1
        
        except CrossDeviceError as e:
            errors.append(f"CRITICAL: Cross-device move blocked for {dst_path}: {str(e)}")
            skipped += 1

        except FileOperationError as e:
            errors.append(f"Failed to restore {dst_path}: {str(e)}")
            skipped += 1

        except Exception as e:
            errors.append(f"Unexpected error restoring {dst_path}: {str(e)}")
            skipped += 1
    
    # Delete run record after successful undo
    if restored > 0:
        db.delete_run(run_id)
    
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
    from backend.config import validate_path_in_vault
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

@router.post("/api/duplicates/delete_single")
async def delete_single_duplicate(file_path: str) -> Dict[str, Any]:
    """
    Delete a single duplicate file (move to RAW_DUPLICATES).
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    src_path = Path(file_path)
    
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Validate file is in vault
    from backend.config import validate_path_in_vault
    if not validate_path_in_vault(src_path, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="File must be within vault")
    
    # Ensure duplicates folder exists
    config.ensure_duplicates_folder()
    
    # Create delete run
    run_id = db.create_delete_run()
    
    try:
        # Determine which folder to use as base
        # If file is in RAW, use RAW as base, otherwise use vault root
        if src_path.is_relative_to(config.RAW_FOLDER):
            base_folder = config.RAW_FOLDER
        else:
            base_folder = config.VAULT_ROOT
        
        # Move file preserving structure
        dst_path, was_conflict = safe_move_with_structure(
            src_path,
            base_folder,
            config.DUPLICATES_FOLDER,
            conflict_suffix="DUP"
        )
        
        # Record operation for undo
        db.record_delete_operation(run_id, src_path, dst_path)
        
        # Invalidate hash cache for both paths
        db.invalidate_hash(src_path)
        db.invalidate_hash(dst_path)
        
        return {
            "run_id": run_id,
            "moved": True,
            "src_path": str(src_path),
            "dst_path": str(dst_path),
            "was_conflict": was_conflict
        }
    
    except CrossDeviceError as e:
        raise HTTPException(status_code=500, detail=f"CRITICAL: Cross-device move blocked for {src_path}: {str(e)}")

    except FileOperationError as e:
        raise HTTPException(status_code=500, detail=f"Failed to move {src_path}: {str(e)}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error moving {src_path}: {str(e)}")

