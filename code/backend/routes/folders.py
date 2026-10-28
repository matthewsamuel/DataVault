"""
DataVault Dashboard - Folder Routes
"""
from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List, Dict, Any

from backend.config import Config
from backend.paths import ArchiveFilter

router = APIRouter()

# Global state (set by main.py)
config: Config = None

def init_router(cfg: Config):
    """Initialize router with config"""
    global config
    config = cfg

@router.get("/api/folders")
def get_folders_endpoint() -> Dict[str, Any]:
    """Get folder tree from vault root"""
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    if not config.VAULT_ROOT.exists():
        raise HTTPException(
            status_code=503,
            detail="Vault not available. Is T7 mounted?"
        )
    
    from backend.scanner import get_folders
    
    # Without the app's own folders, TRASH, RAW_DUPLICATES, hidden folders and symlinks
    folders = get_folders(config.VAULT_ROOT, ArchiveFilter(config))
    
    return {
        "vault_root": str(config.VAULT_ROOT),
        "folders": folders
    }
