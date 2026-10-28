"""
DataVault Dashboard - Background job routes (see backend/jobs.py)

POST /api/jobs                 {type, params}       start (or the same job if it is already active)
GET  /api/jobs?limit&type                           newest first
GET  /api/jobs/{id}
GET  /api/jobs/{id}/result                          the stored result of a finished job
POST /api/jobs/{id}/cancel | /pause | /resume
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from backend.jobs import JobError, JobManager

router = APIRouter()

manager: Optional[JobManager] = None

MAX_ID = 2 ** 63


def init_router(job_manager: JobManager):
    global manager
    manager = job_manager


def get_manager() -> JobManager:
    if manager is None:
        raise HTTPException(status_code=503, detail="Achtergrondtaken zijn niet beschikbaar (configuratie niet geladen)")
    return manager


def call(fn, *args, **kwargs):
    """Run a manager call; a JobError becomes the HTTP answer it describes."""
    try:
        return fn(*args, **kwargs)
    except JobError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


def _check_id(job_id: int) -> int:
    if not 0 < job_id < MAX_ID:
        raise HTTPException(status_code=400, detail="Ongeldig taaknummer")
    return job_id


@router.post("/api/jobs")
def start_job(body: Any = Body(...)):
    """{type: 'dup_scan'|'hashing'|'thumbnails', params: {...}} -> {job, created} (201 when new)."""
    jobs = get_manager()
    if not isinstance(body, dict) or not isinstance(body.get("type"), str):
        raise HTTPException(status_code=400, detail="Geef type (en params) op")
    job, created = call(jobs.start, body["type"], body.get("params"))
    return JSONResponse(status_code=201 if created else 200, content={"job": job, "created": created})


@router.get("/api/jobs")
def list_jobs(limit: int = 30, type: Optional[str] = None) -> Dict[str, Any]:
    jobs = get_manager()
    limit = max(1, min(int(limit), 100))
    return {"jobs": call(jobs.list, limit, type)}


@router.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> Dict[str, Any]:
    return call(get_manager().get, _check_id(job_id))


@router.get("/api/jobs/{job_id}/result")
def get_job_result(job_id: int) -> Dict[str, Any]:
    jobs = get_manager()
    job_id = _check_id(job_id)
    return {"job": call(jobs.get, job_id), "result": call(jobs.result, job_id)}


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> Dict[str, Any]:
    return call(get_manager().cancel, _check_id(job_id))


@router.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: int) -> Dict[str, Any]:
    return call(get_manager().pause, _check_id(job_id))


@router.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: int) -> Dict[str, Any]:
    return call(get_manager().resume, _check_id(job_id))
