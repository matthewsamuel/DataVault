"""
DataVault Dashboard - Database Management
SQLite database for hash caching and undo tracking
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional, List, Dict, Any, Set, Tuple, Iterator
from datetime import datetime
import time
import uuid

from backend import migrations

# How long a writer waits for another thread's write transaction before giving up
BUSY_TIMEOUT_MS = 10000


class Database:
    """
    SQLite database manager.

    One connection per thread (PERF-2/SCAN-DUP-DB-13: no more connect, commit and
    close per call), in WAL mode so readers never wait for a writer, with a
    busy_timeout so two writers queue instead of failing. Every method runs in
    `with self._connect() as conn:`, which commits on success and rolls back on any
    error, so a failed call can never leave a transaction (and its write lock) open.

    The database lives in the vault's cache folder. Before every use the file is
    stat'ed: when it is gone (disk unplugged, vault renamed) that is an
    OperationalError (503 in main.py), and when it is back as another file (the disk
    was mounted again) the thread opens a fresh connection instead of using a stale one.
    """
    
    def __init__(self, db_path: Path, prepare: Optional[Callable[[], None]] = None):
        """
        prepare: called before the database is first opened; it makes the cache folder and
        raises when the vault is not there. With prepare the database opens lazily
        (SCAN-DUP-DB-12, MEDIA-THUMBS-TIMELINE-16): an app started while the disk is not
        mounted comes up anyway, answers 503, and opens (and migrates) the database on the
        first request after the disk is back, without a restart and without ever making
        folders under /Volumes. Without prepare the parent folder is made and the database
        opens at once (tests, scripts).
        """
        self.db_path = db_path
        self._prepare = prepare
        self._ready = False
        self._ready_lock = threading.RLock()
        self._on_ready: List[Callable[[], None]] = []
        self._local = threading.local()
        self._open_lock = threading.Lock()
        self._open: Dict[int, Tuple[threading.Thread, sqlite3.Connection]] = {}
        if prepare is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_ready()
        else:
            try:
                self._ensure_ready()
            except sqlite3.OperationalError:
                pass  # opened on first use once the vault is there

    @property
    def ready(self) -> bool:
        return self._ready

    def when_ready(self, callback: Callable[[], None]) -> None:
        """Run callback once the database is open: now, or right after it first opens."""
        with self._ready_lock:
            if not self._ready:
                self._on_ready.append(callback)
                return
        callback()

    def _ensure_ready(self) -> None:
        """Open the database for the first time: prepare, migrate, WAL, then the callbacks."""
        if self._ready:
            return
        with self._ready_lock:
            if self._ready:
                return
            if self._prepare is not None:
                try:
                    self._prepare()
                except OSError as e:
                    raise sqlite3.OperationalError(f"unable to open database file: {e}") from e
            try:
                self._init_db()
            except sqlite3.Error:
                raise
            except OSError as e:
                raise sqlite3.OperationalError(f"unable to open database file: {e}") from e
            self._ready = True
            callbacks, self._on_ready = self._on_ready, []
        for callback in callbacks:
            try:
                callback()
            except Exception as e:
                print(f"Database ready callback failed: {e}")

    def _init_db(self):
        """Create or upgrade the schema via numbered migrations (see migrations.py)"""
        migrations.migrate(self.db_path)
        conn = sqlite3.connect(str(self.db_path), timeout=BUSY_TIMEOUT_MS / 1000)
        try:
            # Persistent in the file: every later connection is in WAL mode too
            conn.execute("PRAGMA journal_mode=WAL")
        finally:
            conn.close()

    def _thread_connection(self) -> sqlite3.Connection:
        if not self._ready:
            self._ensure_ready()
        try:
            st = os.stat(self.db_path)
        except OSError as e:
            self.release_thread()
            raise sqlite3.OperationalError(f"unable to open database file: {e.strerror or e}") from e
        identity = (st.st_dev, st.st_ino)
        conn = getattr(self._local, "conn", None)
        if conn is not None and self._local.identity != identity:
            self.release_thread()
            conn = None
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path), timeout=BUSY_TIMEOUT_MS / 1000, check_same_thread=False
            )
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            self._local.conn, self._local.identity = conn, identity
            current = threading.current_thread()
            with self._open_lock:
                # Connections of threads that have ended (idle threadpool workers) are closed here
                for key, (thread, other) in list(self._open.items()):
                    if not thread.is_alive():
                        del self._open[key]
                        try:
                            other.close()
                        except sqlite3.Error:
                            pass
                self._open[id(current)] = (current, conn)
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """This thread's connection; commit on success, roll back on any error."""
        conn = self._thread_connection()
        try:
            yield conn
            if conn.in_transaction:
                conn.commit()
        except BaseException as e:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            if isinstance(e, sqlite3.Error) and not isinstance(e, sqlite3.IntegrityError):
                # I/O errors and the like: start over with a fresh connection next time
                self.release_thread()
            raise

    def release_thread(self) -> None:
        """Close this thread's connection (a job thread calls this when it ends)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._open_lock:
            self._open.pop(id(threading.current_thread()), None)
        try:
            conn.close()
        except sqlite3.Error:
            pass

    def close_all(self) -> None:
        """Close every connection (app shutdown)."""
        with self._open_lock:
            items = list(self._open.values())
            self._open.clear()
        for _, conn in items:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    # Rating methods
    def get_rating(self, path: str) -> int:
        """Get current rating for a file"""
        with self._connect() as conn:
            result = conn.execute("SELECT rating FROM file_ratings WHERE path = ?", (path,)).fetchone()
        return result[0] if result else 1000

    def update_rating(self, path: str, new_rating: int):
        """Update rating for a file"""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO file_ratings (path, rating, votes, updated_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    rating = ?,
                    votes = votes + 1,
                    updated_at = CURRENT_TIMESTAMP
            """, (path, new_rating, new_rating))

    def apply_vote(self, path: str, change: int, floor: int = 100, start: int = 1000) -> Tuple[int, int]:
        """Add change to a file's rating (never below floor) and count the vote, in one
        statement, so votes that arrive at the same time can never overwrite each other.
        A file without a rating starts at start. Returns (rating, votes)."""
        with self._connect() as conn:
            rating, votes = conn.execute("""
                INSERT INTO file_ratings (path, rating, votes, updated_at)
                VALUES (?, max(?, ? + ?), 1, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    rating = max(?, rating + ?),
                    votes = votes + 1,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING rating, votes
            """, (path, floor, start, change, floor, change)).fetchone()
        return rating, votes

    def get_all_ratings(self) -> Dict[str, int]:
        """Get all file ratings as a dictionary for bulk access"""
        with self._connect() as conn:
            results = conn.execute("SELECT path, rating FROM file_ratings").fetchall()
        return {row[0]: row[1] for row in results}

    def get_ratings_in_folder(self, folder: str) -> Dict[str, Tuple[int, int]]:
        """Ratings of the files directly inside folder: {path: (rating, votes)}.
        A range query on the primary key, so it stays fast with many ratings."""
        prefix = folder.rstrip("/") + "/"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path, rating, votes FROM file_ratings WHERE path >= ? AND path < ?",
                (prefix, prefix[:-1] + "0"),  # '0' is the character right after '/'
            ).fetchall()
        return {path: (rating, votes) for path, rating, votes in rows if "/" not in path[len(prefix):]}

    # Settings methods
    def get_folder_status(self, path: str) -> bool:
        """Check if a folder is enabled for shows"""
        with self._connect() as conn:
            result = conn.execute("SELECT enabled FROM show_settings WHERE path = ?", (path,)).fetchone()
        return bool(result[0]) if result else True

    def set_folder_status(self, path: str, enabled: bool):
        """Enable/disable a folder for shows"""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO show_settings (path, enabled, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    enabled = ?,
                    updated_at = CURRENT_TIMESTAMP
            """, (path, 1 if enabled else 0, 1 if enabled else 0))

    def get_disabled_show_folders(self) -> List[str]:
        """Folders switched off for Shows (enabled = 0)"""
        with self._connect() as conn:
            rows = conn.execute("SELECT path FROM show_settings WHERE enabled = 0").fetchall()
        return [row[0] for row in rows]

    def set_show_folder_disabled(self, path: str, disabled: bool):
        """Switch a folder off for Shows, or back on (by forgetting it)"""
        with self._connect() as conn:
            if disabled:
                conn.execute("INSERT OR REPLACE INTO show_settings (path, enabled) VALUES (?, 0)", (path,))
            else:
                conn.execute("DELETE FROM show_settings WHERE path = ? AND enabled = 0", (path,))
    
    # Hash caching methods
    #
    # A cached hash is only used while the file is provably unchanged: same algorithm,
    # same inode, size, mtime_ns and ctime_ns (migration 6). Rows from before that
    # migration have algo NULL and are rehashed the next time a scan needs them.

    def get_cached_hashes(self, folder: Path, algo: str) -> Dict[str, Tuple[Tuple[int, int, int, int], str]]:
        """
        Every cached hash below folder in one query:
        {path: ((ino, size, mtime_ns, ctime_ns), hash)}.
        """
        prefix = str(folder).rstrip("/") + "/"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path, ino, size, mtime_ns, ctime_ns, hash FROM file_hashes "
                "WHERE algo = ? AND substr(path, 1, ?) = ?",
                (algo, len(prefix), prefix),
            ).fetchall()
        return {row[0]: ((row[1], row[2], row[3], row[4]), row[5]) for row in rows}

    def store_hashes(self, entries: List[Tuple[str, Tuple[int, int, int, int], str]], algo: str):
        """Store [(path, (ino, size, mtime_ns, ctime_ns), hash)] in one transaction."""
        if not entries:
            return
        now = datetime.now().isoformat(sep=" ")
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO file_hashes
                    (path, size, mtime, hash, algo, ino, mtime_ns, ctime_ns, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (path, fp[1], fp[2] / 1e9, digest, algo, fp[0], fp[2], fp[3], now)
                    for path, fp, digest in entries
                ],
            )

    def prune_hashes(self, folder: Path, existing: Set[str]) -> int:
        """Forget cached hashes below folder for paths that a full scan no longer found."""
        prefix = str(folder).rstrip("/") + "/"
        with self._connect() as conn:
            paths = [
                row[0] for row in conn.execute(
                    "SELECT path FROM file_hashes WHERE substr(path, 1, ?) = ?",
                    (len(prefix), prefix),
                )
            ]
            gone = [(path,) for path in paths if path not in existing]
            conn.executemany("DELETE FROM file_hashes WHERE path = ?", gone)
        return len(gone)
    
    def invalidate_hash(self, path: Path):
        """Remove hash from cache"""
        with self._connect() as conn:
            conn.execute("DELETE FROM file_hashes WHERE path = ?", (str(path),))
    
    # Capture dates (migration 9, backend/media_dates.py). A row is only trusted while
    # the file still has the size and mtime_ns it was read at.

    def get_media_dates(self, paths: List[str]) -> Dict[str, Tuple[int, int, float, str]]:
        """{path: (size, mtime_ns, taken, source)} for the given paths that have a row."""
        found: Dict[str, Tuple[int, int, float, str]] = {}
        with self._connect() as conn:
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                for row in conn.execute(
                    f"SELECT path, size, mtime_ns, taken, source FROM media_dates "
                    f"WHERE path IN ({','.join('?' * len(chunk))})", chunk,
                ):
                    found[row[0]] = (row[1], row[2], row[3], row[4])
        return found

    def store_media_dates(self, rows: List[Tuple[str, int, int, float, str]]) -> None:
        """Store [(path, size, mtime_ns, taken, source)] in one transaction."""
        if not rows:
            return
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO media_dates (path, size, mtime_ns, taken, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(*row, now) for row in rows],
            )

    def media_dates_between(self, start: float, end: float, limit: int = 2000) -> List[Tuple[str, int, int, float, str]]:
        """Rows with start <= taken < end, oldest first."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT path, size, mtime_ns, taken, source FROM media_dates "
                "WHERE taken >= ? AND taken < ? ORDER BY taken LIMIT ?",
                (start, end, limit),
            ).fetchall()

    def media_dates_on_day(self, month: int, day: int, limit: int = 2000) -> List[Tuple[str, int, int, float, str]]:
        """Rows taken on this month and day (local time) of any year."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT path, size, mtime_ns, taken, source FROM media_dates "
                "WHERE strftime('%m-%d', taken, 'unixepoch', 'localtime') = ? LIMIT ?",
                (f"{month:02d}-{day:02d}", limit),
            ).fetchall()

    def prune_media_dates(self, folder: Path, existing: Set[str]) -> int:
        """Forget dates below folder for paths a full walk of it no longer found."""
        prefix = str(folder).rstrip("/") + "/"
        with self._connect() as conn:
            paths = [row[0] for row in conn.execute(
                "SELECT path FROM media_dates WHERE substr(path, 1, ?) = ?", (len(prefix), prefix))]
            gone = [(path,) for path in paths if path not in existing]
            conn.executemany("DELETE FROM media_dates WHERE path = ?", gone)
        return len(gone)

    # App settings (migration 10): small JSON values chosen in the UI

    def get_setting(self, key: str, default: Any = None) -> Any:
        import json
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        import json
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False), time.time()),
            )

    # Recovery actions (migration 12, backend/recovery.py)
    #
    # One action is one user action that moved archive files, with one recovery_moves row
    # per file. Like a delete run, an action only exists once its first file has moved,
    # and it goes once its last file is restored.

    def count_delete_runs(self) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM delete_runs r "
                "WHERE EXISTS (SELECT 1 FROM delete_operations o WHERE o.run_id = r.run_id)"
            ).fetchone()[0]

    def record_recovery_move(
        self,
        action_id: Optional[int],
        kind: str,
        title: str,
        src_path: Path,
        dst_path: Path,
        dst_ino: Optional[int],
        dst_size: Optional[int],
        folder: Optional[str] = None,
        details: Optional[str] = None,
    ) -> int:
        """
        Record one moved file. Without an action_id (or with one that is gone, e.g. undone
        in the meantime) a new action is started in the same transaction. Returns the
        action id the file was recorded under.
        """
        now = time.time()
        with self._connect() as conn:
            exists = action_id is not None and conn.execute(
                "SELECT 1 FROM recovery_actions WHERE id = ?", (action_id,)
            ).fetchone()
            if not exists:
                action_id = conn.execute(
                    "INSERT INTO recovery_actions (kind, title, folder, details, created_at) VALUES (?, ?, ?, ?, ?)",
                    (kind, title, folder, details, now),
                ).lastrowid
            conn.execute(
                "INSERT INTO recovery_moves (action_id, src_path, dst_path, dst_ino, dst_size, moved_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (action_id, str(src_path), str(dst_path), dst_ino, dst_size, now),
            )
        return action_id

    _ACTION_COLUMNS = """
        a.id, a.kind, a.title, a.folder, a.details, a.created_at,
        (SELECT COUNT(*) FROM recovery_moves m WHERE m.action_id = a.id) AS remaining
    """

    @staticmethod
    def _action_row(row) -> Dict[str, Any]:
        return {
            "id": row[0], "kind": row[1], "title": row[2], "folder": row[3],
            "details": row[4], "created_at": row[5], "remaining": row[6],
        }

    def get_recovery_actions(self, limit: int) -> List[Dict[str, Any]]:
        """Actions that still have recorded files, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {self._ACTION_COLUMNS} FROM recovery_actions a
                WHERE EXISTS (SELECT 1 FROM recovery_moves m WHERE m.action_id = a.id)
                ORDER BY a.created_at DESC, a.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._action_row(r) for r in rows]

    def count_recovery_actions(self) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM recovery_actions a "
                "WHERE EXISTS (SELECT 1 FROM recovery_moves m WHERE m.action_id = a.id)"
            ).fetchone()[0]

    def get_recovery_action(self, action_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._ACTION_COLUMNS} FROM recovery_actions a WHERE a.id = ?", (action_id,)
            ).fetchone()
        return self._action_row(row) if row else None

    def get_recovery_moves(self, action_id: int, after_id: int = 0, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """The recorded files of one action (all, or a page: id > after_id, at most limit)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, src_path, dst_path, dst_ino, dst_size, moved_at FROM recovery_moves "
                "WHERE action_id = ? AND id > ? ORDER BY id LIMIT ?",
                (action_id, after_id, -1 if limit is None else limit),
            ).fetchall()
        return [
            {"id": r[0], "src_path": r[1], "dst_path": r[2], "dst_ino": r[3], "dst_size": r[4], "moved_at": r[5]}
            for r in rows
        ]

    def delete_recovery_move(self, move_id: int) -> None:
        """Forget one restored file; its action goes once it has no files left."""
        with self._connect() as conn:
            row = conn.execute("SELECT action_id FROM recovery_moves WHERE id = ?", (move_id,)).fetchone()
            conn.execute("DELETE FROM recovery_moves WHERE id = ?", (move_id,))
            if row:
                conn.execute(
                    "DELETE FROM recovery_actions WHERE id = ? "
                    "AND NOT EXISTS (SELECT 1 FROM recovery_moves WHERE action_id = ?)",
                    (row[0], row[0]),
                )

    # Delete run tracking methods
    #
    # One run is one user action in the duplicates view (one click on 'opruimen'),
    # however many files and requests it takes. A run only exists once its first
    # file has been moved, so an empty run can never stand in the way of undo.
    # Runs are ordered by seq, an AUTOINCREMENT number (migration 7), never by clock time.

    _RUN_COLUMNS = """
        r.seq, r.run_id, r.timestamp, r.created_at, r.total_files, r.folder,
        (SELECT COUNT(*) FROM delete_operations o WHERE o.run_id = r.run_id) AS remaining
    """

    @staticmethod
    def _run_row(row) -> Dict[str, Any]:
        return {
            "seq": row[0],
            "run_id": row[1],
            "timestamp": row[2],
            "created_at": row[3],
            "total_files": row[4],
            "folder": row[5],
            "remaining": row[6],
        }

    def record_delete_operation(
        self, run_id: Optional[str], src_path: Path, dst_path: Path, folder: Optional[str] = None
    ) -> str:
        """
        Record one moved file. Without a run_id (or with one that no longer exists,
        e.g. because it was undone in the meantime) a new run is started in the same
        transaction. Returns the run_id the file was recorded under.
        """
        with self._connect() as conn:
            exists = run_id is not None and conn.execute(
                "SELECT 1 FROM delete_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not exists:
                run_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO delete_runs (run_id, timestamp, total_files, created_at, folder) "
                    "VALUES (?, ?, 0, ?, ?)",
                    (run_id, datetime.now().isoformat(sep=" "), time.time(), folder),
                )
            conn.execute(
                "INSERT INTO delete_operations (run_id, src_path, dst_path, timestamp) VALUES (?, ?, ?, ?)",
                (run_id, str(src_path), str(dst_path), datetime.now().isoformat(sep=" ")),
            )
            conn.execute(
                "UPDATE delete_runs SET total_files = total_files + 1 WHERE run_id = ?", (run_id,)
            )
        return run_id
    
    def get_last_delete_run(self) -> Optional[Dict[str, Any]]:
        """Most recent delete run that still has recorded files"""
        runs = self.get_delete_runs(limit=1)
        return runs[0] if runs else None
    
    def get_delete_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._RUN_COLUMNS} FROM delete_runs r WHERE r.run_id = ?", (run_id,)
            ).fetchone()
        return self._run_row(row) if row else None
    
    def get_delete_operations(self, run_id: str, after_id: int = 0, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """Get the operations of a delete run (all, or a page: id > after_id, at most limit)"""
        with self._connect() as conn:
            results = conn.execute("""
                SELECT id, src_path, dst_path
                FROM delete_operations
                WHERE run_id = ? AND id > ?
                ORDER BY id
                LIMIT ?
            """, (run_id, after_id, -1 if limit is None else limit)).fetchall()
        return [
            {"id": row[0], "src_path": row[1], "dst_path": row[2]}
            for row in results
        ]
    
    def get_delete_runs(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Delete runs that still have recorded files, newest (highest seq) first"""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {self._RUN_COLUMNS} FROM delete_runs r
                WHERE EXISTS (SELECT 1 FROM delete_operations o WHERE o.run_id = r.run_id)
                ORDER BY r.seq DESC
                LIMIT ?
                """,
                (-1 if limit is None else limit,),
            ).fetchall()
        return [self._run_row(row) for row in rows]
    
    def delete_delete_operation(self, op_id: int):
        """Forget one restored file of a run; the run itself goes once it is empty"""
        with self._connect() as conn:
            row = conn.execute("SELECT run_id FROM delete_operations WHERE id = ?", (op_id,)).fetchone()
            conn.execute("DELETE FROM delete_operations WHERE id = ?", (op_id,))
            if row:
                conn.execute("""
                    DELETE FROM delete_runs
                    WHERE run_id = ?
                      AND NOT EXISTS (SELECT 1 FROM delete_operations WHERE run_id = ?)
                """, (row[0], row[0]))
    
    # Keurer methods

    def get_keurer_decided_paths(self, folder: str) -> List[str]:
        """
        Geeft alle al beoordeelde paden terug voor een specifieke map.
        Verplaatsingen tellen niet mee: komt zo'n bestand ooit terug, dan mag
        het opnieuw beoordeeld worden.
        """
        with self._connect() as conn:
            results = conn.execute(
                "SELECT path FROM keurer_decisions WHERE folder = ? AND decision != 'move'",
                (folder,),
            ).fetchall()
        return [row[0] for row in results]

    def record_keurer_decision(
        self,
        path: str,
        folder: str,
        decision: str,
        dest_path: Optional[str] = None,
        dest_ino: Optional[int] = None,
        dest_size: Optional[int] = None,
    ) -> int:
        """
        Sla een houd/verwijder/verplaats-beslissing op. Een houd/verwijder-beslissing
        vervangt alleen een eerdere houd-beslissing voor hetzelfde pad (die heeft niets
        om terug te zetten). Verwijder- en verplaats-beslissingen blijven altijd staan
        (migraties 4 en 5), zodat hun undo nooit verloren gaat.
        dest_path is waar het bestand nu staat (TRASH of doelmap), nodig voor undo;
        dest_ino en dest_size identificeren dat bestand (een rename houdt de inode).
        Geeft het id van de beslissing terug.
        """
        with self._connect() as conn:
            if decision in ("keep", "delete"):
                conn.execute(
                    "DELETE FROM keurer_decisions WHERE path = ? AND folder = ? AND decision = 'keep'",
                    (path, folder),
                )
            cursor = conn.execute(
                """
                INSERT INTO keurer_decisions
                    (path, folder, decision, dest_path, dest_ino, dest_size, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (path, folder, decision, dest_path, dest_ino, dest_size),
            )
            decision_id = cursor.lastrowid
        return decision_id

    _KEURER_COLUMNS = "id, path, folder, decision, dest_path, decided_at, dest_ino, dest_size"

    @staticmethod
    def _keurer_row(row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "path": row[1],
            "folder": row[2],
            "decision": row[3],
            "dest_path": row[4],
            "decided_at": row[5],
            "dest_ino": row[6],
            "dest_size": row[7],
        }

    def get_keurer_decision(self, decision_id: int) -> Optional[Dict[str, Any]]:
        """Een enkele beslissing op id, of None."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._KEURER_COLUMNS} FROM keurer_decisions WHERE id = ?",
                (decision_id,),
            ).fetchone()
        return self._keurer_row(row) if row else None

    def get_recent_keurer_decisions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """De nieuwste beslissingen eerst (voor de undo-stapel)."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._KEURER_COLUMNS} FROM keurer_decisions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._keurer_row(r) for r in rows]

    def delete_keurer_decision_by_id(self, decision_id: int):
        """Verwijder een beslissing (na een geslaagde undo)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM keurer_decisions WHERE id = ?", (decision_id,))

    def get_keurer_moves(self, limit: int) -> List[Dict[str, Any]]:
        """De nieuwste beslissingen die een bestand verplaatsten (weggooien, verplaatsen), voor het herstelcentrum."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._KEURER_COLUMNS} FROM keurer_decisions "
                "WHERE decision IN ('delete', 'move') ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._keurer_row(r) for r in rows]

    def count_keurer_moves(self) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM keurer_decisions WHERE decision IN ('delete', 'move')"
            ).fetchone()[0]

    def get_keurer_stats(self) -> Dict[str, Any]:
        """Geeft streak, dagdoel-voortgang en totaal terug."""
        with self._connect() as conn:
            daily_counts, total, today = self._keurer_counts(conn)

        # Bereken huidige streak (opeenvolgende dagen met >= 20 beslissingen)
        return self._keurer_streak(daily_counts, total, today)

    @staticmethod
    def _keurer_counts(conn: sqlite3.Connection):
        cursor = conn.cursor()

        # Verplaatsingen tellen (zoals altijd) niet mee voor dagdoel en streak
        cursor.execute("SELECT COUNT(*) FROM keurer_decisions WHERE decision != 'move'")
        total = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM keurer_decisions
            WHERE DATE(decided_at, 'localtime') = DATE('now', 'localtime')
              AND decision != 'move'
            """
        )
        today = cursor.fetchone()[0]

        # Dagelijkse tellingen voor streakberekening
        cursor.execute(
            """
            SELECT DATE(decided_at, 'localtime') AS day, COUNT(*) AS cnt
            FROM keurer_decisions
            WHERE decision != 'move'
            GROUP BY day
            ORDER BY day DESC
            LIMIT 400
            """
        )
        daily_counts: Dict[str, int] = {row[0]: row[1] for row in cursor.fetchall()}
        return daily_counts, total, today

    @staticmethod
    def _keurer_streak(daily_counts: Dict[str, int], total: int, today: int) -> Dict[str, Any]:
        from datetime import date, timedelta

        streak = 0
        check_date = date.today()
        today_str = str(check_date)

        # Als dagdoel nog niet bereikt, begin bij gisteren
        if daily_counts.get(today_str, 0) < 20:
            check_date = check_date - timedelta(days=1)

        while True:
            day_str = str(check_date)
            if daily_counts.get(day_str, 0) >= 20:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break

        return {
            "total": total,
            "today": today,
            "goal": 20,
            "streak": streak,
            "goal_reached_today": today >= 20,
        }
