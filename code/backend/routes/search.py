"""
DataVault Dashboard - Search routes ('Slim zoeken' and file names, backend/semantic.py)

GET  /api/search/status     model installed?, how many photos are recognised, the jobs
GET  /api/search/semantic   q + filters: photos and videos that match a text, best first
GET  /api/search/similar    path (or id) + filters: photos that look like this one
GET  /api/search/filename   q + filters: files whose name contains q, newest first
POST /api/search/warm       load the text model ahead of the first query (window opened)

Installing the model and recognising the photos are jobs: POST /api/jobs
{type: 'model_download'} (only when the user clicks) and {type: 'semantic'}.
The filters are those of /api/library/media (media_index._where).
"""
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend import media_index, semantic
from backend.config import Config
from backend.paths import resolve_in_vault
from backend.routes.library import MAX_INT, _date

router = APIRouter()

MAX_PAGE = 200

config: Config = None
db = None
manager = None


def init_router(cfg: Config, database, job_manager=None):
    global config, db, manager
    config = cfg
    db = database
    manager = job_manager


def _job(job_type: str) -> Optional[Dict[str, Any]]:
    if manager is None:
        return None
    for item in manager.list(limit=10, job_type=job_type):
        return {key: item.get(key) for key in ("id", "status", "processed", "total", "message", "error",
                                               "eta_seconds", "params", "summary")}
    return None


@router.get("/status")
def search_status() -> Dict[str, Any]:
    """
    {model: {id, label, licence, homepage, sources, size, folder, installed}, ready,
    library_ready, total, embedded, pending, undecodable, device, per_second, job, download}.
    ready: the model is installed (search works for what is recognised so far).
    """
    model = semantic.model_info(config)
    info = semantic.state(db)
    return {
        "model": model,
        "ready": semantic.model_ready(config),
        "library_ready": media_index.usable(db),
        **semantic.counts(db, fresh=True),
        "device": info.get("device"),
        "per_second": info.get("per_second"),
        "last_run_at": info.get("last_run_at"),
        "job": _job(semantic.EMBED_JOB),
        "download": _job(semantic.DOWNLOAD_JOB),
    }


def _filters(kind, date_from, date_to, folder, recursive, camera, has_gps, min_rating, orientation) -> Dict[str, Any]:
    """The library filters, checked as /api/library/media checks them."""
    filters: Dict[str, Any] = {}
    for value in kind or []:
        if value not in ("photo", "video"):
            raise HTTPException(status_code=400, detail="kind is photo of video")
    if kind:
        filters["kind"] = kind
    start, end = _date(date_from, "begindatum"), _date(date_to, "einddatum", next_day=True)
    if start is not None:
        filters["date_from"] = start
    if end is not None:
        filters["date_to"] = end
    if folder:
        filters["folder"] = str(resolve_in_vault(folder, config, folder=True))
        filters["recursive"] = recursive
    if camera:
        filters["camera"] = camera
    if has_gps is not None:
        filters["has_gps"] = has_gps
    if min_rating is not None:
        filters["min_rating"] = max(-MAX_INT, min(min_rating, MAX_INT))
    if orientation:
        if orientation not in media_index.ORIENTATIONS:
            raise HTTPException(status_code=400, detail="orientation is landscape, portrait of square")
        filters["orientation"] = orientation
    return filters


def _paging(offset: int, limit: int):
    return max(0, min(offset, MAX_INT)), max(1, min(limit, MAX_PAGE))


def _answer(result: Dict[str, Any], started: float, **extra) -> Dict[str, Any]:
    counts = semantic.counts(db)
    return {**result, **extra, "took_ms": round((time.perf_counter() - started) * 1000, 1),
            "library_ready": media_index.usable(db), "indexed": counts["embedded"], "pending": counts["pending"]}


def _not_ready(e: semantic.NotReady):
    raise HTTPException(status_code=409, detail=str(e))


@router.get("/semantic")
def search_semantic(
    q: str = Query(..., min_length=1, max_length=semantic.MAX_QUERY_CHARS),
    kind: Optional[List[str]] = Query(None),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    folder: Optional[str] = None,
    recursive: bool = True,
    camera: Optional[str] = None,
    has_gps: Optional[bool] = None,
    min_rating: Optional[int] = None,
    orientation: Optional[str] = None,
    include_weak: bool = False,
    offset: int = 0,
    limit: int = 60,
) -> Dict[str, Any]:
    """
    {total, ranked, offset, limit, next_offset, items: [media row + score], took_ms, indexed,
    pending}. total counts the results above the relevance cut; include_weak pages on
    through the less certain ones (up to 'ranked'). 409 when the model is not installed.
    """
    started = time.perf_counter()
    if not q.strip():
        raise HTTPException(status_code=400, detail="Typ waar je naar zoekt")
    filters = _filters(kind, date_from, date_to, folder, recursive, camera, has_gps, min_rating, orientation)
    offset, limit = _paging(offset, limit)
    try:
        result = semantic.search_text(db, config, q, filters, offset, limit, include_weak)
    except semantic.NotReady as e:
        _not_ready(e)
    return _answer(result, started, mode="semantic", query=q)


@router.get("/similar")
def search_similar(
    path: Optional[str] = None,
    id: Optional[int] = None,
    kind: Optional[List[str]] = Query(None),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    folder: Optional[str] = None,
    recursive: bool = True,
    camera: Optional[str] = None,
    has_gps: Optional[bool] = None,
    min_rating: Optional[int] = None,
    orientation: Optional[str] = None,
    include_weak: bool = False,
    offset: int = 0,
    limit: int = 60,
) -> Dict[str, Any]:
    """Photos and videos that look like the one at path (or with media id), best first."""
    started = time.perf_counter()
    if path:
        target = resolve_in_vault(path, config)
        media_id = semantic.media_id_for_path(db, str(target))
    elif id is not None:
        media_id = max(0, min(id, MAX_INT))
    else:
        raise HTTPException(status_code=400, detail="Geef path of id op")
    if media_id is None:
        raise HTTPException(status_code=404, detail="Deze foto staat (nog) niet in de bibliotheek")
    filters = _filters(kind, date_from, date_to, folder, recursive, camera, has_gps, min_rating, orientation)
    offset, limit = _paging(offset, limit)
    try:
        result = semantic.search_similar(db, config, media_id, filters, offset, limit, include_weak)
    except semantic.NotReady as e:
        _not_ready(e)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=f"{e}: Slim zoeken is nog bezig met je foto's")
    return _answer(result, started, mode="similar", source_id=media_id)


@router.get("/filename")
def search_filename(
    q: str = Query(..., min_length=1, max_length=semantic.MAX_QUERY_CHARS),
    kind: Optional[List[str]] = Query(None),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    folder: Optional[str] = None,
    recursive: bool = True,
    camera: Optional[str] = None,
    has_gps: Optional[bool] = None,
    min_rating: Optional[int] = None,
    orientation: Optional[str] = None,
    offset: int = 0,
    limit: int = 60,
) -> Dict[str, Any]:
    """Photos and videos in the library whose file name contains q, newest first."""
    started = time.perf_counter()
    if not q.strip():
        raise HTTPException(status_code=400, detail="Typ (een deel van) een bestandsnaam")
    filters = _filters(kind, date_from, date_to, folder, recursive, camera, has_gps, min_rating, orientation)
    offset, limit = _paging(offset, limit)
    result = semantic.search_filename(db, q, filters, offset, limit)
    return {**result, "mode": "filename", "query": q, "library_ready": media_index.usable(db),
            "took_ms": round((time.perf_counter() - started) * 1000, 1)}


@router.post("/warm")
def search_warm() -> Dict[str, Any]:
    """Load the text model and the vectors now, so the first query is quick. Cheap when loaded."""
    if not semantic.model_ready(config):
        return {"ready": False}
    import threading

    def warm():
        try:
            semantic.warm_up(config)
            semantic.vector_index().refresh(db, semantic.current_model())
        except Exception:
            pass
        finally:
            db.release_thread()

    threading.Thread(target=warm, name="semantic-warm", daemon=True).start()
    return {"ready": True}
