"""
DataVault Dashboard - Kaart: geotagged photos and videos on a map

GET  /api/map/points      the media with a GPS position as compact arrays, in chunks
GET  /api/map/locate     {id, lat, lon, has_gps} of one file (for 'Op kaart tonen' in the viewer)
GET  /api/map/items       full items (for the viewer and popups) for a few ids
GET  /api/map/settings    {osm_tiles}: the opt-in OpenStreetMap tiles
PUT  /api/map/settings    {osm_tiles: bool}

Reads the media index (backend/media_index.py) only. The filters are the ones of the
Albums screen (backend/library_filters.py: year, date_from/date_to, folder, camera, kind,
..., or album=id for a smart album); has_gps is implied. Nothing here touches a file.

The position of a photo is read at indexing time (backend/media_meta.py): 0,0 and
out-of-range values count as 'no position' there, so they never reach this API.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from backend import library_filters, media_index, smart_albums
from backend.config import Config
from backend.routes.albums import item as album_item

router = APIRouter()

MAX_INT = 2 ** 62
CHUNK_DEFAULT = 20000
CHUNK_MAX = 50000
ITEMS_MAX = 200
KIND_CODES = {"photo": 0, "video": 1, "audio": 2}
SETTING_OSM = "map_osm_tiles"

config: Config = None
db = None


def init_router(cfg: Config, database):
    global config, db
    config = cfg
    db = database


def _bad(message: str, status: int = 400):
    raise HTTPException(status_code=status, detail=message)


def _bbox(text: Optional[str]) -> Optional[List[float]]:
    """'zuid,west,noord,oost' in degrees."""
    if text is None or text == "":
        return None
    try:
        south, west, north, east = (float(v) for v in text.split(","))
    except ValueError:
        _bad("Het kaartvak moet vier getallen zijn: zuid,west,noord,oost")
    if not all(abs(v) < 1e4 for v in (south, west, north, east)) or south > north:
        _bad("Ongeldig kaartvak")
    south, north = max(south, -90.0), min(north, 90.0)
    return [south, west, north, east]


def _selection(request: Request, album: Optional[int]) -> Dict[str, Any]:
    try:
        spec = library_filters.from_params(request.query_params.multi_items(),
                                           reserved=("album", "bbox", "after", "limit"))
        if album is not None:
            if spec:
                _bad("Kies een album of filters, niet allebei")
            found = smart_albums.get_album(db, album)
            if found is None:
                _bad("Album niet gevonden", 404)
            spec = found["filters"]
        spec = {k: v for k, v in spec.items() if k != "has_gps"}
        return library_filters.to_query(spec, config)
    except library_filters.FilterError as e:
        _bad(str(e))


@router.get("/points")
def points(request: Request, album: Optional[int] = Query(None, ge=0, le=MAX_INT),
           bbox: Optional[str] = None, after: int = Query(0, ge=0, le=MAX_INT),
           limit: int = Query(CHUNK_DEFAULT, ge=1)) -> Dict[str, Any]:
    """
    {ready, total, without_gps, bounds, version, next, points: [[id, lat, lon, taken, kind], ...]}.
    total, bounds ([south, west, north, east] of all matching points, null when none) and
    without_gps (the same filter, no position) describe the whole selection; points is one
    chunk of it by id, after = the last id of the previous chunk, next = what to pass as
    `after` for the following chunk (null at the end). kind is 0 photo, 1 video, 2 audio.
    bbox limits the points (not total or bounds) to what is in view.
    """
    query = _selection(request, album)
    area = _bbox(bbox)
    limit = min(limit, CHUNK_MAX)
    base = {**query, "has_gps": True}
    where, args = media_index._where(base)
    page_where, page_args = media_index._where({**base, "bbox": area} if area else base)
    with db._connect() as conn:
        total, south, west, north, east, newest, ids = conn.execute(
            f"SELECT COUNT(*), MIN(m.lat), MIN(m.lon), MAX(m.lat), MAX(m.lon), COALESCE(MAX(m.indexed_at), 0), "
            f"COALESCE(SUM(m.id), 0) FROM media m WHERE {where}", args).fetchone()
        rows = conn.execute(
            f"SELECT m.id, m.lat, m.lon, m.taken, m.kind FROM media m WHERE {page_where} AND m.id > ? "
            f"ORDER BY m.id LIMIT ?", (*page_args, after, limit + 1)).fetchall()
    without = media_index.count(db, {**query, "has_gps": False})
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "ready": media_index.usable(db),
        "total": total,
        "without_gps": without,
        "bounds": [south, west, north, east] if total else None,
        "version": f"{total:x}.{ids:x}.{int(newest * 1000):x}",
        "next": rows[-1][0] if more and rows else None,
        "points": [[r[0], r[1], r[2], None if r[3] is None else int(r[3]), KIND_CODES.get(r[4], 0)] for r in rows],
    }


@router.get("/items")
def items(ids: str = Query(..., description="comma-separated media ids")) -> Dict[str, Any]:
    """{items: [...]}: the media rows for these ids (at most 200), in the order asked, with lat and lon."""
    try:
        wanted = [int(v) for v in ids.split(",") if v.strip()]
    except ValueError:
        _bad("Ongeldige id's")
    if len(wanted) > ITEMS_MAX:
        _bad(f"Hoogstens {ITEMS_MAX} bestanden tegelijk")
    if not wanted:
        return {"items": []}
    columns = ", ".join(f"m.{c}" for c in media_index.ROW_COLUMNS)
    with db._connect() as conn:
        rows = conn.execute(
            f"SELECT {columns}, r.rating, r.votes FROM media m LEFT JOIN file_ratings r ON r.path = m.path "
            f"WHERE m.missing = 0 AND m.id IN ({', '.join('?' * len(wanted))})", wanted).fetchall()
    by_id = {row[0]: media_index._item(row) for row in rows}
    return {"items": [album_item(by_id[i]) for i in wanted if i in by_id]}


@router.get("/locate")
def locate(path: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """{has_gps, id?, lat?, lon?} for one file; the path is checked like every path from a request."""
    from backend.paths import resolve_in_vault
    resolved = str(resolve_in_vault(path, config))
    with db._connect() as conn:
        row = conn.execute("SELECT id, lat, lon FROM media WHERE path = ? AND missing = 0", (resolved,)).fetchone()
    if row is None or row[1] is None:
        return {"has_gps": False}
    return {"has_gps": True, "id": row[0], "lat": row[1], "lon": row[2]}


@router.get("/settings")
def get_settings() -> Dict[str, Any]:
    return {"osm_tiles": db.get_setting(SETTING_OSM, False) is True}


@router.put("/settings")
def put_settings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    value = payload.get("osm_tiles")
    if not isinstance(value, bool):
        _bad("osm_tiles moet waar of onwaar zijn")
    db.set_setting(SETTING_OSM, value)
    return {"osm_tiles": value}
