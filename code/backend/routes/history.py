"""
DataVault Dashboard - Activiteit and location history (DataGatherer step 3.9c)

GET  /api/activity/facets       {sources: [{id, name, icon, count}], kinds: [{id, label, icon, count}], first, last}
GET  /api/activity/days         [{day, count, kinds}] newest first; filters source=, kind= (repeatable), q=, from=, to=
GET  /api/activity/events       the events of one day (day=YYYY-MM-DD) with the same filters
GET  /api/location/summary      {points, visits, first, last, inferred, settings}
GET  /api/location/days         [{day, count}] the local days with location points
GET  /api/location/track        {points: [[ts_ms, lat, lon, accuracy]], visits, truncated} of one day
PUT  /api/location/settings     {max_gap_minutes, max_accuracy_m} for the photo estimates
POST /api/location/geotag       starts the job that estimates positions again (datagatherer.geotag)

Read-only on the DataGatherer tables (backend/datagatherer/activity_store.py and
location_store.py); nothing here touches a file.
"""
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.datagatherer import activity_store, geotag, location_store, registry
from backend.datagatherer.jobs import GEOTAG_TYPE
from backend.jobs import JobError

router = APIRouter()

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

config = None
db = None
jobs = None


def init_router(cfg, database, job_manager) -> None:
    global config, db, jobs
    config, db, jobs = cfg, database, job_manager


def _day(value: Optional[str], name: str = "day") -> Optional[str]:
    if value in (None, ""):
        return None
    if not DAY_RE.match(value):
        raise HTTPException(status_code=400, detail=f"{name} moet een datum zijn (JJJJ-MM-DD)")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} is geen bestaande datum")
    return value


def _filters(request: Request) -> Dict[str, Any]:
    params = request.query_params
    sources = [s for s in params.getlist("source") if s][:20]
    kinds = [k for k in params.getlist("kind") if k][:20]
    if any(len(v) > 40 for v in sources + kinds):
        raise HTTPException(status_code=400, detail="Ongeldig filter")
    q = (params.get("q") or "").strip()[:100] or None
    return {"sources": sources or None, "kinds": kinds or None, "q": q}


@router.get("/api/activity/facets")
def activity_facets() -> Dict[str, Any]:
    out = activity_store.facets(db)
    for source in out["sources"]:
        connector = registry.get(source["id"])
        source["name"] = connector.name if connector else source["id"]
        source["icon"] = connector.icon if connector else "fas fa-database"
    return out


@router.get("/api/activity/days")
def activity_days(request: Request, date_from: Optional[str] = Query(None, alias="from"),
                  date_to: Optional[str] = Query(None, alias="to"),
                  limit: int = Query(400, ge=1, le=4000)) -> Dict[str, Any]:
    f = _filters(request)
    days = activity_store.days(db, f["sources"], f["kinds"], f["q"], _day(date_from, "from"), _day(date_to, "to"), limit)
    return {"days": days}


@router.get("/api/activity/events")
def activity_events(request: Request, day: str = Query(...), limit: int = Query(500, ge=1, le=2000)) -> Dict[str, Any]:
    f = _filters(request)
    events = activity_store.events(db, _day(day), f["sources"], f["kinds"], f["q"], limit + 1)
    names = {c.id: (c.name, c.icon) for c in registry.connectors()}
    for ev in events:
        ev["source_name"], ev["source_icon"] = names.get(ev["source"], (ev["source"], "fas fa-database"))
    return {"day": day, "events": events[:limit], "truncated": len(events) > limit}


@router.get("/api/location/summary")
def location_summary() -> Dict[str, Any]:
    cov = location_store.coverage(db)
    with db._connect() as conn:
        visits = conn.execute("SELECT COUNT(*) FROM location_visits").fetchone()[0]
    return {"points": cov.records, "visits": visits, "first": cov.first, "last": cov.last,
            "inferred": geotag.count(db), "settings": geotag.settings(db)}


@router.get("/api/location/days")
def location_days() -> Dict[str, Any]:
    return {"days": location_store.days(db)}


@router.get("/api/location/track")
def location_track(day: str = Query(...)) -> Dict[str, Any]:
    start, end = activity_store.local_day_bounds(_day(day))
    return {"day": day, **location_store.track(db, start, end)}


@router.put("/api/location/settings")
def location_settings(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    current = geotag.settings(db)
    for key, lo, hi, label in (("max_gap_minutes", 1, 360, "De maximale tijd"), ("max_accuracy_m", 10, 10000, "De nauwkeurigheid")):
        if key in body:
            value = body[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
                raise HTTPException(status_code=400, detail=f"{label} moet tussen {lo} en {hi} liggen")
            current[key] = value
    db.set_setting(geotag.SETTINGS_KEY, current)
    return current


@router.post("/api/location/geotag")
def start_geotag() -> JSONResponse:
    if jobs is None:
        raise HTTPException(status_code=503, detail="Achtergrondtaken zijn niet beschikbaar")
    try:
        job, created = jobs.start(GEOTAG_TYPE, {"full": True})
    except JobError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return JSONResponse({"job": job, "created": created}, status_code=201 if created else 200)
