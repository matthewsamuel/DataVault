"""
DataVault Dashboard - Timeline Routes
The photo timeline: every photo and video of the chosen folders, ordered by capture
date (backend/media_dates.py: EXIF or the video's own date, else birthtime, else mtime).

The scan answers in a compact form (one short row per file, paths relative to the
vault), a page at a time when the client asks for one (offset, limit).

It comes from the media index (backend/media_index.py) when the index matches the disk
for the chosen folders (every folder below them still has the mtime it was indexed
with): a few SQL queries, no walk. Otherwise (no finished index pass yet, or something
changed since) it walks the folders as before and asks for an index pass. The walk's
answer is cached per folder selection until a folder below it changes (its mtime: a
file added, removed or renamed), MEDIA-THUMBS-TIMELINE-12/PERF-6. When a folder or
file could not be read, or the vault went away during the scan, the answer says
complete: false. Such an answer only serves the rest of its own paging (requests with
an offset, for INCOMPLETE_SECONDS): a new first page scans again.
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
import time

from backend.config import Config, MEDIA_EXTENSIONS, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS
from backend import media_dates, media_index
from backend.paths import ArchiveFilter, scan_dir, vault_folder_or_none, walk_archive

router = APIRouter()

# Global state
config: Config = None
db = None

# Cached walk answers: {selection: (folder mtimes, payload, complete, monotonic time)}, newest last
SCAN_CACHE_SIZE = 4
INCOMPLETE_SECONDS = 120.0   # an incomplete answer serves the later pages of its paging this long
_scan_cache: "OrderedDict[Tuple[str, ...], Tuple[Tuple[Tuple[str, int], ...], Dict[str, Any], bool, float]]" = OrderedDict()
MAX_PAGE = 50000
_scan_cache_lock = threading.Lock()

TYPE_CODES = {"photo": "p", "video": "v"}


def init_router(cfg: Config, database=None):
    """Initialize router with config and database"""
    global config, db
    config = cfg
    db = database
    with _scan_cache_lock:
        _scan_cache.clear()


def _resolved_folders(entries: List[str]) -> Tuple[List[Path], List[str]]:
    """The entries as resolved vault folders, plus those that are not (outside the vault, not a folder)."""
    folders: List[Path] = []
    skipped: List[str] = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            continue
        folder = vault_folder_or_none(entry, config)
        if folder is None or not folder.is_dir():
            skipped.append(entry)
            continue
        folders.append(folder)
    return folders, skipped


def selected_roots(folders: List[str]) -> Tuple[List[Path], List[str]]:
    """
    The chosen folders as resolved vault folders, without any that lies inside another
    chosen one (a parent and its child would show every photo twice,
    MEDIA-THUMBS-TIMELINE-14), plus the entries that were skipped (outside the vault,
    not a folder): those are reported, never silently dropped.
    """
    roots, skipped = _resolved_folders(folders)
    kept: List[Path] = []
    for folder in sorted(set(roots), key=lambda p: (len(p.parts), str(p))):
        if not any(folder == k or folder.is_relative_to(k) for k in kept):
            kept.append(folder)
    return sorted(kept, key=str), skipped


def selected_marks(folders: List[str], excluded: Optional[List[str]] = None) -> Tuple[List[Path], List[Path], List[str]]:
    """
    (roots, left out, skipped) of a selection with exceptions: a file is in it when the
    nearest marked folder above it (chosen or left out) is a chosen one. So a folder
    left out inside a chosen one drops out with everything below it, and a folder
    chosen inside a left-out one is in again. Marks that change nothing are dropped:
    a chosen folder inside a chosen one, a left-out folder that is not inside a chosen
    one. A folder both chosen and left out counts as chosen. skipped as in
    selected_roots (only the chosen entries: a left-out folder that is gone leaves
    nothing out).
    """
    chosen, skipped = _resolved_folders(folders)
    left_out, _ = _resolved_folders(excluded or [])
    marks: Dict[Path, bool] = {f: False for f in left_out}
    marks.update({f: True for f in chosen})

    def above(folder: Path) -> Optional[bool]:
        nearest = None
        for mark in marks:
            if mark != folder and folder.is_relative_to(mark) and (nearest is None or len(mark.parts) > len(nearest.parts)):
                nearest = mark
        return None if nearest is None else marks[nearest]

    roots = sorted((f for f, c in marks.items() if c and above(f) is not True), key=str)
    excludes = sorted((f for f, c in marks.items() if not c and above(f) is True), key=str)
    return roots, excludes, skipped


def _walk_media(roots: List[Path], errors: List[str],
                excludes: Optional[List[Path]] = None) -> Tuple[List[Tuple[str, os.stat_result]], List[Tuple[str, int]]]:
    """
    Every photo and video below roots with its stat, and the mtime of every folder
    walked, never entering a folder in excludes (a root inside one is walked on its
    own). Every folder or file that could not be read is added to errors.
    """
    skip = {str(e) for e in excludes or []}
    files: List[Tuple[str, os.stat_result]] = []
    folders: List[Tuple[str, int]] = []
    extensions = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
    archive_filter = ArchiveFilter(config)

    def onerror(path, error):
        errors.append(path)

    for top in roots:
        # walk_archive skips the app's own folders, TRASH, RAW_DUPLICATES, hidden files and symlinks
        for root, dirs, names in walk_archive(top, config, archive_filter, onerror=onerror):
            dirs[:] = sorted(d for d in dirs if os.path.join(root, d) not in skip)
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


def _summary(rows: List[Tuple[int, int, int]]) -> Tuple[Dict[int, Dict[int, int]], Dict[int, int]]:
    """date_index {year: {month: count}} and year_boundaries {year: index of its first row}."""
    date_index: Dict[int, Dict[int, int]] = defaultdict(dict)
    year_boundaries: Dict[int, int] = {}
    seen = 0
    for year, month, n in rows:
        date_index[year][month] = date_index[year].get(month, 0) + n
        year_boundaries.setdefault(year, seen)
        seen += n
    return dict(date_index), year_boundaries


def _index_payload(roots: List[Path], excludes: List[Path], offset: int, limit: Optional[int]) -> Dict[str, Any]:
    """The answer from the media index: one page of rows, the counts of the whole selection."""
    filters = {"kind": ["photo", "video"], "folders": [str(r) for r in roots],
               "exclude_folders": [str(e) for e in excludes]}
    vault = str(config.VAULT_ROOT).rstrip("/") + "/"
    # First: a change after this shows as a new version on the next page
    version = "i" + media_index.selection_version(db, filters)
    histogram = media_index.date_histogram(db, filters)
    date_index, year_boundaries = _summary(histogram)
    total = sum(n for _, _, n in histogram)
    rows = []
    sources: Dict[str, int] = defaultdict(int)
    for taken, path, size, kind, source in media_index.timeline_page(db, roots, offset, limit, excludes):
        rows.append([round(taken, 3), path[len(vault):] if path.startswith(vault) else path, size,
                     TYPE_CODES.get(kind, "p"), media_dates.SOURCES.get(source, "m")])
    with db._connect() as conn:
        where, args = media_index._where(filters)
        for source, n in conn.execute(f"SELECT taken_source, COUNT(*) FROM media m WHERE {where} "
                                      "GROUP BY taken_source", args):
            sources[media_dates.SOURCES.get(source, "m")] += n
    return {
        "total": total,
        "root": str(config.VAULT_ROOT),
        "columns": ["timestamp", "path", "size", "type", "date_source"],
        "rows": rows,
        "date_index": date_index,
        "year_boundaries": year_boundaries,
        "date_sources": dict(sources),
        "folders": [str(r) for r in roots],
        "excluded": [str(e) for e in excludes],
        "skipped": [],
        "complete": True,
        "errors": 0,
        "source": "index",
        "version": version,
    }


def _scan_payload(roots: List[Path], excludes: List[Path]) -> Tuple[Dict[str, Any], Tuple[Tuple[str, int], ...], bool]:
    """(answer, folder mtimes, complete) of a walk. Only a complete answer may be cached."""
    errors: List[str] = []
    files, folders = _walk_media(roots, errors, excludes)
    dates = media_dates.capture_dates(files, db, read_errors=errors, vault_root=config.VAULT_ROOT)
    complete = not errors and config.VAULT_ROOT.is_dir()
    if db is not None and complete:
        # Only after a full walk: a folder that could not be read is not 'gone'
        existing = {path for path, _ in files}
        for top in roots:
            # Not below a folder that was left out: its files were not walked, not gone
            if any(e.is_relative_to(top) for e in excludes):
                continue
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
        "excluded": [str(e) for e in excludes],
        "skipped": [],
        "complete": complete,
        "errors": len(errors),
        "source": "walk",
        "version": f"w{hash(tuple(folders)) & 0xFFFFFFFF:x}",
    }
    return payload, tuple(folders), complete


def _walk_answer(roots: List[Path], excludes: List[Path], continuation: bool = False) -> Dict[str, Any]:
    """
    The walk's answer for this selection, from the cache while no folder changed. An
    incomplete answer is reused only for a continuation (a later page of the same
    paging, shortly after): paging must not walk the whole selection for every page,
    but a new first page tries again what could not be read.
    """
    key = tuple(str(r) for r in roots) + tuple("-" + str(e) for e in excludes)
    with _scan_cache_lock:
        cached = _scan_cache.get(key)
    if cached is not None and _unchanged(cached[0]):
        folder_mtimes, payload, complete, stored = cached
        if complete or (continuation and time.monotonic() - stored < INCOMPLETE_SECONDS):
            return payload
    payload, folder_mtimes, complete = _scan_payload(roots, excludes)
    with _scan_cache_lock:
        _scan_cache[key] = (folder_mtimes, payload, complete, time.monotonic())
        _scan_cache.move_to_end(key)
        while len(_scan_cache) > SCAN_CACHE_SIZE:
            _scan_cache.popitem(last=False)
    return payload


@router.get("/scan")
def timeline_scan(
    folders: List[str] = Query(..., description="Folder paths, one 'folders' parameter per folder"),
    exclude: List[str] = Query([], description="Folders left out of the chosen ones, one 'exclude' parameter per folder"),
    offset: int = Query(0, ge=0, le=2 ** 62),   # larger does not fit in an SQLite integer
    limit: Optional[int] = Query(None, ge=1, le=MAX_PAGE),
):
    """
    The photos and videos of the chosen folders, ordered by capture date (oldest first).

    One 'folders' parameter per folder (a folder name may contain a comma,
    MEDIA-THUMBS-TIMELINE-13); one 'exclude' per folder left out of a chosen one, with
    everything below it (selected_marks: the nearest marked folder above a file decides). offset and limit ask for one page of rows (no limit: all
    of them). Answer: {total, root, columns, rows, offset, next_offset, date_index,
    year_boundaries, date_sources, folders, excluded, skipped, complete, errors, source,
    version};
    each row is [timestamp, path relative to root, size, 'p'|'v', date source code].
    total, date_index and year_boundaries always describe the whole selection;
    next_offset is null on the last page. source is 'index' or 'walk'; when version
    differs between pages, the selection changed in between and the client starts over
    (or, when only the source changed and total and date_index are the same, goes on:
    both give the same rows).
    complete is false (errors counts the folders and files) when something could not
    be read.
    """
    roots, excludes, skipped = selected_marks(folders, exclude)
    if not roots:
        if skipped:
            raise HTTPException(status_code=404, detail="Geen van de gekozen mappen bestaat (nog) in het archief")
        raise HTTPException(status_code=400, detail="Geen mappen gekozen")

    if db is not None and media_index.fresh_for(db, roots):
        data = _index_payload(roots, excludes, offset, limit)
    else:
        # The index is not there yet or behind the disk: walk now, and bring it up to date
        media_index.request_update()
        payload = _walk_answer(roots, excludes, continuation=offset > 0)
        data = dict(payload)
        end = None if limit is None else offset + limit
        if offset or end is not None:
            data["rows"] = payload["rows"][offset:end]
    data["offset"] = offset
    data["next_offset"] = offset + len(data["rows"]) if offset + len(data["rows"]) < data["total"] else None
    if skipped:
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
