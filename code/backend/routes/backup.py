"""
DataVault Dashboard - Database backup routes (backend/backup.py)

GET  /api/backup/status    where the backups are, the newest, how many, when the next is due
POST /api/backup/run       make one now ('Nu back-uppen' in Instellingen)

Restoring is not an API call: it needs the app stopped (see code/restore_backup.py).
"""
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from backend import backup
from backend.config import Config

router = APIRouter()

config: Config = None
auto = None


def init_router(cfg: Config, auto_backup=None):
    global config, auto
    config = cfg
    auto = auto_backup


def _require_config():
    if config is None:
        raise HTTPException(status_code=503, detail="Configuratie niet geladen")


@router.get("/status")
def backup_status() -> Dict[str, Any]:
    """Works without the archive: the backups are on the Mac's own disk."""
    _require_config()
    return backup.status(config, auto_enabled=auto is not None)


@router.post("/run")
def backup_run() -> Dict[str, Any]:
    """Make a backup now. A plain def: FastAPI runs it in a worker thread, so copying a
    large database (a second or two) never blocks other requests."""
    _require_config()
    try:
        result = backup.make_backup(config, manual=True, keep_daily=config.BACKUP_KEEP_DAILY,
                                    keep_weekly=config.BACKUP_KEEP_WEEKLY)
    except backup.BackupError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"backup": result, "status": backup.status(config, auto_enabled=auto is not None)}
