"""
DataVault Dashboard - Albums: browsing the library with filters, smart albums and 'on this day'

GET    /api/library/browse          one page of the index for a filter (backend/library_filters.py)
GET    /api/library/facets          capture years, cameras and file types, for the filter bar
GET    /api/library/on-this-day     photos and videos of one calendar day in earlier years, per year
GET    /api/library/albums          the smart albums with their counts
POST   /api/library/albums          a new album {name, filters, sort, icon?}
GET    /api/library/albums/{id}
PATCH  /api/library/albums/{id}     {name?, filters?, sort?, icon?, hidden?}
DELETE /api/library/albums/{id}     removes the album (its definition only)
POST   /api/library/albums/defaults the default albums back (added again and shown)

Everything here reads the media index (backend/media_index.py) and the app's own
database; an album is a live query and never copies, moves or changes a file.
"""
import calendar
import sqlite3
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Path, Query, Request

from backend import library_filters, media_index, smart_albums
from backend.config import Config

router = APIRouter()

MAX_INT = 2 ** 62
PAGE_MAX = 1000
OTD_PER_YEAR = 60          # items per year in one answer; the rest is one click away (browse that day)
OTD_PER_YEAR_MAX = 500
FACETS_SECONDS = 60.0

config: Config = None
db = None


def init_router(cfg: Config, database):
    global config, db
    config = cfg
    db = database
    if db is not None:
        # The default albums, once (the database may open later: the vault is not there yet)
        db.when_ready(_add_defaults)


def _add_defaults() -> None:
    try:
        smart_albums.ensure_defaults(db)
    except sqlite3.Error:
        pass   # tried again on the next start


def _bad(error: Exception, status: int = 400):
    raise HTTPException(status_code=getattr(error, "status", status), detail=str(error))


def item(row: Dict[str, Any]) -> Dict[str, Any]:
    """An index row in the shape the viewer and the grids use ({path, name, type, timestamp, ...})."""
    out = {
        "id": row["id"],
        "path": row["path"],
        "name": row["name"],
        "type": row["kind"],
        "ext": row["ext"],
        "size": row["size"],
        "timestamp": row["taken"],
        "date_source": row["taken_source"],
        "width": row["width"],
        "height": row["height"],
        "duration": row["duration"],
        "camera": " ".join(p for p in (row["camera_make"], row["camera_model"]) if p) or None,
        "has_gps": row["lat"] is not None,
    }
    if row["lat"] is not None:
        out["lat"], out["lon"] = row["lat"], row["lon"]
    if "rating" in row:
        out["rating"] = row["rating"]
        out["votes"] = row["votes"]
    return out


def _query_for(spec: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return library_filters.to_query(spec, config)
    except library_filters.FilterError as e:
        _bad(e)


def _album_or_404(album_id: int) -> Dict[str, Any]:
    album = smart_albums.get_album(db, album_id)
    if album is None:
        raise HTTPException(status_code=404, detail="Album niet gevonden")
    return album


# ---------- browsing ----------

@router.get("/browse")
def browse(request: Request, album: Optional[int] = Query(None, ge=0, le=MAX_INT),
           sort: Optional[str] = None, offset: int = 0, limit: int = 200) -> Dict[str, Any]:
    """
    One page of the library for a filter given as query parameters (the keys of
    library_filters, e.g. ?kind=photo&kind=video&year=2023&folder=RAW/2019), or for
    a smart album (album=id, with its own filter and sort; no other filter then).
    Answers {ready, total, offset, limit, sort, album, filters, version, items}:
    filters is the normal form (what the address shows), version changes when the
    rows of this selection change (a client paging through it starts over then).
    """
    try:
        spec = library_filters.from_params(request.query_params.multi_items(),
                                           reserved=("album", "sort", "offset", "limit"))
    except library_filters.FilterError as e:
        _bad(e)
    if album is not None:
        if spec:
            raise HTTPException(status_code=400, detail="Kies een album of filters, niet allebei")
        found = _album_or_404(album)
        spec = found["filters"]
        sort = sort or found["sort"]
    try:
        sort = library_filters.check_sort(sort or library_filters.DEFAULT_SORT)
    except library_filters.FilterError as e:
        _bad(e)
    query = _query_for(spec)
    limit = max(1, min(limit, PAGE_MAX))
    offset = max(0, min(offset, MAX_INT))
    # Count and version in one read, before the rows: a page never claims a newer version than it shows
    total, version = media_index.selection_info(db, query)
    rows = media_index.query(db, query, sort=sort, offset=offset, limit=limit)
    return {
        "ready": media_index.usable(db),
        "total": total,
        "offset": offset,
        "limit": limit,
        "sort": sort,
        "album": album,
        "filters": spec,
        "version": version,
        "items": [item(r) for r in rows],
    }


_facets_cache: Dict[str, Any] = {}
_facets_lock = threading.Lock()


@router.get("/facets")
def facets() -> Dict[str, Any]:
    """{ready, years: [[year, n]], cameras: [{make, model, label, count}], exts: [{kind, ext, count}]}."""
    generation = media_index.generation()
    now = time.monotonic()
    key = str(getattr(db, "db_path", id(db)))
    with _facets_lock:
        hit = _facets_cache.get(key)
        if hit and hit[0] == generation and now - hit[1] < FACETS_SECONDS:
            return hit[2]
    data = media_index.facets(db)
    for camera in data["cameras"]:
        make, model = camera["make"] or "", camera["model"] or ""
        # 'Apple iPhone 12', but 'Canon EOS 80D' only once when the model already names the make
        camera["label"] = model if make and model.lower().startswith(make.lower()) else " ".join(
            p for p in (make, model) if p)
        camera["value"] = " ".join(p for p in (make, model) if p) if make and model else (model or make)
    answer = {"ready": media_index.usable(db), **data}
    with _facets_lock:
        _facets_cache.clear()      # one database per app; never grows
        _facets_cache[key] = (generation, now, answer)
    return answer


# ---------- on this day ----------

def _today() -> date:
    return date.today()


def _parse_day(value: Optional[str]) -> date:
    """MM-DD or YYYY-MM-DD (the year is ignored); 29 February is always a valid day."""
    if not value:
        return _today()
    parts = value.strip().split("-")
    try:
        if len(parts) == 3 and len(parts[0]) == 4:
            parts = parts[1:]
        if len(parts) != 2 or not all(p.isdigit() and len(p) <= 2 for p in parts):
            raise ValueError
        return date(2000, int(parts[0]), int(parts[1]))   # 2000 is a leap year
    except ValueError:
        raise HTTPException(status_code=400, detail="Ongeldige dag, verwacht MM-DD of JJJJ-MM-DD")


@router.get("/on-this-day")
def on_this_day(date_: Optional[str] = Query(None, alias="date"),
                allowed_folders: Optional[List[str]] = Query(None),
                per_year: int = Query(OTD_PER_YEAR, ge=0, le=OTD_PER_YEAR_MAX)) -> Dict[str, Any]:
    """
    Photos and videos taken on one calendar day (today, or date=MM-DD) in the years
    before this one, newest year first: {ready, month, day, year, total, years:
    [{year, years_ago, total, items}]}. Each year holds its first per_year items by
    capture time (total says how many there are), and filters: the same selection as a
    browse filter (library_filters), so 'Alle N bekijken' opens exactly those N.
    Capture dates are local wall-clock time, as in the timeline, and only real ones
    count (EXIF or the video itself): a file dated only by its copy day is no memory.
    allowed_folders (one parameter per folder) limits it to those folders, like the
    'op deze dag' choice on Home. In a year that is not a leap year, 28 February also
    shows the photos of 29 February.
    """
    today = _today()
    day = _parse_day(date_)
    month_days = [(day.month, day.day)]
    with_leap_day = (day.month, day.day) == (2, 28) and not calendar.isleap(today.year)
    if with_leap_day:
        month_days.append((2, 29))
    filters: Dict[str, Any] = {"kind": ["photo", "video"], "capture_date": "known", "month_days": month_days,
                               "date_to": datetime(today.year, 1, 1).timestamp()}
    browse: Dict[str, Any] = {"kind": ["photo", "video"], "capture_date": "known"}
    if allowed_folders:
        from backend.routes.timeline import selected_roots
        roots, _ = selected_roots(allowed_folders)
        if not roots:
            return {"ready": media_index.usable(db), "month": day.month, "day": day.day, "year": today.year,
                    "total": 0, "years": []}
        if not any(root == config.VAULT_ROOT for root in roots):
            filters["folders"] = [str(r) for r in roots]
            browse["folders"] = sorted(library_filters.relative_folder(str(r), config) for r in roots)

    def year_filter(year: int) -> Dict[str, Any]:
        last = 29 if with_leap_day and calendar.isleap(year) else day.day
        return {**browse, "date_from": f"{year:04d}-{day.month:02d}-{day.day:02d}",
                "date_to": f"{year:04d}-{day.month:02d}-{last:02d}"}

    groups = media_index.year_groups(db, filters, per_year=per_year)
    years = [{"year": y, "years_ago": today.year - y, "total": n, "items": [item(r) for r in rows],
              "filters": year_filter(y)}
             for y, n, rows in groups]
    return {
        "ready": media_index.usable(db),
        "month": day.month,
        "day": day.day,
        "year": today.year,
        "total": sum(g["total"] for g in years),
        "years": years,
    }


# ---------- smart albums ----------

def _with_count(album: Dict[str, Any]) -> Dict[str, Any]:
    try:
        query = library_filters.to_query(album["filters"], config)
        album["count"] = smart_albums.cached_count(db, album["id"], album["filters"], query)
        album["error"] = None
    except (library_filters.FilterError, HTTPException) as e:
        # A folder that is no longer in the vault, an old filter: the album stays, with a reason
        album["count"] = None
        album["error"] = getattr(e, "detail", None) or str(e)
    album["builtin"] = album["builtin"] or None
    return album


@router.get("/albums")
def list_albums(with_hidden: bool = True) -> Dict[str, Any]:
    """{ready, all, albums: [{id, name, filters, sort, icon, builtin, hidden, count, error, ...}]}"""
    albums = [_with_count(a) for a in smart_albums.list_albums(db, with_hidden=with_hidden)]
    return {"ready": media_index.usable(db),
            "all": smart_albums.cached_count(db, "all", {}, {}),
            "albums": albums,
            "icons": list(smart_albums.ICONS)}


@router.post("/albums", status_code=201)
def create_album(data: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return _with_count(smart_albums.create_album(db, config, data))
    except smart_albums.AlbumError as e:
        _bad(e)


@router.post("/albums/defaults")
def restore_defaults() -> Dict[str, Any]:
    added = smart_albums.restore_defaults(db)
    return {"added": added, **list_albums()}


ALBUM_ID = Path(..., ge=0, le=MAX_INT)


@router.get("/albums/{album_id}")
def get_album(album_id: int = ALBUM_ID) -> Dict[str, Any]:
    return _with_count(_album_or_404(album_id))


@router.patch("/albums/{album_id}")
def update_album(album_id: int = ALBUM_ID, data: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return _with_count(smart_albums.update_album(db, config, album_id, data))
    except smart_albums.AlbumError as e:
        _bad(e)


@router.delete("/albums/{album_id}")
def delete_album(album_id: int = ALBUM_ID) -> Dict[str, Any]:
    try:
        return {"deleted": smart_albums.delete_album(db, album_id)}
    except smart_albums.AlbumError as e:
        _bad(e)
