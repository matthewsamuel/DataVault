"""
DataVault Dashboard - Safe File Operations

CRITICAL: every move of an archive file in the app goes through move_no_overwrite().
- One atomic rename on the same volume: mtime, birthtime, EXIF, extended
  attributes and Finder flags all stay with the file (it is the same inode).
- Never overwrites: the rename itself is exclusive (macOS renamex_np with
  RENAME_EXCL, elsewhere os.link + unlink of the old name), so even two moves
  racing for the same name cannot clobber each other.
- No copy-then-delete fallback: a cross-volume move is refused.
- Only regular files: directories and symlinks are refused.
"""
import ctypes
import ctypes.util
import errno
import os
import stat
import sys
from pathlib import Path
from typing import Tuple


class FileOperationError(Exception):
    """Raised when file operation fails"""
    pass


class CrossDeviceError(FileOperationError):
    """Raised when trying to move across devices"""
    pass


class DestinationExistsError(FileOperationError):
    """Raised when the exact destination is taken and renaming is not allowed"""
    pass


class DestinationFolderError(FileOperationError):
    """Raised when the destination folder cannot be created (e.g. a file is in the way)"""
    pass


MAX_CONFLICT_SUFFIX = 9999

# Flags that make rename() fail with EPERM; the owner can clear and restore them
_USER_LOCK_FLAGS = getattr(stat, "UF_IMMUTABLE", 0) | getattr(stat, "UF_APPEND", 0)

# macOS <stdio.h>: renamex_np(from, to, RENAME_EXCL) fails with EEXIST instead of replacing
_RENAME_EXCL = 0x00000004
_renamex_np = None
if sys.platform == "darwin":
    try:
        _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        _renamex_np = _libc.renamex_np
        _renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        _renamex_np.restype = ctypes.c_int
    except (OSError, AttributeError):
        _renamex_np = None


def _link_then_unlink(src: Path, dst: Path) -> None:
    """Exclusive move for systems without renamex_np: hard link, verify, drop old name."""
    os.link(src, dst, follow_symlinks=False)  # EEXIST if dst is taken
    src_st = os.lstat(src)
    dst_st = os.lstat(dst)
    if (src_st.st_dev, src_st.st_ino) != (dst_st.st_dev, dst_st.st_ino):
        raise FileOperationError(f"Hard link check failed for {src} -> {dst}")
    os.unlink(src)  # the data stays reachable via dst (same inode)


def _exclusive_rename(src: Path, dst: Path) -> None:
    """Atomically rename src to dst; raises OSError(EEXIST) if dst exists."""
    if _renamex_np is not None:
        if _renamex_np(os.fsencode(src), os.fsencode(dst), _RENAME_EXCL) == 0:
            return
        err = ctypes.get_errno()
        if err not in (errno.ENOTSUP, errno.EINVAL):
            raise OSError(err, os.strerror(err), str(src), None, str(dst))
        # Filesystem without RENAME_EXCL support: fall through to the link method
    _link_then_unlink(src, dst)


def conflict_name(path: Path, counter: int, suffix_prefix: str = "DUP") -> Path:
    """name.ext -> name_DUP_001.ext"""
    return path.parent / f"{path.stem}_{suffix_prefix}_{counter:03d}{path.suffix}"


def move_no_overwrite(
    src: Path,
    dst: Path,
    rename_on_conflict: bool = True,
    suffix_prefix: str = "DUP",
) -> Path:
    """
    MANDATORY: the only way the app moves an archive file.

    Moves the regular file src to dst without ever replacing an existing file.
    If dst is taken, the next free name dst_DUP_001.ext, _DUP_002, ... is used
    (or DestinationExistsError when rename_on_conflict is False).
    Locked files (uchg/uappnd) are unlocked for the rename and locked again.

    Returns the final destination path.

    Raises:
        FileOperationError: source missing, not a regular file, or move failed
        DestinationFolderError: the destination folder cannot be created
        CrossDeviceError: source and destination are on different volumes
        DestinationExistsError: dst taken and rename_on_conflict is False
    """
    src = Path(src)
    dst = Path(dst)

    try:
        src_st = os.lstat(src)
    except FileNotFoundError:
        raise FileOperationError(f"Source file does not exist: {src}")
    if stat.S_ISLNK(src_st.st_mode):
        raise FileOperationError(f"Refusing to move a symlink: {src}")
    if not stat.S_ISREG(src_st.st_mode):
        raise FileOperationError(f"Refusing to move something that is not a regular file: {src}")

    if os.path.abspath(src) == os.path.abspath(dst):
        raise FileOperationError(f"Source and destination are the same: {src}")

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise DestinationFolderError(f"Cannot create destination folder {dst.parent}: {e}") from e
    if os.stat(dst.parent).st_dev != src_st.st_dev:
        raise CrossDeviceError(
            f"Cannot move across volumes: {src} -> {dst}. "
            "Source and destination must be on the same volume to keep all metadata."
        )

    flags = getattr(src_st, "st_flags", 0)
    lock_flags = flags & _USER_LOCK_FLAGS
    if lock_flags:
        try:
            os.chflags(src, flags & ~lock_flags, follow_symlinks=False)
        except OSError as e:
            raise FileOperationError(f"Cannot unlock {src}: {e}") from e

    candidate = dst
    counter = 0
    try:
        while True:
            try:
                _exclusive_rename(src, candidate)
                break
            except OSError as e:
                if e.errno == errno.EEXIST:
                    if not rename_on_conflict:
                        raise DestinationExistsError(f"Destination already exists: {candidate}") from e
                    counter += 1
                    if counter > MAX_CONFLICT_SUFFIX:
                        raise FileOperationError(f"Too many conflicting files for {dst}") from e
                    candidate = conflict_name(dst, counter, suffix_prefix)
                    continue
                if e.errno == errno.EXDEV:
                    raise CrossDeviceError(f"Cannot move across volumes: {src} -> {candidate}") from e
                raise FileOperationError(f"Failed to move {src} -> {candidate}: {e}") from e
    except Exception:
        if lock_flags:
            try:
                os.chflags(src, flags, follow_symlinks=False)
            except OSError:
                pass
        raise

    if lock_flags:
        try:
            os.chflags(candidate, flags, follow_symlinks=False)
        except OSError as e:
            print(f"WARNING: moved {candidate} but could not restore its lock flag: {e}")
    return candidate


def files_identical(a: Path, b: Path, chunk_size: int = 1024 * 1024) -> bool:
    """Byte-for-byte comparison (no cached hashes): same size and same content."""
    if os.stat(a).st_size != os.stat(b).st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            ca = fa.read(chunk_size)
            cb = fb.read(chunk_size)
            if ca != cb:
                return False
            if not ca:
                return True


def is_inside(path: Path, folder: Path) -> bool:
    """True if path (after resolving symlinks and '..') lies inside folder."""
    try:
        return Path(path).resolve().is_relative_to(Path(folder).resolve())
    except (OSError, RuntimeError, ValueError):
        return False


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
    Move file preserving folder structure, never overwriting.

    Returns:
        Tuple of (destination_path, was_conflict)

    Raises:
        FileOperationError: If operation fails
        CrossDeviceError: If cross-device move attempted
    """
    wanted = recreate_folder_structure(src_path, src_root, dst_root)
    final = move_no_overwrite(src_path, wanted, suffix_prefix=conflict_suffix)
    return final, final != wanted
