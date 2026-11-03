"""
DataGatherer - which time zone a place is in, offline

A photo's EXIF capture time is the camera's local wall-clock time; location history is
in UTC. To match the two, geotag.py needs the UTC offset at the place and moment the
photo was taken. Without a network and without a big time-zone-boundary file this is
done in two cheap steps:

1. the country, by a point-in-polygon test on the vendored Natural Earth 1:50m country
   outlines (frontend/vendor/naturalearth/countries-50m.geojson, also used by the Kaart);
2. the IANA zone of that country closest to the point, from backend/data/country_zones.json,
   generated once from the tz database's zone.tab and iso3166.tab (public domain). For a
   country with one zone (all of Western Europe) this is exact; for big countries (the US,
   Russia, Brazil, ...) the nearest zone city is a good approximation.

A point at sea or just outside the 1:50m outline falls back to the nearest zone city
overall (zone_at then says how='dichtstbij'). Python's zoneinfo (the system tz database)
turns the zone into an offset, with daylight saving time.
"""
import json
import math
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BACKEND = Path(__file__).resolve().parent.parent
COUNTRIES_GEOJSON = BACKEND.parent / "frontend" / "vendor" / "naturalearth" / "countries-50m.geojson"
COUNTRY_ZONES = BACKEND / "data" / "country_zones.json"
DEFAULT_ZONE = "Europe/Brussels"
NEAREST_MAX_KM = 400        # beyond this from any zone city (open ocean): no zone

_lock = threading.Lock()
_data: Optional[dict] = None


def _load() -> dict:
    global _data
    with _lock:
        if _data is None:
            shapes = []
            try:
                geo = json.loads(COUNTRIES_GEOJSON.read_text(encoding="utf-8"))
                for feature in geo.get("features", []):
                    geom = feature.get("geometry") or {}
                    polys = geom.get("coordinates") or []
                    if geom.get("type") == "Polygon":
                        polys = [polys]
                    elif geom.get("type") != "MultiPolygon":
                        continue
                    for poly in polys:
                        ring = poly[0] if poly else []
                        if len(ring) < 3:
                            continue
                        lons = [p[0] for p in ring]
                        lats = [p[1] for p in ring]
                        props = feature.get("properties") or {}
                        # country_zones.json is keyed by the English Natural Earth name: 'e' where the
                        # Dutch name 'n' differs from it (België / Belgium), else 'n' itself.
                        shapes.append((props.get("e") or props.get("n"), props.get("n"),
                                       (min(lats), min(lons), max(lats), max(lons)), poly))
            except (OSError, ValueError, KeyError):
                shapes = []
            try:
                zones = json.loads(COUNTRY_ZONES.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                zones = {"countries": {}, "all": []}
            _data = {"shapes": shapes, "countries": zones.get("countries", {}), "all": zones.get("all", [])}
        return _data


def _in_ring(lat: float, lon: float, ring: List[List[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _country(lat: float, lon: float) -> Optional[Tuple[str, str]]:
    """(English Natural Earth name, Dutch name) of the country at this point, or None."""
    for english, dutch, (s, w, n, e), poly in _load()["shapes"]:
        if not (s <= lat <= n and w <= lon <= e):
            continue
        if _in_ring(lat, lon, poly[0]) and not any(_in_ring(lat, lon, hole) for hole in poly[1:]):
            return english, dutch
    return None


def country_at(lat: float, lon: float) -> Optional[str]:
    """The Dutch name of the country at this point (as on the Kaart), or None (sea, or no data)."""
    found = _country(lat, lon)
    return found[1] if found else None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _nearest(lat: float, lon: float, zones: List[List]) -> Tuple[Optional[str], float]:
    best, best_km = None, float("inf")
    for zone, zlat, zlon in zones:
        d = _km(lat, lon, zlat, zlon)
        if d < best_km:
            best, best_km = zone, d
    return best, best_km


def zone_at(lat: float, lon: float) -> Tuple[Optional[str], str]:
    """
    (IANA zone, how) for a point: how is 'land' (the country's zone closest to the point),
    'dichtstbij' (no country outline here: the nearest zone city within NEAREST_MAX_KM) or
    'onbekend' (zone None).
    """
    data = _load()
    country = _country(lat, lon)
    zones = data["countries"].get(country[0]) if country else None
    if zones:
        return (zones[0][0] if len(zones) == 1 else _nearest(lat, lon, zones)[0]), "land"
    zone, km = _nearest(lat, lon, data["all"])
    if zone is not None and km <= NEAREST_MAX_KM:
        return zone, "dichtstbij"
    return None, "onbekend"


def cache_info() -> Dict[str, int]:
    data = _load()
    return {"shapes": len(data["shapes"]), "countries": len(data["countries"])}
