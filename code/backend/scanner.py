"""
DataVault Dashboard - File Scanner
Scan folders and compute hashes with caching
"""
import hashlib
import xxhash
from pathlib import Path
from typing import List, Dict, Set, Any
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

from backend.config import (
    Config, EXCLUDED_FOLDER_NAMES, MEDIA_EXTENSIONS,
    is_media_file, validate_path_in_vault
)
from backend.database import Database
from backend.progress import (
    ScanProgress, set_scan_progress, update_scan_progress,
    is_scan_cancelled, reset_scan_cancellation
)
from datetime import datetime

def get_folders(root_path: Path, vault_root: Path, excluded_paths: Set[Path]) -> List[Dict[str, Any]]:
    """
    Recursively get folder tree excluding specific folders.
    
    Args:
        root_path: Starting directory
        vault_root: Vault root for validation
        excluded_paths: Set of absolute paths to exclude
    
    Returns:
        List of folder dicts with name, path, children
    """
    if not root_path.exists() or not root_path.is_dir():
        return []
    
    # Validate path is within vault
    if not validate_path_in_vault(root_path, vault_root):
        print(f"Path outside vault: {root_path}")
        return []
    
    folders = []
    
    try:
        entries = sorted(root_path.iterdir(), key=lambda p: p.name.lower())
        
        for entry in entries:
            # Skip if in excluded paths
            if entry in excluded_paths:
                continue
                
            # Skip hidden files/folders
            if entry.name.startswith('.'):
                continue
            
            # Skip excluded folder names
            if entry.name in EXCLUDED_FOLDER_NAMES:
                continue
            
            if entry.is_dir():
                # Recursively get children
                children = get_folders(entry, vault_root, excluded_paths)
                
                folders.append({
                    "name": entry.name,
                    "path": str(entry),
                    "children": children
                })
    
    except PermissionError:
        pass
    
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


def list_media_files(folder_path: Path, vault_root: Path) -> List[Dict[str, Any]]:
    """
    List all media files in a folder (non-recursive).
    
    Args:
        folder_path: Folder to list
        vault_root: Vault root for validation
    
    Returns:
        List of file info dicts
    """
    if not folder_path.exists() or not folder_path.is_dir():
        return []
    
    # Validate path
    if not validate_path_in_vault(folder_path, vault_root):
        print(f"Access denied: path outside vault - {folder_path}")
        return []
    
    items = []
    
    try:
        entries = sorted(folder_path.iterdir(), key=lambda p: p.name.lower())
        
        for entry in entries:
            # Skip hidden files
            if entry.name.startswith('.'):
                continue
            
            try:
                stat = entry.stat()
            except (PermissionError, OSError):
                continue
            
            if entry.is_dir():
                # Folder
                items.append({
                    "path": str(entry),
                    "name": entry.name,
                    "type": "folder",
                    "modified": stat.st_mtime
                })
            else:
                # File
                file_type = get_file_type(entry)
                
                items.append({
                    "path": str(entry),
                    "name": entry.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "type": file_type
                })
    
    except PermissionError:
        pass
    
    return items


# Alias for API compatibility
def get_media_files(folder_path: Path, vault_root: Path) -> List[Dict[str, Any]]:
    """Alias for list_media_files for backwards compatibility."""
    return list_media_files(folder_path, vault_root)



def compute_hash(file_path: Path, db: Database) -> str:
    """
    Compute SHA-256 hash with caching.
    Uses cache if file size and mtime unchanged.
    
    Args:
        file_path: File to hash
        db: Database for caching
    
    Returns:
        SHA-256 hash as hex string
    """
    stat = file_path.stat()
    size = stat.st_size
    mtime = stat.st_mtime
    
    # Check cache
    cached_hash = db.get_cached_hash(file_path, size, mtime)
    if cached_hash:
        return cached_hash
    
    # Compute hash
    sha256 = hashlib.sha256()
    
    with open(file_path, 'rb') as f:
        # Read in chunks for memory efficiency
        while chunk := f.read(8192):
            sha256.update(chunk)
    
    hash_hex = sha256.hexdigest()
    
    # Store in cache
    db.store_hash(file_path, size, mtime, hash_hex)
    
    return hash_hex


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


def find_duplicates(config: Config, db: Database) -> List[Dict[str, Any]]:
    """
    Find RAW files that have exact duplicates elsewhere in vault.
    
    A file is a duplicate if:
    - It's in RAW folder
    - An identical file (same SHA-256) exists elsewhere in vault
    
    Args:
        config: Configuration object
        db: Database for hash caching
    
    Returns:
        List of duplicate info dicts
    """
    raw_folder = Path(config.vault_root) / "RAW"
    
    if not raw_folder.exists():
        return []
    
    # Reset cancellation flag
    reset_scan_cancellation()
    
    # Initialize progress tracking
    progress = ScanProgress(
        status='scanning',
        phase='Scanning for duplicates...',
        started_at=datetime.now()
    )
    set_scan_progress(progress)
    
    # Count RAW files
    raw_count = 0
    for root, _, files in os.walk(raw_folder):
        for filename in files:
            if is_media_file(Path(root) / filename):
                raw_count += 1
    
    # Count total vault files (for scanning)
    vault_count = 0
    for root, _, files in os.walk(config.vault_root):
        for filename in files:
            if is_media_file(Path(root) / filename):
                vault_count += 1
    
    total_files = raw_count + vault_count
    
    update_scan_progress(
        total_files=total_files,
        processed_files=0
    )
    
    # Build hash index of entire vault
    vault_hashes: Dict[str, List[str]] = {}
    processed_count = 0
    
    print(f"Scanning vault for file hashes... ({vault_count} files)")
    
    for root, dirs, files in os.walk(config.vault_root):
        # Check for cancellation
        if is_scan_cancelled():
            print("Scan cancelled")
            update_scan_progress(
                status='cancelled',
                phase='Scan cancelled by user',
                completed_at=datetime.now()
            )
            return []
        
        # Sort for deterministic order
        dirs.sort()
        files.sort()
        
        for filename in files:
            file_path = Path(root) / filename
            
            if not is_media_file(file_path):
                continue
            
            processed_count += 1
            
            try:
                file_hash = compute_hash(file_path, db)
                
                if file_hash not in vault_hashes:
                    vault_hashes[file_hash] = []
                    
                vault_hashes[file_hash].append(str(file_path))
                
                update_scan_progress(processed_files=processed_count)
                    
            except Exception as e:
                print(f"Error checking {file_path}: {e}")
                update_scan_progress(processed_files=processed_count)
                continue
    
    print(f"Found {len(vault_hashes)} unique hashes in vault")
    
    # Find RAW files that have duplicates elsewhere
    duplicates = []
    
    update_scan_progress(phase='Finding RAW duplicates...')
    
    for root, dirs, files in os.walk(raw_folder):
        if is_scan_cancelled():
            update_scan_progress(
                status='cancelled',
                phase='Scan cancelled by user',
                completed_at=datetime.now()
            )
            return []
        
        dirs.sort()
        files.sort()
        
        for filename in files:
            file_path = Path(root) / filename
            
            if not is_media_file(file_path):
                continue
            
            processed_count += 1
            
            try:
                file_hash = compute_hash(file_path, db)
                
                # Check if this hash has other occurrences
                if file_hash in vault_hashes:
                    matching_paths = vault_hashes[file_hash]
                    
                    # Find duplicates (excluding self)
                    other_paths = [p for p in matching_paths if p != str(file_path)]
                    
                    if other_paths:
                        duplicates.append({
                            "path": str(file_path),
                            "duplicate_of": other_paths[0],
                            "size": file_path.stat().st_size,
                            "hash": file_hash,
                            "all_locations": matching_paths
                        })
                        
                        update_scan_progress(
                            processed_files=processed_count,
                            duplicates_found=len(duplicates)
                        )
                    
            except Exception as e:
                print(f"Error checking RAW file {file_path}: {e}")
                update_scan_progress(processed_files=processed_count)
                continue
    
    print(f"Found {len(duplicates)} RAW duplicates")
    
    # Mark as complete
    update_scan_progress(
        status='complete',
        phase='Scan complete',
        completed_at=datetime.now()
    )
    
    return duplicates


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
    
    for root, _, files in os.walk(folder_path):
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
