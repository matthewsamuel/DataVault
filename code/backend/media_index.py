"""
DataVault Dashboard - The media index

One row per photo, video and audio file in the vault (table media, migration 11)
with what the app filters, sorts and groups on: capture date, size, dimensions,
orientation, camera, lens, GPS position, duration (backend/media_meta.py). Features
query it (query(), count(), date_histogram()) instead of walking the disk.

Keeping it up to date (the 'index' job, job_types.index_type -> run_pass):
- The walk uses the one exclusion rule of backend/paths.py and is deterministic
  (sorted, depth first). media_folders keeps every folder's mtime at its last
  listing. A folder whose mtime is unchanged has the same entries, so an
  incremental pass only stats it and takes its subfolders from the table: an
  unchanged vault costs one stat per folder. A changed folder is listed and only
  files whose size or mtime_ns changed (or are new) are read again.
  A full pass (full=True, the 'Bibliotheek bijwerken' button) lists every folder
  and compares every file, which also catches a file edited in place.
- Work is written in bounded batches (executemany). A folder's row is written only
  after its files and its whole subtree (post-order), and never while something
  below it could not be listed in this pass. So a stored row means "everything
  below this folder was in the index at this mtime": a cancelled, paused or
  interrupted pass resumes by itself (what was written is skipped next time, what
  was not is listed again), and a folder that failed is tried again next time.
- A file that is gone from its folder is marked missing (MISSING_NEW, hidden from
  every query), never deleted at once. At the end of a pass, once every folder had
  its turn, a missing file whose (inode, size, birthtime), or (size, content hash),
  matches a file that appeared since is a move made outside the app (Finder): the
  row keeps its id and its ratings and Keurer 'keep' follow it. A row is removed
  only when it was already missing at the end of the pass before (MISSING_CONFIRMED)
  and is still not found: a file moved during a pass, into a folder the walk had
  already listed, is only seen in its new place by the next pass, and must still be
  there to be matched then. Never below a folder that could not be read in this pass.
- Moves made by the app (every move_no_overwrite) update the row, the rating and
  the Keurer decisions at once: record_move(), registered in main.create_app.

Freshness: usable() is true once a pass has finished. fresh_for(roots) checks
the folders of a selection against their stored mtimes (one stat per folder), so
the timeline only uses the index when it matches the disk, and falls back to its
own walk (and asks for an update) when it does not.

Never moves, changes or deletes an archive file: it reads the archive and writes
the app's own database.
"""
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from backend import media_meta
from backend.config import media_type
from backend.paths import ArchiveFilter, excluded_folders, scan_dir

INDEX_JOB = "index"
STATE_KEY = "media_index"     # app_settings: {last_pass_at, last_complete_at, last_full_at, ...}
BATCH_FILES = 400             # files read and written per transaction
READ_WORKERS = 4              # file reads in parallel (I/O bound)
FLUSH_SECONDS = 2.0           # pending work is written at least this often
PROGRESS_SECONDS = 0.5
MOVE_HASH_LIMIT = 500         # files hashed per pass to recognise moves by content
FAILED_FOLDERS_KEPT = 200
# The walk's rules for media_folders rows. An index written under older rules (a
# folder row written before its subfolders were visited) gets one pass that lists
# every folder, as a full pass does, before unchanged folders are trusted again.
WALK_VERSION = 2
UNTRUSTED = -1    # media_folders.mtime_ns of a row the next pass must list again

# Every column of a media row that comes from the file (media_meta.read_metadata)
META_COLUMNS = ("kind", "ext", "size", "mtime_ns", "inode", "birthtime", "taken", "taken_source",
                "width", "height", "orientation", "camera_make", "camera_model", "lens",
                "lat", "lon", "altitude", "duration", "meta_status")
ROW_COLUMNS = ("id", "path", "folder", "name") + META_COLUMNS + ("added_at", "indexed_at", "seen_at")

# media.missing: 0 present; MISSING_NEW gone since the end of the last pass (or moved
# into TRASH/RAW_DUPLICATES by the app); MISSING_CONFIRMED still gone when a pass
# finished, removed at the end of the next one unless it is found (back) by then
MISSING_NEW = 1
MISSING_CONFIRMED = 2

# Bumped on every write anywhere in the index (a cheap "something changed"); a client
# paging through one selection uses selection_version() instead
_generation = 0
_generation_lock = threading.Lock()


def _bump() -> None:
    global _generation
    with _generation_lock:
        _generation += 1


def generation() -> int:
    return _generation


# ---------- state ----------

def state(db) -> Dict[str, Any]:
    try:
        value = db.get_setting(STATE_KEY, {})
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def usable(db) -> bool:
    """True once a pass has finished: the index then holds the whole vault (as of that pass)."""
    return bool(state(db).get("last_pass_at"))


def _prefix_range(folder: str) -> Tuple[str, str]:
    """[low, high) of every path strictly below folder ('0' is the character after '/')."""
    prefix = folder.rstrip("/") + "/"
    return prefix, prefix[:-1] + "0"


def fresh_for(db, roots: Sequence[Path]) -> bool:
    """
    True when the index may answer for these folders: a pass has finished, every
    folder below them still has the mtime it was listed with, and none of them
    failed to list in the last pass. Costs one stat per folder.
    """
    info = state(db)
    if not info.get("last_pass_at"):
        return False
    failed = info.get("failed_folders") or []
    try:
        with db._connect() as conn:
            for root in roots:
                root = str(root)
                low, high = _prefix_range(root)
                if any(f == root or low <= f < high or root.startswith(f.rstrip("/") + "/") for f in failed):
                    return False
                rows = conn.execute(
                    "SELECT path, mtime_ns FROM media_folders WHERE path = ? OR (path >= ? AND path < ?)",
                    (root, low, high)).fetchall()
                if not any(path == root for path, _ in rows):
                    return False
                for path, mtime_ns in rows:
                    if os.stat(path).st_mtime_ns != mtime_ns:
                        return False
    except Exception:
        return False
    return True


# Set by main.create_app: ask for an incremental pass (the index is behind the disk)
_updater: Optional[Callable[[], None]] = None


def set_updater(fn: Optional[Callable[[], None]]) -> None:
    global _updater
    _updater = fn


def request_update() -> None:
    if _updater is not None:
        try:
            _updater()
        except Exception:
            pass


# ---------- which files belong in the index ----------

def indexable(path: Path, config) -> bool:
    """A photo, video or audio file inside the vault that a walk would list (see paths.ArchiveFilter)."""
    from backend.file_ops import is_inside
    path = Path(path)
    try:
        rel = path.relative_to(config.VAULT_ROOT)
    except ValueError:
        return False
    if not rel.parts or any(part.startswith(".") for part in rel.parts):
        return False
    if media_type(path) == "other":
        return False
    return not any(is_inside(path, folder) for folder in excluded_folders(config))


def _row_values(path: str, meta: Dict[str, Any], now: float) -> Tuple:
    folder, name = os.path.split(path)
    return (path, folder, name, *[meta[c] for c in META_COLUMNS], now, now, now)


_UPSERT = f"""
    INSERT INTO media (path, folder, name, {', '.join(META_COLUMNS)}, added_at, indexed_at, seen_at, missing)
    VALUES ({', '.join('?' * (3 + len(META_COLUMNS) + 3))}, 0)
    ON CONFLICT(path) DO UPDATE SET
        {', '.join(f'{c} = excluded.{c}' for c in ('folder', 'name') + META_COLUMNS)},
        indexed_at = excluded.indexed_at, seen_at = excluded.seen_at, missing = 0
"""


def _fallback_meta(path: str, st: os.stat_result, status: str) -> Dict[str, Any]:
    """A row with only what stat knows (a file whose own metadata could not be read)."""
    from backend import media_dates
    birthtime = getattr(st, "st_birthtime", None)
    taken, source = media_dates.file_date(st)
    meta = {c: None for c in META_COLUMNS}
    meta.update(kind=media_type(Path(path)), ext=os.path.splitext(path)[1].lower(), size=st.st_size,
                mtime_ns=st.st_mtime_ns, inode=st.st_ino,
                birthtime=birthtime if birthtime and birthtime > 0 else None,
                taken=taken, taken_source=source, meta_status=status)
    return meta


def read_file(path: str, st: os.stat_result) -> Optional[Dict[str, Any]]:
    """
    The metadata of one file for its row; None when the file is gone (it moved or was
    removed after the listing). A read error gives a row with the file's own dates and
    meta_status 'error', read again on the next pass. Raises OSError only when the
    error may be the vault going away (the caller checks).
    """
    try:
        return media_meta.read_metadata(Path(path), st)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno is None:
            return _fallback_meta(path, st, media_meta.META_NONE)
        raise
    except Exception:
        # A file the parsers do not understand: never let one file stop the pass
        return _fallback_meta(path, st, media_meta.META_NONE)


# ---------- the pass ----------

class _Pass:
    def __init__(self, ctx, config, db, full: bool, errors, read_errors, vault_unavailable):
        self.ctx = ctx
        self.config = config
        self.db = db
        self.full = full
        self.errors = errors                # folders and entries that could not be listed
        self.read_errors = read_errors      # files whose metadata could not be read
        self.vault_unavailable = vault_unavailable
        self.relist = full or state(db).get("walk_version") != WALK_VERSION
        self.filter = ArchiveFilter(config)
        self.root = str(config.VAULT_ROOT)
        self.now = time.time()
        self.counts = defaultdict(int)
        self.failed: List[str] = []
        self.pending_reads: List[Tuple[str, os.stat_result]] = []
        self.pending_folders: List[Tuple[str, Optional[str], int]] = []
        self.listed: Dict[str, Tuple[Optional[str], int]] = {}   # listed, row waits for the subtree
        self.tainted = set()    # folders with something in or below them that failed in this pass
        self.pending_missing: List[str] = []
        self.pending_present: List[str] = []    # rows that were missing and are back
        self.pending_seen: List[str] = []       # listed folders: their present rows are seen now
        self.last_flush = time.monotonic()
        self.last_progress = 0.0

    # -- database --

    def _load(self):
        with self.db._connect() as conn:
            self.known = {path: (mtime_ns, parent, last_scanned) for path, parent, mtime_ns, last_scanned
                          in conn.execute("SELECT path, parent, mtime_ns, last_scanned FROM media_folders")}
            self.retry = {row[0] for row in conn.execute(
                "SELECT DISTINCT folder FROM media WHERE meta_status = 'error' AND missing = 0")}
        self.children: Dict[str, List[str]] = defaultdict(list)
        for path, (_, parent, _) in self.known.items():
            if parent is not None:
                self.children[parent].append(path)

    def _folder_rows(self, folder: str) -> Dict[str, Tuple[int, int, Optional[str], int]]:
        with self.db._connect() as conn:
            return {row[0]: row[1:] for row in conn.execute(
                "SELECT path, size, mtime_ns, meta_status, missing FROM media WHERE folder = ?", (folder,))}

    # -- walking --

    def walk(self) -> None:
        self._load()
        full_started = self.ctx.checkpoint.get("full_started") if self.relist else None
        if self.relist and full_started is None:
            full_started = self.now
            self.ctx.save_checkpoint({**self.ctx.checkpoint, "full_started": full_started})
        self.full_started = full_started
        self.visited = set()
        # (folder, False) visits a folder; (folder, True) comes back to it once its
        # whole subtree was visited, and only then is its row due (post-order)
        stack: List[Tuple[str, bool]] = [(self.root, False)]
        while stack:
            self.ctx.check()
            folder, subtree_done = stack.pop()
            if subtree_done:
                self._commit(folder)
                continue
            self.visited.add(folder)
            subdirs = self._visit(folder)
            if subdirs is not None:
                if folder in self.listed:
                    stack.append((folder, True))
                stack.extend((path, False) for path in sorted(subdirs, reverse=True))
            self._progress()
            if len(self.pending_reads) >= BATCH_FILES or (
                    (self.pending_reads or self.pending_folders)
                    and time.monotonic() - self.last_flush >= FLUSH_SECONDS):
                self.flush()
        self.flush()

    def _visit(self, folder: str) -> Optional[List[str]]:
        try:
            st = os.stat(folder)
        except OSError as e:
            self._fail(folder, e)
            return None
        known = self.known.get(folder)
        unchanged = known is not None and known[0] == st.st_mtime_ns and folder not in self.retry
        if unchanged and self.relist:
            # A full pass lists every folder once; a resumed one skips what it already listed
            unchanged = known[2] >= self.full_started
        if unchanged:
            self.counts["folders_unchanged"] += 1
            return self.children.get(folder, [])
        try:
            entries = scan_dir(Path(folder), self.filter)
        except OSError as e:
            self._fail(folder, e)
            return None
        self.counts["folders_listed"] += 1
        subdirs: List[str] = []
        files: Dict[str, os.stat_result] = {}
        unreadable = set()
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append(entry.path)
                elif entry.is_file(follow_symlinks=False) and media_type(Path(entry.name)) != "other":
                    files[entry.path] = entry.stat(follow_symlinks=False)
            except OSError as e:
                self.errors(entry.path, e)
                unreadable.add(entry.path)
        rows = self._folder_rows(folder)
        for path, fst in sorted(files.items()):
            row = rows.get(path)
            if row is None:
                self.pending_reads.append((path, fst))
                self.counts["new"] += 1
            elif row[0] != fst.st_size or row[1] != fst.st_mtime_ns or row[2] == media_meta.META_ERROR:
                self.pending_reads.append((path, fst))
                self.counts["changed"] += 1
            elif row[3]:
                self.pending_present.append(path)
        for path, row in rows.items():
            if path not in files and path not in unreadable and not row[3]:
                self.pending_missing.append(path)
        self.pending_seen.append(folder)
        parent = None if folder == self.root else os.path.dirname(folder)
        self.listed[folder] = (parent, st.st_mtime_ns)
        if unreadable:
            # A folder with an entry it could not read is listed again next time
            self.failed.append(folder)
            self._taint(folder)
        # Subfolders that are no longer there (removed, renamed, now hidden) are found
        # after the walk: known folders that were never visited
        return subdirs

    def _fail(self, folder: str, error: OSError) -> None:
        self.errors(folder, error)
        self.failed.append(folder)
        self._taint(folder)
        self.vault_unavailable()

    def _taint(self, folder: str) -> None:
        """Something in folder failed: no row for it or any folder above it in this pass."""
        while folder not in self.tainted:
            self.tainted.add(folder)
            if folder == self.root or len(folder) <= len(self.root):
                break
            folder = os.path.dirname(folder)

    def _commit(self, folder: str) -> None:
        """
        The whole subtree of a listed folder was visited: its row is due, unless
        something in or below it failed. Then the row keeps its old mtime (or stays
        absent), so the next pass lists the folder again and retries what failed.
        """
        parent, mtime_ns = self.listed.pop(folder)
        if folder not in self.tainted:
            self.pending_folders.append((folder, parent, mtime_ns))

    def _progress(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_progress < PROGRESS_SECONDS:
            return
        self.last_progress = now
        visited = len(self.visited)
        total = max(len(self.known), visited) if self.known else None
        self.ctx.progress(visited, total,
                          f"Mappen nalopen... {visited:,} · {self.counts['read']:,} bestand(en) gelezen"
                          .replace(",", "."))

    # -- writing --

    def _read_all(self, items: List[Tuple[str, os.stat_result]]) -> List[Tuple]:
        from backend.jobs import bounded_map
        values = []
        stop = lambda: self.ctx.stopping  # noqa: E731
        outcomes = bounded_map(lambda item: read_file(*item), items, workers=READ_WORKERS, stop=stop)
        for (path, st), meta, error in outcomes:
            if error is not None:
                self.read_errors(path, error)
                self.vault_unavailable()
                meta = _fallback_meta(path, st, media_meta.META_ERROR)
            if meta is None:
                continue  # gone since the listing: its folder changed, the next pass sees it
            values.append(_row_values(path, meta, time.time()))
        self.ctx.check()
        return values

    def flush(self) -> None:
        reads, self.pending_reads = self.pending_reads, []
        for i in range(0, len(reads), BATCH_FILES):
            chunk = reads[i:i + BATCH_FILES]
            values = self._read_all(chunk)
            self.vault_unavailable()
            with self.db._connect() as conn:
                conn.executemany(_UPSERT, values)
            self.counts["read"] += len(values)
            self._progress()
        now = time.time()
        with self.db._connect() as conn:
            conn.executemany("UPDATE media SET missing = ? WHERE path = ?",
                             [(MISSING_NEW, p) for p in self.pending_missing])
            conn.executemany("UPDATE media SET missing = 0, seen_at = ? WHERE path = ?",
                             [(now, p) for p in self.pending_present])
            conn.executemany("UPDATE media SET seen_at = ? WHERE folder = ? AND missing = 0",
                             [(now, f) for f in self.pending_seen])
            conn.executemany(
                "INSERT INTO media_folders (path, parent, mtime_ns, last_scanned) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET parent = excluded.parent, mtime_ns = excluded.mtime_ns, "
                "last_scanned = excluded.last_scanned",
                [(path, parent, mtime_ns, now) for path, parent, mtime_ns in self.pending_folders])
        self.counts["missing"] += len(self.pending_missing)
        if self.pending_missing or self.pending_present or reads:
            _bump()   # rows changed (a folder row alone changes no answer)
        self.pending_missing, self.pending_present, self.pending_seen, self.pending_folders = [], [], [], []
        self.last_flush = time.monotonic()

    # -- the end of a pass --

    def _under_failed(self, path: str) -> bool:
        return any(path == f or path.startswith(f.rstrip("/") + "/") for f in self.failed)

    def finish(self) -> None:
        """Folders that are gone, then moves made outside the app, then remove what stays missing."""
        gone = [path for path in self.known
                if path not in self.visited and not self._under_failed(path)]
        if gone:
            with self.db._connect() as conn:
                conn.executemany("UPDATE media SET missing = ? WHERE folder = ? AND missing = 0",
                                 [(MISSING_NEW, p) for p in gone])
                conn.executemany("DELETE FROM media_folders WHERE path = ?", [(p,) for p in gone])
            self.counts["folders_gone"] = len(gone)
            _bump()
        self.ctx.check()
        self.vault_unavailable()
        self.counts["moved"] = match_moves(self.db, self.ctx)
        self.vault_unavailable()
        self.counts["removed"] = purge_missing(self.db, self._under_failed)

    def distrust_unlisted(self) -> None:
        """
        After a pass that listed every folder (full, or the first under WALK_VERSION):
        the rows it could not write (a folder that failed, and every folder above it)
        and the rows below a folder it could not list are older than this pass, maybe
        written under older rules. They get mtime UNTRUSTED, so the next pass lists
        those folders again instead of trusting them, and the rest of the vault is
        trusted from now on even though this pass was not complete.
        """
        with self.db._connect() as conn:
            conn.executemany("UPDATE media_folders SET mtime_ns = ? WHERE path = ?",
                             [(UNTRUSTED, path) for path in self.tainted])
            for folder in set(self.failed):
                low, high = _prefix_range(folder)
                conn.execute("UPDATE media_folders SET mtime_ns = ? WHERE path >= ? AND path < ? "
                             "AND last_scanned < ?", (UNTRUSTED, low, high, self.full_started))


def run_pass(ctx, config, db, full: bool, errors, read_errors, vault_unavailable) -> Dict[str, Any]:
    """
    One index pass over the whole vault (see the top of this module). errors and
    read_errors are error logs (job_types._JobErrors); vault_unavailable() raises
    jobs.VaultUnavailable when the vault went away.
    """
    started = time.monotonic()
    run = _Pass(ctx, config, db, full, errors, read_errors, vault_unavailable)
    ctx.progress(0, None, "Mappen nalopen...")
    run.walk()
    run.finish()
    if run.relist:
        run.distrust_unlisted()
    complete = errors.count == 0
    now = time.time()
    with db._connect() as conn:
        files, missing = conn.execute(
            "SELECT COUNT(*) - COALESCE(SUM(missing != 0), 0), COALESCE(SUM(missing != 0), 0) FROM media").fetchone()
        folders = conn.execute("SELECT COUNT(*) FROM media_folders").fetchone()[0]
    info = {**state(db), "last_pass_at": now, "files": files, "folders": folders,
            "failed_folders": sorted(set(run.failed))[:FAILED_FOLDERS_KEPT],
            "last_seconds": round(time.monotonic() - started, 3)}
    if complete:
        info["last_complete_at"] = now
    if run.relist:
        # Every folder was listed under the current rules, or its row is distrusted
        # (one folder that keeps failing must not make every later pass list them all)
        info["walk_version"] = WALK_VERSION
    if full:
        info["last_full_at"] = now
    db.set_setting(STATE_KEY, info)
    counts = run.counts
    return {
        "full": full,
        "files": files,
        "folders": folders,
        "folders_listed": counts["folders_listed"],
        "folders_unchanged": counts["folders_unchanged"],
        "new": counts["new"],
        "changed": counts["changed"],
        "read": counts["read"],
        "moved": counts["moved"],
        "removed": counts["removed"],
        "still_missing": missing,
        "errors": errors.count,
        "error_samples": errors.samples,
        "read_errors": read_errors.count,
        "read_error_samples": read_errors.samples,
        "complete": complete,
        "seconds": info["last_seconds"],
    }


def pass_message(result: Dict[str, Any]) -> str:
    def n(value: int) -> str:
        return f"{value:,}".replace(",", ".")
    parts = [f"Klaar: {n(result['files'])} bestanden"]
    changes = []
    if result["new"]:
        changes.append(f"{n(result['new'])} nieuw")
    if result["changed"]:
        changes.append(f"{n(result['changed'])} gewijzigd")
    if result["moved"]:
        changes.append(f"{n(result['moved'])} verplaatst")
    if result["removed"]:
        changes.append(f"{n(result['removed'])} weg")
    parts.append(", ".join(changes) if changes else "niets veranderd")
    if result["errors"]:
        parts.append(f"{n(result['errors'])} map(pen) of bestand(en) niet leesbaar")
    if result["read_errors"]:
        parts.append(f"{n(result['read_errors'])} bestand(en) niet te lezen (volgende keer opnieuw)")
    return "; ".join(parts)


# ---------- moves ----------

def _carry_user_data(conn, src: str, dst: str, carry_keep: bool = True, keurer_dest: bool = False) -> None:
    """
    What the user gave a file follows it to its new path: its Shows rating, and (with
    carry_keep: only into the library, never into TRASH or RAW_DUPLICATES, where a Keurer
    'delete' replaces it) a Keurer 'keep', now for the folder it is in. With keurer_dest,
    a Keurer delete/move record that points at src points at dst instead, so its undo
    still finds the file.
    """
    if conn.execute("SELECT 1 FROM file_ratings WHERE path = ?", (src,)).fetchone():
        # dst is a free name (moves never overwrite): a rating there belonged to a file that is gone
        conn.execute("DELETE FROM file_ratings WHERE path = ?", (dst,))
        conn.execute("UPDATE file_ratings SET path = ? WHERE path = ?", (dst, src))
    folder = os.path.dirname(dst)
    if carry_keep and not conn.execute("SELECT 1 FROM keurer_decisions WHERE path = ? AND folder = ? AND decision = 'keep'",
                        (dst, folder)).fetchone():
        conn.execute("UPDATE keurer_decisions SET path = ?, folder = ? WHERE id = ("
                     "SELECT id FROM keurer_decisions WHERE path = ? AND decision = 'keep' "
                     "ORDER BY id DESC LIMIT 1)", (dst, folder, src))
    if keurer_dest:
        conn.execute("UPDATE keurer_decisions SET dest_path = ? WHERE dest_path = ? AND decision != 'keep'",
                     (dst, src))


def record_move(db, config, src, dst) -> None:
    """
    The app moved src to dst (file_ops.on_moved): the index row, the rating and the
    Keurer decisions follow at once. Into TRASH or RAW_DUPLICATES the row stays (with
    its id) but is hidden as missing, so an undo brings it back as it was; passes
    remove it once it stayed there (see purge_missing). Best effort: the next pass recognises the move
    by inode anyway.
    """
    src, dst = str(src), str(dst)
    if db is None or not getattr(db, "ready", True):
        return
    with db._connect() as conn:
        row = conn.execute("SELECT id FROM media WHERE path = ?", (src,)).fetchone()
    shown = indexable(Path(dst), config)
    meta = None
    if shown and row is None:
        try:
            meta = read_file(dst, os.lstat(dst))
        except OSError:
            meta = None
    now = time.time()
    folder, name = os.path.split(dst)
    with db._connect() as conn:
        _carry_user_data(conn, src, dst, carry_keep=shown, keurer_dest=True)
        conn.execute("DELETE FROM media WHERE path = ?", (dst,))
        if row is not None:
            # (indexed_at too: the row changed, see selection_version)
            conn.execute("UPDATE media SET path = ?, folder = ?, name = ?, missing = ?, seen_at = ?, "
                         "indexed_at = ? WHERE id = ?",
                         (dst, folder, name, 0 if shown else MISSING_NEW, now, now, row[0]))
        elif meta is not None:
            conn.execute(_UPSERT, _row_values(dst, meta, now))
    _bump()


def match_moves(db, ctx=None) -> int:
    """
    Missing rows that are the same file as a row that appeared since (a move outside
    the app): same inode, size and birthtime; or, when the old path had a cached
    content hash, same size and content. The old row takes the new path (keeping its
    id), the new row goes, and the rating and Keurer 'keep' follow. Returns the count.
    """
    with db._connect() as conn:
        missing = conn.execute(
            "SELECT id, path, inode, size, birthtime, seen_at, mtime_ns FROM media WHERE missing != 0").fetchall()
        if not missing:
            return 0
        since = min(row[5] for row in missing)
        candidates = conn.execute(
            "SELECT id, path, inode, size, birthtime, added_at FROM media "
            "WHERE missing = 0 AND added_at >= ?", (since,)).fetchall()
    if not candidates:
        return 0
    by_identity: Dict[Tuple, List[Tuple]] = defaultdict(list)
    by_size: Dict[int, List[Tuple]] = defaultdict(list)
    for cand in candidates:
        by_identity[(cand[2], cand[3], cand[4])].append(cand)
        by_size[cand[3]].append(cand)
    claimed = set()
    pairs: List[Tuple[Tuple, Tuple]] = []
    unmatched = []
    for old in missing:
        found = [c for c in by_identity.get((old[2], old[3], old[4]), ())
                 if c[0] not in claimed and c[5] >= old[5]]
        if old[2] is not None and len(found) == 1:
            claimed.add(found[0][0])
            pairs.append((old, found[0]))
        else:
            unmatched.append(old)
    if unmatched:
        pairs.extend(_match_by_content(db, unmatched, by_size, claimed, ctx))
    for old, new in pairs:
        _apply_move(db, old, new)
    if pairs:
        _bump()
    return len(pairs)


def _match_by_content(db, unmatched, by_size, claimed, ctx) -> List[Tuple[Tuple, Tuple]]:
    from backend.scanner import HASH_ALGO, hash_file_xxh3
    pairs = []
    hashed: Dict[str, Optional[str]] = {}
    budget = MOVE_HASH_LIMIT
    for old in unmatched:
        options = [c for c in by_size.get(old[3], ()) if c[0] not in claimed and c[5] >= old[5]]
        if not options:
            continue
        with db._connect() as conn:
            row = conn.execute(
                "SELECT hash FROM file_hashes WHERE path = ? AND algo = ? AND size = ? AND ino IS ? "
                "AND mtime_ns = ?", (old[1], HASH_ALGO, old[3], old[2], old[6])).fetchone()
        if row is None:
            continue  # the old content is not known: no guessing
        matches = []
        for cand in options:
            if cand[1] not in hashed:
                if budget <= 0:
                    break
                budget -= 1
                if ctx is not None:
                    ctx.check()
                try:
                    hashed[cand[1]] = hash_file_xxh3(Path(cand[1]))
                except OSError:
                    hashed[cand[1]] = None
            if hashed[cand[1]] == row[0]:
                matches.append(cand)
        if len(matches) == 1:
            claimed.add(matches[0][0])
            pairs.append((old, matches[0]))
    return pairs


def _apply_move(db, old: Tuple, new: Tuple) -> None:
    columns = ("path", "folder", "name") + META_COLUMNS + ("indexed_at", "seen_at")
    with db._connect() as conn:
        values = conn.execute(f"SELECT {', '.join(columns)} FROM media WHERE id = ?", (new[0],)).fetchone()
        if values is None:
            return
        _carry_user_data(conn, old[1], new[1])
        conn.execute("DELETE FROM media WHERE id = ?", (new[0],))
        # The old row keeps its fingerprint (near_dups); the new row's goes with it
        conn.execute("DELETE FROM media_phash WHERE media_id = ?", (new[0],))
        conn.execute("DELETE FROM near_dup_pairs WHERE a = ?1 OR b = ?1", (new[0],))
        conn.execute(f"UPDATE media SET {', '.join(f'{c} = ?' for c in columns)}, missing = 0 WHERE id = ?",
                     (*values, old[0]))


def purge_missing(db, keep: Callable[[str], bool] = lambda path: False) -> int:
    """
    At the end of a pass: remove the rows that were already missing when the pass
    before ended (MISSING_CONFIRMED), then confirm the ones that went missing since
    (MISSING_NEW), so they are removed at the end of the next pass unless it finds
    them. Rows keep(path) protects (below a folder that could not be read) are left
    as they are. Returns the count removed.
    """
    with db._connect() as conn:
        rows = conn.execute("SELECT id, path, missing FROM media WHERE missing != 0").fetchall()
        gone = [(row[0],) for row in rows if row[2] == MISSING_CONFIRMED and not keep(row[1])]
        confirm = [(MISSING_CONFIRMED, row[0]) for row in rows if row[2] == MISSING_NEW and not keep(row[1])]
        # (Later steps that keep their own per-media tables clean them here, by id)
        conn.executemany("DELETE FROM media_phash WHERE media_id = ?", gone)
        conn.executemany("DELETE FROM near_dup_pairs WHERE a = ?1 OR b = ?1", gone)
        conn.executemany("DELETE FROM media WHERE id = ?", gone)
        conn.executemany("UPDATE media SET missing = ? WHERE id = ? AND missing != 0", confirm)
    if gone:
        _bump()
    return len(gone)


# ---------- queries ----------

ORIENTATIONS = ("landscape", "portrait", "square")
# Ties are broken by id (the order the index found the files in, which follows the
# sorted walk), not by path: a long path in every sort record made a deep page of a
# 100k library three times slower. Names keep the path, so equal names go by folder.
SORTS = {
    "taken": "m.taken ASC, m.id ASC",
    "-taken": "m.taken DESC, m.id DESC",
    "name": "m.name ASC, m.path ASC",
    "-name": "m.name DESC, m.path DESC",
    "size": "m.size ASC, m.id ASC",
    "-size": "m.size DESC, m.id ASC",
    "added": "m.added_at ASC, m.id ASC",
    "-added": "m.added_at DESC, m.id DESC",
    # Shows rating (query() joins file_ratings as r); files nobody voted on come last
    "rating": "r.rating IS NULL, r.rating ASC, m.taken ASC, m.id ASC",
    "-rating": "r.rating IS NULL, r.rating DESC, m.taken DESC, m.id DESC",
    "path": "m.path ASC",
    "random": "random()",
}
MAX_LIMIT = 10000


def _as_list(value) -> List:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _where(filters: Optional[Dict[str, Any]]) -> Tuple[str, List[Any]]:
    """
    SQL for the filters (all optional, combined with AND):
      kind          'photo' | 'video' | 'audio', or a list of them
      ext           '.jpg' or a list (lower case, with the dot)
      date_from     taken >= this epoch;  date_to: taken < this epoch
      folder        an absolute folder: files in it and below (recursive=False: only in it)
      folders       a list of such folders (any of them)
      camera        'Canon EOS 80D' (make + model) or just the model, case-insensitive
      camera_make, camera_model   exact, case-insensitive
      has_gps       True: only with a position; False: only without
      bbox          (south, west, north, east) in degrees
      min_rating    Shows rating at least this (files never voted on are left out)
      orientation   'landscape' | 'portrait' | 'square' (as shown, after rotation)
      month_day     (month, day): taken on that day of any year (local time)
      month_days    a list of such (month, day) pairs (any of them)
      month         1..12: taken in that month of any year (local time)
      exclude_year  leave out this (local) year
      camera_contains  the camera make or model contains this text (case-insensitive)
      keurer        'keep': the Keurer kept it; 'undecided': no Keurer decision on this path
      capture_date  'known': a date from EXIF or the video itself; 'missing': only the
                    date of the file (birth or modification time), or none
    Hidden (missing) rows are never returned.
    """
    filters = filters or {}
    clauses = ["m.missing = 0"]
    args: List[Any] = []

    def any_of(column: str, values: List[Any]) -> None:
        clauses.append(f"{column} IN ({', '.join('?' * len(values))})")
        args.extend(values)

    kinds = _as_list(filters.get("kind"))
    if kinds:
        any_of("m.kind", kinds)
    exts = [str(e).lower() if str(e).startswith(".") else "." + str(e).lower() for e in _as_list(filters.get("ext"))]
    if exts:
        any_of("m.ext", exts)
    if filters.get("date_from") is not None:
        clauses.append("m.taken >= ?")
        args.append(float(filters["date_from"]))
    if filters.get("date_to") is not None:
        clauses.append("m.taken < ?")
        args.append(float(filters["date_to"]))
    folders = [str(f) for f in _as_list(filters.get("folders"))]
    if filters.get("folder") is not None:
        folders.append(str(filters["folder"]))
    if folders:
        parts = []
        for folder in folders:
            folder = folder.rstrip("/") or "/"
            if filters.get("recursive", True):
                low, high = _prefix_range(folder)
                parts.append("(m.path >= ? AND m.path < ?)")
                args.extend([low, high])
            else:
                parts.append("m.folder = ?")
                args.append(folder)
        clauses.append("(" + " OR ".join(parts) + ")")
    if filters.get("camera"):
        clauses.append("(m.camera_model = ? COLLATE NOCASE OR "
                       "(COALESCE(m.camera_make, '') || ' ' || COALESCE(m.camera_model, '')) = ? COLLATE NOCASE)")
        args.extend([filters["camera"], filters["camera"]])
    for key in ("camera_make", "camera_model"):
        if filters.get(key):
            clauses.append(f"m.{key} = ? COLLATE NOCASE")
            args.append(filters[key])
    if filters.get("camera_contains"):
        pattern = "%" + "".join("\\" + c if c in "%_\\" else c for c in str(filters["camera_contains"])) + "%"
        clauses.append("(m.camera_model LIKE ? ESCAPE '\\' OR m.camera_make LIKE ? ESCAPE '\\')")
        args.extend([pattern, pattern])
    if filters.get("has_gps") is True:
        clauses.append("m.lat IS NOT NULL")
    elif filters.get("has_gps") is False:
        clauses.append("m.lat IS NULL")
    if filters.get("bbox") is not None:
        south, west, north, east = (float(v) for v in filters["bbox"])
        clauses.append("m.lat BETWEEN ? AND ?")
        args.extend([south, north])
        if west <= east:
            clauses.append("m.lon BETWEEN ? AND ?")
            args.extend([west, east])
        else:  # across the date line
            clauses.append("(m.lon >= ? OR m.lon <= ?)")
            args.extend([west, east])
    if filters.get("min_rating") is not None:
        clauses.append("EXISTS (SELECT 1 FROM file_ratings r WHERE r.path = m.path AND r.rating >= ?)")
        args.append(int(filters["min_rating"]))
    orientation = filters.get("orientation")
    if orientation:
        if orientation not in ORIENTATIONS:
            raise ValueError(f"orientation must be one of {', '.join(ORIENTATIONS)}")
        op = {"landscape": ">", "portrait": "<", "square": "="}[orientation]
        clauses.append(f"m.width IS NOT NULL AND m.height IS NOT NULL AND m.width {op} m.height")
    month_days = []
    if filters.get("month_day") is not None:
        month_days.append(filters["month_day"])
    month_days.extend(filters.get("month_days") or [])
    if month_days:
        any_of("strftime('%m-%d', m.taken, 'unixepoch', 'localtime')",
               [f"{int(month):02d}-{int(day):02d}" for month, day in month_days])
    if filters.get("month") is not None:
        clauses.append("strftime('%m', m.taken, 'unixepoch', 'localtime') = ?")
        args.append(f"{int(filters['month']):02d}")
    keurer = filters.get("keurer")
    if keurer == "keep":
        clauses.append("EXISTS (SELECT 1 FROM keurer_decisions k WHERE k.path = m.path AND k.decision = 'keep')")
    elif keurer == "undecided":
        clauses.append("NOT EXISTS (SELECT 1 FROM keurer_decisions k WHERE k.path = m.path)")
    elif keurer is not None:
        raise ValueError("keurer must be 'keep' or 'undecided'")
    capture = filters.get("capture_date")
    if capture == "known":
        clauses.append("m.taken_source IN ('exif', 'video')")
    elif capture == "missing":
        clauses.append("(m.taken_source IS NULL OR m.taken_source NOT IN ('exif', 'video'))")
    elif capture is not None:
        raise ValueError("capture_date must be 'known' or 'missing'")
    if filters.get("exclude_year") is not None:
        clauses.append("strftime('%Y', m.taken, 'unixepoch', 'localtime') != ?")
        args.append(f"{int(filters['exclude_year']):04d}")
    return " AND ".join(clauses), args


def _item(row) -> Dict[str, Any]:
    item = dict(zip(ROW_COLUMNS + ("rating", "votes"), row))
    if item["rating"] is None:
        del item["rating"], item["votes"]
    return item


def query(db, filters: Optional[Dict[str, Any]] = None, sort: str = "taken",
          offset: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
    """
    The media rows matching filters (see _where), sorted (SORTS), one page of them.
    Each row is a dict of ROW_COLUMNS, plus rating and votes when the file was voted on.

    A deferred join: the sort and the offset run over the ids and the sort keys only,
    and the full rows are read for the ids of this page. Sorting every full row (paths,
    camera names, ...) up to a deep offset made page 250 of 500 take most of a second.
    """
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    where, args = _where(filters)
    limit = max(0, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    if not limit:
        return []
    order = SORTS[sort]
    # file_ratings is only needed in the sort for the rating sorts
    ratings = "LEFT JOIN file_ratings r ON r.path = m.path " if "r." in order else ""
    columns = ", ".join(f"m.{c}" for c in ROW_COLUMNS)
    with db._connect() as conn:
        ids = [row[0] for row in conn.execute(
            f"SELECT m.id FROM media m {ratings}WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            (*args, limit, offset)).fetchall()]
        if not ids:
            return []
        rows = conn.execute(
            f"SELECT {columns}, r.rating, r.votes FROM media m LEFT JOIN file_ratings r ON r.path = m.path "
            f"WHERE m.id IN ({', '.join('?' * len(ids))})", ids).fetchall()
    by_id = {row[0]: row for row in rows}
    # In the order of the sort (a row removed between the two reads is left out)
    return [_item(by_id[i]) for i in ids if i in by_id]


def count(db, filters: Optional[Dict[str, Any]] = None) -> int:
    where, args = _where(filters)
    with db._connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM media m WHERE {where}", args).fetchone()[0]


def date_histogram(db, filters: Optional[Dict[str, Any]] = None) -> List[Tuple[int, int, int]]:
    """[(year, month, count)] of the matching rows by local capture date, oldest first."""
    where, args = _where(filters)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT CAST(strftime('%Y', m.taken, 'unixepoch', 'localtime') AS INTEGER) AS y, "
            "CAST(strftime('%m', m.taken, 'unixepoch', 'localtime') AS INTEGER) AS mo, COUNT(*) "
            f"FROM media m WHERE {where} AND m.taken IS NOT NULL GROUP BY y, mo ORDER BY y, mo", args).fetchall()
    return [tuple(row) for row in rows]


def year_groups(db, filters: Optional[Dict[str, Any]] = None,
                per_year: int = 60) -> List[Tuple[int, int, List[Dict[str, Any]]]]:
    """
    The matching rows grouped by local capture year, newest year first:
    [(year, rows in that year, the first per_year of them by capture time)].
    For 'on this day' (filters with month_day): one query, whatever the number of years.
    """
    where, args = _where(filters)
    per_year = max(0, min(int(per_year), MAX_LIMIT))
    columns = ", ".join(f"m.{c}" for c in ROW_COLUMNS)
    year = "CAST(strftime('%Y', m.taken, 'unixepoch', 'localtime') AS INTEGER)"
    with db._connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM (SELECT {columns}, r.rating, r.votes, {year} AS y, "
            f"ROW_NUMBER() OVER (PARTITION BY {year} ORDER BY m.taken, m.path) AS rn, "
            f"COUNT(*) OVER (PARTITION BY {year}) AS n "
            f"FROM media m LEFT JOIN file_ratings r ON r.path = m.path WHERE {where} AND m.taken IS NOT NULL) "
            "WHERE rn <= ? ORDER BY y DESC, rn ASC", (*args, max(per_year, 1))).fetchall()
    groups: List[Tuple[int, int, List[Dict[str, Any]]]] = []
    for row in rows:
        y, n = row[-3], row[-1]
        if not groups or groups[-1][0] != y:
            groups.append((y, n, []))
        if per_year:
            groups[-1][2].append(_item(row[:-3]))
    return groups


def facets(db) -> Dict[str, Any]:
    """
    What a filter bar can offer, over every visible row: the capture years, the
    cameras (make, model) and the file types, each with how many files have it.
    """
    year = "CAST(strftime('%Y', taken, 'unixepoch', 'localtime') AS INTEGER)"
    with db._connect() as conn:
        years = conn.execute(f"SELECT {year} AS y, COUNT(*) FROM media WHERE missing = 0 AND taken IS NOT NULL "
                             "GROUP BY y ORDER BY y DESC").fetchall()
        cameras = conn.execute(
            "SELECT camera_make, camera_model, COUNT(*) AS n FROM media WHERE missing = 0 "
            "AND (camera_make IS NOT NULL OR camera_model IS NOT NULL) "
            "GROUP BY camera_make, camera_model ORDER BY n DESC, camera_make, camera_model").fetchall()
        exts = conn.execute("SELECT kind, ext, COUNT(*) AS n FROM media WHERE missing = 0 "
                            "GROUP BY kind, ext ORDER BY n DESC, ext").fetchall()
    return {"years": [list(r) for r in years],
            "cameras": [{"make": make, "model": model, "count": n} for make, model, n in cameras],
            "exts": [{"kind": kind, "ext": ext, "count": n} for kind, ext, n in exts]}


def selection_info(db, filters: Optional[Dict[str, Any]] = None) -> Tuple[int, str]:
    """
    (count, version) of the rows matching filters, in one read. The version is a short
    tag that changes when those rows change (a row added, removed, hidden, shown again,
    re-read or moved), and stays the same for writes elsewhere in the index or a pass
    that changes nothing. For a client that pages through one selection. Ratings are
    not part of it.
    """
    where, args = _where(filters)
    with db._connect() as conn:
        n, ids, newest = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(m.id), 0), COALESCE(MAX(m.indexed_at), 0) FROM media m WHERE {where}",
            args).fetchone()
    return n, f"{n:x}.{ids:x}.{int(newest * 1000):x}"


def selection_version(db, filters: Optional[Dict[str, Any]] = None) -> str:
    """The version of selection_info() alone."""
    return selection_info(db, filters)[1]


def timeline_page(db, roots: Sequence[Path], offset: int, limit: Optional[int]) -> List[Tuple]:
    """(taken, path, size, kind, taken_source) of the photos and videos below roots, by date."""
    where, args = _where({"kind": ["photo", "video"], "folders": [str(r) for r in roots]})
    sql = (f"SELECT m.taken, m.path, m.size, m.kind, m.taken_source FROM media m WHERE {where} "
           "ORDER BY m.taken ASC, m.path ASC")
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args = [*args, int(limit), int(offset)]
    elif offset:
        sql += " LIMIT -1 OFFSET ?"
        args = [*args, int(offset)]
    with db._connect() as conn:
        return conn.execute(sql, args).fetchall()


def random_rows(db, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    return query(db, filters, sort="random", limit=limit)


def stats(db) -> Dict[str, Any]:
    """Counts for the library status: files per kind, hidden rows, folders."""
    with db._connect() as conn:
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM media WHERE missing = 0 GROUP BY kind").fetchall())
        missing = conn.execute("SELECT COUNT(*) FROM media WHERE missing != 0").fetchone()[0]
        folders = conn.execute("SELECT COUNT(*) FROM media_folders").fetchone()[0]
    return {"files": sum(kinds.values()), "photos": kinds.get("photo", 0), "videos": kinds.get("video", 0),
            "audio": kinds.get("audio", 0), "missing": missing, "folders": folders}


# ---------- automatic passes ----------

AUTO_POLL_SECONDS = 5.0        # how often the vault is checked (start, unplug, return)
AUTO_INTERVAL_SECONDS = 1800.0  # an incremental pass at least this often while the app runs


class AutoIndexer:
    """
    Queues an incremental pass (params {'auto': True}) when the app starts with the
    vault there, when the vault comes back after it was gone, every
    AUTO_INTERVAL_SECONDS, and when request() is called (the timeline found the index
    behind the disk). Never two at once: a running pass of any kind is left alone.

    What the user does with a pass sticks: after they paused or cancelled the latest
    one, no automatic pass starts until the app starts again or the vault comes back
    (a paused pass is theirs to resume; a manual pass they start ends the hold).
    """

    def __init__(self, manager, poll: float = AUTO_POLL_SECONDS, interval: float = AUTO_INTERVAL_SECONDS):
        self.manager = manager
        self.poll = poll
        self.interval = interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._requested = False
        self._thread: Optional[threading.Thread] = None
        self._since = time.time()        # the app's start or the vault's latest return
        self._superseded = set()         # passes this indexer cancelled itself

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="media-index-auto", daemon=True)
            self._thread.start()

    def stop(self, wait: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(wait)

    def request(self) -> None:
        self._requested = True
        self._wake.set()

    def _loop(self) -> None:
        was_available = False
        last_start = None
        try:
            while not self._stop.is_set():
                available = self.manager.vault_available()
                if available and not was_available:
                    self.vault_returned()
                due = available and (not was_available or self._requested or last_start is None
                                     or time.monotonic() - last_start >= self.interval)
                if due:
                    self._requested = False
                    if self.start_pass():
                        last_start = time.monotonic()
                    elif last_start is None:
                        last_start = time.monotonic()
                was_available = available
                self._wake.wait(self.poll)
                self._wake.clear()
        finally:
            try:
                self.manager.db.release_thread()
            except Exception:
                pass

    def vault_returned(self) -> None:
        """The app started or the vault came back: a pause or cancel from before no longer holds."""
        self._since = time.time()

    def held(self, jobs: List[Dict[str, Any]]) -> bool:
        """True when the user paused or cancelled the latest pass since the app start or vault return."""
        from backend.jobs import CANCELLED, PAUSED
        latest = next((job for job in jobs if job["id"] not in self._superseded), None)
        if latest is None:
            return False
        if latest["status"] == PAUSED:
            return (latest.get("updated_at") or 0) >= self._since
        if latest["status"] == CANCELLED:
            return (latest.get("finished_at") or 0) >= self._since
        return False

    def start_pass(self) -> bool:
        """
        Queue an automatic pass unless one (of any kind) is active or the user holds
        them (held()). True when one was queued.
        """
        from backend.jobs import ACTIVE, INTERRUPTED, PAUSED, JobError
        try:
            jobs = self.manager.list(limit=100, job_type=INDEX_JOB)   # newest first
            if any(job["status"] in ACTIVE for job in jobs) or self.held(jobs):
                return False
            self.manager.start(INDEX_JOB, {"auto": True}, exclusive=True)
            # An automatic pass that a restart interrupted, or that was paused before
            # the app started or the vault came back, is superseded by this one
            # (the new pass finishes what it left: see run_pass)
            for job in jobs:
                if (job.get("params") or {}).get("auto") and job["status"] in (INTERRUPTED, PAUSED):
                    self._superseded.add(job["id"])
                    try:
                        self.manager.cancel(job["id"])
                    except JobError:
                        pass
            return True
        except Exception:
            return False
