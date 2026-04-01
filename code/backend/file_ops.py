"""
DataVault Dashboard - Safe File Operations
CRITICAL: Uses only os.rename() / os.replace() to preserve metadata
NO shutil.move(), NO copy-then-delete
"""
import os
from pathlib import Path
from typing import Tuple

class FileOperationError(Exception):
    """Raised when file operation fails"""
    pass

class CrossDeviceError(FileOperationError):
    """Raised when trying to move across devices"""
    pass

def safe_rename(src: Path, dst: Path, allow_cross_device: bool = False) -> None:
    """
    MANDATORY: Safe file rename using os.rename() or os.replace() ONLY.
    
    This preserves ALL metadata including:
    - Modified time
    - Created time  
    - EXIF data
    - macOS extended attributes
    
    Args:
        src: Source file path
        dst: Destination file path
        allow_cross_device: If True, allows fallback (DISABLED BY DEFAULT)
    
    Raises:
        FileOperationError: If operation fails
        CrossDeviceError: If cross-device move attempted without flag
    """
    if not src.exists():
        raise FileOperationError(f"Source file does not exist: {src}")
    
    # Ensure destination directory exists
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # Primary method: os.replace() - atomic rename, handles existing files
        os.replace(str(src), str(dst))
    except OSError as e:
        # Check if it's a cross-device error (errno 18)
        if e.errno == 18:  # EXDEV - Cross-device link
            if not allow_cross_device:
                raise CrossDeviceError(
                    f"Cannot move across devices: {src} -> {dst}\n"
                    f"Source and destination must be on the same volume.\n"
                    f"This operation is blocked to preserve metadata."
                ) from e
            else:
                # Unsafe fallback - only if explicitly enabled
                # This is logged and should be surfaced to the user
                print(f"WARNING: Cross-device move detected, using fallback: {src} -> {dst}")
                import shutil
                shutil.move(str(src), str(dst))
        else:
            raise FileOperationError(f"Failed to move file: {e}") from e

def handle_conflict(dst_path: Path, suffix_prefix: str = "DUP") -> Path:
    """
    Handle filename conflict by adding suffix.
    
    Args:
        dst_path: Desired destination path
        suffix_prefix: Prefix for suffix (DUP or RESTORED)
    
    Returns:
        Non-conflicting path with suffix like _DUP_001
    """
    if not dst_path.exists():
        return dst_path
    
    # Extract parts
    parent = dst_path.parent
    stem = dst_path.stem
    suffix = dst_path.suffix
    
    # Find next available number
    counter = 1
    while True:
        new_name = f"{stem}_{suffix_prefix}_{counter:03d}{suffix}"
        new_path = parent / new_name
        if not new_path.exists():
            return new_path
        counter += 1
        if counter > 9999:  # Safety limit
            raise FileOperationError("Too many conflicting files")

def recreate_folder_structure(
    src_path: Path,
    src_root: Path,
    dst_root: Path
) -> Path:
    """
    Recreate relative folder structure from source to destination.
    
    Example:
        src_path: /vault/RAW/2010/100D3000a/DSC_0120.JPG
        src_root: /vault/RAW
        dst_root: /vault/RAW_DUPLICATES
        returns:  /vault/RAW_DUPLICATES/2010/100D3000a/DSC_0120.JPG
    
    Args:
        src_path: Full path to source file
        src_root: Root directory of source
        dst_root: Root directory of destination
    
    Returns:
        Full path to destination file preserving structure
    """
    # Get relative path from source root
    try:
        rel_path = src_path.relative_to(src_root)
    except ValueError:
        raise FileOperationError(
            f"Source path {src_path} is not under {src_root}"
        )
    
    # Construct destination path
    dst_path = dst_root / rel_path
    
    return dst_path

def safe_move_with_structure(
    src_path: Path,
    src_root: Path,
    dst_root: Path,
    conflict_suffix: str = "DUP"
) -> Tuple[Path, bool]:
    """
    Move file preserving folder structure, handle conflicts.
    
    Args:
        src_path: Source file to move
        src_root: Source root directory
        dst_root: Destination root directory
        conflict_suffix: Suffix for conflicting files
    
    Returns:
        Tuple of (destination_path, was_conflict)
    
    Raises:
        FileOperationError: If operation fails
        CrossDeviceError: If cross-device move attempted
    """
    # Recreate structure
    dst_path = recreate_folder_structure(src_path, src_root, dst_root)
    
    # Handle conflicts
    was_conflict = dst_path.exists()
    if was_conflict:
        dst_path = handle_conflict(dst_path, conflict_suffix)
    
    # Perform safe rename
    safe_rename(src_path, dst_path, allow_cross_device=False)
    
    return dst_path, was_conflict
