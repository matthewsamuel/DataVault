"""
DataGatherer - reading a browser's history database from a COPY (Chrome and Safari)

The browser keeps its history database open and locked, and it is the user's live data:
DataVault never opens it in place. snapshot() copies the database (plus its -wal journal,
so the newest visits are in it) into a fresh temporary folder in the app's own cache
folder, the copy is read there, and the temporary copy is removed afterwards (it is the
app's own scratch copy, never an archive file). A database the user picked by hand
(Bestand kiezen) is archived like any export and read from the archive.

Timestamps: Chrome counts microseconds since 1601-01-01 UTC, Safari seconds (a float)
since 2001-01-01 UTC (Mac absolute time).

In test mode (DATAVAULT_TESTING=1) the real browser folders are never used: only
$DATAVAULT_CHROME_DIR / $DATAVAULT_SAFARI_DIR inside the temp directory.
"""
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import quote

from backend.datagatherer import activity_store as store

CHROME_EPOCH_OFFSET_S = 11644473600      # 1601-01-01 -> 1970-01-01
MAC_EPOCH_OFFSET_S = 978307200            # 1970-01-01 -> 2001-01-01
SQLITE_MAGIC = b"SQLite format 3\x00"


class NoAccess(Exception):
    """macOS refused to let DataVault read the file (Full Disk Access)."""


def _testing() -> bool:
    return os.environ.get("DATAVAULT_TESTING") == "1"


def _test_dir(env: str) -> Optional[Path]:
    value = os.environ.get(env)
    if not value:
        return None
    path = Path(value)
    temp = Path(tempfile.gettempdir()).resolve()
    return path if path.resolve().is_relative_to(temp) else None


def chrome_root() -> Optional[Path]:
    if _testing():
        return _test_dir("DATAVAULT_CHROME_DIR")
    return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"


def safari_root() -> Optional[Path]:
    if _testing():
        return _test_dir("DATAVAULT_SAFARI_DIR")
    return Path.home() / "Library" / "Safari"


def chrome_profiles(root: Optional[Path]) -> List[Path]:
    """The History files of every Chrome profile ('Default', 'Profile 1', ...)."""
    if root is None or not root.is_dir():
        return []
    found = []
    try:
        for entry in sorted(os.scandir(root), key=lambda e: e.name):
            if entry.is_dir(follow_symlinks=False) and (entry.name == "Default" or entry.name.startswith("Profile ")):
                history = Path(entry.path) / "History"
                if history.is_file():
                    found.append(history)
    except OSError:
        return []
    return found


@contextmanager
def snapshot(db_file: Path, cache_folder: Path) -> Iterator[Path]:
    """A private copy of a live SQLite database (and its -wal), removed when done."""
    staging = Path(cache_folder) / "datagatherer-snapshots"
    staging.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(dir=staging, prefix="snapshot-"))
    copy = folder / db_file.name
    try:
        try:
            shutil.copyfile(db_file, copy)
            wal = db_file.with_name(db_file.name + "-wal")
            if wal.is_file():
                shutil.copyfile(wal, folder / wal.name)
        except PermissionError as e:
            raise NoAccess(str(e)) from e
        yield copy
    finally:
        for entry in os.scandir(folder):
            os.unlink(entry.path)
        os.rmdir(folder)


def is_sqlite(head: bytes) -> bool:
    return head.startswith(SQLITE_MAGIC)


def _connect(path: Path) -> sqlite3.Connection:
    """
    Read-only. Our own snapshot (with its -wal next to it) is opened normally so SQLite reads
    the journal in; any other file (an archived export) as immutable, so SQLite never writes
    a journal or -shm file next to it.
    """
    if Path(str(path) + "-wal").exists() and Path(path).parent.name.startswith("snapshot-"):
        conn = sqlite3.connect(str(path))
    else:
        conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True)
    conn.execute("PRAGMA query_only = 1")
    return conn


def tables(path: Path) -> set:
    try:
        conn = _connect(path)
        try:
            return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return set()


def chrome_visits(path: Path, since_ms: int = 0, profile: str = "") -> Iterator[Dict[str, Any]]:
    """Every visit in a Chrome History database copy (newer than since_ms)."""
    since_us = (since_ms // 1000 + CHROME_EPOCH_OFFSET_S) * 1000000 if since_ms else 0
    conn = _connect(path)
    try:
        rows = conn.execute("SELECT v.visit_time, u.url, u.title, v.visit_duration, v.transition FROM visits v "
                            "JOIN urls u ON u.id = v.url WHERE v.visit_time > ? ORDER BY v.visit_time", (since_us,))
        for visit_time, url, title, duration, transition in rows:
            if not visit_time or not url:
                continue
            ts = visit_time // 1000 - CHROME_EPOCH_OFFSET_S * 1000
            details: Dict[str, Any] = {"browser": "Chrome"}
            if profile:
                details["profiel"] = profile
            if duration:
                details["duur_s"] = round(duration / 1e6, 1)
            yield store.event("web_visit", ts, store.key("web_visit", ts, url), title=title or url, url=url, details=details)
    except sqlite3.DatabaseError as e:
        raise ValueError(f"Geen Chrome-geschiedenis: {e}") from e
    finally:
        conn.close()


def safari_visits(path: Path, since_ms: int = 0) -> Iterator[Dict[str, Any]]:
    """Every visit in a Safari History.db copy (newer than since_ms), redirects left out."""
    since = since_ms / 1000 - MAC_EPOCH_OFFSET_S if since_ms else -1e12
    conn = _connect(path)
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(history_visits)")}
        redirect = " AND v.redirect_source IS NULL" if "redirect_source" in columns else ""
        rows = conn.execute("SELECT v.visit_time, i.url, v.title FROM history_visits v "
                            "JOIN history_items i ON i.id = v.history_item "
                            f"WHERE v.visit_time > ?{redirect} ORDER BY v.visit_time", (since,))
        for visit_time, url, title in rows:
            if visit_time is None or not url:
                continue
            ts = int(round((visit_time + MAC_EPOCH_OFFSET_S) * 1000))
            yield store.event("web_visit", ts, store.key("web_visit", ts, url), title=title or url, url=url,
                              details={"browser": "Safari"})
    except sqlite3.DatabaseError as e:
        raise ValueError(f"Geen Safari-geschiedenis: {e}") from e
    finally:
        conn.close()
