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
import tempfile
import threading
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Set, Tuple

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


def _on_disk_spelling(base: str, names: Tuple[str, ...]) -> Path:
    """
    base / names with every name spelled as it is on disk, whatever Unicode form the
    request used (APFS finds a file under NFC and NFD alike, so an existence check
    alone would keep the request's spelling and stored keys would depend on the
    client). Names that do not exist (yet) get their NFC form. ASCII names have
    only one form and are taken as given, so ordinary paths cost no listing.
    Only base and the folders below it are listed: callers pass a base inside
    the vault (or the vault root's own parents, which come from the config).
    """
    current = base
    exists = True
    for name in names:
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


def _names_below(path: Path, root: Path) -> Optional[Tuple[str, ...]]:
    """
    The names of path below root, or None if path is not root or inside it. Purely
    lexical (path must be resolved); names are compared in NFC, so the request may
    spell the vault root itself in another Unicode form.
    """
    parts, root_parts = path.parts, root.parts
    if len(parts) < len(root_parts):
        return None
    for part, root_part in zip(parts, root_parts):
        if part != root_part and unicodedata.normalize("NFC", part) != unicodedata.normalize("NFC", root_part):
            return None
    return parts[len(root_parts):]


def _resolve_entry(path: Path) -> Path:
    """path with '..' and every symlink above its last name resolved (the last name is kept)."""
    if path.name in ("", ".."):
        return path.resolve()
    return path.parent.resolve() / path.name


def locate(user_path: Any, config, folder: bool = False) -> Optional[Path]:
    """
    The absolute path inside the vault that user_path names, or None when it is
    outside the vault (after resolving '..' and symlinks) or not a usable path.
    Containment is checked before any name is re-spelled, so a path outside the
    vault never causes a listing of a folder outside the vault.
    """
    if isinstance(user_path, Path):
        user_path = str(user_path)
    if not isinstance(user_path, str) or not user_path or "\x00" in user_path:
        return None
    path = Path(user_path)
    if not path.is_absolute():
        path = config.VAULT_ROOT / path
    try:
        root = config.VAULT_ROOT.resolve()
        disk_root = _on_disk_spelling(root.anchor, root.parts[1:])
        entry = _resolve_entry(path)
        names = _names_below(entry, root)
        if names is None:
            return None
        spelled = _on_disk_spelling(str(disk_root), names)
        if spelled != entry:
            # Resolve once more in the on-disk spelling: on a filesystem that keeps Unicode
            # forms apart, the request's form may not have found a folder (or a symlink in it)
            entry = _resolve_entry(spelled)
            names = _names_below(entry, root)
            if names is None:
                return None
            entry = _on_disk_spelling(str(disk_root), names)
        target = entry.resolve()
        if target != entry:
            # A symlink: check where it points, in the on-disk spelling (its text may use another form)
            names = _names_below(target, root)
            if names is None:
                return None
            target = _on_disk_spelling(str(disk_root), names)
    except (OSError, RuntimeError, ValueError):
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


def walk_archive(
    top,
    config,
    archive_filter: Optional[ArchiveFilter] = None,
    onerror: Optional[Callable[[str, OSError], None]] = None,
) -> Iterator[Tuple[str, List[str], List[str]]]:
    """
    Drop-in for os.walk(top) over the archive: yields (root, dirnames, filenames)
    top-down, never yields or enters excluded entries, never follows symlinks.
    Callers may prune or sort dirnames in place, as with os.walk. Unreadable folders
    and entries are skipped; onerror(path, error) is told about each one, so a caller
    that must not miss files (the duplicate scan) can count and report them.
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
                except OSError as e:
                    if onerror:
                        onerror(entry.path, e)
                    continue
        except OSError as e:
            if onerror:
                onerror(root, e)
            continue
        yield root, dirnames, filenames
        stack.extend(os.path.join(root, d) for d in reversed(dirnames))


# --- The backup folder chosen in Instellingen (backend/backup.py) -----------------------
#
# The database backups may go to any folder of the user's own on the Mac's disk or on another
# disk (a dedicated folder on the T7 too), but never into the archive (its media folders,
# TRASH, RAW_DUPLICATES), DataVault's own folders (cache, database, app, models), a system
# folder, or the root of a disk or the home folder itself: a backup folder is a folder of its own.

# Never a backup folder, itself or anything below it (the real path, symlinks resolved)
SYSTEM_TREES = ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/dev", "/Applications", "/cores",
                "/opt", "/private/etc", "/private/var/db", "/private/var/root")
# Never a backup folder itself (a folder below them is fine)
SYSTEM_FOLDERS = ("/", "/Users", "/Volumes", "/private", "/private/var", "/home", "/tmp", "/var")


class BackupFolderError(ValueError):
    """A folder that cannot hold the backups; the text is Dutch and for the user."""


def _home() -> Path:
    return Path(os.path.realpath(Path.home()))


def _app_support() -> Path:
    """~/Library/Application Support/DataVault: the default backup folder lives here (allowed)."""
    return _home() / "Library" / "Application Support" / "DataVault"


def _volume_root(real: Path) -> bool:
    return len(real.parts) == 3 and real.parts[1] == "Volumes"


def backup_folder_problem(real: Path, config) -> Optional[str]:
    """Why real (an absolute, resolved path) cannot be the backup folder, or None. Only looks
    at where it is, not at the disk (cheap: the folder browser asks this for every folder)."""
    home = _home()
    if real in {Path(p) for p in SYSTEM_FOLDERS} or real == home or _volume_root(real):
        return "Kies of maak een eigen map voor de back-ups, niet de hoofdmap van een schijf of je thuismap"
    if any(real == Path(t) or real.is_relative_to(t) for t in SYSTEM_TREES) \
            or (real.is_relative_to(home / "Library") and not real.is_relative_to(_app_support())):
        return "Dit is een systeemmap; kies of maak een eigen map, bijvoorbeeld op de T7 naast het archief"
    protected = (
        ("Back-ups in de cache van DataVault kan niet: dat is dezelfde map als de database", config.CACHE_FOLDER),
        ("Back-ups in de map van de database kan niet: kies een map ernaast", Path(config.DB_PATH).parent),
        ("Back-ups in RAW_DUPLICATES kan niet: kies een map buiten het archief", config.DUPLICATES_FOLDER),
        ("Back-ups in de prullenmand (TRASH) kan niet: kies een map buiten het archief", config.TRASH_FOLDER),
        ("Back-ups in de RAW-map kan niet: kies een map buiten het archief", config.RAW_FOLDER),
        ("Back-ups in het archief kan niet: daar staan je foto's en bestanden. Kies een map ernaast", config.VAULT_ROOT),
        ("Back-ups in de map van de app zelf kan niet", APP_DIR),
        ("Back-ups in de map met de modellen kan niet", getattr(config, "MODELS_FOLDER", None)),
    )
    for message, folder in protected:
        if folder is not None and is_inside(real, Path(os.path.realpath(folder))):
            return message
    if os.environ.get("DATAVAULT_TESTING") == "1":
        temp_root = Path(os.path.realpath(tempfile.gettempdir()))
        if not real.is_relative_to(temp_root):
            return "In de testmodus kan de back-upmap alleen in de tijdelijke map staan"
    return None


def check_backup_folder(raw: Any, config, create: Optional[str] = None) -> Tuple[Path, Optional[str]]:
    """
    The backup folder chosen in Instellingen as an absolute real path, plus a warning (or
    None); BackupFolderError when it cannot be used. The folder must exist (a disk that is not
    connected is not silently replaced by an empty folder on the Mac) and DataVault must be able
    to write in it, which is tried with a small file of its own that is removed again.
    create: the name of a new folder to make inside raw first (one level, never overwrites).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise BackupFolderError("Kies een map voor de back-ups")
    if "\0" in raw or (create is not None and "\0" in create):
        raise BackupFolderError("Ongeldig pad")
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise BackupFolderError("Geef een volledig pad op (beginnend met / of ~)")
    try:
        real = Path(os.path.realpath(path))
    except (OSError, ValueError):
        raise BackupFolderError("Ongeldig pad")
    if create is not None:
        name = create.strip()
        if not name or name in (".", "..") or "/" in name or name.startswith("."):
            raise BackupFolderError("Geef de nieuwe map een gewone naam (zonder / en niet beginnend met een punt)")
        if len(name.encode("utf-8")) > 255:
            raise BackupFolderError("Die naam is te lang")
        if not real.is_dir():
            raise BackupFolderError(f"De map {real} bestaat niet (meer)")
        problem = backup_folder_problem(real / name, config)
        if problem:
            raise BackupFolderError(problem)
        target = real / unicodedata.normalize("NFC", name)
        try:
            target.mkdir()   # one level, in a folder that exists; an existing folder is fine
        except FileExistsError:
            if not target.is_dir() or target.is_symlink():
                raise BackupFolderError(f"Er staat al een bestand met de naam {name}")
        except OSError as e:
            raise BackupFolderError(f"De map kon niet worden gemaakt ({e.strerror or e})")
        real = Path(os.path.realpath(target))
    problem = backup_folder_problem(real, config)
    if problem:
        raise BackupFolderError(problem)
    if not real.exists():
        raise BackupFolderError(f"De map {real} bestaat niet. Is de schijf aangesloten?")
    if not real.is_dir():
        raise BackupFolderError("Op die plek staat een bestand, geen map")
    if not os.access(real, os.W_OK | os.X_OK):
        raise BackupFolderError(f"In {real} mag DataVault niet schrijven")
    try:
        fd, probe = tempfile.mkstemp(prefix=".datavault-schrijftest-", dir=str(real))
        os.close(fd)
        os.unlink(probe)   # our own test file, made just now
    except OSError as e:
        raise BackupFolderError(f"Schrijven in {real} lukte niet ({e.strerror or e})")
    warning = None
    try:
        if os.stat(real).st_dev == os.stat(Path(config.DB_PATH).parent).st_dev:
            warning = ("Deze map staat op dezelfde schijf als de database. Gaat die schijf kapot, dan ben je "
                       "beide kwijt; een map op een andere schijf (zoals deze Mac) is veiliger.")
    except OSError:
        pass
    return real, warning
