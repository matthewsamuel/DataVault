"""
DataVault Dashboard - Export routes (see backend/export.py)

GET  /api/export/defaults       {base, free_bytes}: the default folder (~/Pictures/DataVault Export)
POST /api/export/plan           {paths, mode, max_edge, destination?} -> size, free space, fit, checks
POST /api/export/best           {date_from, date_to, count|percent, folder?, album_id?} -> the preview set
POST /api/export/choose-folder  the macOS folder dialog -> {path} (or {cancelled: true})
POST /api/export/reveal         {path}: show an export folder in Finder

The export itself is a job: POST /api/jobs {type: 'export', params: {paths, destination, mode,
max_edge, structure, manifest}}.
"""
import subprocess
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

from backend import export
from backend.config import Config
from backend.file_ops import is_inside

router = APIRouter()

config: Config = None
db = None


def init_router(cfg: Config, database):
    global config, db
    config = cfg
    db = database


def _object(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Verwacht een object")
    return body


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except export.ExportError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/defaults")
def defaults() -> Dict[str, Any]:
    base = export.default_base()
    return {"base": str(base), "free_bytes": export.free_bytes(base)}


@router.post("/plan")
def plan(body: Any = Body(...)) -> Dict[str, Any]:
    body = _object(body)
    return _guard(export.plan, db, config, body.get("paths"), body.get("mode", "originals"),
                  body.get("max_edge", export.DEFAULT_EDGE), body.get("destination"))


@router.post("/best")
def best(body: Any = Body(...)) -> Dict[str, Any]:
    body = _object(body)
    return _guard(export.best_of, db, config, {"date_from": body.get("date_from"), "date_to": body.get("date_to")},
                  count=body.get("count"), percent=body.get("percent"), folder=body.get("folder") or None,
                  album_id=body.get("album_id"))


@router.post("/choose-folder")
def choose_folder() -> Dict[str, Any]:
    """The native folder dialog of macOS (this app runs on the user's own Mac)."""
    script = ('POSIX path of (choose folder with prompt "Kies een map voor de export" '
              f'default location (POSIX file "{_dialog_start()}"))')
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        raise HTTPException(status_code=500, detail="Het mappenvenster kon niet worden geopend; typ het pad zelf")
    if out.returncode != 0:
        return {"cancelled": True}
    return {"path": out.stdout.strip().rstrip("/") or "/"}


def _dialog_start() -> str:
    base = export.default_base()
    return str(export._existing_ancestor(base)).replace('"', "")


@router.post("/reveal")
def reveal(body: Any = Body(...)) -> Dict[str, Any]:
    """Show a folder in Finder. Only folders outside the archive (the export folders)."""
    path = _object(body).get("path")
    if not isinstance(path, str) or not path.strip():
        raise HTTPException(status_code=400, detail="Geef een map op")
    folder = Path(path).expanduser()
    if not folder.is_absolute() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Map niet gevonden")
    if is_inside(folder, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="Dit is geen exportmap")
    try:
        subprocess.run(["open", str(folder)], check=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        raise HTTPException(status_code=500, detail=f"Openen in Finder mislukt: {e}")
    return {"success": True}
