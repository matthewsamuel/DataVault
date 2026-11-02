"""
DataVault Dashboard - File Scanner
Scan folders and compute hashes with caching
"""
import xxhash
from pathlib import Path
from typing import List, Dict, Set, Any, Optional, Tuple
from collections import defaultdict
import os
import sqlite3
import stat as stat_module
import time

from backend.config import Config, MEDIA_EXTENSIONS, is_media_file
from backend.paths import ArchiveFilter, scan_dir, walk_archive
from backend.database import Database
from backend.jobs import bounded_map
from backend.file_ops import sqlite_ino
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
    """'photo', 'video', 'audio' or 'other', from the extension (see config.media_type)."""
    from backend.config import media_type
    return media_type(file_path)


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

HASH_WORKERS = 4
HASH_FLUSH_FILES = 2000     # new hashes are written to the cache in batches of this many...
HASH_FLUSH_SECONDS = 10.0   # ...or at least this often, so a resumed scan skips them
PROGRESS_SECONDS = 0.25     # progress messages are built at most this often


class ScanAborted(Exception):
    """The scanned folder disappeared during the scan (e.g. the disk was unplugged)."""


class _QuietReporter:
    """Progress and stop checks for a scan that is not a background job."""

    stopping = False

    def check(self) -> None:
        pass

    def progress(self, processed=None, total=None, message=None) -> None:
        pass


class _ErrorLog:
    def __init__(self):
        self.count = 0
        self.samples: List[Dict[str, str]] = []

    def __call__(self, path, exc) -> None:
        self.count += 1
        if len(self.samples) < MAX_ERROR_SAMPLES:
            reason = (exc.strerror or str(exc)) if isinstance(exc, OSError) else str(exc)
            self.samples.append({"path": str(path), "error": reason})


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
    return (sqlite_ino(st.st_ino), st.st_size, st.st_mtime_ns, st.st_ctime_ns)

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


def is_hidden(path: Path, top: Path, st: os.stat_result) -> bool:
    """A dot name anywhere below top (the scanned folder), or the Finder 'hidden' flag."""
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
        "hidden": is_hidden(path, top, st),
        "path_len": len(str(path)),
        "name_rank": 0,
    }


def collect_media_files(folder_path: Path, config: Config, reporter, record_error) -> Tuple[Dict[str, Tuple[int, int, int, int]], Set[str], int]:
    """
    Walk folder_path in sorted order with the archive's one exclusion rule (never
    the cache, TRASH, RAW_DUPLICATES, hidden or '._' files, symlinks) and return
    ({path: fingerprint} of the non-empty media files, every media path seen, empty files).
    Only plain strings and small tuples are kept per file (PERF-13).
    """
    files: Dict[str, Tuple[int, int, int, int]] = {}
    seen: Set[str] = set()
    empty = 0
    last = time.monotonic()
    for root, dirnames, filenames in walk_archive(folder_path, config, onerror=record_error):
        reporter.check()
        dirnames.sort()
        for filename in sorted(filenames):
            if not is_media_file(Path(filename)):
                continue
            path = os.path.join(root, filename)
            try:
                st = os.lstat(path)
            except OSError as e:
                record_error(path, e)
                continue
            if not stat_module.S_ISREG(st.st_mode):
                continue
            seen.add(path)
            if st.st_size == 0:
                empty += 1  # empty files are all 'identical' and never a real copy
                continue
            files[path] = _fingerprint(st)
        now = time.monotonic()
        if now - last >= PROGRESS_SECONDS:
            last = now
            reporter.progress(len(files), None, f'Bestanden zoeken... {len(files)} gevonden')
    return files, seen, empty


def hash_files(
    paths: List[str],
    fingerprints: Dict[str, Tuple[int, int, int, int]],
    cache_folder: Path,
    db: Database,
    reporter,
    record_error,
    label: str = 'Vergelijken',
    stats: Optional[Dict[str, int]] = None,
) -> Tuple[Dict[str, str], int]:
    """
    xxHash3 of every path, from the cache where the fingerprint still matches.
    The rest is hashed on HASH_WORKERS threads with bounded submission; new hashes
    go to the cache in batches (HASH_FLUSH_FILES / HASH_FLUSH_SECONDS) so a stopped
    or interrupted run loses at most one batch. A file that changed while it was
    read is an error, not a hash. Returns ({path: hash}, number newly hashed).
    stats['stored'] (if given) counts the new hashes that reached the cache, also
    when the run stops halfway (a resumed job adds them to its total).
    """
    total = len(paths)
    cache = db.get_cached_hashes(cache_folder, HASH_ALGO) if paths else {}
    hashes: Dict[str, str] = {}
    to_hash: List[str] = []
    for path in paths:
        cached = cache.get(path)
        if cached and cached[0] == fingerprints[path]:
            hashes[path] = cached[1]
        else:
            to_hash.append(path)
    del cache

    processed = len(hashes)
    reporter.progress(processed, total, f'{label}... {processed}/{total}')
    batch: List[Tuple[str, Tuple[int, int, int, int], str]] = []
    newly = 0
    last_flush = last_report = time.monotonic()

    def flush():
        nonlocal batch, last_flush
        entries, batch = batch, []
        last_flush = time.monotonic()
        try:
            db.store_hashes(entries, HASH_ALGO)
            if stats is not None:
                stats['stored'] = stats.get('stored', 0) + len(entries)
        except sqlite3.Error as e:  # the cache is an optimisation; a failed write loses nothing
            print(f"Hash cache write failed: {e}")

    try:
        # On a stop nothing new starts, but what is being hashed still lands in the cache
        stop = lambda: reporter.stopping  # noqa: E731
        for path, digest, error in bounded_map(hash_file_xxh3, to_hash, workers=HASH_WORKERS, stop=stop):
            processed += 1
            if error is None:
                try:
                    after = _fingerprint(os.lstat(path))
                except OSError as e:
                    error = e
            if error is not None:
                record_error(path, error)
            elif after == fingerprints[path]:
                hashes[path] = digest
                batch.append((path, after, digest))
                newly += 1
            else:
                record_error(path, OSError(0, "bestand veranderde tijdens de scan"))
            now = time.monotonic()
            if len(batch) >= HASH_FLUSH_FILES or (batch and now - last_flush >= HASH_FLUSH_SECONDS):
                flush()
            if now - last_report >= PROGRESS_SECONDS or processed == total:
                last_report = now
                reporter.progress(processed, total, f'{label}... {processed}/{total}')
    finally:
        if batch:
            flush()
    reporter.check()
    return hashes, newly


def find_duplicates_in_folder(
    folder_path: Path,
    config: Config,
    db: Database,
    rule: str = DEFAULT_KEEP_RULE,
    prefer_folder: Optional[str] = None,
    reporter=None,
    errors=None,
) -> Dict[str, Any]:
    """
    Find groups of byte-identical media files below folder_path.
    
    1. Walk the folder (collect_media_files) and group the non-empty media files by size.
    2. Hash only files that share their size with another file (hash_files: a cached
       hash is used only while inode, size, mtime_ns and ctime_ns are unchanged).
    3. Group by hash and pick the copy that stays with choose_keeper(rule).
    
    reporter (a jobs.JobContext) gets progress and may stop the scan: its check()
    raises jobs.JobStopped, after the hashes found so far are in the cache.
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
    reporter = reporter or _QuietReporter()
    if errors is None:  # a job passes its own, to see whether the vault went away meanwhile
        errors = _ErrorLog()
    
    def gone():
        return errors.count and not folder_path.is_dir()
    
    # PHASE 1: group by size (files with different sizes cannot be duplicates)
    reporter.progress(0, None, 'Bestanden zoeken...')
    fingerprints, seen, empty_skipped = collect_media_files(folder_path, config, reporter, errors)
    if gone():
        raise ScanAborted(str(folder_path))
    file_count = len(fingerprints)
    size_groups: Dict[int, List[str]] = defaultdict(list)
    for path, fp in fingerprints.items():
        size_groups[fp[1]].append(path)
    
    # PHASE 2: hash only files that share their size with another file
    candidates = sorted(path for paths in size_groups.values() if len(paths) > 1 for path in paths)
    del size_groups
    reporter.progress(0, len(candidates),
                      f'Voorfilter klaar: {len(candidates)} bestanden te vergelijken '
                      f'({file_count - len(candidates)} uniek van grootte)')
    hashes, _ = hash_files(candidates, fingerprints, folder_path, db, reporter, errors)
    
    if errors.count == 0:
        try:
            db.prune_hashes(folder_path, seen)
        except sqlite3.Error as e:
            print(f"Hash cache prune failed: {e}")
    del seen
    
    if gone():
        raise ScanAborted(str(folder_path))
    
    # PHASE 3: group by hash and choose the copy that stays
    by_hash: Dict[Tuple[int, str], List[str]] = defaultdict(list)
    for path, digest in hashes.items():
        by_hash[(fingerprints[path][1], digest)].append(path)
    del hashes
    
    groups = []
    for (size, digest), paths in by_hash.items():
        if len(paths) < 2:
            continue
        paths.sort()
        members = []
        for path in paths:
            try:
                st = os.lstat(path)
            except OSError as e:
                errors(path, e)
                continue
            if _fingerprint(st) != fingerprints[path]:
                errors(path, OSError(0, "bestand veranderde tijdens de scan"))
                continue
            members.append((Path(path), st))
        if len(members) < 2:
            continue
        exif_ts = exif_capture_time(members[0][0])  # identical bytes: one read per group
        copies = [_copy_info(p, st, folder_path, exif_ts) for p, st in members]
        for rank, copy in enumerate(copies):
            copy["name_rank"] = rank
        groups.append({
            "hash": digest,
            "size": size,
            "type": get_file_type(members[0][0]),
            "copies": copies,
            "keep": choose_keeper(copies, rule, prefer_folder),
        })
    groups.sort(key=lambda g: g["copies"][0]["path"])
    for i, group in enumerate(groups):
        group["id"] = i
    
    found = sum(len(g["copies"]) - 1 for g in groups)
    print(f"Duplicate scan of {folder_path}: {file_count} files, {len(groups)} groups, {found} duplicates, {errors.count} errors")
    
    return {
        "total_files": file_count,
        "empty_skipped": empty_skipped,
        "groups": groups,
        "duplicates_found": found,
        "cancelled": False,
        "complete": errors.count == 0,
        "errors": errors.count,
        "error_samples": errors.samples,
        "rule": rule,
    }
