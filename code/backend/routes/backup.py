"""
DataVault Dashboard - Database backup routes (backend/backup.py)

GET  /api/backup/status    where the backups are, the newest, how many, when the next is due
POST /api/backup/run       make one now ('Nu back-uppen' in Instellingen): starts the 'backup' job
                           (job_types.backup_type) and answers 202 with it at once
GET  /api/backup/folders   the folder browser of 'Andere map kiezen' (?path=: its subfolders;
                           without: the starting points), folder names only
POST /api/backup/folder/check  {path}: can this folder hold the backups (paths.check_backup_folder)
POST /api/backup/folder/create  {path, name}: make a new folder inside path (checked, one level)
PUT  /api/backup/folder    {path, create?}: use this folder (create: a new folder inside it
                           first); {path: null}: back to the folder from config.json

Restoring is not an API call: it needs the app stopped (see code/restore_backup.py).
"""
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from backend import backup
from backend.config import Config
from backend.paths import BackupFolderError, backup_folder_problem, check_backup_folder

router = APIRouter()

config: Config = None
auto = None
jobs = None
db = None

MAX_FOLDERS = 2000  # subfolders listed per folder in the browser


def init_router(cfg: Config, auto_backup=None, job_manager=None, database=None):
    global config, auto, jobs, db
    config = cfg
    auto = auto_backup
    jobs = job_manager
    db = database


def _require_config():
    if config is None:
        raise HTTPException(status_code=503, detail="Configuratie niet geladen")


@router.get("/status")
def backup_status() -> Dict[str, Any]:
    """Works without the archive: the backups are on the Mac's own disk."""
    _require_config()
    return _status()


def _status() -> Dict[str, Any]:
    return backup.status(config, auto_enabled=auto is not None,
                         next_check_at=getattr(auto, "next_check_at", None))


@router.post("/run", status_code=202)
def backup_run() -> Dict[str, Any]:
    """Start a backup as a job: copying a large database takes minutes (2 min for 1 GB), which
    must not hold a request open. The page and the job dock follow the job."""
    _require_config()
    if jobs is None:
        raise HTTPException(status_code=503, detail="Achtergrondtaken zijn niet beschikbaar")
    from backend.job_types import BACKUP_JOB
    from backend.jobs import JobError
    try:
        job, _ = jobs.start(BACKUP_JOB, {})
    except JobError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return {"job": job, "status": _status()}


# ==================== CHOOSING THE FOLDER (Instellingen > Back-ups) ====================

def _roots() -> List[Dict[str, Any]]:
    """Where the browser starts: the home folder and every connected disk (in test mode only
    the temp folder, so a test never lists anything of the real Mac)."""
    if os.environ.get("DATAVAULT_TESTING") == "1":
        temp = Path(os.path.realpath(tempfile.gettempdir()))
        return [_entry(temp, "Tijdelijke map", known_children=None, icon="fa-flask")]
    home = Path(os.path.realpath(Path.home()))
    roots = [_entry(home, f"Thuismap ({home.name})", known_children=None, icon="fa-house")]
    try:
        volumes = sorted(os.scandir("/Volumes"), key=lambda e: e.name.lower())
    except OSError:
        volumes = []
    for volume in volumes:
        # The Mac's own disk is a symlink to / in /Volumes: it is reached through the home folder
        if volume.name.startswith(".") or volume.is_symlink():
            continue
        try:
            if volume.is_dir(follow_symlinks=False):
                roots.append(_entry(Path(volume.path), volume.name, known_children=None, icon="fa-hard-drive"))
        except OSError:
            continue
    return roots


def _entry(path: Path, name: Optional[str] = None, known_children: Optional[bool] = None,
           icon: Optional[str] = None) -> Dict[str, Any]:
    problem = backup_folder_problem(path, config)
    # Into the archive or DataVault's own folders there is nothing to choose: closed
    inside_protected = bool(problem) and "systeemmap" not in problem and "hoofdmap" not in problem \
        and "testmodus" not in problem
    return {"name": name or path.name, "path": str(path), "allowed": problem is None, "reason": problem,
            "has_children": False if inside_protected else known_children, "icon": icon}


def _has_subfolders(path: Path) -> bool:
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False):
                    return True
    except OSError:
        return False
    return False


def _subfolders(folder: Path) -> List[Dict[str, Any]]:
    home = Path(os.path.realpath(Path.home()))
    try:
        entries = [e for e in os.scandir(folder)
                   if not e.name.startswith(".") and e.name not in ("$RECYCLE.BIN", "System Volume Information")
                   and e.is_dir(follow_symlinks=False)]
    except OSError as e:
        raise HTTPException(status_code=403, detail=f"Deze map kan niet worden gelezen ({e.strerror or e})")
    entries.sort(key=lambda e: e.name.lower())
    # Directly in the home folder: no peeking inside (Bureaublad, Documenten and Downloads would
    # make macOS ask for permission); whether they have subfolders shows when you open them
    peek = folder != home
    result = []
    for e in entries[:MAX_FOLDERS]:
        path = Path(e.path)
        if path == home / "Library":
            continue
        result.append(_entry(path, known_children=_has_subfolders(path) if peek else None))
    return result


@router.get("/folders")
def backup_folders(path: Optional[str] = Query(None, max_length=4096)) -> Dict[str, Any]:
    """The folder browser: starting points, or the subfolders of path (names only)."""
    _require_config()
    if not path:
        return {"path": None, "folders": _roots()}
    if "\0" in path or not path.startswith("/"):
        raise HTTPException(status_code=400, detail="Ongeldig pad")
    real = Path(os.path.realpath(path))
    allowed_roots = [Path(r["path"]) for r in _roots()]
    if not any(real == r or real.is_relative_to(r) for r in allowed_roots):
        raise HTTPException(status_code=403, detail="Deze map valt buiten de plekken die je hier kunt kiezen")
    problem = backup_folder_problem(real, config)
    if problem and _entry(real)["has_children"] is False:
        raise HTTPException(status_code=403, detail=problem)
    if not real.is_dir():
        raise HTTPException(status_code=404, detail="Deze map bestaat niet (meer)")
    return {"path": str(real), "folders": _subfolders(real)}


def _body(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Verwacht een object")
    return body


@router.post("/folder/check")
def backup_folder_check(body: Any = Body(...)) -> Dict[str, Any]:
    """Can this folder hold the backups? Answers {ok, path, warning, error} (never an error status)."""
    _require_config()
    raw = _body(body).get("path")
    try:
        real, warning = check_backup_folder(raw, config)
    except BackupFolderError as e:
        return {"ok": False, "path": raw if isinstance(raw, str) else None, "warning": None, "error": str(e)}
    return {"ok": True, "path": str(real), "warning": warning, "error": None,
            "current": real == Path(backup.folder_of(config))}


@router.post("/folder/create")
def backup_folder_create(body: Any = Body(...)) -> Dict[str, Any]:
    """'Map maken' in the folder browser: a new folder inside path, checked like a backup folder
    (it is not used yet; PUT /folder does that). Answers like /folder/check."""
    _require_config()
    body = _body(body)
    name = body.get("name")
    if not isinstance(name, str):
        raise HTTPException(status_code=400, detail="Geef de nieuwe map een naam")
    try:
        real, warning = check_backup_folder(body.get("path"), config, create=name)
    except BackupFolderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "path": str(real), "warning": warning, "error": None, "current": False}


@router.put("/folder")
def backup_folder_set(body: Any = Body(...)) -> Dict[str, Any]:
    """Use this folder for the backups from now on (stored in app_settings), or {path: null}:
    the folder from config.json again. The existing backups stay where they are."""
    _require_config()
    if db is None:
        raise HTTPException(status_code=503, detail="De database is niet beschikbaar")
    body = _body(body)
    if backup.running():
        raise HTTPException(status_code=409, detail="Er wordt nu een back-up gemaakt; wacht tot hij klaar is")
    if body.get("path") is None:
        db.set_setting(backup.SETTING_KEY, None)
        backup.use_folder(config, None)
        return {"warning": None, "status": _status()}
    create = body.get("create")
    if create is not None and not isinstance(create, str):
        raise HTTPException(status_code=400, detail="create moet de naam van een nieuwe map zijn")
    try:
        real, warning = check_backup_folder(body.get("path"), config, create=create)
    except BackupFolderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.set_setting(backup.SETTING_KEY, str(real))
    backup.use_folder(config, real)
    return {"warning": warning, "status": _status()}
