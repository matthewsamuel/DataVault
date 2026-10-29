"""
DataGatherer - the Downloads watcher and the opt-in schedules

One small thread (started in main._lifespan, like the media index's AutoIndexer; tests set
DATAVAULT_GATHER_DAEMON=0) that wakes every INTERVAL seconds and:

1. Downloads watcher, ONLY while an export request is open (dg_requests): lists the watch
   folders (~/Downloads plus config 'datagatherer_watch_folders'; never recursive),
   skips what is still downloading (.crdownload, .download, .part, a file whose size or
   mtime changed since the last look), recognises an export by its CONTENT (the
   connector's detect(): the members of a zip, the start of a JSON file) and starts a
   'datagatherer.watch' job that COPIES it into the vault and reads it. The original is
   never touched: the card tells the user it may be removed from Downloads.
2. Schedules, ONLY for connectors whose schedule the user switched on (off by default):
   a sync job with trigger 'schedule' once the interval has passed (not while Spotify asked
   to wait (Retry-After) or the source must be connected again).

Nothing here goes online itself: a scheduled sync goes through the network guard.
In test mode (DATAVAULT_TESTING=1) the real ~/Downloads is never read: only configured
watch folders inside the temp directory count.
"""
import fnmatch
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import raw_store, registry, state

INTERVAL = 30.0
STABLE_AGE = 60.0                  # a file untouched this long is complete, also on the first look
DOWNLOADING_SUFFIXES = (".crdownload", ".download", ".part", ".partial", ".opdownload")
EXPORT_SUFFIXES = (".zip", ".json", ".tgz", ".tar.gz", ".txt", ".csv", ".db")


def _testing() -> bool:
    return os.environ.get("DATAVAULT_TESTING") == "1"


def watch_folders(config) -> List[Path]:
    """
    The folders the watcher looks in (only those that exist): ~/Downloads plus the extra
    folders of config 'datagatherer_watch_folders' (for example a synced cloud folder).
    """
    folders = ([] if _testing() else [Path.home() / "Downloads"]) + list(config.GATHER_WATCH_FOLDERS or [])
    if _testing():
        temp = Path(tempfile.gettempdir()).resolve()
        folders = [f for f in folders if Path(f).resolve().is_relative_to(temp)]
    return [Path(f) for f in folders if Path(f).is_dir()]


def own_watch_folder(config, db, connector) -> Optional[Path]:
    """
    The folder this connector follows on its own (its 'watch_folder' setting, for example the
    synced Google Drive folder of a scheduled Takeout or an iCloud Drive folder with Timeline
    exports): watched ALWAYS, not only while an export is requested, because the user chose it
    for exactly this. Never a folder inside the vault; in test mode only inside the temp directory.
    """
    if not any(f.key == "watch_folder" for f in connector.settings):
        return None
    value = state.get_settings(db, connector.id).get("watch_folder")
    if not isinstance(value, str) or not value.strip():
        return None
    folder = Path(os.path.expanduser(value.strip()))
    try:
        if not folder.is_absolute() or not folder.is_dir():
            return None
        resolved = folder.resolve()
        if resolved.is_relative_to(config.VAULT_ROOT.resolve()):
            return None
        if _testing() and not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            return None
    except (OSError, RuntimeError):
        return None
    return folder


def in_watch_folder(config, path: Path, extra: Optional[Path] = None) -> bool:
    """True if path is a file directly in one of the watch folders (no subfolders, no symlinks)."""
    try:
        parent = path.parent.resolve()
    except (OSError, RuntimeError):
        return False
    folders = watch_folders(config) + ([extra] if extra is not None else [])
    return not path.is_symlink() and any(parent == f.resolve() for f in folders)


class _Seen:
    """What the watcher saw last time per file: (size, mtime_ns), and per connector what its detect() said."""

    def __init__(self):
        self.sizes: Dict[str, tuple] = {}
        self.kinds: Dict[tuple, Optional[str]] = {}
        self.started: set = set()


_seen = _Seen()
_seen_lock = threading.Lock()


def _downloading(entry: os.DirEntry, names: set) -> bool:
    lower = entry.name.lower()
    if lower.endswith(DOWNLOADING_SUFFIXES):
        return True
    return any(f"{entry.name}{suffix}" in names for suffix in DOWNLOADING_SUFFIXES)


def candidates(config, db, connector, now: Optional[float] = None,
               folders: Optional[List[Path]] = None) -> List[Dict[str, Any]]:
    """
    Exports of connector in the watch folders (default: Downloads and the extra folders, plus
    the connector's own folder): [{path, name, size, mtime, kind, stable, already}] (already:
    this file was copied into the vault before). Reads only zip directories and the start of
    files, and only once per (path, size, mtime).
    """
    now = time.time() if now is None else now
    found = []
    patterns = [p.lower() for p in connector.file_patterns]
    if folders is None:
        own = own_watch_folder(config, db, connector)
        folders = watch_folders(config) + ([own] if own is not None else [])
    for folder in folders:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        names = {e.name for e in entries}
        for entry in sorted(entries, key=lambda e: e.name):
            lower = entry.name.lower()
            if entry.name.startswith(".") or _downloading(entry, names) or not lower.endswith(EXPORT_SUFFIXES):
                continue
            if not any(fnmatch.fnmatch(lower, p) for p in patterns):
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_size == 0:
                continue
            key = (entry.path, st.st_size, st.st_mtime_ns)
            # What detect() said is per connector: a Spotify zip is no Takeout, and the other way round
            kind_key = (connector.id, *key)
            with _seen_lock:
                previous = _seen.sizes.get(entry.path)
                _seen.sizes[entry.path] = key[1:]
                cached = kind_key in _seen.kinds
                kind = _seen.kinds.get(kind_key)
            stable = previous == key[1:] or now - st.st_mtime >= STABLE_AGE
            if not stable:
                continue   # still being written: look again next time
            if not cached:
                path = Path(entry.path)
                kind = connector.detect(path, raw_store.read_zip_names(path))
                with _seen_lock:
                    _seen.kinds[kind_key] = kind
            if kind is None:
                continue
            already = _already(db, entry.path, st.st_size)
            found.append({"path": entry.path, "name": entry.name, "size": st.st_size, "mtime": st.st_mtime,
                          "kind": kind, "stable": True, "already": already})
    return found


def _already(db, path: str, size: int) -> bool:
    with db._connect() as conn:
        return conn.execute("SELECT 1 FROM dg_raw_imports WHERE original_path = ? AND size = ?",
                            (path, size)).fetchone() is not None


def forget_seen() -> None:
    with _seen_lock:
        _seen.sizes.clear()
        _seen.kinds.clear()
        _seen.started.clear()


class Daemon:
    def __init__(self, config, db, get_jobs, interval: float = INTERVAL):
        self.config = config
        self.db = db
        self.get_jobs = get_jobs
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_tick: Optional[float] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="datagatherer", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception as e:  # a bad tick (vault gone mid-way) never stops the thread
                print(f"DataGatherer: {e}")
            finally:
                self.db.release_thread()

    def tick(self, now: Optional[float] = None) -> Dict[str, List[str]]:
        """One round: returns what it started (for tests): {'watch': [paths], 'sync': [source ids]}."""
        now = time.time() if now is None else now
        self.last_tick = now
        started = {"watch": [], "sync": []}
        if not self.db.ready or not self.config.VAULT_ROOT.is_dir():
            return started
        started["watch"] = self.watch_tick(now)
        started["sync"] = self.schedule_tick(now)
        return started

    def watch_tick(self, now: float) -> List[str]:
        requests = state.open_requests(self.db)
        # Folders a source follows on its own (a synced cloud folder) are watched without a request
        own = {}
        for connector in registry.connectors():
            if connector.has("export_watch"):
                folder = own_watch_folder(self.config, self.db, connector)
                if folder is not None:
                    own[connector.id] = folder
        if not requests and not own:
            with _seen_lock:
                _seen.sizes.clear()
            return []
        started = []
        requested = {r["source_id"] for r in requests}
        for source_id in sorted(requested | set(own)):
            connector = registry.get(source_id)
            if connector is None or not connector.has("export_watch"):
                continue
            # Downloads only while an export is requested; the own folder always
            folders = None if source_id in requested else [own[source_id]]
            for cand in candidates(self.config, self.db, connector, now, folders):
                key = (cand["path"], cand["size"], cand["mtime"])
                if cand["already"] or key in _seen.started:
                    continue
                from backend.datagatherer.jobs import WATCH_TYPE
                try:
                    self.get_jobs().start(WATCH_TYPE, {"source": source_id, "path": cand["path"]})
                except Exception as e:
                    print(f"DataGatherer: {cand['name']} not started: {e}")
                    continue
                with _seen_lock:
                    _seen.started.add(key)
                started.append(cand["path"])
        return started

    def schedule_tick(self, now: float) -> List[str]:
        from backend.datagatherer.jobs import sync_type_name
        started = []
        for row in state.scheduled(self.db):
            connector = registry.get(row["source_id"])
            if connector is None or not connector.has("api_sync"):
                continue
            minutes = row["schedule_minutes"] or connector.schedule_minutes
            if row["last_sync_at"] and now - row["last_sync_at"] < minutes * 60:
                continue
            if row["retry_after"] and now < row["retry_after"]:
                continue
            st = state.get_state(self.db, connector.id)
            if st["needs_reconnect"]:
                continue
            try:
                self.get_jobs().start(sync_type_name(connector.id), {"trigger": "schedule"})
            except Exception as e:
                print(f"DataGatherer: scheduled sync of {connector.id} not started: {e}")
                continue
            state.update(self.db, connector.id, last_sync_at=now)
            started.append(connector.id)
        return started
