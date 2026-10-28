"""
DataVault Dashboard - 'Beste uit reeks': near-duplicates and bursts (backend/near_dups.py)

GET  /api/series/status          how many photos are compared, and the 'phash' job if it runs
GET  /api/series                 one page of series (strictness, sort, offset, limit)
POST /api/series/keep            keep some photos of a series, move the others to
                                 RAW_DUPLICATES/near-duplicates (one recovery action)
POST /api/series/keep-all        keep every photo of a series: it is not shown again
POST /api/series/reviews/reset   show the series decided on with keep-all again

The comparison itself runs as a job: POST /api/jobs {type: 'phash'}. Exact byte copies
stay on the duplicates screen; here they count as one photo, listed with it.
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException, Query

from backend import media_index, near_dups, recovery
from backend.config import Config
from backend.jobs import ACTIVE

router = APIRouter()

config: Config = None
db = None
manager = None

MAX_PAGE = 50

recovery.register_kind(near_dups.RECOVERY_KIND, "Beste uit reeks")


def init_router(cfg: Config, database, job_manager=None):
    global config, db, manager
    config = cfg
    db = database
    manager = job_manager


def _strictness(value: str) -> str:
    try:
        return near_dups.strictness_or_default(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Strengheid moet streng, normaal of ruim zijn")


def _ids(body: Any, key: str) -> List[int]:
    value = body.get(key) if isinstance(body, dict) else None
    if (not isinstance(value, list) or not value or len(value) > near_dups.MAX_MEMBERS
            or not all(isinstance(i, int) and not isinstance(i, bool) and 0 < i < 2 ** 62 for i in value)):
        raise HTTPException(status_code=400, detail=f"{key} moet een lijst met foto-nummers zijn")
    return value


@router.get("/status")
def series_status() -> Dict[str, Any]:
    """
    {ready, photos, analysed, undecodable, errors, pending, pairs, reviewed, last_run_at,
    last_complete_at, job}. ready is false until the media index finished its first pass.
    pending counts photos that still have to be compared. job: the comparison that runs
    or waits, if any; last_job: the newest one (also when paused or cancelled).
    """
    info = near_dups.state(db)
    job = last_job = None
    if manager is not None:
        jobs = manager.list(limit=10, job_type=near_dups.PHASH_JOB)
        if jobs:
            last_job = {"id": jobs[0]["id"], "status": jobs[0]["status"]}
        for item in jobs:
            if item["status"] in ACTIVE:
                job = {"id": item["id"], "status": item["status"]}
                break
    return {
        "ready": media_index.usable(db),
        **near_dups.counts(db),
        "last_run_at": info.get("last_run_at"),
        "last_complete_at": info.get("last_complete_at"),
        "job": job,
        "last_job": last_job,
        "strictness": list(near_dups.STRICTNESS),
        "default_strictness": near_dups.DEFAULT_STRICTNESS,
        "holding_folder": str(near_dups.holding_folder(config)),
    }


@router.get("")
def series_page(
    strictness: str = near_dups.DEFAULT_STRICTNESS,
    sort: str = "size",
    offset: int = Query(0, ge=0, le=2 ** 62),
    limit: int = 20,
) -> Dict[str, Any]:
    """
    {strictness, sort, total, photos, offset, limit, next_offset, version, items}. sort:
    size (most photos first) or recent (newest first). Every item: {key, kind ('burst' or
    'near'), count, taken, folder, best, members: [{id, path, name, size, width, height,
    taken, camera, score, breakdown: {sharpness, resolution, exposure, choice}, best,
    keurer_keep, rating, votes, copies: [{id, path}]}]}.
    """
    strictness = _strictness(strictness)
    if sort not in near_dups.SORTS:
        raise HTTPException(status_code=400, detail="Sorteer op size of recent")
    limit = max(1, min(limit, MAX_PAGE))
    return {"ready": media_index.usable(db), **near_dups.page(db, strictness, sort, offset, limit)}


@router.post("/keep")
def keep(body: Any = Body(...)) -> Dict[str, Any]:
    """
    {members: [ids], keep: [ids]}: keep these photos of the series, move the others (and
    their exact copies) to RAW_DUPLICATES/near-duplicates as one recovery action.
    Answers {action_id, moved, failed, kept, destination}.
    """
    members, kept = _ids(body, "members"), _ids(body, "keep")
    try:
        return near_dups.keep_best(db, config, members, kept)
    except near_dups.KeepError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.post("/keep-all")
def keep_all(body: Any = Body(...)) -> Dict[str, Any]:
    """{members: [ids]}: every photo stays; this series is not shown again (until it changes)."""
    members = _ids(body, "members")
    if len(set(members)) < 2 or near_dups.find_series(db, members) is None:
        raise HTTPException(status_code=409, detail="Deze foto's vormen geen reeks (meer); vernieuw de lijst")
    return {"key": near_dups.record_review(db, members, "keep_all")}


@router.post("/reviews/reset")
def reset_reviews() -> Dict[str, Any]:
    return {"shown_again": near_dups.reset_reviews(db)}
