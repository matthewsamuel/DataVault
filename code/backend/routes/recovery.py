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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query

from backend import recovery
from backend.config import Config
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
    if not rec["dest_path"]:
        return "unknown"
    state = keurer_routes._dest_state(rec)
    if state == "ok" and os.path.lexists(rec["path"]):
        return "occupied"
    return state


def _keurer_entry(rec: Dict[str, Any]) -> Dict[str, Any]:
    delete = rec["decision"] == "delete"
    dest = rec["dest_path"] or ""
    place = "de prullenmand" if delete else recovery.place_of(dest, config)
    state = _keurer_state(rec)
    file = recovery.file_entry(rec["path"], dest, state, place)
    if not rec["dest_path"]:
        file["reason"] = "Van deze oudere beslissing is niet bewaard waar het bestand staat; zet het zo nodig met de hand terug"
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


def _op_state(op: Dict[str, Any]) -> str:
    if not duplicates_routes._paths_allowed(op):
        return "refused"
    if not duplicates_routes._still_there(op):
        return "missing"
    # The duplicates undo never overwrites: a taken name gets _RESTORED_001 next to it
    return "beside" if os.path.lexists(op["src_path"]) else "ok"


def _run_entry(run: Dict[str, Any]) -> Dict[str, Any]:
    ops = db.get_delete_operations(run["run_id"])
    files = [recovery.file_entry(op["src_path"], op["dst_path"], _op_state(op), "RAW_DUPLICATES") for op in ops]
    n = len(ops)
    return {
        "key": f"duplicates:{run['run_id']}",
        "source": "duplicates",
        "label": "Duplicaten",
        "title": f"{n} duplica{'at' if n == 1 else 'ten'} naar RAW_DUPLICATES",
        "folder": run.get("folder"),
        "created_at": _run_time(run),
        **recovery.summarize(files, n),
        "files": files[:recovery.FILES_PER_ENTRY],
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
