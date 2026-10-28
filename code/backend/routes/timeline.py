"""
DataVault Dashboard - Timeline Routes
The photo timeline: every photo and video of the chosen folders, ordered by capture
date (backend/media_dates.py: EXIF or the video's own date, else birthtime, else mtime).

The scan answers in a compact form (one short row per file, paths relative to the
vault) and is cached per folder selection until a folder below it changes (its mtime:
a file added, removed or renamed), MEDIA-THUMBS-TIMELINE-12/PERF-6. Only a complete
scan is cached: when a folder or file could not be read, or the vault went away during
the scan, the answer says complete: false and the next request scans again.
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from collections import OrderedDict, defaultdict
from datetime import datetime
import json
import os
import threading

from backend.config import Config, MEDIA_EXTENSIONS, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS
from backend import media_dates
from backend.paths import ArchiveFilter, scan_dir, vault_folder_or_none, walk_archive

router = APIRouter()

# Global state
config: Config = None
db = None

# Cached scan answers: {selection: (folder mtimes, JSON bytes)}, newest last
SCAN_CACHE_SIZE = 4
_scan_cache: "OrderedDict[Tuple[str, ...], Tuple[Tuple[Tuple[str, int], ...], bytes]]" = OrderedDict()
_scan_cache_lock = threading.Lock()

TYPE_CODES = {"photo": "p", "video": "v"}


def init_router(cfg: Config, database=None):
    """Initialize router with config and database"""
    global config, db
    config = cfg
    db = database
    with _scan_cache_lock:
        _scan_cache.clear()


def selected_roots(folders: List[str]) -> Tuple[List[Path], List[str]]:
    """
    The chosen folders as resolved vault folders, without any that lies inside another
    chosen one (a parent and its child would show every photo twice,
    MEDIA-THUMBS-TIMELINE-14), plus the entries that were skipped (outside the vault,
    not a folder): those are reported, never silently dropped.
    """
    roots: List[Path] = []
    skipped: List[str] = []
    for entry in folders:
        if not isinstance(entry, str) or not entry.strip():
            continue
        folder = vault_folder_or_none(entry, config)
        if folder is None or not folder.is_dir():
            skipped.append(entry)
            continue
        roots.append(folder)
    kept: List[Path] = []
    for folder in sorted(set(roots), key=lambda p: (len(p.parts), str(p))):
        if not any(folder == k or folder.is_relative_to(k) for k in kept):
            kept.append(folder)
    return sorted(kept, key=str), skipped


def _walk_media(roots: List[Path], errors: List[str]) -> Tuple[List[Tuple[str, os.stat_result]], List[Tuple[str, int]]]:
    """
    Every photo and video below roots with its stat, and the mtime of every folder
    walked. Every folder or file that could not be read is added to errors.
    """
    files: List[Tuple[str, os.stat_result]] = []
    folders: List[Tuple[str, int]] = []
    extensions = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
    archive_filter = ArchiveFilter(config)

    def onerror(path, error):
        errors.append(path)

    for top in roots:
        # walk_archive skips the app's own folders, TRASH, RAW_DUPLICATES, hidden files and symlinks
        for root, dirs, names in walk_archive(top, config, archive_filter, onerror=onerror):
            dirs.sort()
            try:
                folders.append((root, os.stat(root).st_mtime_ns))
            except OSError:
                errors.append(root)
                continue
            for name in names:
                if os.path.splitext(name)[1].lower() not in extensions:
                    continue
                path = os.path.join(root, name)
                try:
                    files.append((path, os.stat(path)))
                except OSError:
                    errors.append(path)
                    continue
    return files, folders


def _unchanged(folders: Tuple[Tuple[str, int], ...]) -> bool:
    for path, mtime_ns in folders:
        try:
            if os.stat(path).st_mtime_ns != mtime_ns:
                return False
        except OSError:
            return False
    return True


def _scan_payload(roots: List[Path]) -> Tuple[bytes, Tuple[Tuple[str, int], ...], bool]:
    """(JSON answer, folder mtimes, complete). Only a complete answer may be cached."""
    errors: List[str] = []
    files, folders = _walk_media(roots, errors)
    dates = media_dates.capture_dates(files, db, read_errors=errors, vault_root=config.VAULT_ROOT)
    complete = not errors and config.VAULT_ROOT.is_dir()
    if db is not None and complete:
        # Only after a full walk: a folder that could not be read is not 'gone'
        existing = {path for path, _ in files}
        for top in roots:
            try:
                db.prune_media_dates(top, existing)
            except Exception:
                pass

    vault = str(config.VAULT_ROOT).rstrip("/") + "/"
    rows = []
    for path, st in files:
        ts, source = dates[path]
        ext = os.path.splitext(path)[1].lower()
        rows.append((ts, path[len(vault):] if path.startswith(vault) else path, st.st_size,
                     "v" if ext in VIDEO_EXTENSIONS else "p", media_dates.SOURCES[source]))
    rows.sort(key=lambda r: (r[0], r[1]))

    date_index = defaultdict(lambda: defaultdict(int))
    year_boundaries: Dict[int, int] = {}
    sources: Dict[str, int] = defaultdict(int)
    for i, row in enumerate(rows):
        dt = datetime.fromtimestamp(row[0])
        date_index[dt.year][dt.month] += 1
        year_boundaries.setdefault(dt.year, i)
        sources[row[4]] += 1

    payload = {
        "total": len(rows),
        "root": str(config.VAULT_ROOT),
        "columns": ["timestamp", "path", "size", "type", "date_source"],
        "rows": [[round(r[0], 3), r[1], r[2], r[3], r[4]] for r in rows],
        "date_index": {year: dict(months) for year, months in sorted(date_index.items())},
        "year_boundaries": year_boundaries,
        "date_sources": dict(sources),
        "folders": [str(r) for r in roots],
        "skipped": [],
        "complete": complete,
        "errors": len(errors),
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return body, tuple(folders), complete


@router.get("/scan")
def timeline_scan(
    folders: List[str] = Query(..., description="Folder paths, one 'folders' parameter per folder")
):
    """
    Scan the chosen folders for photos and videos, ordered by capture date (oldest first).

    One 'folders' parameter per folder (a folder name may contain a comma,
    MEDIA-THUMBS-TIMELINE-13). Answer: {total, root, columns, rows, date_index,
    year_boundaries, date_sources, folders, skipped, complete, errors}; each row is
    [timestamp, path relative to root, size, 'p'|'v', date source code]. complete is
    false (errors counts the folders and files) when something could not be read.
    """
    roots, skipped = selected_roots(folders)
    if not roots:
        if skipped:
            raise HTTPException(status_code=404, detail="Geen van de gekozen mappen bestaat (nog) in het archief")
        raise HTTPException(status_code=400, detail="Geen mappen gekozen")

    key = tuple(str(r) for r in roots)
    with _scan_cache_lock:
        cached = _scan_cache.get(key)
    if cached is not None and _unchanged(cached[0]):
        body = cached[1]
    else:
        body, folder_mtimes, complete = _scan_payload(roots)
        with _scan_cache_lock:
            if complete:
                _scan_cache[key] = (folder_mtimes, body)
                _scan_cache.move_to_end(key)
                while len(_scan_cache) > SCAN_CACHE_SIZE:
                    _scan_cache.popitem(last=False)
            else:
                _scan_cache.pop(key, None)

    if skipped:
        # Rare; the cached body stays the same for every request
        data = json.loads(body)
        data["skipped"] = skipped
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Response(content=body, media_type="application/json")


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
