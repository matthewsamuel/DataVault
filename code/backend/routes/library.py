"""
DataVault Dashboard - Library (media index) routes

GET /api/library/status    how complete and how recent the media index is, and its counts
GET /api/library/media     one page of the index through media_index.query (filters, sort)

A pass is started like any job: POST /api/jobs {type: 'index', params: {full: true}}.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend import media_index
from backend.config import Config
from backend.jobs import ACTIVE
from backend.paths import resolve_in_vault

router = APIRouter()

config: Config = None
db = None
manager = None


def init_router(cfg: Config, database, job_manager=None):
    global config, db, manager
    config = cfg
    db = database
    manager = job_manager


@router.get("/status")
def library_status() -> Dict[str, Any]:
    """
    {ready, files, photos, videos, audio, missing, folders, last_pass_at, last_complete_at,
    last_full_at, last_seconds, job}; ready is true once a pass has finished (before
    that, the timeline and 'on this day' walk the disk as they always did). job is the
    pass that is running or waiting, if any.
    """
    info = media_index.state(db)
    job = None
    if manager is not None:
        for item in manager.list(limit=10, job_type=media_index.INDEX_JOB):
            if item["status"] in ACTIVE:
                job = {"id": item["id"], "status": item["status"], "auto": bool(item["params"].get("auto"))}
                break
    return {
        "ready": bool(info.get("last_pass_at")),
        **media_index.stats(db),
        "last_pass_at": info.get("last_pass_at"),
        "last_complete_at": info.get("last_complete_at"),
        "last_full_at": info.get("last_full_at"),
        "last_seconds": info.get("last_seconds"),
        "job": job,
    }


def _date(value: Optional[str], name: str) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").timestamp()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Ongeldige {name}, verwacht JJJJ-MM-DD")


@router.get("/media")
def library_media(
    kind: Optional[List[str]] = Query(None),
    ext: Optional[List[str]] = Query(None),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    folder: Optional[str] = None,
    recursive: bool = True,
    camera: Optional[str] = None,
    has_gps: Optional[bool] = None,
    min_rating: Optional[int] = None,
    orientation: Optional[str] = None,
    sort: str = "taken",
    offset: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    One page of the media index. date_from and date_to are YYYY-MM-DD (date_to included);
    folder is a vault folder (its subfolders too unless recursive=false). Answers
    {ready, total, offset, limit, items}; ready false means the first pass has not
    finished yet, so the list is not complete.
    """
    filters: Dict[str, Any] = {}
    for value in kind or []:
        if value not in ("photo", "video", "audio"):
            raise HTTPException(status_code=400, detail="kind is photo, video of audio")
    if kind:
        filters["kind"] = kind
    if ext:
        filters["ext"] = ext
    start, end = _date(date_from, "begindatum"), _date(date_to, "einddatum")
    if start is not None:
        filters["date_from"] = start
    if end is not None:
        filters["date_to"] = (datetime.fromtimestamp(end) + timedelta(days=1)).timestamp()
    if folder:
        filters["folder"] = str(resolve_in_vault(folder, config, folder=True))
        filters["recursive"] = recursive
    if camera:
        filters["camera"] = camera
    if has_gps is not None:
        filters["has_gps"] = has_gps
    if min_rating is not None:
        filters["min_rating"] = min_rating
    if orientation:
        if orientation not in media_index.ORIENTATIONS:
            raise HTTPException(status_code=400, detail="orientation is landscape, portrait of square")
        filters["orientation"] = orientation
    if sort not in media_index.SORTS:
        raise HTTPException(status_code=400, detail=f"sort is een van: {', '.join(media_index.SORTS)}")
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    return {
        "ready": media_index.usable(db),
        "total": media_index.count(db, filters),
        "offset": offset,
        "limit": limit,
        "items": media_index.query(db, filters, sort=sort, offset=offset, limit=limit),
    }
