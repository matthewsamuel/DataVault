"""
DataVault Dashboard - File Scanner
Scan folders and compute hashes with caching
"""
import xxhash
from pathlib import Path
from typing import List, Dict, Set, Any, Optional, Tuple
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import stat as stat_module

from backend.config import Config, MEDIA_EXTENSIONS, is_media_file
from backend.paths import ArchiveFilter, scan_dir, walk_archive
from backend.database import Database
from backend.progress import (
    ScanProgress, set_scan_progress, update_scan_progress,
    is_scan_cancelled, reset_scan_cancellation
)
from datetime import datetime

def get_folders(root_path: Path, archive_filter: ArchiveFilter) -> List[Dict[str, Any]]:
    """
    Recursively get the folder tree below root_path.
    Skips what archive_filter excludes: the app's own folders, TRASH,
    RAW_DUPLICATES, hidden folders and symlinks (see backend/paths.py).
    
    Returns:
        List of folder dicts with name, path, children
    """
    folders = []
    
    try:
        entries = sorted(scan_dir(root_path, archive_filter), key=lambda e: e.name.lower())
    except OSError:
        return folders
    
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        path = Path(entry.path)
        folders.append({
            "name": entry.name,
            "path": str(path),
            "children": get_folders(path, archive_filter)
        })
    
    return folders


def get_file_type(file_path: Path) -> str:
    """
    Determine if file is photo, video, or audio based on extension.
    
    Args:
        file_path: Path to file
    
    Returns:
        'photo', 'video', or 'audio'
    """
    ext = file_path.suffix.lower()
    from backend.config import VIDEO_EXTENSIONS, AUDIO_EXTENSIONS
    
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    elif ext in AUDIO_EXTENSIONS:
        return 'audio'
    else:
        return 'photo'


def list_media_files(folder_path: Path, archive_filter: ArchiveFilter) -> List[Dict[str, Any]]:
    """
    List the files and subfolders of a folder (non-recursive), skipping
    what archive_filter excludes (see backend/paths.py).
    
    Args:
        folder_path: Folder to list (already validated by the route)
        archive_filter: The archive's exclusion rule
    
    Returns:
        List of file info dicts
    """
    items = []
    
    try:
        entries = sorted(scan_dir(folder_path, archive_filter), key=lambda e: e.name.lower())
    except OSError:
        return items
    
    for entry in entries:
        try:
            stat = entry.stat(follow_symlinks=False)
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        
        if is_dir:
            # Folder
            items.append({
                "path": entry.path,
                "name": entry.name,
                "type": "folder",
                "modified": stat.st_mtime
            })
        else:
            # File
            items.append({
                "path": entry.path,
                "name": entry.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "type": get_file_type(Path(entry.name))
            })
    
    return items


# Alias for API compatibility
def get_media_files(folder_path: Path, archive_filter: ArchiveFilter) -> List[Dict[str, Any]]:
    """Alias for list_media_files for backwards compatibility."""
    return list_media_files(folder_path, archive_filter)



# --- Duplicate detection -----------------------------------------------------
#
# A folder scan groups byte-identical files (same size, then same xxHash3-128) and
# picks, per group, the copy that stays by an explicit, fully deterministic rule.
# Nothing here moves a file: the duplicates route compares every copy byte for byte
# with the copy that stays right before it moves anything.

HASH_ALGO = "xxh3_128"

# Rules for which copy of a group stays. Every rule first prefers a copy that is
# not hidden, and ends with the path in alphabetical order (name_rank), so the
# choice never depends on the order in which the disk or the threads answer.
# Copies are byte-identical, so their EXIF capture date is too: within a group the
# file date (birthtime, else mtime) is what tells an older copy from a newer one.
KEEP_RULES = ("oldest", "newest", "shortest", "folder")
DEFAULT_KEEP_RULE = "oldest"

MAX_ERROR_SAMPLES = 20


class ScanAborted(Exception):
    """The scanned folder disappeared during the scan (e.g. the disk was unplugged)."""


def hash_file_xxh3(file_path: Path) -> str:
    """
    xxHash3-128 of a file's content, read in 1 MB chunks.
    
    xxHash3 is 5-10x faster than SHA-256 for duplicate detection; 128 bits make an
    accidental collision negligible, and every move is byte-compared first anyway.
    """
    hasher = xxhash.xxh3_128()
    with open(file_path, 'rb') as f:
        while chunk := f.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _fingerprint(st: os.stat_result):
    """What must be unchanged for a cached hash to be trusted (see database.py)."""
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


try:  # HEIC/HEIF EXIF, same as thumbnail.py
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass


def exif_capture_time(file_path: Path):
    """EXIF DateTimeOriginal as a local-time epoch, or None (no EXIF, not a photo, unreadable)."""
    from backend.config import is_photo
    if not is_photo(file_path):
        return None
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            value = img.getexif().get_ifd(0x8769).get(0x9003)
        if isinstance(value, bytes):
            value = value.decode("ascii", "ignore")
        if not value:
            return None
        return datetime.strptime(str(value).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        return None


def _is_hidden(path: Path, top: Path, st: os.stat_result) -> bool:
    """A dot name anywhere below the scanned folder, or the Finder 'hidden' flag."""
    try:
        parts = path.relative_to(top).parts
    except ValueError:
        parts = (path.name,)
    if any(part.startswith(".") for part in parts):
        return True
    return bool(getattr(st, "st_flags", 0) & getattr(stat_module, "UF_HIDDEN", 0))


def _inside(path: str, folder: Optional[str]) -> bool:
    if not folder:
        return False
    folder = folder.rstrip("/")
    return path == folder or path.startswith(folder + "/")


def keeper_sort_key(copy: Dict[str, Any], rule: str = DEFAULT_KEEP_RULE, prefer_folder: Optional[str] = None):
    """
    Sort key for the copies of one group; the smallest key stays. frontend/duplicates.js
    (keepSortKey) implements the same keys, so a rule switch in the UI is instant.
    - oldest:   capture date, then file date, oldest first; then shortest path
    - newest:   capture date, then file date, newest first; then shortest path
    - shortest: shortest path; then oldest
    - folder:   a copy inside prefer_folder; then oldest (also when no copy is inside it)
    """
    hidden = 1 if copy["hidden"] else 0
    oldest = (copy["capture_ts"], copy["file_ts"])
    if rule == "newest":
        return (hidden, -copy["capture_ts"], -copy["file_ts"], copy["path_len"], copy["name_rank"])
    if rule == "shortest":
        return (hidden, copy["path_len"], *oldest, copy["name_rank"])
    if rule == "folder":
        outside = 0 if _inside(copy["path"], prefer_folder) else 1
        return (hidden, outside, *oldest, copy["path_len"], copy["name_rank"])
    return (hidden, *oldest, copy["path_len"], copy["name_rank"])


def choose_keeper(copies: List[Dict[str, Any]], rule: str = DEFAULT_KEEP_RULE, prefer_folder: Optional[str] = None) -> int:
    """Index in copies of the copy that stays under rule."""
    if rule not in KEEP_RULES:
        raise ValueError(f"unknown rule: {rule}")
    return min(range(len(copies)), key=lambda i: keeper_sort_key(copies[i], rule, prefer_folder))


def _copy_info(path: Path, st: os.stat_result, top: Path, exif_ts) -> Dict[str, Any]:
    birthtime = getattr(st, "st_birthtime", None)
    if birthtime and birthtime > 0:
        file_ts, source = float(birthtime), "birthtime"
    else:
        file_ts, source = float(st.st_mtime), "mtime"
    return {
        "path": str(path),
        "name": path.name,
        "folder": str(path.parent),
        "rel": str(path.relative_to(top)) if path != top else path.name,
        "size": st.st_size,
        "capture_ts": exif_ts if exif_ts is not None else file_ts,
        "capture_source": "exif" if exif_ts is not None else source,
        "file_ts": file_ts,
        "file_ts_source": source,
        "mtime": float(st.st_mtime),
        "hidden": _is_hidden(path, top, st),
        "path_len": len(str(path)),
        "name_rank": 0,
    }


def find_duplicates_in_folder(
    folder_path: Path,
    config: Config,
    db: Database,
    rule: str = DEFAULT_KEEP_RULE,
    prefer_folder: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Find groups of byte-identical media files below folder_path.
    
    1. Walk the folder in sorted order with the archive's one exclusion rule
       (never the cache, TRASH, RAW_DUPLICATES, hidden or '._' files, symlinks)
       and group the non-empty media files by size.
    2. Hash only files that share their size with another file. A cached hash is
       used only while inode, size, mtime_ns and ctime_ns are unchanged; the rest
       is hashed on up to 4 threads. New hashes are stored in one transaction.
    3. Group by hash and pick the copy that stays with choose_keeper(rule).
    
    Every folder or file that cannot be read is counted and reported (errors,
    error_samples, complete=False): a scan never says 'complete' while it missed
    files. If the folder itself disappears, ScanAborted is raised.
    
    Always returns the same shape:
        {total_files, empty_skipped, groups, duplicates_found, cancelled,
         complete, errors, error_samples, rule}
    where each group is {id, hash, size, type, copies: [...], keep: index}
    and copies are sorted by path (name_rank is that order).
    """
    if rule not in KEEP_RULES:
        raise ValueError(f"unknown rule: {rule}")
    
    reset_scan_cancellation()
    
    progress = ScanProgress(
        status='counting',
        phase='Bestanden tellen...',
        started_at=datetime.now(),
        total_files=0,
        processed_files=0
    )
    set_scan_progress(progress)
    
    errors = 0
    error_samples: List[Dict[str, str]] = []
    
    def record_error(path, exc):
        nonlocal errors
        errors += 1
        if len(error_samples) < MAX_ERROR_SAMPLES:
            error_samples.append({"path": str(path), "error": exc.strerror or str(exc) if isinstance(exc, OSError) else str(exc)})
    
    def result(groups=(), cancelled=False):
        groups = list(groups)
        return {
            "total_files": file_count,
            "empty_skipped": empty_skipped,
            "groups": groups,
            "duplicates_found": sum(len(g["copies"]) - 1 for g in groups),
            "cancelled": cancelled,
            "complete": not cancelled and errors == 0,
            "errors": errors,
            "error_samples": error_samples,
            "rule": rule,
        }
    
    def cancelled_result():
        update_scan_progress(status='cancelled', phase='Scan geannuleerd', completed_at=datetime.now())
        return result(cancelled=True)
    
    # PHASE 1: group by size (files with different sizes cannot be duplicates)
    size_groups: Dict[int, List[Path]] = defaultdict(list)
    stats: Dict[str, os.stat_result] = {}
    seen: Set[str] = set()
    file_count = 0
    empty_skipped = 0
    
    for root, dirnames, filenames in walk_archive(folder_path, config, onerror=record_error):
        if is_scan_cancelled():
            return cancelled_result()
        dirnames.sort()
        for filename in sorted(filenames):
            file_path = Path(root) / filename
            if not is_media_file(file_path):
                continue
            try:
                st = os.lstat(file_path)
            except OSError as e:
                record_error(file_path, e)
                continue
            if not stat_module.S_ISREG(st.st_mode):
                continue
            seen.add(str(file_path))
            if st.st_size == 0:
                empty_skipped += 1  # empty files are all 'identical' and never a real copy
                continue
            file_count += 1
            size_groups[st.st_size].append(file_path)
            stats[str(file_path)] = st
            if file_count % 50 == 0:
                update_scan_progress(phase=f'Groeperen op grootte... {file_count} bestanden', total_files=file_count)
    
    if errors and not folder_path.is_dir():
        update_scan_progress(status='error', phase='Map niet meer bereikbaar',
                             error_message=f'{folder_path} is niet meer bereikbaar', completed_at=datetime.now())
        raise ScanAborted(str(folder_path))
    
    # PHASE 2: hash only files that share their size with another file
    candidates = sorted(
        (path for paths in size_groups.values() if len(paths) > 1 for path in paths),
        key=str,
    )
    update_scan_progress(
        status='scanning',
        phase=f'Voorfilter klaar: {len(candidates)} bestanden te vergelijken ({file_count - len(candidates)} uniek van grootte)',
        total_files=len(candidates),
        processed_files=0
    )
    
    cache = db.get_cached_hashes(folder_path, HASH_ALGO) if candidates else {}
    hashes: Dict[str, str] = {}
    to_hash: List[Path] = []
    for path in candidates:
        cached = cache.get(str(path))
        if cached and cached[0] == _fingerprint(stats[str(path)]):
            hashes[str(path)] = cached[1]
        else:
            to_hash.append(path)
    
    processed = len(hashes)
    update_scan_progress(processed_files=processed)
    new_entries = []
    
    if to_hash:
        executor = ThreadPoolExecutor(max_workers=min(4, len(to_hash)))
        try:
            futures = {executor.submit(hash_file_xxh3, path): path for path in to_hash}
            for future in as_completed(futures):
                if is_scan_cancelled():
                    executor.shutdown(wait=False, cancel_futures=True)
                    db.store_hashes(new_entries, HASH_ALGO)
                    return cancelled_result()
                path = futures[future]
                processed += 1
                try:
                    digest = future.result()
                    after = os.lstat(path)
                except Exception as e:
                    record_error(path, e)
                else:
                    if _fingerprint(after) == _fingerprint(stats[str(path)]):
                        hashes[str(path)] = digest
                        new_entries.append((str(path), _fingerprint(after), digest))
                    else:
                        record_error(path, OSError(0, "bestand veranderde tijdens de scan"))
                if processed % 10 == 0 or processed == len(candidates):
                    update_scan_progress(
                        processed_files=processed,
                        phase=f'Vergelijken... {processed}/{len(candidates)}'
                    )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
    
    try:
        db.store_hashes(new_entries, HASH_ALGO)
        if errors == 0:
            db.prune_hashes(folder_path, seen)
    except Exception as e:  # the cache is an optimisation; a failed write loses nothing
        print(f"Hash cache write failed: {e}")
    
    if errors and not folder_path.is_dir():
        update_scan_progress(status='error', phase='Map niet meer bereikbaar',
                             error_message=f'{folder_path} is niet meer bereikbaar', completed_at=datetime.now())
        raise ScanAborted(str(folder_path))
    
    # PHASE 3: group by hash and choose the copy that stays
    by_hash: Dict[Tuple[int, str], List[Path]] = defaultdict(list)
    for path_str, digest in hashes.items():
        by_hash[(stats[path_str].st_size, digest)].append(Path(path_str))
    
    groups = []
    for (size, digest), paths in by_hash.items():
        if len(paths) < 2:
            continue
        paths.sort(key=str)
        exif_ts = exif_capture_time(paths[0])  # identical bytes: one read per group
        copies = [_copy_info(p, stats[str(p)], folder_path, exif_ts) for p in paths]
        for rank, copy in enumerate(copies):
            copy["name_rank"] = rank
        groups.append({
            "hash": digest,
            "size": size,
            "type": get_file_type(paths[0]),
            "copies": copies,
            "keep": choose_keeper(copies, rule, prefer_folder),
        })
    groups.sort(key=lambda g: g["copies"][0]["path"])
    for i, group in enumerate(groups):
        group["id"] = i
    
    found = sum(len(g["copies"]) - 1 for g in groups)
    if errors:
        update_scan_progress(
            status='incomplete',
            phase=f'Scan klaar met {errors} leesfout(en): {found} duplicaten gevonden',
            duplicates_found=found,
            error_message=f'{errors} bestand(en) of map(pen) konden niet gelezen worden',
            completed_at=datetime.now()
        )
    else:
        update_scan_progress(
            status='complete',
            phase=f'Scan klaar: {found} duplicaten gevonden',
            duplicates_found=found,
            completed_at=datetime.now()
        )
    print(f"Duplicate scan of {folder_path}: {file_count} files, {len(groups)} groups, {found} duplicates, {errors} errors")
    
    return result(groups)
