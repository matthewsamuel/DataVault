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
- One app-wide lock (ARCHIVE_LOCK): routes hold it from their last check until
  the move is done, so two requests can never both move "the other copy" away.
- Never touches the app's own folders (cache, database): see protect_paths().
"""
import ctypes
import ctypes.util
import errno
import os
import stat
import sys
import threading
import unicodedata
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple


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


class ProtectedPathError(FileOperationError):
    """Raised when a move would take a file out of, or into, the app's own folders"""
    pass


# Every archive move (Keurer, viewer, duplicates, undo) runs under this lock.
# Re-entrant, so a route can hold it across its checks and still call move_no_overwrite.
ARCHIVE_LOCK = threading.RLock()

# Folders/files no move may ever touch (the app's cache and live database); set at startup
_protected_paths: List[Path] = []


# Called with (src, final_dst) after every successful move, e.g. to carry a cached
# thumbnail over to the new path. Set at startup; a failing callback never fails a move.
_after_move: List[Callable[[Path, Path], None]] = []


def on_moved(callback: Callable[[Path, Path], None]) -> None:
    """Register a callback for every successful archive move (replaces earlier ones)."""
    _after_move[:] = [callback]


def protect_paths(paths: Iterable[Path]) -> None:
    """Register the app's own folders/files; move_no_overwrite refuses anything inside them."""
    global _protected_paths
    _protected_paths = [Path(p) for p in paths]


def protected_path_for(path: Path) -> Optional[Path]:
    """The registered protected folder/file that path lies in (or is), else None."""
    for protected in _protected_paths:
        if is_inside(path, protected):
            return protected
    return None


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

    with ARCHIVE_LOCK:
        final = _move_no_overwrite_locked(src, dst, rename_on_conflict, suffix_prefix)
    for callback in list(_after_move):
        try:
            callback(src, final)
        except Exception:
            pass
    return final


def _move_no_overwrite_locked(src: Path, dst: Path, rename_on_conflict: bool, suffix_prefix: str) -> Path:
    for path in (src, dst):
        protected = protected_path_for(path)
        if protected is not None:
            raise ProtectedPathError(f"Refusing to move {src} -> {dst}: {path} is inside {protected}")

    try:
        src_st = os.lstat(src)
    except FileNotFoundError:
        raise FileOperationError(f"Source file does not exist: {src}")
    if stat.S_ISLNK(src_st.st_mode):
        raise FileOperationError(f"Refusing to move a symlink: {src}")
    if not stat.S_ISREG(src_st.st_mode):
        raise FileOperationError(f"Refusing to move something that is not a regular file: {src}")

    if os.path.abspath(src) == os.path.abspath(dst) or _same_existing_file(src, dst):
        # (the second check catches a different spelling, e.g. other letter case)
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


def _identity(path: Path) -> Optional[Tuple[int, int]]:
    """(device, inode) of an existing path, None if it does not exist."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_dev, st.st_ino


def _same_existing_file(a: Path, b: Path) -> bool:
    ida = _identity(a)
    return ida is not None and ida == _identity(b)


def _folded(path: Path) -> Path:
    """Spelling as a case- and normalisation-insensitive volume (default macOS) sees it."""
    return Path(unicodedata.normalize("NFC", str(path)).casefold())


def is_inside(path: Path, folder: Path) -> bool:
    """
    True if path lies inside folder (or is folder itself), however it is spelled.

    Security-critical for every protected-folder check (cache, TRASH,
    RAW_DUPLICATES). Resolving symlinks and '..' is not enough: on a
    case-insensitive volume <vault>/.DATAVAULT_CACHE is the cache folder too,
    and resolve() keeps the spelling it was given. So every existing ancestor
    of the path is compared with the folder by identity (device + inode).
    If the folder does not exist yet, a case-insensitive comparison of the
    names is used, which errs on the side of "inside".
    """
    try:
        resolved = Path(path).resolve()
        resolved_folder = Path(folder).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if resolved.is_relative_to(resolved_folder):
        return True

    folder_id = _identity(resolved_folder)
    if folder_id is None:
        return _folded(resolved).is_relative_to(_folded(resolved_folder))

    for ancestor in (resolved, *resolved.parents):
        if _identity(ancestor) == folder_id:
            return True
    return False


COPY_CHUNK = 4 * 1024 * 1024


def copy_no_overwrite(src: Path, dst: Path, expected_sha256: Optional[str] = None,
                      check: Optional[Callable[[int], None]] = None) -> Tuple[Path, str, int]:
    """
    Copy a file INTO the vault (DataGatherer's raw exports) without ever overwriting.
    The source is only read; it stays where it is, untouched.

    The bytes go to a hidden temporary file next to dst (created exclusively), are
    fsync'ed, and only then get their real name with the same exclusive rename as
    move_no_overwrite. When dst is taken, 'name 2.ext', 'name 3.ext', ... is used. If the
    copy fails halfway, only that temporary file (made by this call, never an archive
    file) is removed again. expected_sha256: the copy must have exactly these bytes (the
    source changed while being copied otherwise). check(bytes_done) is called between
    chunks and may raise to stop (a job's cancel or pause).

    Returns (final path, sha256, size). The mtime of the source is kept.
    """
    import hashlib
    import uuid
    src, dst = Path(src), Path(dst)
    src_st = os.stat(src)
    if not stat.S_ISREG(src_st.st_mode):
        raise FileOperationError(f"Refusing to copy something that is not a regular file: {src}")
    for path in (dst,):
        protected = protected_path_for(path)
        if protected is not None:
            raise ProtectedPathError(f"Refusing to copy into {protected}: {dst}")
    temp = dst.parent / f".{dst.name}.datavault-partial-{uuid.uuid4().hex[:8]}"
    digest = hashlib.sha256()
    size = 0
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as source:
            while True:
                chunk = source.read(COPY_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
                if check is not None:
                    check(size)
            out.flush()
            os.fsync(out.fileno())
        sha = digest.hexdigest()
        if expected_sha256 is not None and sha != expected_sha256:
            raise FileOperationError(f"{src} changed while it was being copied")
        os.utime(temp, ns=(src_st.st_atime_ns, src_st.st_mtime_ns))
        candidate, counter = dst, 1
        while True:
            try:
                _exclusive_rename(temp, candidate)
                return candidate, sha, size
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise FileOperationError(f"Failed to copy {src} -> {candidate}: {e}") from e
                counter += 1
                if counter > MAX_CONFLICT_SUFFIX:
                    raise FileOperationError(f"Too many conflicting files for {dst}") from e
                candidate = dst.parent / f"{dst.stem} {counter}{dst.suffix}"
    except BaseException:
        # Only the partial copy this call made itself (exclusive create above); never an archive file
        try:
            if temp.exists():
                os.unlink(temp)
        except OSError:
            pass
        raise


def same_folder(a: Path, b: Path) -> bool:
    """True if a and b are the same folder, also when spelled differently."""
    if _same_existing_file(a, b):
        return True
    try:
        return Path(a).resolve() == Path(b).resolve()
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
