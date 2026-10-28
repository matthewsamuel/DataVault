"""
DataVault Dashboard - Background jobs

Long work (a duplicate scan of 100k files, hashing, thumbnails for a whole folder)
runs in a worker thread instead of inside an HTTP request, so the server keeps
answering, the page can be reloaded, and the work can be cancelled, paused and
resumed. Every job is a row in the 'jobs' table (migration 8):

    queued -> running -> done | failed | cancelled
                      -> paused             (user; resume continues it)
                      -> waiting_for_vault  (disk unplugged; continues by itself)
                      -> interrupted        (server stopped; resume continues it)

At most one job of a type runs at a time; a second one waits as 'queued'. A job
handler gets a JobContext and calls ctx.check() between steps (cancel, pause and
server shutdown take effect there), ctx.progress() as often as it likes (it is
cheap: memory only, written to the database at most once per PERSIST_INTERVAL),
and ctx.save_checkpoint() for what a resumed run may skip. Handlers here never
move archive files: they only read the archive and write the app's own cache.

If the vault disappears while a job runs (the handler fails, or finishes, while
VAULT_ROOT is gone), the job goes to 'waiting_for_vault', polls for the vault and
then runs the handler again from its checkpoint. A job is only ever 'done' when
its handler finished while the vault was there: partial work is never 'done'.
On app start, jobs left running, queued or waiting become 'interrupted'.
"""
import json
import sqlite3
import threading
import time
import zlib
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Optional, Tuple

QUEUED = "queued"
RUNNING = "running"
PAUSED = "paused"
WAITING_FOR_VAULT = "waiting_for_vault"
CANCELLED = "cancelled"
FAILED = "failed"
DONE = "done"
INTERRUPTED = "interrupted"

STATUSES = (QUEUED, RUNNING, PAUSED, WAITING_FOR_VAULT, CANCELLED, FAILED, DONE, INTERRUPTED)
ACTIVE = (QUEUED, RUNNING, WAITING_FOR_VAULT)      # has (or waits for) a worker thread
RESUMABLE = (PAUSED, INTERRUPTED, FAILED)          # can be continued from its checkpoint
FINISHED = (CANCELLED, FAILED, DONE)

PERSIST_INTERVAL = 1.0       # seconds between progress writes to the database
VAULT_POLL_INTERVAL = 2.0    # seconds between checks for an unplugged vault
ETA_WINDOW = 30.0            # seconds of progress samples behind the ETA
JOBS_TO_KEEP = 100           # finished jobs kept in the list (their results go with them)
SHUTDOWN_WAIT = 3.0


KEEP = object()  # ctx.progress(total=KEEP): leave the total as it is


class JobError(Exception):
    """A request about jobs that cannot be honoured; status_code is the HTTP answer."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class JobStopped(Exception):
    """Raised by ctx.check() when the job must stop: 'cancel', 'pause' or 'shutdown'."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class VaultUnavailable(Exception):
    """A handler may raise this when it notices the vault is gone; the job then waits for it."""


def bounded_map(fn: Callable[[Any], Any], items: Iterable[Any], workers: int = 4,
                window: Optional[int] = None, stop: Optional[Callable[[], bool]] = None,
                ) -> Iterator[Tuple[Any, Any, Optional[BaseException]]]:
    """
    Run fn over items on `workers` threads with at most `window` submitted at once
    (PERF-13: never 94k futures in memory). Yields (item, result, error) in
    completion order. Once stop() is true nothing new is submitted, work that has
    not started is dropped, and the calls already running are still yielded (so
    their result is not lost). Closing the generator early also cancels what has
    not started and waits only for the few calls that are running.
    """
    window = window or workers * 4
    iterator = iter(items)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {}
        try:
            for item in iterator:
                pending[executor.submit(fn, item)] = item
                if len(pending) >= window:
                    break
            while pending:
                if stop is not None and stop():
                    for future in [f for f in pending if f.cancel()]:
                        del pending[future]
                    if not pending:
                        break
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    item = pending.pop(future)
                    try:
                        value, error = future.result(), None
                    except Exception as e:
                        value, error = None, e
                    if stop is None or not stop():
                        nxt = next(iterator, _END)
                        if nxt is not _END:
                            pending[executor.submit(fn, nxt)] = nxt
                    yield item, value, error
        finally:
            for future in pending:
                future.cancel()


_END = object()


@dataclass
class JobType:
    name: str
    label: str                                              # Dutch, for the job dock
    prepare: Callable[[Dict[str, Any]], Dict[str, Any]]     # check + normalise params; raise JobError
    run: Callable[["JobContext"], Dict[str, Any]]           # the work; returns the result
    summarize: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None   # small, for the list
    present: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None     # result as served later
    on_done: Optional[Callable[[Dict[str, Any]], None]] = None
    results_to_keep: int = 5                                 # stored results of this type
    describe: Optional[Callable[[Dict[str, Any]], str]] = None   # what it works on, for the dock
    jobs_to_keep: Optional[int] = None                       # finished jobs of this type kept (None: no own limit)


class _Live:
    """A job that has (or waits for) a worker thread: its state lives in memory."""

    def __init__(self, row: Dict[str, Any]):
        self.row = row
        self.stop_reason: Optional[str] = None
        self.wake = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last_persist = 0.0
        self.dirty = False
        self.samples: Deque[Tuple[float, int]] = deque()


class JobContext:
    """What a job handler gets: its params and checkpoint, progress, and stop checks."""

    def __init__(self, manager: "JobManager", live: _Live):
        self._manager = manager
        self._live = live
        self.job_id: int = live.row["id"]
        self.params: Dict[str, Any] = live.row["params"]
        self.checkpoint: Dict[str, Any] = dict(live.row.get("checkpoint") or {})

    def check(self) -> None:
        """Stop here if the job was cancelled or paused, or the server is stopping."""
        reason = self._live.stop_reason
        if reason:
            raise JobStopped(reason)

    @property
    def stopping(self) -> bool:
        return self._live.stop_reason is not None

    def progress(self, processed: Optional[int] = None, total: Any = KEEP,
                 message: Optional[str] = None) -> None:
        """total=None means 'not known yet' (e.g. while counting); leave it out to keep it."""
        self._manager._progress(self._live, processed, total, message)

    def save_checkpoint(self, data: Dict[str, Any]) -> None:
        self.checkpoint = dict(data)
        self._manager._update(self._live, checkpoint=self.checkpoint, persist=True)

    def vault_available(self) -> bool:
        return self._manager.vault_available()


def _dumps(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _loads(text: Optional[str]) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


class JobManager:
    _COLUMNS = ("id", "type", "params", "status", "processed", "total", "message", "error",
                "checkpoint", "summary", "created_at", "updated_at", "started_at", "finished_at")
    _JSON_COLUMNS = ("params", "checkpoint", "summary")
    # Whether the result is still stored (older ones are pruned, see results_to_keep)
    _SELECT = ", ".join(_COLUMNS) + ", EXISTS(SELECT 1 FROM job_results r WHERE r.job_id = jobs.id)"

    def __init__(self, db, config, types: List[JobType], vault_poll_interval: float = VAULT_POLL_INTERVAL):
        self.db = db
        self.config = config
        self.types: Dict[str, JobType] = {t.name: t for t in types}
        self.vault_poll_interval = vault_poll_interval
        self._lock = threading.RLock()
        self._live: Dict[int, _Live] = {}
        # Jobs that ended while the database was unreachable (vault unplugged): their
        # final state is kept here and written as soon as the database is back
        self._unsaved: Dict[int, _Live] = {}
        self._closed = False

    # ---------- database ----------

    def _row(self, values) -> Dict[str, Any]:
        row = dict(zip(self._COLUMNS, values))
        row["has_result"] = bool(values[len(self._COLUMNS)]) if len(values) > len(self._COLUMNS) else False
        for column in self._JSON_COLUMNS:
            row[column] = _loads(row[column])
        if row["params"] is None:
            row["params"] = {}
        return row

    def _db_get(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self.db._connect() as conn:
            values = conn.execute(
                f"SELECT {self._SELECT} FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._row(values) if values else None

    def _db_list(self, limit: int, job_type: Optional[str]) -> List[Dict[str, Any]]:
        sql = f"SELECT {self._SELECT} FROM jobs"
        args: List[Any] = []
        if job_type:
            sql += " WHERE type = ?"
            args.append(job_type)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.db._connect() as conn:
            return [self._row(v) for v in conn.execute(sql, args).fetchall()]

    def _db_write(self, row: Dict[str, Any]) -> None:
        values = [row[c] for c in self._COLUMNS]
        for i, column in enumerate(self._COLUMNS):
            if column in self._JSON_COLUMNS:
                values[i] = _dumps(values[i])
        with self.db._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {', '.join(f'{c} = ?' for c in self._COLUMNS[1:])} WHERE id = ?",
                values[1:] + [row["id"]],
            )

    def _persist(self, live: _Live) -> None:
        """Best effort: with the vault unplugged the database is gone too; memory stays right."""
        try:
            self._db_write(live.row)
        except (sqlite3.Error, OSError):
            live.dirty = True
            return
        live.dirty = False
        live.last_persist = time.monotonic()

    def _flush_unsaved(self) -> None:
        with self._lock:
            pending = list(self._unsaved.values())
        for live in pending:
            self._persist(live)
            if not live.dirty:
                with self._lock:
                    self._unsaved.pop(live.row["id"], None)

    def _known(self, job_id: int) -> Optional[_Live]:
        """A job whose state in memory is newer than the database (running, or not saved yet)."""
        return self._live.get(job_id) or self._unsaved.get(job_id)

    def _store_result(self, job: Dict[str, Any], result: Dict[str, Any], summary: Optional[Dict[str, Any]]) -> None:
        """Result, summary and status 'done' in one transaction: never 'done' without its result."""
        blob = zlib.compress(json.dumps(result, ensure_ascii=False).encode("utf-8"), 6)
        now = time.time()
        with self.db._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO job_results (job_id, encoding, data) VALUES (?, 'json+zlib', ?)",
                (job["id"], blob),
            )
            conn.execute(
                "UPDATE jobs SET status = ?, summary = ?, processed = ?, total = ?, message = ?, "
                "error = NULL, updated_at = ?, finished_at = ? WHERE id = ?",
                (DONE, _dumps(summary), job["processed"], job["total"], job["message"], now, now, job["id"]),
            )

    def _prune(self, job_type: str) -> None:
        """Keep the newest results of a type and the newest JOBS_TO_KEEP finished jobs."""
        keep = self.types[job_type].results_to_keep if job_type in self.types else 0
        try:
            with self.db._connect() as conn:
                conn.execute(
                    """
                    DELETE FROM job_results WHERE job_id IN (
                        SELECT r.job_id FROM job_results r JOIN jobs j ON j.id = r.job_id
                        WHERE j.type = ? ORDER BY r.job_id DESC LIMIT -1 OFFSET ?
                    )
                    """,
                    (job_type, keep),
                )
                old = [r[0] for r in conn.execute(
                    f"SELECT id FROM jobs WHERE status IN ({','.join('?' * len(FINISHED))}) "
                    "ORDER BY id DESC LIMIT -1 OFFSET ?",
                    (*FINISHED, JOBS_TO_KEEP),
                )]
                own_limit = self.types[job_type].jobs_to_keep if job_type in self.types else None
                if own_limit is not None:
                    # A type that runs often (the automatic index pass) never pushes other jobs out
                    old += [r[0] for r in conn.execute(
                        f"SELECT id FROM jobs WHERE type = ? AND status IN ({','.join('?' * len(FINISHED))}) "
                        "ORDER BY id DESC LIMIT -1 OFFSET ?",
                        (job_type, *FINISHED, own_limit),
                    )]
                conn.executemany("DELETE FROM job_results WHERE job_id = ?", [(i,) for i in old])
                conn.executemany("DELETE FROM jobs WHERE id = ?", [(i,) for i in old])
        except sqlite3.Error:
            pass

    # ---------- startup and shutdown ----------

    def recover(self) -> int:
        """Jobs this process did not start cannot be running: mark them interrupted."""
        now = time.time()
        with self.db._connect() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET status = ?, updated_at = ?, message = ? "
                f"WHERE status IN ({','.join('?' * len(ACTIVE))})",
                (INTERRUPTED, now, "Onderbroken: de server is gestopt. Hervat om verder te gaan.", *ACTIVE),
            )
            return cursor.rowcount

    def shutdown(self, wait: float = SHUTDOWN_WAIT) -> None:
        """Stop every worker at its next check; what is still running is left 'interrupted'."""
        with self._lock:
            self._closed = True
            lives = list(self._live.values())
            for live in lives:
                live.stop_reason = "shutdown"
                live.wake.set()
        deadline = time.monotonic() + wait
        for live in lives:
            if live.thread is not None:
                live.thread.join(max(0.0, deadline - time.monotonic()))
        self._flush_unsaved()
        for live in lives:
            if live.row["status"] in ACTIVE:
                live.row["status"] = INTERRUPTED
                live.row["message"] = "Onderbroken: de server is gestopt. Hervat om verder te gaan."
                live.row["updated_at"] = time.time()
                self._persist(live)

    def vault_available(self) -> bool:
        try:
            return self.config.VAULT_ROOT.is_dir() and self.config.DB_PATH.is_file()
        except OSError:
            return False

    # ---------- public API ----------

    def start(self, job_type: str, params: Any, exclusive: bool = False) -> Tuple[Dict[str, Any], bool]:
        """
        Queue a job. Returns (job, created). The same type with the same params while
        one is still active returns that job instead of a second one (a double click).
        exclusive: refuse (409) while any job of this type is active, instead of queueing.
        """
        if job_type not in self.types:
            raise JobError(400, f"Onbekend type taak: {job_type}")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise JobError(400, "params moet een object zijn")
        params = self.types[job_type].prepare(params)
        with self._lock:
            if self._closed:
                raise JobError(503, "De server stopt; probeer het zo opnieuw")
            for live in self._live.values():
                if live.row["type"] == job_type and live.row["status"] in ACTIVE:
                    if exclusive:
                        raise JobError(409, f"Er loopt al een taak '{self.types[job_type].label}'")
                    if live.row["params"] == params:
                        return self._public(live.row, live), False
            now = time.time()
            with self.db._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO jobs (type, params, status, processed, created_at, updated_at, message) "
                    "VALUES (?, ?, ?, 0, ?, ?, ?)",
                    (job_type, _dumps(params), QUEUED, now, now, "In de wachtrij"),
                )
                job_id = cursor.lastrowid
            row = self._db_get(job_id)
            live = _Live(row)
            self._live[job_id] = live
            self._schedule(job_type)
            return self._public(live.row, live), True

    def get(self, job_id: int) -> Dict[str, Any]:
        self._flush_unsaved()
        with self._lock:
            live = self._known(job_id)
            if live is not None:
                return self._public(live.row, live if job_id in self._live else None)
        row = self._db_get(job_id)
        if row is None:
            raise JobError(404, "Deze taak bestaat niet (meer)")
        return self._public(row, None)

    def list(self, limit: int = 30, job_type: Optional[str] = None) -> List[Dict[str, Any]]:
        self._flush_unsaved()
        try:
            rows = self._db_list(limit, job_type)
        except (sqlite3.Error, OSError):
            if not self._live and not self._unsaved:
                raise
            rows = []  # vault unplugged: the jobs in memory (e.g. waiting for it) still show
        with self._lock:
            out = []
            for row in rows:
                live = self._known(row["id"])
                out.append(self._public(live.row, live if row["id"] in self._live else None)
                           if live else self._public(row, None))
            # A job in memory whose row could not be read (vault just unplugged) still shows
            listed = {row["id"] for row in rows}
            known = {**self._unsaved, **self._live}
            extra = [self._public(l.row, l if i in self._live else None) for i, l in known.items()
                     if i not in listed and (job_type is None or l.row["type"] == job_type)]
        return sorted(out + extra, key=lambda j: j["id"], reverse=True)[:limit]

    def wait(self, job_id: int, statuses=(QUEUED, RUNNING), timeout: Optional[float] = None) -> Dict[str, Any]:
        """Block until the job has left `statuses` (or timeout); returns the job."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            job = self.get(job_id)
            if job["status"] not in statuses or (deadline is not None and time.monotonic() >= deadline):
                return job
            time.sleep(0.05)

    def result(self, job_id: int) -> Dict[str, Any]:
        job = self.get(job_id)
        if job["status"] != DONE:
            raise JobError(409, "Deze taak heeft (nog) geen resultaat")
        with self.db._connect() as conn:
            row = conn.execute("SELECT encoding, data FROM job_results WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobError(410, "Het resultaat van deze taak is niet meer bewaard; start hem opnieuw")
        result = json.loads(zlib.decompress(row[1]).decode("utf-8"))
        present = self.types[job["type"]].present if job["type"] in self.types else None
        return present(result) if present else result

    def cancel(self, job_id: int) -> Dict[str, Any]:
        return self._stop(job_id, "cancel")

    def pause(self, job_id: int) -> Dict[str, Any]:
        return self._stop(job_id, "pause")

    def resume(self, job_id: int) -> Dict[str, Any]:
        with self._lock:
            if self._closed:
                raise JobError(503, "De server stopt; probeer het zo opnieuw")
            # A worker that just stopped is still in _live until its cleanup ran: its row
            # in memory is the newest; the resumed run gets a _Live of its own
            ending = self._live.get(job_id)
            if ending is not None and ending.row["status"] in ACTIVE:
                return self._public(ending.row, ending)
            self._flush_unsaved()
            known = self._unsaved.get(job_id) or ending
            row = dict(known.row) if known else self._db_get(job_id)
            if row is None:
                raise JobError(404, "Deze taak bestaat niet (meer)")
            if row["status"] not in RESUMABLE:
                raise JobError(409, "Alleen een gepauzeerde, onderbroken of mislukte taak kan hervat worden")
            if row["type"] not in self.types:
                raise JobError(409, f"Onbekend type taak: {row['type']}")
            row.update(status=QUEUED, error=None, finished_at=None, updated_at=time.time(),
                       message="In de wachtrij (hervat)")
            self._unsaved.pop(job_id, None)
            live = _Live(row)
            self._live[job_id] = live
            self._persist(live)
            self._schedule(row["type"])
            return self._public(row, live)

    # ---------- internals ----------

    def _stop(self, job_id: int, reason: str) -> Dict[str, Any]:
        with self._lock:
            live = self._live.get(job_id)
            if live is None or live.row["status"] not in ACTIVE:
                # Nothing runs (a worker that just stopped only has its cleanup left)
                self._flush_unsaved()
                unsaved = self._unsaved.get(job_id)
                known = unsaved or live
                row = known.row if known else self._db_get(job_id)
                if row is None:
                    raise JobError(404, "Deze taak bestaat niet (meer)")
                if reason == "cancel" and row["status"] in (PAUSED, INTERRUPTED):
                    # Nothing runs: just close it
                    row.update(status=CANCELLED, updated_at=time.time(), finished_at=time.time(),
                               message="Geannuleerd")
                    if known:
                        self._persist(known)
                        if unsaved and not unsaved.dirty:
                            self._unsaved.pop(job_id, None)
                    else:
                        self._db_write(row)
                    return self._public(row, None)
                raise JobError(409, "Deze taak loopt niet")
            if live.row["status"] == QUEUED and live.thread is None:
                # Never started: settle it here
                del self._live[job_id]
                live.row.update(status=CANCELLED if reason == "cancel" else PAUSED,
                                updated_at=time.time(),
                                message="Geannuleerd" if reason == "cancel" else "Gepauzeerd")
                if reason == "cancel":
                    live.row["finished_at"] = time.time()
                self._persist(live)
                if live.dirty:
                    self._unsaved[job_id] = live
                return self._public(live.row, None)
            if live.stop_reason != "cancel":  # cancel wins over a pause asked for earlier
                live.stop_reason = reason
            live.row["message"] = "Wordt geannuleerd..." if reason == "cancel" else "Wordt gepauzeerd..."
            live.wake.set()
            return self._public(live.row, live)

    def _schedule(self, job_type: str) -> None:
        """Start the oldest queued job of this type when none of that type is running. Caller holds _lock."""
        if self._closed:
            return
        if any(l.thread is not None for l in self._live.values() if l.row["type"] == job_type):
            return
        queued = sorted((l for l in self._live.values()
                         if l.row["type"] == job_type and l.row["status"] == QUEUED and l.thread is None),
                        key=lambda l: l.row["id"])
        if not queued:
            return
        live = queued[0]
        live.thread = threading.Thread(target=self._worker, args=(live,),
                                       name=f"job-{live.row['id']}-{job_type}", daemon=True)
        live.thread.start()

    def _update(self, live: _Live, persist: bool = False, **fields) -> None:
        with self._lock:
            live.row.update(fields)
            live.row["updated_at"] = time.time()
        if persist or live.dirty or time.monotonic() - live.last_persist >= PERSIST_INTERVAL:
            self._persist(live)

    def _progress(self, live: _Live, processed, total, message) -> None:
        row = live.row
        now = time.monotonic()
        if total is not KEEP and total != row["total"]:
            live.samples.clear()  # a new phase: its own rate
            row["total"] = total
        if processed is not None:
            row["processed"] = processed
            if not live.samples or now - live.samples[-1][0] >= 0.5:
                live.samples.append((now, processed))
                while live.samples and now - live.samples[0][0] > ETA_WINDOW:
                    live.samples.popleft()
        if message is not None:
            row["message"] = message
        row["updated_at"] = time.time()
        if live.dirty or now - live.last_persist >= PERSIST_INTERVAL:
            self._persist(live)

    @staticmethod
    def _eta(live: _Live) -> Optional[float]:
        row = live.row
        if row["status"] != RUNNING or not row["total"] or len(live.samples) < 2:
            return None
        (t0, p0), (t1, p1) = live.samples[0], live.samples[-1]
        if t1 - t0 < 2.0 or p1 <= p0:
            return None
        rate = (p1 - p0) / (t1 - t0)
        return max(0.0, (row["total"] - row["processed"]) / rate)

    def _public(self, row: Dict[str, Any], live: Optional[_Live]) -> Dict[str, Any]:
        job_type = self.types.get(row["type"])
        status = row["status"]
        return {
            "id": row["id"],
            "type": row["type"],
            "label": job_type.label if job_type else row["type"],
            "target": job_type.describe(row["params"]) if job_type and job_type.describe else None,
            "params": row["params"],
            "status": status,
            "processed": row["processed"],
            "total": row["total"],
            "message": row["message"],
            "error": row["error"],
            "summary": row["summary"],
            "has_result": status == DONE and bool(row.get("has_result")),
            "checkpoint": row["checkpoint"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "eta_seconds": self._eta(live) if live else None,
            "stopping": bool(live and live.stop_reason),
            "can_cancel": status in ACTIVE or status in (PAUSED, INTERRUPTED),
            "can_pause": status in ACTIVE,
            "can_resume": status in RESUMABLE,
        }

    def _wait_for_vault(self, live: _Live) -> bool:
        """Poll until the vault is back (True) or the job is stopped (False)."""
        # What was counted before the disk went is not progress: the run starts over (from the cache)
        live.samples.clear()
        self._update(live, status=WAITING_FOR_VAULT, persist=True, processed=0, total=None,
                     message="Wachten op het archief (schijf losgekoppeld?). Gaat vanzelf verder.")
        while not live.stop_reason:
            if self.vault_available():
                return True
            live.wake.wait(self.vault_poll_interval)
            live.wake.clear()
        return False

    def _worker(self, live: _Live) -> None:
        job_type = self.types[live.row["type"]]
        try:
            if live.stop_reason:
                self._finish_stopped(live)
                return
            if not self.vault_available() and not self._wait_for_vault(live):
                self._finish_stopped(live)
                return
            while True:
                ctx = JobContext(self, live)
                self._update(live, persist=True, status=RUNNING, error=None,
                             started_at=live.row["started_at"] or time.time(),
                             message=live.row["message"] if live.row["status"] == RUNNING else "Bezig...")
                try:
                    result = job_type.run(ctx)
                    if not self.vault_available():
                        raise VaultUnavailable()
                    summary = job_type.summarize(result) if job_type.summarize else None
                    self._store_result(live.row, result, summary)
                except JobStopped:
                    self._finish_stopped(live)
                    return
                except Exception as e:
                    if live.stop_reason:
                        self._finish_stopped(live)
                        return
                    if isinstance(e, VaultUnavailable) or not self.vault_available():
                        if self._wait_for_vault(live):
                            continue  # again from the checkpoint
                        self._finish_stopped(live)
                        return
                    import traceback
                    traceback.print_exc()
                    detail = getattr(e, "detail", None) or str(e) or type(e).__name__
                    self._update(live, persist=True, status=FAILED, error=detail,
                                 finished_at=time.time(), message="Mislukt")
                    return
                if job_type.on_done:
                    try:
                        job_type.on_done(result)
                    except Exception:
                        pass
                with self._lock:
                    live.row.update(status=DONE, summary=summary, has_result=True,
                                    finished_at=time.time(), updated_at=time.time())
                return
        finally:
            with self._lock:
                job_id = live.row["id"]
                # resume() may already have put a new run of this job in its place
                if self._live.get(job_id) is live:
                    del self._live[job_id]
                    if live.dirty:
                        self._unsaved[job_id] = live
                live.thread = None
            self.db.release_thread()
            self._prune(live.row["type"])
            with self._lock:
                self._schedule(live.row["type"])

    def _finish_stopped(self, live: _Live) -> None:
        reason = live.stop_reason or "cancel"
        if reason == "cancel":
            fields = dict(status=CANCELLED, finished_at=time.time(), message="Geannuleerd")
        elif reason == "pause":
            fields = dict(status=PAUSED, message="Gepauzeerd")
        else:
            fields = dict(status=INTERRUPTED,
                          message="Onderbroken: de server is gestopt. Hervat om verder te gaan.")
        self._update(live, persist=True, **fields)
