"""
DataVault Dashboard - Recovery center routes (Herstelcentrum)

GET  /api/recovery          every action that moved archive files, newest first, with per
                            file whether it can go back and, if not, why (in Dutch)
POST /api/recovery/undo     undo one action recorded through backend.recovery.ActionRecorder

Keurer decisions and duplicate runs keep their own undo endpoints; their entries name
that endpoint in 'undo', so the page always calls the endpoint that owns the record.
See backend/recovery.py for how a new kind of action plugs in.
"""
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query

from backend import recovery
from backend.config import Config
from backend.file_ops import is_inside
from backend.paths import is_in_vault
from backend.routes import duplicates as duplicates_routes
from backend.routes import keurer as keurer_routes

router = APIRouter()

config: Config = None
db = None

MAX_LIMIT = 200


def init_router(cfg: Config, database):
    global config, db
    config = cfg
    db = database
    recovery.register_provider(recovery.Provider(
        name="keurer", newest=_keurer_newest, entry=_keurer_entry, count=database.count_keurer_moves))
    recovery.register_provider(recovery.Provider(
        name="duplicates", newest=_runs_newest, entry=_run_entry, count=database.count_delete_runs))
    recovery.register_provider(recovery.actions_provider(database, cfg))


def _require():
    if not config or not db:
        raise HTTPException(status_code=500, detail="Configuratie niet geïnitialiseerd")


# ==================== KEURER ====================

def _sqlite_utc(value: Any) -> float:
    """keurer_decisions.decided_at is CURRENT_TIMESTAMP: 'YYYY-MM-DD HH:MM:SS' in UTC."""
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _keurer_newest(limit: int):
    return [(_sqlite_utc(rec["decided_at"]), rec) for rec in db.get_keurer_moves(limit)]


def _keurer_state(rec: Dict[str, Any]) -> str:
    state = keurer_routes._dest_state(rec)
    if state == "ok" and os.path.lexists(rec["path"]):
        return "occupied"
    return state


def _keurer_entry(rec: Dict[str, Any]) -> Dict[str, Any]:
    delete = rec["decision"] == "delete"
    # get_keurer_moves leaves out old decisions without dest_path: those can never go back
    dest = rec["dest_path"]
    place = "de prullenmand" if delete else recovery.place_of(dest, config)
    file = recovery.file_entry(rec["path"], dest, _keurer_state(rec), place)
    return {
        "key": f"keurer:{rec['id']}",
        "source": "keurer",
        "label": "Keurer",
        "title": "Naar de prullenmand" if delete else f"Verplaatst naar {Path(dest).parent.name or 'een andere map'}",
        "folder": rec["folder"],
        "created_at": _sqlite_utc(rec["decided_at"]),
        **recovery.summarize([file], 1),
        "files": [file],
        "note": None,
        "undo": {"url": "/api/keurer/undo", "body": {"id": rec["id"]}},
    }


# ==================== DUPLICATES ====================

def _run_time(run: Dict[str, Any]) -> float:
    if run.get("created_at"):
        return float(run["created_at"])
    try:
        return datetime.fromisoformat(str(run.get("timestamp"))).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _runs_newest(limit: int):
    return [(_run_time(run), run) for run in db.get_delete_runs(limit=limit)]


def _op_state(op: Dict[str, Any], checks: recovery.FolderChecks) -> str:
    src, dst = op["src_path"], op["dst_path"]
    dst_entry = recovery.lstat_or_none(dst)
    src_entry = recovery.lstat_or_none(src)
    # duplicates_routes._paths_allowed, split per path so each half is done once per folder
    allowed = (
        checks.check("dup_dst", dst, dst_entry,
                     lambda: is_inside(Path(dst), config.DUPLICATES_FOLDER))
        and checks.check("dup_src", src, src_entry,
                         lambda: is_in_vault(src, config, allow_internal=True)
                         and duplicates_routes._in_protected_folder(Path(src)) is None)
    )
    if not allowed:
        return "refused"
    if dst_entry is None or not stat.S_ISREG(dst_entry.st_mode):
        return "missing"
    # The duplicates undo never overwrites: a taken name gets _RESTORED_001 next to it
    return "beside" if src_entry is not None else "ok"


def _still_regular(path: str) -> bool:
    entry = recovery.lstat_or_none(path)
    return entry is not None and stat.S_ISREG(entry.st_mode)


def _run_entry(run: Dict[str, Any]) -> Dict[str, Any]:
    checks = recovery.FolderChecks(config)
    listed = recovery.list_files(
        recovery.paged(lambda after, limit: db.get_delete_operations(run["run_id"], after, limit)),
        run["remaining"],
        lambda op: _op_state(op, checks),
        lambda op, state: recovery.file_entry(op["src_path"], op["dst_path"], state, "RAW_DUPLICATES"),
        # Beyond the shown files: a file that is no longer in RAW_DUPLICATES needs no path checks
        lambda op: _still_regular(op["dst_path"]) and _op_state(op, checks) in recovery.RESTORABLE_STATES,
    )
    n = listed["file_count"]
    return {
        "key": f"duplicates:{run['run_id']}",
        "source": "duplicates",
        "label": "Duplicaten",
        "title": f"{n} duplica{'at' if n == 1 else 'ten'} naar RAW_DUPLICATES",
        "folder": run.get("folder"),
        "created_at": _run_time(run),
        **listed,
        "note": "Staat er op de oude plek intussen een ander bestand, dan komt het teruggezette bestand "
                "ernaast (naam_RESTORED_001); er wordt niets overschreven.",
        "undo": {"url": "/api/duplicates/undo", "body": {"run_id": run["run_id"]}},
    }


# ==================== ROUTES ====================

@router.get("")
def list_recovery(offset: int = Query(0, ge=0, le=2 ** 62), limit: int = Query(50, ge=1)) -> Dict[str, Any]:
    """Every action that can be (or could have been) undone, newest first."""
    _require()
    return recovery.entries(offset=offset, limit=min(limit, MAX_LIMIT))


@router.post("/undo")
def undo_recovery_action(body: Any = Body(...)) -> Dict[str, Any]:
    """Undo one action from the shared recovery tables: {"action_id": int}."""
    _require()
    action_id = body.get("action_id") if isinstance(body, dict) else None
    if not isinstance(action_id, int) or isinstance(action_id, bool) or not 0 < action_id < 2 ** 63:
        raise HTTPException(status_code=400, detail="Ontbrekend of ongeldig action_id")
    try:
        return recovery.undo_action(db, config, action_id)
    except recovery.UndoError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
