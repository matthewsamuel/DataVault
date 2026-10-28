"""
DataVault Dashboard - File Scanner
Scan folders and compute hashes with caching
"""
import xxhash
from pathlib import Path
from typing import List, Dict, Set, Any
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

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



def compute_hash_xxh3(file_path: Path, db: Database) -> str:
    """
    Compute xxHash3-128 hash with caching (optimized for speed).
    Uses cache if file size and mtime unchanged.
    
    xxHash3 is 5-10x faster than SHA-256 for duplicate detection
    while maintaining negligible collision probability with 128-bit output.
    
    Args:
        file_path: File to hash
        db: Database for caching
    
    Returns:
        xxHash3-128 hash as hex string
    """
    stat = file_path.stat()
    size = stat.st_size
    mtime = stat.st_mtime
    
    # Check cache
    cached_hash = db.get_cached_hash(file_path, size, mtime)
    if cached_hash:
        return cached_hash
    
    # Compute xxHash3-128
    hasher = xxhash.xxh3_128()
    
    with open(file_path, 'rb') as f:
        # Read in 64KB chunks (optimal for modern SSDs)
        while chunk := f.read(65536):
            hasher.update(chunk)
    
    hash_hex = hasher.hexdigest()
    
    # Store in cache
    db.store_hash(file_path, size, mtime, hash_hex)
    
    return hash_hex


def find_duplicates_in_folder(folder_path: Path, config: Config, db: Database) -> List[Dict[str, Any]]:
    """
    Find duplicate files within a specific folder (OPTIMIZED VERSION).
    
    Optimizations:
    1. Size-based pre-filtering: Only hash files with matching sizes
    2. Parallel hashing: Use ThreadPoolExecutor for multi-core processing
    3. Immediate progress feedback during all phases
    
    A file is a duplicate if another file with the same hash exists
    within the same folder tree. For 3+ identical files (A, B, C):
    - A is kept as the original
    - B and C are both marked as duplicates of A
    - Result after deletion: only A remains
    
    Args:
        folder_path: Folder to scan for duplicates
        config: Configuration object
        db: Database for hash caching
    
    Returns:
        List of duplicate info dicts with:
        - path: Path to duplicate file
        - duplicate_of: Path to original file
        - size: File size
        - hash: xxHash3-128 hash
    """
    if not folder_path.exists() or not folder_path.is_dir():
        return []
    
    # Reset cancellation flag
    reset_scan_cancellation()
    
    # Initialize progress tracking with immediate feedback
    progress = ScanProgress(
        status='counting',
        phase='Counting files...',
        started_at=datetime.now(),
        total_files=0,
        processed_files=0
    )
    set_scan_progress(progress)
    
    # PHASE 1: Group files by size WITH IMMEDIATE FEEDBACK
    # Files with different sizes cannot be duplicates
    size_groups: Dict[int, List[Path]] = defaultdict(list)
    file_count = 0
    
    # Never TRASH, RAW_DUPLICATES, the cache, hidden files or symlinks (backend/paths.py):
    # a hidden copy must never become the "original" that every visible copy is a duplicate of
    for root, _, files in walk_archive(folder_path, config):
        if is_scan_cancelled():
            update_scan_progress(status='cancelled', phase='Scan cancelled by user', completed_at=datetime.now())
            return []
        
        for filename in files:
            file_path = Path(root) / filename
            
            if not is_media_file(file_path):
                continue
                
            try:
                size = file_path.stat().st_size
                size_groups[size].append(file_path)
                file_count += 1
                
                # Update progress every 10 files during counting
                if file_count % 10 == 0:
                    update_scan_progress(
                        phase=f'Grouping by size... {file_count} files',
                        total_files=file_count
                    )
            except Exception as e:
                print(f"Error getting size for {file_path}: {e}")
                continue
    
    # PHASE 2: Identify files that need hashing
    # Only hash files that have potential duplicates (same size)
    files_to_hash = []
    for size, paths in size_groups.items():
        if len(paths) > 1:  # Multiple files of same size = potential duplicates
            files_to_hash.extend(paths)
    
    unique_sized_files = file_count - len(files_to_hash)
    
    update_scan_progress(
        status='scanning',
        phase=f'Pre-filter complete: {len(files_to_hash)} files to hash ({unique_sized_files} skipped)',
        total_files=len(files_to_hash),
        processed_files=0
    )
    
    print(f"Size pre-filtering: {file_count} total files, {len(files_to_hash)} need hashing, {unique_sized_files} unique sizes (skipped)")
    
    if len(files_to_hash) == 0:
        update_scan_progress(
            status='complete',
            phase='No potential duplicates found',
            completed_at=datetime.now()
        )
        return []
    
    # PHASE 3: Hash files in parallel
    update_scan_progress(phase='Hashing files in parallel...')
    
    hash_to_path: Dict[str, str] = {}
    duplicates = []
    processed_count = 0
    
    # Parallel hashing with ThreadPoolExecutor
    max_workers = min(4, len(files_to_hash))  # Use up to 4 cores
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all hashing tasks
        future_to_file = {
            executor.submit(compute_hash_xxh3, file_path, db): file_path
            for file_path in files_to_hash
        }
        
        # Process results as they complete
        for future in as_completed(future_to_file):
            if is_scan_cancelled():
                executor.shutdown(wait=False, cancel_futures=True)
                update_scan_progress(status='cancelled', phase='Scan cancelled by user', completed_at=datetime.now())
                return []
            
            file_path = future_to_file[future]
            processed_count += 1
            
            try:
                file_hash = future.result()
                
                # Check if hash already seen
                if file_hash in hash_to_path:
                    # This is a duplicate!
                    # For 3+ files with same hash, all others are duplicates of the first
                    duplicates.append({
                        "path": str(file_path),
                        "duplicate_of": hash_to_path[file_hash],
                        "size": file_path.stat().st_size,
                        "hash": file_hash
                    })
                    update_scan_progress(
                        processed_files=processed_count,
                        duplicates_found=len(duplicates),
                        phase=f'Hashing... {processed_count}/{len(files_to_hash)} ({len(duplicates)} duplicates)'
                    )
                else:
                    # First occurrence of this hash - this becomes the "original"
                    hash_to_path[file_hash] = str(file_path)
                    update_scan_progress(
                        processed_files=processed_count,
                        phase=f'Hashing... {processed_count}/{len(files_to_hash)} ({len(duplicates)} duplicates)'
                    )
                    
            except Exception as e:
                print(f"Error hashing {file_path}: {e}")
                update_scan_progress(processed_files=processed_count)
                continue
    
    print(f"Found {len(duplicates)} duplicates in folder (parallel scan complete)")
    
    # Scan complete
    update_scan_progress(
        status='complete',
        phase=f'Scan complete: {len(duplicates)} duplicates found',
        completed_at=datetime.now()
    )
    
    print(f"Found {len(duplicates)} duplicates in folder (parallel scan complete)")
    
    # Return both the total file count and duplicates
    return {
        "total_files": file_count,
        "duplicates": duplicates
    }
