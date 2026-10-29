"""
DataVault Dashboard - Automatic database backups (and the way back)

The database (ratings, comparisons, Keurer decisions, undo history, recovery records,
settings, the media index) lives on the T7 next to the archive. One copy of it is not
enough: a broken disk or a damaged file would take years of ratings and every undo
record with it. So the app copies it to the Mac's internal disk:

- on start, when the newest backup is older than MAX_AGE (24 h), and then every
  CHECK_INTERVAL while it runs (so about once a day), and whenever the user clicks
  'Nu back-uppen' in Instellingen;
- before every migration (migrations.backup_database, a full copy next to the database
  in db_backups/; that one stays as it was).

How a backup is made (make_backup):
1. The SQLite backup API copies the live database page by page in one read transaction:
   a consistent snapshot, including what is still in the WAL file, while the app keeps
   writing. It is written to a hidden '.partial' file in the backup folder.
2. Compact: the rows of tables the app can rebuild from the archive by itself are
   removed from the COPY (their schema stays, so a restored database opens as it is and
   fills them again): the hash cache (file_hashes), the capture date cache (media_dates),
   the folder listing times of the index (media_folders: the next pass then simply lists
   every folder once) and stored job results (job_results; a finished scan says 'start
   it again'). Later caches register with register_rebuildable(); tables whose name
   contains 'phash' or 'embedding' count as rebuildable too (perceptual hashes and CLIP
   vectors are large and are computed again from the photos). The media rows themselves
   are kept: their ids are what albums and other user data point at. Then VACUUM, and
   journal_mode DELETE, so the backup is one small self-contained file.
3. PRAGMA integrity_check on the copy. Only a copy that answers 'ok' is kept: it is
   gzip-compressed (a database of 100,000 photos goes from about 60 MB to under 10 MB),
   the compressed file is read back and compared, and it gets its final name
   (datavault-YYYYMMDD-HHMMSS.db.gz, or ...-handmatig.db.gz), linked into place so
   nothing is ever overwritten. A file system without hard links (exFAT or FAT on a USB
   stick, some network shares) gets an exclusively created new file instead (O_EXCL:
   it fails if the name exists), synced and compared again. A failed copy is removed and
   the error is reported in Dutch. 'gunzip -k <file>' gives a normal SQLite database to
   look into.
4. Rotation: one backup of each of the last KEEP_DAILY days that have one, plus one of
   each of the last KEEP_WEEKLY weeks (the newest automatic one of that day or week, or
   the newest made by hand when there is no automatic one), plus the newest KEEP_MANUAL
   made with 'Nu back-uppen' (a backup made by hand just before something risky must not
   go because a second one was made the same day, and it never pushes out that day's
   automatic one); the others go. Only files in the backup folder with exactly these
   names are ever removed, never anything else.

The backup folder is ~/Library/Application Support/DataVault/backups unless config.json
says otherwise ("backup_folder", "backup_keep_daily", "backup_keep_weekly"). With
DATAVAULT_TESTING=1 it defaults to <cache>/backups, so a test never writes outside the
temp directory. DATAVAULT_AUTO_BACKUP=0 turns the automatic backups off (tests).

Restore (restore_backup): only with the app stopped (restore_backup.py checks the app
lock and running DataVault servers). The live database is first saved in full as
datavault-voor-herstel-YYYYMMDD-HHMMSS.db in the backup folder and checked; only then is
the backup copied INTO the live database with the backup API (no file is replaced or
deleted). See code/restore_backup.py and the README.
"""
import contextlib
import errno
import fcntl
import gzip
import hashlib
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

from backend import migrations
from backend.config import DEFAULT_CONFIG_PATH

MAX_AGE = 24 * 3600              # a backup older than this is due
CHECK_INTERVAL = 3600            # how often the running app checks whether one is due
FIRST_CHECK_DELAY = 20           # seconds after start (the database and index start first)
KEEP_DAILY = 7
KEEP_WEEKLY = 4
KEEP_MANUAL = 5                  # backups made with 'Nu back-uppen' that are kept apart
KEEP_BEFORE_RESTORE = 5
FREE_SPACE_MARGIN = 64 * 1024 * 1024

NAME_RE = re.compile(r"^datavault-(\d{8})-(\d{6})(-handmatig)?\.db(\.gz)?$")
BEFORE_RESTORE_RE = re.compile(r"^datavault-voor-herstel-(\d{8})-(\d{6})\.db(\.gz)?$")
GZIP_LEVEL = 1                   # level 6 makes it only 5% smaller and takes 4x as long

REBUILDABLE_TABLES: Set[str] = {"file_hashes", "media_dates", "media_folders", "job_results"}
REBUILDABLE_NAME_PARTS = ("phash", "embedding")

LOCK_NAME = "datavault.app.lock"

# What os.link raises on a file system without hard links (exFAT, FAT, some shares)
NO_HARD_LINKS = {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.EMLINK, errno.ENOSYS}


class BackupError(Exception):
    """A backup or restore that could not be made (Dutch message); status is the HTTP code."""

    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


def register_rebuildable(table: str) -> None:
    """A table whose rows the app can make again by itself: emptied in the (compact) backup."""
    REBUILDABLE_TABLES.add(table)


def _say(message: str) -> None:
    try:
        print(message, flush=True)
    except OSError:
        pass


# ==================== WHERE AND WHICH ====================

def folder_of(config) -> Path:
    return Path(config.BACKUP_FOLDER)


def _stamp(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y%m%d-%H%M%S")


def _parse(name: str) -> Optional[Dict[str, Any]]:
    match = NAME_RE.match(name)
    kind = "auto"
    if match:
        kind = "handmatig" if match.group(3) else "auto"
    else:
        match = BEFORE_RESTORE_RE.match(name)
        kind = "voor-herstel"
    if not match:
        return None
    try:
        created = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return {"kind": kind, "created": created}


def list_backups(folder: Path, include_before_restore: bool = True) -> List[Dict[str, Any]]:
    """Every backup file in folder (regular files with our names only), newest first."""
    items = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    for entry in entries:
        info = _parse(entry.name)
        if info is None or (info["kind"] == "voor-herstel" and not include_before_restore):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        items.append({
            "name": entry.name, "path": str(Path(folder) / entry.name), "size": size,
            "created_at": info["created"].timestamp(), "kind": info["kind"],
        })
    items.sort(key=lambda item: (item["created_at"], item["name"]), reverse=True)
    return items


def newest(folder: Path) -> Optional[Dict[str, Any]]:
    backups = list_backups(folder, include_before_restore=False)
    return backups[0] if backups else None


def is_due(config, now: Optional[float] = None, max_age: float = MAX_AGE) -> bool:
    last = newest(folder_of(config))
    now = time.time() if now is None else now
    return last is None or now - last["created_at"] >= max_age


def rotate(folder: Path, keep_daily: int = KEEP_DAILY, keep_weekly: int = KEEP_WEEKLY,
           keep_before_restore: int = KEEP_BEFORE_RESTORE, keep_manual: int = KEEP_MANUAL) -> List[str]:
    """
    Keep one backup for each of the newest keep_daily days, and for each of the newest
    keep_weekly ISO weeks (always at least the newest backup); remove the rest. A day or
    week keeps its newest AUTOMATIC backup when it has one: a backup made by hand later that
    day must not push out the morning's automatic one (often the last copy from before
    whatever made you click 'Nu back-uppen'); only a day with nothing automatic keeps its
    newest one made by hand. Kept apart as well: the newest keep_manual backups made by
    hand and the newest keep_before_restore safety copies made before a restore. Returns
    the names removed. Only regular files with our exact names are touched.
    """
    backups = list_backups(folder)
    regular = [b for b in backups if b["kind"] != "voor-herstel"]
    keep: Set[str] = set()
    for key, limit in ((lambda m: m.strftime("%Y-%m-%d"), keep_daily),
                       (lambda m: m.isocalendar()[:2], keep_weekly)):
        chosen: Dict[Any, Dict[str, Any]] = {}     # period -> its backup, newest period first
        for item in regular:    # newest first, so the first one per period is its newest
            period = key(datetime.fromtimestamp(item["created_at"]))
            if period not in chosen:
                if len(chosen) >= limit:
                    break
                chosen[period] = item
            elif chosen[period]["kind"] == "handmatig" and item["kind"] == "auto":
                chosen[period] = item
        keep.update(item["name"] for item in chosen.values())
    if regular:
        keep.add(regular[0]["name"])
    keep.update(b["name"] for b in [b for b in regular if b["kind"] == "handmatig"][:keep_manual])
    keep.update(b["name"] for b in [b for b in backups if b["kind"] == "voor-herstel"][:keep_before_restore])
    removed = []
    for item in backups:
        if item["name"] in keep:
            continue
        path = Path(folder) / item["name"]
        try:
            if path.is_symlink() or not path.is_file():
                continue
            path.unlink()   # only our own backup copies, never archive files
            removed.append(item["name"])
        except OSError as e:
            _say(f"Back-up {item['name']} kon niet weg: {e}")
    return removed


# ==================== MAKING ONE ====================

_lock = threading.Lock()
_last_error: Optional[Dict[str, Any]] = None
_last_result: Optional[Dict[str, Any]] = None


def running() -> bool:
    return _lock.locked()


def last_error() -> Optional[Dict[str, Any]]:
    return _last_error


def _not_writable(folder: Path) -> BackupError:
    return BackupError(f"De back-upmap {folder} is niet schrijfbaar. Maak hem schrijfbaar, of kies een "
                       "andere map in config.json (backup_folder).", 500)


def _writable(folder: Path) -> bool:
    return os.access(folder, os.W_OK | os.X_OK)


def _reason(e: BaseException, folder: Path) -> BackupError:
    """A Dutch message for what went wrong while copying (OSError or sqlite3.Error); the
    technical text only in parentheses when there is no better description."""
    if isinstance(e, OSError):
        code = e.errno
        if code in (errno.EACCES, errno.EPERM, errno.EROFS):
            return _not_writable(folder)
        if code in (errno.ENOSPC, errno.EDQUOT):
            return BackupError(f"De schijf met de back-upmap {folder} is vol.", 507)
        if code == errno.EIO:
            return BackupError("Lees- of schrijffout op de schijf. Probeer het later nog eens.", 500)
        if code == errno.ENOENT:
            return BackupError("Een bestand of map verdween tijdens de back-up. Is de schijf nog aangesloten?", 503)
        return BackupError(f"Er ging iets onverwachts mis ({e.strerror or e}).", 500)
    text = str(e).lower()
    if "unable to open" in text:
        if not _writable(folder):
            return _not_writable(folder)
        return BackupError(f"Het back-upbestand kon niet worden aangemaakt in {folder}.", 500)
    if "locked" in text or "busy" in text:
        return BackupError("De database is even bezet. Probeer het zo nog eens.", 409)
    if "full" in text:
        return BackupError(f"De schijf met de back-upmap {folder} is vol.", 507)
    if "i/o" in text:
        return BackupError("Lees- of schrijffout op de schijf. Probeer het later nog eens.", 500)
    if "malformed" in text or "not a database" in text:
        return BackupError("De database is beschadigd en kon niet gekopieerd worden. "
                           "Zet een eerdere back-up terug (zie 'Een back-up terugzetten').", 500)
    return BackupError(f"Er ging iets onverwachts mis met de database ({e}).", 500)


def _rebuildable(conn: sqlite3.Connection) -> List[str]:
    """Real tables (not virtual, not FTS shadow tables) whose rows can be rebuilt."""
    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
    virtual = [name for name, sql in rows if (sql or "").upper().startswith("CREATE VIRTUAL")]
    names = []
    for name, sql in rows:
        if name in virtual or any(name.startswith(v + "_") for v in virtual):
            continue
        lowered = name.lower()
        if name in REBUILDABLE_TABLES or any(part in lowered for part in REBUILDABLE_NAME_PARTS):
            names.append(name)
    return sorted(names)


def _integrity(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    return "ok" if rows == [("ok",)] else "; ".join(str(r[0]) for r in rows[:5])


def _schema_version(path: Path) -> Optional[int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _live_size(db_path: Path) -> int:
    size = 0
    for suffix in ("", "-wal"):
        try:
            size += os.stat(f"{db_path}{suffix}").st_size
        except OSError:
            pass
    return size


def _snapshot(db_path: Path, target: Path) -> None:
    """A consistent copy of the live database (backup API: one read transaction, WAL included)."""
    if not db_path.is_file():
        raise BackupError("De database is niet bereikbaar. Is de schijf aangesloten?", 503)
    source = sqlite3.connect(str(db_path), timeout=30)
    try:
        dest = sqlite3.connect(str(target))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def _compact(target: Path) -> Dict[str, int]:
    """Empty the rebuildable tables of the copy, make it one file (journal DELETE) and VACUUM."""
    conn = sqlite3.connect(str(target), isolation_level=None)
    emptied: Dict[str, int] = {}
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN")
        for table in _rebuildable(conn):
            try:
                emptied[table] = conn.execute(f'DELETE FROM "{table}"').rowcount
            except sqlite3.Error:
                continue   # e.g. a table that needs an extension: then it stays as it is
        conn.execute("COMMIT")
        conn.execute("VACUUM")
    finally:
        conn.close()
    return emptied


def _sha256(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _compress(plain: Path, packed: Path) -> None:
    """gzip plain into packed (a new file), then read it back: it must give the same bytes."""
    with open(plain, "rb") as src, open(packed, "xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=GZIP_LEVEL, mtime=0) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
    with open(plain, "rb") as src:
        expected = _sha256(src)
    with gzip.open(packed, "rb") as src:
        if _sha256(src) != expected:
            raise BackupError("De gecomprimeerde back-up klopt niet met het origineel; hij is niet bewaard.", 500)


def _place(tmp: Path, final: Path) -> None:
    """Give the finished copy its name without ever overwriting a file (a hard link fails if
    the name is taken), then drop the temporary name. Without hard links (exFAT): an
    exclusive copy (_copy_new)."""
    with open(tmp, "rb") as handle:
        os.fsync(handle.fileno())
    try:
        os.link(tmp, final)
    except OSError as e:
        if e.errno not in NO_HARD_LINKS:
            raise
        _copy_new(tmp, final)
    tmp.unlink()


def _copy_new(src: Path, final: Path) -> None:
    """Copy src to final, a file that must not exist yet (O_EXCL: never overwrites), synced
    to the disk and compared byte for byte (sha256). A copy that does not match is removed
    (it is our own file, made just now) and BackupError is raised."""
    fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as dst, open(src, "rb") as source:
            shutil.copyfileobj(source, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        with open(src, "rb") as a, open(final, "rb") as b:
            same = _sha256(a) == _sha256(b)
    except BaseException:
        with contextlib.suppress(OSError):
            final.unlink()   # our own half-written copy
        raise
    if not same:
        with contextlib.suppress(OSError):
            final.unlink()   # our own copy that does not match
        raise BackupError("De kopie in de back-upmap klopt niet met het origineel; hij is niet bewaard.", 500)


def _check_space(folder: Path, needed: int) -> None:
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < needed + FREE_SPACE_MARGIN:
        raise BackupError(
            f"Te weinig vrije ruimte voor een back-up in {folder}: nodig ongeveer "
            f"{(needed + FREE_SPACE_MARGIN) // (1024 * 1024)} MB, vrij {free // (1024 * 1024)} MB.", 507)


def make_backup(config, manual: bool = False, compact: bool = True, keep_daily: Optional[int] = None,
                keep_weekly: Optional[int] = None) -> Dict[str, Any]:
    """
    Make one backup now (see the module docstring). Returns {name, path, size, created_at,
    kind, schema_version, integrity, emptied, live_size, seconds, removed}. Raises
    BackupError (status 409 when one is already running).
    """
    global _last_error, _last_result
    if not _lock.acquire(blocking=False):
        raise BackupError("Er wordt al een back-up gemaakt", 409)
    started = time.time()
    tmp = packed = None
    try:
        folder = folder_of(config)
        db_path = Path(config.DB_PATH)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
                raise BackupError(f"De back-upmap {folder} kon niet worden aangemaakt: geen schrijfrechten. "
                                  "Kies een andere map in config.json (backup_folder).", 500)
            raise _reason(e, folder)
        if not _writable(folder):
            raise _not_writable(folder)
        live_size = _live_size(db_path)
        _check_space(folder, live_size + live_size // 2)
        stamp = _stamp(started)
        final = folder / f"datavault-{stamp}{'-handmatig' if manual else ''}.db.gz"
        while final.exists():   # two in the same second: wait for the next one
            time.sleep(0.2)
            stamp = _stamp()
            final = folder / f"datavault-{stamp}{'-handmatig' if manual else ''}.db.gz"
        tmp = folder / f".{final.name}.{os.getpid()}.{threading.get_ident()}.partial"
        packed = folder / f"{tmp.name}.gz"
        try:
            _snapshot(db_path, tmp)
            emptied = _compact(tmp) if compact else {}
        except (sqlite3.Error, OSError) as e:
            if not db_path.is_file():
                raise BackupError("De database is niet bereikbaar. Is de schijf aangesloten?", 503)
            raise _reason(e, folder)
        integrity = _integrity(tmp)
        if integrity != "ok":
            raise BackupError(f"De controle vond fouten in de kopie, dus hij is niet bewaard ({integrity}).", 500)
        version = _schema_version(tmp)
        size = tmp.stat().st_size
        try:
            _compress(tmp, packed)
            _place(packed, final)
        except OSError as e:
            raise _reason(e, folder)
        removed = rotate(folder, KEEP_DAILY if keep_daily is None else keep_daily,
                         KEEP_WEEKLY if keep_weekly is None else keep_weekly)
        result = {
            "name": final.name, "path": str(final), "size": final.stat().st_size, "plain_size": size,
            "created_at": started, "kind": "handmatig" if manual else "auto",
            "schema_version": version, "integrity": "ok", "emptied": emptied,
            "live_size": live_size, "seconds": round(time.time() - started, 3), "removed": removed,
        }
        _last_result, _last_error = result, None
        return result
    except BackupError as e:
        _last_error = {"message": str(e), "at": time.time()}
        raise
    finally:
        for leftover in (tmp, packed if tmp is not None else None):
            if leftover is not None:
                try:
                    leftover.unlink()   # our own temporary copies
                except OSError:
                    pass
        _lock.release()


def status(config, auto_enabled: bool = False, next_check_at: Optional[float] = None) -> Dict[str, Any]:
    """What Instellingen shows: where, how many, the newest, when the next one is due and
    when the running app checks next (next_check_at; None when it does not check)."""
    folder = folder_of(config)
    backups = list_backups(folder)
    regular = [b for b in backups if b["kind"] != "voor-herstel"]
    last = regular[0] if regular else None
    return {
        "folder": str(folder),
        "folder_exists": folder.is_dir(),
        "folder_note": getattr(config, "BACKUP_FOLDER_NOTE", None),
        "auto": auto_enabled,
        "max_age_hours": MAX_AGE // 3600,
        "keep_daily": getattr(config, "BACKUP_KEEP_DAILY", KEEP_DAILY),
        "keep_weekly": getattr(config, "BACKUP_KEEP_WEEKLY", KEEP_WEEKLY),
        "last": last,
        "next_due_at": (last["created_at"] + MAX_AGE) if last else None,
        "next_check_at": next_check_at if auto_enabled else None,
        "check_interval_minutes": CHECK_INTERVAL // 60,
        "keep_manual": KEEP_MANUAL,
        "backups": backups,
        "count": len(regular),
        "total_size": sum(b["size"] for b in backups),
        "running": running(),
        "last_error": _last_error,
        "rebuildable": sorted(REBUILDABLE_TABLES),
        "restore_command": restore_command(config),
    }


def restore_command(config=None) -> str:
    """The Terminal command that lists (and, with a name after it, restores) the backups of
    THIS app: with --config when it runs with another config than code/config.json, so the
    command never reaches a different database."""
    code_dir = Path(__file__).resolve().parent.parent
    command = f"cd {shlex.quote(str(code_dir))} && venv/bin/python restore_backup.py"
    config_path = getattr(config, "config_path", None)
    if config_path is not None:
        config_path = Path(os.path.abspath(config_path))
        if config_path.resolve() != DEFAULT_CONFIG_PATH.resolve():
            command += f" --config {shlex.quote(str(config_path))}"
    return command


# ==================== AUTOMATIC ====================

class AutoBackup:
    """
    Makes a backup when one is due: FIRST_CHECK_DELAY seconds after the app starts, then
    every CHECK_INTERVAL. While the vault (and so the database) is not there it waits for
    the next check. Never two at once (make_backup holds a lock).
    """

    def __init__(self, config, db, interval: float = CHECK_INTERVAL, first_delay: float = FIRST_CHECK_DELAY):
        self.config = config
        self.db = db
        self.interval = interval
        self.first_delay = first_delay
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.next_check_at: Optional[float] = None   # when the thread checks next (Instellingen)

    def start(self) -> None:
        if self._thread is not None:
            return
        self.next_check_at = time.time() + self.first_delay
        self._thread = threading.Thread(target=self._run, name="auto-backup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)

    def check(self) -> Optional[Dict[str, Any]]:
        """One check: a backup when it is due and the database is there."""
        if not Path(self.config.DB_PATH).is_file() or not getattr(self.db, "ready", True):
            return None
        if not is_due(self.config):
            return None
        try:
            result = make_backup(self.config, keep_daily=self.config.BACKUP_KEEP_DAILY,
                                 keep_weekly=self.config.BACKUP_KEEP_WEEKLY)
        except BackupError as e:
            if e.status != 409:
                _say(f"Automatische back-up mislukt: {e}")
            return None
        _say(f"Back-up gemaakt: {result['path']} ({result['size'] // 1024} KB, {result['seconds']} s)")
        return result

    def _run(self) -> None:
        if self._stop.wait(self.first_delay):
            return
        while True:
            try:
                self.check()
            except Exception as e:   # never let the thread die
                _say(f"Automatische back-up mislukt: {e}")
            self.next_check_at = time.time() + self.interval
            if self._stop.wait(self.interval):
                return


# ==================== APP LOCK AND RESTORE ====================

_app_locks: Dict[str, Any] = {}


def lock_path(config) -> Path:
    return Path(config.DB_PATH).parent / LOCK_NAME


def hold_app_lock(config) -> bool:
    """The running app holds a shared lock next to its database; a restore needs it free."""
    path = str(lock_path(config))
    if path in _app_locks:
        return True
    try:
        handle = open(path, "a")
    except OSError:
        return False
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _app_locks[path] = handle
    return True


def release_app_lock(config) -> None:
    handle = _app_locks.pop(str(lock_path(config)), None)
    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass


def running_servers() -> List[str]:
    """DataVault servers (uvicorn with backend.main) running on this Mac, as 'PID command'."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        line = line.strip()
        pid, _, command = line.partition(" ")
        if pid.isdigit() and int(pid) != os.getpid() and "uvicorn" in command and "backend.main" in command:
            found.append(line)
    return found


@contextlib.contextmanager
def _plain(path: Path) -> Iterator[Path]:
    """The backup as a plain SQLite file: itself, or (gzip) unpacked into a temporary file
    that is removed afterwards."""
    if not path.is_file() or path.is_symlink():
        raise BackupError(f"Geen back-upbestand: {path}", 404)
    with open(path, "rb") as handle:
        packed = handle.read(2) == b"\x1f\x8b"
    if not packed:
        yield path
        return
    fd, name = tempfile.mkstemp(prefix="datavault-herstel-", suffix=".db")
    try:
        try:
            with gzip.open(path, "rb") as src, os.fdopen(fd, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        except (OSError, EOFError, gzip.BadGzipFile) as e:
            raise BackupError(f"Deze back-up is beschadigd (uitpakken mislukt: {e})", 400)
        yield Path(name)
    finally:
        try:
            os.unlink(name)   # our own temporary copy
        except OSError:
            pass


def _check_plain(path: Path) -> int:
    try:
        integrity = _integrity(path)
    except sqlite3.Error as e:
        raise BackupError(f"Dit is geen bruikbare database: {e}", 400)
    if integrity != "ok":
        raise BackupError(f"Deze back-up is beschadigd (integrity_check: {integrity})", 400)
    version = _schema_version(path)
    if version is None:
        raise BackupError("Dit is geen DataVault-database (geen schema_version)", 400)
    if version > migrations.LATEST_VERSION:
        raise BackupError(f"Deze back-up komt van een nieuwere versie van de app (schema {version}); "
                          "werk de app eerst bij.", 400)
    return version


def check_backup_file(path: Path) -> int:
    """A backup that can be restored (plain or gzip): an SQLite file that passes
    integrity_check, with a schema this app knows. Returns its schema version."""
    with _plain(Path(path)) as plain:
        return _check_plain(plain)


def _save_live_copy(db_path: Path, folder: Path) -> Path:
    """The full live database (no table emptied), compressed, saved before anything changes.
    Never overwrites. A damaged database is saved too: that may be why it is restored."""
    folder.mkdir(parents=True, exist_ok=True)
    _check_space(folder, 2 * _live_size(db_path))
    final = folder / f"datavault-voor-herstel-{_stamp()}.db.gz"
    while final.exists():
        time.sleep(0.2)
        final = folder / f"datavault-voor-herstel-{_stamp()}.db.gz"
    tmp = folder / f".{final.name}.{os.getpid()}.partial"
    packed = folder / f"{tmp.name}.gz"
    try:
        _snapshot(db_path, tmp)
        conn = sqlite3.connect(str(tmp))
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()
        _compress(tmp, packed)
        _place(packed, final)
        return final
    except sqlite3.Error:
        # A damaged live database may not copy through SQLite: then byte for byte, with its WAL
        raw = folder / final.name[:-len(".gz")]
        for suffix in ("", "-wal"):
            source = Path(f"{db_path}{suffix}")
            if source.exists():
                with open(source, "rb") as src, open(f"{raw}{suffix}", "xb") as dst:
                    shutil.copyfileobj(src, dst)
        return raw
    finally:
        for leftover in (tmp, packed):
            try:
                leftover.unlink()   # our own temporary copies
            except OSError:
                pass


def restore_backup(config, backup: Path, say: Callable[[str], None] = _say) -> Dict[str, Any]:
    """
    Put a backup back as the live database. Only while the app is stopped: refused when
    the app lock is held or a DataVault server runs. The live database is saved in full
    first (datavault-voor-herstel-...); then the backup is copied into it with the
    backup API, and the result is checked. The app's next start brings the schema up to
    date and refills the emptied caches by itself.
    """
    db_path = Path(config.DB_PATH)
    with _plain(Path(backup)) as plain:
        return _restore_plain(config, db_path, Path(backup), plain, _check_plain(plain), say)


def _restore_plain(config, db_path: Path, backup: Path, plain: Path, version: int, say) -> Dict[str, Any]:
    servers = running_servers()
    if servers:
        raise BackupError("DataVault draait nog (" + "; ".join(servers) + "). Stop de app eerst.", 409)
    lock_file = lock_path(config)
    handle = None
    if lock_file.parent.is_dir():
        handle = open(lock_file, "a")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise BackupError("DataVault heeft de database nog open. Stop de app eerst.", 409)
    try:
        saved = None
        if db_path.exists():
            say("Huidige database bewaren als veiligheidskopie…")
            saved = _save_live_copy(db_path, folder_of(config))
            say(f"Veiligheidskopie: {saved}")
        elif not db_path.parent.is_dir():
            raise BackupError(f"De map van de database bestaat niet: {db_path.parent}. Is de schijf aangesloten?", 503)
        say(f"Back-up terugzetten: {backup}")
        source = sqlite3.connect(f"file:{plain}?mode=ro", uri=True)
        try:
            dest = sqlite3.connect(str(db_path), timeout=30)
            try:
                source.backup(dest)
                integrity = dest.execute("PRAGMA integrity_check").fetchall()
            finally:
                dest.close()
        finally:
            source.close()
        if integrity != [("ok",)]:
            raise BackupError(f"Na het terugzetten is de database niet in orde: {integrity[:3]}. "
                              f"De veiligheidskopie staat in {saved}.", 500)
        return {"restored": str(backup), "schema_version": version, "saved": str(saved) if saved else None}
    finally:
        if handle is not None:
            handle.close()
