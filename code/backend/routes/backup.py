"""
DataVault Dashboard - Database backup routes (backend/backup.py)

GET  /api/backup/status    where the backups are, the newest, how many, when the next is due
POST /api/backup/run       make one now ('Nu back-uppen' in Instellingen): starts the 'backup' job
                           (job_types.backup_type) and answers 202 with it at once

Restoring is not an API call: it needs the app stopped (see code/restore_backup.py).
"""
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from backend import backup
from backend.config import Config

router = APIRouter()

config: Config = None
auto = None
jobs = None


def init_router(cfg: Config, auto_backup=None, job_manager=None):
    global config, auto, jobs
    config = cfg
    auto = auto_backup
    jobs = job_manager


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
