"""
DataVault Dashboard - Timeline Routes
Efficient photo timeline with date-based indexing and pagination.
"""
from fastapi import APIRouter, HTTPException, Query
from pathlib import Path
from typing import Dict, Any, List, Optional
import os
import stat as stat_module
import subprocess
from datetime import datetime, timezone
from collections import defaultdict

from backend.config import (
    Config, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS, MEDIA_EXTENSIONS,
    is_media_file
)
from backend.paths import ArchiveFilter, scan_dir, vault_folder_or_none, walk_archive

router = APIRouter()

# Global state
config: Config = None

def init_router(cfg: Config):
    """Initialize router with config"""
    global config
    config = cfg


def _get_creation_date(file_path: Path) -> float:
    """
    Get the best available creation/taken date for a media file.
    On macOS we use st_birthtime (file creation date) which is typically
    preserved from the original photo/video creation date.
    Falls back to st_mtime if unavailable.
    """
    try:
        s = file_path.stat()
        # macOS: st_birthtime is the real creation date
        birthtime = getattr(s, 'st_birthtime', None)
        if birthtime and birthtime > 0:
            return birthtime
        return s.st_mtime
    except (OSError, PermissionError):
        return 0.0


def _scan_folder_recursive(folder_path: Path, photo_only: bool = False) -> List[Dict[str, Any]]:
    """
    Recursively scan a folder for media files and return them with timestamps.
    Returns a flat list sorted by creation date.
    """
    results = []
    extensions = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS if not photo_only else PHOTO_EXTENSIONS

    # walk_archive skips the app's own folders, TRASH, RAW_DUPLICATES, hidden files and symlinks
    for root, dirs, files in walk_archive(folder_path, config):
        dirs.sort()

        for filename in sorted(files):
            file_path = Path(root) / filename
            ext = file_path.suffix.lower()

            if ext not in extensions:
                continue

            try:
                s = file_path.stat()
                birthtime = getattr(s, 'st_birthtime', None)
                if birthtime and birthtime > 0:
                    ts = birthtime
                else:
                    ts = s.st_mtime

                file_type = 'video' if ext in VIDEO_EXTENSIONS else 'photo'

                results.append({
                    "path": str(file_path),
                    "name": filename,
                    "timestamp": ts,
                    "size": s.st_size,
                    "type": file_type
                })
            except (OSError, PermissionError):
                continue

    return results


@router.get("/scan")
def timeline_scan(
    folders: str = Query(..., description="Comma-separated folder paths")
) -> Dict[str, Any]:
    """
    Scan selected folders recursively for media files with timestamps.
    Returns metadata sorted chronologically (oldest first).
    Only returns lightweight metadata — thumbnails are loaded lazily on frontend.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")

    folder_paths = [p.strip() for p in folders.split(",") if p.strip()]

    if not folder_paths:
        raise HTTPException(status_code=400, detail="No folders specified")

    all_files = []

    for folder_str in folder_paths:
        # Skip paths outside the vault (or app-internal) silently, the same way as missing ones
        folder_path = vault_folder_or_none(folder_str, config)
        if folder_path is None or not folder_path.is_dir():
            continue

        files = _scan_folder_recursive(folder_path)
        all_files.extend(files)

    # Sort by timestamp (oldest first)
    all_files.sort(key=lambda f: f["timestamp"])

    # Build date index: year -> month -> count
    date_index = defaultdict(lambda: defaultdict(int))
    for f in all_files:
        dt = datetime.fromtimestamp(f["timestamp"])
        date_index[dt.year][dt.month] += 1

    # Convert to serializable format
    date_index_serializable = {
        year: dict(months)
        for year, months in sorted(date_index.items())
    }

    # Build year boundaries (index of first photo per year) for quick jumping
    year_boundaries = {}
    for i, f in enumerate(all_files):
        year = datetime.fromtimestamp(f["timestamp"]).year
        if year not in year_boundaries:
            year_boundaries[year] = i

    return {
        "total": len(all_files),
        "files": all_files,
        "date_index": date_index_serializable,
        "year_boundaries": year_boundaries
    }


@router.get("/folders")
def timeline_folders() -> Dict[str, Any]:
    """
    Get top-level folders in vault for the timeline folder picker.
    Returns folder tree with media file counts.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")

    if not config.VAULT_ROOT.exists():
        raise HTTPException(status_code=503, detail="Vault not available")

    archive_filter = ArchiveFilter(config)

    def build_tree(root: Path, depth: int = 0, max_depth: int = 3) -> List[Dict]:
        if depth > max_depth:
            return []

        items = []
        try:
            for dir_entry in sorted(scan_dir(root, archive_filter), key=lambda e: e.name.lower()):
                if not dir_entry.is_dir(follow_symlinks=False):
                    continue
                entry = Path(dir_entry.path)

                # Quick media count (non-recursive, just this folder)
                media_count = 0
                try:
                    for f in scan_dir(entry, archive_filter):
                        if f.is_file(follow_symlinks=False) and Path(f.name).suffix.lower() in MEDIA_EXTENSIONS:
                            media_count += 1
                except (PermissionError, OSError):
                    pass

                children = build_tree(entry, depth + 1, max_depth)

                items.append({
                    "name": entry.name,
                    "path": str(entry),
                    "media_count": media_count,
                    "children": children
                })
        except (PermissionError, OSError):
            pass

        return items

    folders = build_tree(config.VAULT_ROOT)

    return {
        "vault_root": str(config.VAULT_ROOT),
        "folders": folders
    }
