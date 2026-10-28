"""
DataVault Dashboard - Path safety

Security-critical. Every route that takes a path or folder from a request runs
it through resolve_in_vault(), so there is one rule for what a request may touch:
- relative input is taken relative to the vault root, absolute input as is;
- every name is spelled as it is on disk (looked up by its NFC form), so a name
  typed or copied in another Unicode form finds its file and stored keys never
  depend on the form the client sent; a name that does not exist yet gets NFC;
- after resolving '..' and symlinks the path must lie inside VAULT_ROOT, both
  for the folder it is in and for what it points to (a symlink out of the vault
  is refused);
- the app's own files (cache folder, database, the app folder if it lives in
  the vault) are refused unless a route explicitly handles them itself;
- outside, internal and malformed paths all get the same 403, whether they
  exist or not, so the answer never reveals what exists outside the vault.

For a file, the returned path keeps its last component unresolved (only the
folders above it are resolved), so a route can still see and refuse a symlink
instead of silently acting on its target. For a folder (folder=True) the path is
fully resolved, so listings and stored paths always use one spelling.

Walks over the archive (folder tree, viewer, timeline, stats, Shows, duplicates,
Keurer) all use one exclusion rule, ArchiveFilter / walk_archive(): never the
app's own folders, TRASH or RAW_DUPLICATES (matched by identity, not by name, so
a user folder that happens to be called 'dashboard' stays visible), no hidden
files or folders (including macOS '._' AppleDouble files) and no symlinks.
"""
import os
import stat
import threading
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterator, List, Optional, Set, Tuple

from fastapi import HTTPException

from backend.file_ops import is_inside, same_folder

ACCESS_DENIED = "Toegang geweigerd: pad ligt buiten het archief"

# The app's own folder (repository root). Only treated as internal if it lies inside the vault.
APP_DIR = Path(__file__).resolve().parent.parent.parent


# Per folder: NFC form of each name -> the names on disk with that form.
# Keyed by (path, inode, mtime): adding, removing or renaming an entry changes the mtime.
_NAMES_CACHE: "OrderedDict[str, Tuple[Tuple[int, int], dict]]" = OrderedDict()
_NAMES_CACHE_SIZE = 256
_names_lock = threading.Lock()


def _names_by_nfc(folder: str) -> Optional[dict]:
    """{NFC name: [names on disk]} for one folder, or None if it cannot be listed."""
    try:
        st = os.stat(folder)
    except OSError:
        return None
    key = (st.st_ino, st.st_mtime_ns)
    with _names_lock:
        cached = _NAMES_CACHE.get(folder)
        if cached is not None and cached[0] == key:
            _NAMES_CACHE.move_to_end(folder)
            return cached[1]
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    by_nfc: dict = {}
    for name in names:
        by_nfc.setdefault(unicodedata.normalize("NFC", name), []).append(name)
    with _names_lock:
        _NAMES_CACHE[folder] = (key, by_nfc)
        _NAMES_CACHE.move_to_end(folder)
        while len(_NAMES_CACHE) > _NAMES_CACHE_SIZE:
            _NAMES_CACHE.popitem(last=False)
    return by_nfc


def _on_disk_spelling(path: Path) -> Path:
    """
    The path with every name spelled as it is on disk, whatever Unicode form the
    request used (APFS finds a file under NFC and NFD alike, so an existence check
    alone would keep the request's spelling and stored keys would depend on the
    client). Names that do not exist (yet) get their NFC form. ASCII names have
    only one form and are taken as given, so ordinary paths cost no listing.
    """
    text = str(path)
    if text.isascii():
        return path
    parts = Path(text).parts
    current = parts[0]
    exists = True
    for name in parts[1:]:
        if name.isascii() or name in (".", ".."):
            current = os.path.join(current, name)
            continue
        if exists:
            by_nfc = _names_by_nfc(current)
            matches = by_nfc.get(unicodedata.normalize("NFC", name)) if by_nfc is not None else None
            if matches:
                # The exact spelling if it is there (a filesystem that keeps both forms apart), else the one on disk
                current = os.path.join(current, name if name in matches else sorted(matches)[0])
                continue
            exists = False
        current = os.path.join(current, unicodedata.normalize("NFC", name))
    return Path(current)


def locate(user_path: Any, config, folder: bool = False) -> Optional[Path]:
    """
    The absolute path inside the vault that user_path names, or None when it is
    outside the vault (after resolving '..' and symlinks) or not a usable path.
    """
    if isinstance(user_path, Path):
        user_path = str(user_path)
    if not isinstance(user_path, str) or not user_path or "\x00" in user_path:
        return None
    path = Path(user_path)
    if not path.is_absolute():
        path = config.VAULT_ROOT / path
    path = _on_disk_spelling(path)
    try:
        root = config.VAULT_ROOT.resolve()
        if path.name in ("", ".."):
            entry = path.resolve()
        else:
            entry = path.parent.resolve() / path.name
        target = entry.resolve()
        if target != entry:
            target = _on_disk_spelling(target)  # a symlink's text may use another Unicode form
    except (OSError, RuntimeError, ValueError):
        return None
    if not entry.is_relative_to(root) or not target.is_relative_to(root):
        return None
    try:
        os.lstat(entry)
    except (FileNotFoundError, NotADirectoryError):
        pass  # may not exist yet (a destination); routes check existence themselves
    except (OSError, ValueError):
        return None  # e.g. a name too long for the filesystem: not a usable path
    return target if folder else entry


def app_dir_in_vault(config) -> Optional[Path]:
    """The app folder, if it lives inside the vault (and is not the vault itself)."""
    if is_inside(APP_DIR, config.VAULT_ROOT) and not same_folder(APP_DIR, config.VAULT_ROOT):
        return APP_DIR
    return None


def internal_folder_for(path: Path, config) -> Optional[Path]:
    """The app-internal folder/file (cache, database, app folder) that path lies in, else None."""
    for internal in (config.CACHE_FOLDER, config.DB_PATH, app_dir_in_vault(config)):
        if internal is not None and is_inside(path, internal):
            return internal
    return None


def is_in_vault(path: Any, config, allow_internal: bool = False) -> bool:
    """Bool form of resolve_in_vault, for paths that come from the database."""
    located = locate(path, config)
    if located is None:
        return False
    return allow_internal or internal_folder_for(located, config) is None


def resolve_in_vault(user_path: Any, config, allow_internal: bool = False, folder: bool = False) -> Path:
    """
    Validate a path from a request and return it as an absolute path in the vault.
    Raises HTTPException 400 for a non-string, 403 for anything outside the vault or
    inside the app's own folders (the same answer whether the path exists or not).
    """
    if isinstance(user_path, Path):
        user_path = str(user_path)
    if not isinstance(user_path, str):
        raise HTTPException(status_code=400, detail="Ongeldig pad")
    located = locate(user_path, config, folder=folder)
    if located is None:
        raise HTTPException(status_code=403, detail=ACCESS_DENIED)
    if not allow_internal and internal_folder_for(located, config) is not None:
        raise HTTPException(status_code=403, detail=ACCESS_DENIED)
    return located


def vault_folder_or_none(user_path: Any, config) -> Optional[Path]:
    """
    For lists of folders (timeline, on-this-day): the resolved folder, or None for
    anything resolve_in_vault would refuse, so callers can skip it quietly.
    """
    try:
        return resolve_in_vault(user_path, config, folder=True)
    except HTTPException:
        return None


# --- Exclusions for walks over the archive -----------------------------------

def excluded_folders(config) -> List[Path]:
    """Folders a walk over the archive never shows or enters."""
    folders = [config.CACHE_FOLDER, config.TRASH_FOLDER, config.DUPLICATES_FOLDER]
    app_dir = app_dir_in_vault(config)
    if app_dir is not None:
        folders.append(app_dir)
    return folders


class ArchiveFilter:
    """
    The one exclusion rule for every walk over the archive. Build one per walk
    (it snapshots the identities of the excluded folders) and ask skip(entry).
    Skipped: hidden names (also '._' AppleDouble files), symlinks, and the
    excluded folders, compared by device + inode so every spelling matches and
    an unrelated folder with the same name does not.
    """

    def __init__(self, config):
        self._ids: Set[Tuple[int, int]] = set()
        for folder in excluded_folders(config):
            try:
                st = os.stat(folder)
            except OSError:
                continue  # does not exist (yet), so it cannot show up in a listing either
            self._ids.add((st.st_dev, st.st_ino))
        self._inodes = {ino for _, ino in self._ids}

    def skip(self, entry: os.DirEntry) -> bool:
        if entry.name.startswith("."):
            return True
        try:
            if entry.is_symlink():
                return True
            if entry.inode() in self._inodes and entry.is_dir(follow_symlinks=False):
                st = entry.stat(follow_symlinks=False)
                return (st.st_dev, st.st_ino) in self._ids
        except OSError:
            return True
        return False

    def skip_path(self, path: Path) -> bool:
        """skip() for a single path (lstat instead of a directory entry)."""
        path = Path(path)
        if path.name.startswith("."):
            return True
        try:
            st = os.lstat(path)
        except OSError:
            return True
        if stat.S_ISLNK(st.st_mode):
            return True
        return (st.st_dev, st.st_ino) in self._ids


def scan_dir(folder: Path, archive_filter: ArchiveFilter) -> List[os.DirEntry]:
    """The entries of one folder that a listing may show (unsorted)."""
    with os.scandir(folder) as it:
        return [entry for entry in it if not archive_filter.skip(entry)]


def walk_archive(top, config, archive_filter: Optional[ArchiveFilter] = None) -> Iterator[Tuple[str, List[str], List[str]]]:
    """
    Drop-in for os.walk(top) over the archive: yields (root, dirnames, filenames)
    top-down, never yields or enters excluded entries, never follows symlinks.
    Callers may prune dirnames in place, as with os.walk. Unreadable folders are skipped.
    """
    archive_filter = archive_filter or ArchiveFilter(config)
    stack = [str(top)]
    while stack:
        root = stack.pop()
        dirnames: List[str] = []
        filenames: List[str] = []
        try:
            for entry in scan_dir(Path(root), archive_filter):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirnames.append(entry.name)
                    elif entry.is_file(follow_symlinks=False):
                        filenames.append(entry.name)
                except OSError:
                    continue
        except OSError:
            continue
        yield root, dirnames, filenames
        stack.extend(os.path.join(root, d) for d in reversed(dirnames))
