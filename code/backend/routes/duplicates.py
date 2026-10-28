"""
DataVault Dashboard - Duplicates Routes

A folder scan returns groups of byte-identical files with, per group, the copy
that stays under the chosen rule (see scanner.choose_keeper). The client may
pick another copy per group; the server never trusts that choice blindly: right
before every move it checks, under ARCHIVE_LOCK, that the copy that stays still
exists and is byte-for-byte identical. Moved files go to RAW_DUPLICATES (never
deleted) and one user action is one undo run, however many requests it takes.
"""
from fastapi import APIRouter, Body, HTTPException
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import os
import sqlite3
import stat

from backend.config import Config
from backend.scanner import is_hidden
from backend.paths import resolve_in_vault, is_in_vault, vault_folder_or_none
from backend.file_ops import (
    safe_move_with_structure, move_no_overwrite, files_identical, is_inside, ARCHIVE_LOCK,
    FileOperationError, CrossDeviceError, ProtectedPathError,
)

router = APIRouter()

# Global state
config: Config = None
db = None
last_scan_results: List[Dict[str, Any]] = []  # flat {path, duplicate_of} pairs of the last scan

# Most files one move request may carry; the UI sends bigger actions in chunks under one run_id
MAX_BATCH_FILES = 200
RUNS_TO_LIST = 20


def init_router(cfg: Config, database):
    """Initialize router with config and database"""
    global config, db
    config = cfg
    db = database


def _protected_folders() -> List[Path]:
    """
    Folders the duplicates tool never takes files out of (nor accepts a kept original from).
    Checked with is_inside, so other spellings (raw_duplicates, .DATAVAULT_CACHE) count too.
    """
    return [config.DUPLICATES_FOLDER, config.TRASH_FOLDER, config.CACHE_FOLDER, config.DB_PATH]


def _in_protected_folder(path: Path) -> Optional[Path]:
    for folder in _protected_folders():
        if is_inside(path, folder):
            return folder
    return None


def _require_config():
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")


# ==================== SCAN ====================

@router.get("/api/raw_duplicates/progress")
def get_scan_progress_endpoint():
    """Get current scan progress"""
    from backend.progress import get_scan_progress

    progress = get_scan_progress()
    if progress:
        return progress.to_dict()
    return {
        "status": "idle",
        "phase": "Geen scan bezig",
        "total_files": 0,
        "processed_files": 0,
        "duplicates_found": 0,
        "progress_percent": 0
    }

@router.post("/api/raw_duplicates/cancel")
def cancel_scan_endpoint():
    """Cancel the current scan"""
    from backend.progress import cancel_scan

    cancel_scan()
    return {"status": "cancelled"}


@router.get("/api/raw_duplicates")
def get_duplicates_endpoint() -> Dict[str, Any]:
    """Get last scan results"""
    return {
        "duplicates_found": len(last_scan_results),
        "duplicates": last_scan_results
    }


@router.post("/api/duplicates/scan_folder")
def scan_folder_for_duplicates(
    folder_path: str, rule: str = "oldest", prefer_folder: Optional[str] = None
) -> Dict[str, Any]:
    """
    Scan one folder (and its subfolders) for byte-identical media files.

    rule picks the copy that stays in each group: oldest (default), newest,
    shortest or folder (with prefer_folder). The client can re-apply any rule
    itself from the returned copy data, and override per group.

    A plain def: FastAPI runs it in the threadpool, so progress and cancel are
    answered while it runs. One scan at a time (409 otherwise). A cancelled scan
    is a normal answer (cancelled: true); read errors are counted and reported
    (complete: false); a folder that disappears mid-scan (disk unplugged) gives 503.
    """
    global last_scan_results
    from backend.progress import SCAN_LOCK
    from backend.scanner import find_duplicates_in_folder, KEEP_RULES, ScanAborted

    _require_config()

    # Inside the vault first, existence second: the same 403 for anything outside
    folder = resolve_in_vault(folder_path, config, folder=True)
    # RAW_DUPLICATES and TRASH hold what was already moved: groups there could never be cleaned
    if _in_protected_folder(folder) is not None:
        raise HTTPException(status_code=400, detail="Deze map wordt niet op duplicaten gescand")
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    if rule not in KEEP_RULES:
        raise HTTPException(status_code=400, detail=f"Onbekende regel: kies uit {', '.join(KEEP_RULES)}")
    prefer = None
    if rule == "folder":
        if not prefer_folder:
            raise HTTPException(status_code=400, detail="Kies een voorkeursmap voor deze regel")
        prefer = str(resolve_in_vault(prefer_folder, config, folder=True))

    if not SCAN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Er loopt al een duplicatenscan")
    try:
        result = find_duplicates_in_folder(folder, config, db, rule=rule, prefer_folder=prefer)
    except ScanAborted:
        raise HTTPException(status_code=503, detail="De map is tijdens de scan onbereikbaar geworden (schijf losgekoppeld?)")
    finally:
        SCAN_LOCK.release()

    if not result["cancelled"]:
        last_scan_results = [
            {"path": copy["path"], "duplicate_of": group["copies"][group["keep"]]["path"]}
            for group in result["groups"]
            for i, copy in enumerate(group["copies"]) if i != group["keep"]
        ]

    return {
        "folder": str(folder),
        "total_scanned": result["total_files"],
        **result,
    }


# ==================== MOVE (verified, never overwrites) ====================

def _find_keeper(file_path: Path) -> Optional[str]:
    """The original the last folder scan paired with this duplicate."""
    for dup in last_scan_results:
        if dup.get("path") == str(file_path):
            return dup.get("duplicate_of")
    return None


def _hidden(path: Path) -> bool:
    """Hidden the way the scan sees it: a dot name below the vault root, or UF_HIDDEN."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return is_hidden(path, config.VAULT_ROOT, st)


def _os_error(e: OSError, doing: str) -> HTTPException:
    """
    A file that cannot be read (no permission, a read error on an aging disk, gone
    between two checks) refuses only that file. An unplugged disk gives 503.
    """
    from backend.main import VAULT_UNREACHABLE
    if not config.VAULT_ROOT.is_dir():
        return HTTPException(status_code=503, detail=VAULT_UNREACHABLE)
    reason = e.strerror or str(e)
    return HTTPException(status_code=409, detail=f"Kon het bestand niet {doing}: {reason}. Er is niets verplaatst")


def _move_verified(src_path: Path, keeper: Optional[Path]) -> Tuple[Path, bool]:
    """
    Move src_path to RAW_DUPLICATES, only if keeper still exists and is byte-for-byte
    identical. The caller holds ARCHIVE_LOCK across this and recording the move, so
    nothing (another tab, the Keurer) can move the keeper away in between.
    Raises HTTPException, also for read errors; returns (dst_path, was_conflict).
    """
    try:
        _verify(src_path, keeper)
    except OSError as e:
        raise _os_error(e, "lezen") from e
    return _move_to_duplicates(src_path)


def _verify(src_path: Path, keeper: Optional[Path]) -> None:
    """Every check before a move; raises HTTPException (or OSError when a file cannot be read)."""
    if not os.path.lexists(src_path):
        raise HTTPException(status_code=404, detail="File not found")

    if src_path.is_symlink() or not src_path.is_file():
        raise HTTPException(status_code=400, detail="Only regular files can be moved")

    protected = _in_protected_folder(src_path)
    if protected is not None:
        raise HTTPException(status_code=400, detail=f"Refusing files inside {protected.name}")

    if keeper is None:
        raise HTTPException(
            status_code=409,
            detail="Unknown original for this file; scan the folder again"
        )

    if keeper.is_symlink() or not keeper.is_file() or _in_protected_folder(keeper) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Kept original no longer exists: {keeper}. Nothing was moved; scan again"
        )

    if os.path.samefile(src_path, keeper):
        raise HTTPException(status_code=409, detail="File and original are the same file")

    # A hidden copy (dot name or Finder flag) never stays in place of a visible one:
    # the scan, the UI and Finder would no longer show the only copy left
    if _hidden(keeper) and not _hidden(src_path):
        raise HTTPException(
            status_code=409,
            detail="De kopie die blijft is verborgen; kies een zichtbare kopie. Er is niets verplaatst"
        )

    if not files_identical(src_path, keeper):
        raise HTTPException(
            status_code=409,
            detail=f"File is no longer identical to {keeper.name}. Nothing was moved; scan again"
        )


def _move_to_duplicates(src_path: Path) -> Tuple[Path, bool]:
    config.ensure_duplicates_folder()

    try:
        # Compare real locations (resolves /var -> /private/var style aliases);
        # the file itself is not a symlink, so only its folders are resolved
        real_src = src_path.parent.resolve() / src_path.name

        # Keep the structure below RAW for files in RAW, below the vault root otherwise
        if real_src.is_relative_to(config.RAW_FOLDER.resolve()):
            base_folder = config.RAW_FOLDER.resolve()
        else:
            base_folder = config.VAULT_ROOT.resolve()

        # Never overwrites (see file_ops)
        return safe_move_with_structure(
            real_src,
            base_folder,
            config.DUPLICATES_FOLDER.resolve(),
            conflict_suffix="DUP"
        )

    except ProtectedPathError as e:
        raise HTTPException(status_code=400, detail=f"Refused: {str(e)}")

    except CrossDeviceError as e:
        raise HTTPException(status_code=409, detail=f"CRITICAL: Cross-device move blocked for {src_path}: {str(e)}")

    except FileOperationError as e:
        raise HTTPException(status_code=500, detail=f"Failed to move {src_path}: {str(e)}")

    except OSError as e:
        # move_no_overwrite only lets a plain OSError through before the rename itself
        raise _os_error(e, "verplaatsen") from e


def _forget_hashes(*paths: Path) -> None:
    """
    Drop cached hashes of moved paths. Best effort: the move is already recorded, and a
    stale cache row is never trusted anyway (validated on inode, size and times).
    """
    for path in paths:
        try:
            db.invalidate_hash(path)
        except sqlite3.Error:
            pass


def _record_or_move_back(run_id: Optional[str], src_path: Path, dst_path: Path, folder: Optional[str]) -> str:
    """Record the move for undo; if that fails, put the file back so nothing is untracked."""
    try:
        return db.record_delete_operation(run_id, src_path, dst_path, folder)
    except Exception as e:
        try:
            move_no_overwrite(dst_path, src_path, suffix_prefix="RESTORED")
        except FileOperationError as back:
            raise HTTPException(
                status_code=500,
                detail=f"Could not record the move ({e}) nor move it back ({back}); the file is at {dst_path}"
            )
        raise HTTPException(status_code=500, detail=f"Could not record the move, file put back: {e}")


@router.post("/api/duplicates/delete_single")
def delete_single_duplicate(file_path: str, duplicate_of: Optional[str] = None) -> Dict[str, Any]:
    """
    Move a single duplicate file to RAW_DUPLICATES (one file, its own undo run).

    Only after checking, right before the move, that the kept original
    (duplicate_of, or the one from the last scan) still exists and is
    byte-for-byte identical, so the last copy of a photo can never be moved away.
    The UI uses /api/duplicates/move; this stays for single calls.
    """
    _require_config()

    # Validate file is in vault (a plain file, no folders or symlinks: checked below).
    # The cache and other protected folders get their own 400 further down.
    src_path = resolve_in_vault(file_path, config, allow_internal=True)

    keeper_str = duplicate_of or _find_keeper(src_path)
    keeper = resolve_in_vault(keeper_str, config, allow_internal=True) if keeper_str else None

    with ARCHIVE_LOCK:
        dst_path, was_conflict = _move_verified(src_path, keeper)
        run_id = _record_or_move_back(None, src_path, dst_path, None)

    _forget_hashes(src_path, dst_path)

    return {
        "run_id": run_id,
        "moved": True,
        "src_path": str(src_path),
        "dst_path": str(dst_path),
        "duplicate_of": str(keeper),
        "was_conflict": was_conflict
    }


def _bad_request(detail: str):
    raise HTTPException(status_code=400, detail=detail)


def _parse_move_body(body: Any) -> Tuple[List[Tuple[str, List[str]]], Optional[str], Optional[str]]:
    if not isinstance(body, dict):
        _bad_request("Body must be a JSON object")
    groups = body.get("groups")
    if not isinstance(groups, list) or not groups:
        _bad_request("groups must be a non-empty list")
    parsed = []
    total = 0
    for group in groups:
        if not isinstance(group, dict):
            _bad_request("Every group must be an object with keep and move")
        keep, move = group.get("keep"), group.get("move")
        if not isinstance(keep, str) or not keep:
            _bad_request("keep must be a path")
        if not isinstance(move, list) or not move or not all(isinstance(p, str) and p for p in move):
            _bad_request("move must be a non-empty list of paths")
        total += len(move)
        parsed.append((keep, move))
    if total > MAX_BATCH_FILES:
        _bad_request(f"At most {MAX_BATCH_FILES} files per request; send the rest with the same run_id")
    run_id = body.get("run_id")
    if run_id is not None and (not isinstance(run_id, str) or len(run_id) > 64):
        _bad_request("run_id must be a string")
    folder = body.get("folder")
    if folder is not None and not isinstance(folder, str):
        _bad_request("folder must be a path")
    return parsed, run_id, folder


@router.post("/api/duplicates/move")
def move_duplicates(body: Any = Body(...)) -> Dict[str, Any]:
    """
    Move duplicates to RAW_DUPLICATES as one undo run.

    Body: {"groups": [{"keep": path, "move": [path, ...]}, ...],
           "run_id": optional, "folder": optional scanned folder (label for the run)}
    keep is the copy that stays: the scan's choice, another rule's, or the user's
    own pick for that group. Each file is moved only if keep still exists and is
    byte-for-byte identical to it at that moment; otherwise it stays and is listed
    in failed with the reason. The run is created with the first file that is
    actually moved; pass the returned run_id with the next chunk of the same action
    so the whole action is undone at once.
    """
    _require_config()
    groups, run_id, folder = _parse_move_body(body)
    folder_label = None
    if folder:
        label = vault_folder_or_none(folder, config)
        folder_label = str(label) if label else None

    moved: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    recorded = False

    # Every refusal or error stays with its own file: the rest of the batch still runs,
    # and what was moved is always answered with its run_id
    for keep_str, move_paths in groups:
        try:
            keeper = resolve_in_vault(keep_str, config, allow_internal=True)
        except OSError as e:
            keeper_error = _os_error(e, "lezen")
        except HTTPException as e:
            keeper_error = e
        else:
            keeper_error = None
        if keeper_error is not None:
            failed.extend({"path": p, "keep": keep_str, "status": keeper_error.status_code,
                           "detail": keeper_error.detail} for p in move_paths)
            continue

        for path_str in move_paths:
            try:
                src_path = resolve_in_vault(path_str, config, allow_internal=True)
                with ARCHIVE_LOCK:
                    dst_path, was_conflict = _move_verified(src_path, keeper)
                    run_id = _record_or_move_back(run_id, src_path, dst_path, folder_label)
                recorded = True
            except OSError as e:
                e = _os_error(e, "lezen")
                failed.append({"path": path_str, "keep": keep_str, "status": e.status_code, "detail": e.detail})
                continue
            except HTTPException as e:
                failed.append({"path": path_str, "keep": keep_str, "status": e.status_code, "detail": e.detail})
                continue
            _forget_hashes(src_path, dst_path)
            moved.append({
                "path": path_str,
                "src_path": str(src_path),
                "dst_path": str(dst_path),
                "keep": str(keeper),
                "was_conflict": was_conflict,
            })

    try:
        run = db.get_delete_run(run_id) if run_id else None
    except sqlite3.Error:
        # The moves are recorded; only the summary is missing (disk gone just now?)
        return {"run_id": run_id, "moved": moved, "failed": failed, "run": None}
    if not recorded and run is None:
        run_id = None  # nothing moved and no earlier chunk: there is no run to continue

    return {"run_id": run_id, "moved": moved, "failed": failed, "run": run}


# ==================== UNDO ====================

def _paths_allowed(op: Dict[str, Any]) -> bool:
    """Re-validate what the database says before moving anything."""
    return (
        is_inside(Path(op["dst_path"]), config.DUPLICATES_FOLDER)
        and is_in_vault(op["src_path"], config, allow_internal=True)
        and _in_protected_folder(Path(op["src_path"])) is None
    )


def _still_there(op: Dict[str, Any]) -> bool:
    """The moved file is still a regular file at its RAW_DUPLICATES location."""
    try:
        return stat.S_ISREG(os.lstat(op["dst_path"]).st_mode)
    except OSError:
        return False


def _restorable(op: Dict[str, Any]) -> bool:
    return _paths_allowed(op) and _still_there(op)


def _restore_ops(operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Move the files of one run back where they came from. Caller holds ARCHIVE_LOCK.
    Never overwrites: if the original place is taken, the file comes back as
    name_RESTORED_001.ext next to it. Only restored files are forgotten; files that
    could not be restored stay recorded, so they can be tried again later.
    """
    restored = 0
    skipped = 0
    errors = []
    files = []

    for op in operations:
        # src_path is the ORIGINAL location, dst_path is where it went (in RAW_DUPLICATES).
        dst_path = Path(op["dst_path"])  # Current location
        src_path = Path(op["src_path"])  # Original location

        if not _paths_allowed(op):
            errors.append(f"Refused to restore {dst_path}: path outside RAW_DUPLICATES or vault")
            skipped += 1
            continue

        if not _still_there(op):
            skipped += 1
            continue

        try:
            restored_path = move_no_overwrite(dst_path, src_path, suffix_prefix="RESTORED")

        except CrossDeviceError as e:
            errors.append(f"CRITICAL: Cross-device move blocked for {dst_path}: {str(e)}")
            skipped += 1
            continue

        except FileOperationError as e:
            errors.append(f"Failed to restore {dst_path}: {str(e)}")
            skipped += 1
            continue

        except Exception as e:
            errors.append(f"Unexpected error restoring {dst_path}: {str(e)}")
            skipped += 1
            continue

        db.delete_delete_operation(op["id"])
        db.invalidate_hash(dst_path)
        db.invalidate_hash(restored_path)
        restored += 1
        files.append({"src_path": str(src_path), "restored_path": str(restored_path)})

    return {"restored": restored, "skipped": skipped, "errors": errors, "files": files}


@router.post("/api/raw_duplicates/undo_last")
def undo_last_delete_endpoint() -> Dict[str, Any]:
    """
    Undo the most recent delete run (highest seq) that still has something to restore.

    A run whose files are all gone from RAW_DUPLICATES (e.g. moved by hand) is
    passed over instead of blocking every older run; its records are kept, so it
    becomes restorable again if the files come back.
    """
    _require_config()

    with ARCHIVE_LOCK:
        runs = db.get_delete_runs()
        if not runs:
            raise HTTPException(status_code=404, detail="No delete operations to undo")

        run_id = None
        operations = []
        for run in runs:
            ops = db.get_delete_operations(run["run_id"])
            if any(_restorable(op) for op in ops):
                run_id, operations = run["run_id"], ops
                break

        if run_id is None:
            raise HTTPException(
                status_code=404,
                detail="Nothing left to restore: the moved files are no longer in RAW_DUPLICATES"
            )

        outcome = _restore_ops(operations)

    return {"run_id": run_id, **outcome}


@router.get("/api/duplicates/runs")
def list_delete_runs(limit: int = RUNS_TO_LIST) -> Dict[str, Any]:
    """
    The most recent cleanup actions that can still be undone, newest first:
    {runs: [{run_id, seq, created_at, timestamp, folder, total_files, remaining, restorable}]}.
    restorable counts the files that are still in RAW_DUPLICATES.
    """
    _require_config()
    limit = max(1, min(int(limit), 100))
    runs = db.get_delete_runs(limit=limit)
    for run in runs:
        run["restorable"] = sum(1 for op in db.get_delete_operations(run["run_id"]) if _still_there(op))
    return {"runs": runs}


@router.post("/api/duplicates/undo")
def undo_delete_run(body: Any = Body(...)) -> Dict[str, Any]:
    """
    Undo one cleanup action (a run) as a whole: {"run_id": "..."}.
    404 for an unknown or already undone run, 410 if none of its files is still in
    RAW_DUPLICATES (the records are kept in case they come back).
    """
    _require_config()
    run_id = body.get("run_id") if isinstance(body, dict) else None
    if not isinstance(run_id, str) or not run_id or len(run_id) > 64:
        raise HTTPException(status_code=400, detail="run_id must be a string")

    with ARCHIVE_LOCK:
        run = db.get_delete_run(run_id)
        if run is None or run["remaining"] == 0:
            raise HTTPException(status_code=404, detail="Deze actie bestaat niet (meer) of is al teruggezet")
        operations = db.get_delete_operations(run_id)
        if not any(_restorable(op) for op in operations):
            raise HTTPException(
                status_code=410,
                detail="Niets meer terug te zetten: de bestanden staan niet meer in RAW_DUPLICATES"
            )
        outcome = _restore_ops(operations)
        run = db.get_delete_run(run_id)

    return {"run_id": run_id, **outcome, "run": run if run and run["remaining"] else None}
