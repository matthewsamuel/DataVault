"""
DataVault Dashboard - Folder Routes
"""
from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List, Dict, Any

from backend.config import Config

router = APIRouter()

# Global state (set by main.py)
config: Config = None
excluded_paths = set()

def init_router(cfg: Config):
    """Initialize router with config"""
    global config, excluded_paths
    config = cfg
    excluded_paths = {
        config.DUPLICATES_FOLDER.resolve(),
        config.CACHE_FOLDER.resolve()
    }

@router.get("/api/folders")
async def get_folders_endpoint() -> Dict[str, Any]:
    """Get folder tree from vault root"""
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    if not config.VAULT_ROOT.exists():
        raise HTTPException(
            status_code=503,
            detail="Vault not available. Is T7 mounted?"
        )
    
    from backend.scanner import get_folders
    
    folders = get_folders(config.VAULT_ROOT, config.VAULT_ROOT, excluded_paths)
    
    return {
        "vault_root": str(config.VAULT_ROOT),
        "folders": folders
    }

@router.get("/api/main-folders")
async def get_main_folders_endpoint() -> Dict[str, Any]:
    """Get main folders for homepage dashboard"""
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    if not config.VAULT_ROOT.exists():
        raise HTTPException(
            status_code=503,
            detail="Vault not available. Is T7 mounted?"
        )
    
    main_folders = []
    
    try:
        # Get all top-level folders in vault
        for entry in sorted(config.VAULT_ROOT.iterdir()):
            if not entry.is_dir():
                continue
            
            # Skip excluded folders
            if entry.name.startswith('.') or entry.resolve() in excluded_paths:
                continue
            
            # Count files (quick estimate)
            try:
                children = list(entry.iterdir())
                file_count = len([x for x in children if not x.name.startswith('.')])
            except:
                file_count = 0
            
            stat = entry.stat()
            
            main_folders.append({
                "path": str(entry),
                "name": entry.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "file_count": file_count
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    return {
        "vault_root": str(config.VAULT_ROOT),
        "folders": main_folders
    }
