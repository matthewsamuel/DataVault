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
