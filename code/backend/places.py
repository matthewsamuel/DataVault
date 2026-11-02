"""
DataVault Dashboard - Offline place lookup for the assistant

Turns a place name ("Parijs", "Frankrijk") into an area on the map, using only the
Natural Earth data that is vendored for the Kaart view (frontend/vendor/naturalearth):
the country outlines and the list of capitals and larger cities. No network, no geocoder.

- A city is a box of CITY_RADIUS_KM around its position.
- A country is its outline: photos are matched with a point-in-polygon test, so a
  photo in Belgium does not count for the Netherlands just because the boxes overlap.

Names are matched without accents and case. A country is found by its Dutch name (as on
the Kaart), its English Natural Earth name, or an alias below (Engeland, Amerika, ...);
the Dutch spelling of the common cities is in CITY_ALIASES.
"""
import json
import math
import threading
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DATA = Path(__file__).parent.parent / "frontend" / "vendor" / "naturalearth"
CITY_RADIUS_KM = 25.0

COUNTRY_ALIASES = {
    "nederland": "Netherlands", "belgie": "Belgium", "duitsland": "Germany", "frankrijk": "France",
    "spanje": "Spain", "italie": "Italy", "portugal": "Portugal", "oostenrijk": "Austria",
    "zwitserland": "Switzerland", "luxemburg": "Luxembourg", "engeland": "United Kingdom",
    "verenigd koninkrijk": "United Kingdom", "groot-brittannie": "United Kingdom", "schotland": "United Kingdom",
    "ierland": "Ireland", "ijsland": "Iceland", "noorwegen": "Norway", "zweden": "Sweden",
    "denemarken": "Denmark", "finland": "Finland", "polen": "Poland", "tsjechie": "Czechia",
    "slowakije": "Slovakia", "hongarije": "Hungary", "roemenie": "Romania", "bulgarije": "Bulgaria",
    "griekenland": "Greece", "turkije": "Turkey", "kroatie": "Croatia", "slovenie": "Slovenia",
    "servie": "Serbia", "montenegro": "Montenegro", "albanie": "Albania", "malta": "Malta",
    "cyprus": "Cyprus", "oekraine": "Ukraine", "rusland": "Russia", "estland": "Estonia",
    "letland": "Latvia", "litouwen": "Lithuania", "verenigde staten": "United States of America",
    "amerika": "United States of America", "vs": "United States of America", "usa": "United States of America",
    "canada": "Canada", "mexico": "Mexico", "brazilie": "Brazil", "argentinie": "Argentina",
    "chili": "Chile", "peru": "Peru", "colombia": "Colombia", "cuba": "Cuba", "marokko": "Morocco",
    "tunesie": "Tunisia", "egypte": "Egypt", "zuid-afrika": "South Africa", "kenia": "Kenya",
    "tanzania": "Tanzania", "china": "China", "japan": "Japan", "zuid-korea": "South Korea",
    "india": "India", "thailand": "Thailand", "vietnam": "Vietnam", "indonesie": "Indonesia",
    "maleisie": "Malaysia", "singapore": "Singapore", "filipijnen": "Philippines", "australie": "Australia",
    "nieuw-zeeland": "New Zealand", "israel": "Israel", "jordanie": "Jordan",
    "verenigde arabische emiraten": "United Arab Emirates", "suriname": "Suriname",
}

CITY_ALIASES = {
    "parijs": "Paris", "londen": "London", "wenen": "Vienna", "praag": "Prague", "brussel": "Brussels",
    "keulen": "Cologne", "athene": "Athens", "lissabon": "Lisbon", "kopenhagen": "Copenhagen",
    "moskou": "Moscow", "warschau": "Warsaw", "boedapest": "Budapest", "genève": "Geneva", "geneve": "Geneva",
    "rome": "Rome", "milaan": "Milan", "napels": "Naples", "venetie": "Venice", "florence": "Florence",
    "praag ": "Prague", "berlijn": "Berlin", "munchen": "Munich", "mnchen": "Munich", "hamburg": "Hamburg",
    "zurich": "Zurich", "istanboel": "Istanbul", "kaapstad": "Cape Town", "marrakech": "Marrakesh",
    "new york": "New York", "san francisco": "San Francisco", "los angeles": "Los Angeles",
    "tokio": "Tokyo", "peking": "Beijing", "sjanghai": "Shanghai", "bangkok": "Bangkok",
    "den haag": "The Hague", "luik": "Liege", "gent": "Ghent", "antwerpen": "Antwerp", "rijsel": "Lille",
    "sevilla": "Seville", "valencia": "Valencia", "edinburgh": "Edinburgh", "dublin": "Dublin",
    "oslo": "Oslo", "stockholm": "Stockholm", "helsinki": "Helsinki", "reykjavik": "Reykjavik",
}

_lock = threading.Lock()
_countries: Optional[List[Dict[str, Any]]] = None
_places: Optional[List[Tuple[str, float, float, bool]]] = None


def fold(text: str) -> str:
    """Lower case without accents, for comparing names."""
    text = unicodedata.normalize("NFKD", str(text)).casefold()
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


def _load() -> None:
    global _countries, _places
    with _lock:
        if _countries is not None:
            return
        countries: List[Dict[str, Any]] = []
        with open(DATA / "countries-50m.geojson", encoding="utf-8") as f:
            for feature in json.load(f)["features"]:
                geometry = feature["geometry"]
                polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
                rings = [(poly, _ring_bbox(poly[0])) for poly in polygons]
                props = feature["properties"]
                # 'n' is the Dutch name shown on the Kaart; 'e' the English Natural Earth name where it
                # differs. The English name stays the place's name (the alias tables point at it).
                countries.append({"name": props.get("e") or props["n"], "dutch": props["n"], "code": props.get("a"),
                                  "polygons": rings})
        with open(DATA / "places-50m.json", encoding="utf-8") as f:
            places = [(p[0], float(p[2]), float(p[1]), bool(p[4])) for p in json.load(f)]
        _places = places
        _countries = countries


def _ring_bbox(ring) -> Tuple[float, float, float, float]:
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return (min(lats), min(lons), max(lats), max(lons))   # south, west, north, east


def _in_ring(lat: float, lon: float, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Place:
    """An area: kind 'city' or 'country', its name, and a bounding box [south, west, north, east]."""

    def __init__(self, kind: str, name: str, bbox: List[float], polygons=None):
        self.kind, self.name, self.bbox, self.polygons = kind, name, bbox, polygons

    def contains(self, lat: float, lon: float) -> bool:
        s, w, n, e = self.bbox
        if not (s <= lat <= n and w <= lon <= e):
            return False
        if self.polygons is None:
            return True
        for poly, (ps, pw, pn, pe) in self.polygons:
            if ps <= lat <= pn and pw <= lon <= pe and _in_ring(lat, lon, poly[0]) \
                    and not any(_in_ring(lat, lon, hole) for hole in poly[1:]):
                return True
        return False


def find(name: str) -> Optional[Place]:
    """The place for a name (Dutch or English, with or without accents), or None."""
    _load()
    key = fold(name)
    if not key:
        return None
    english = COUNTRY_ALIASES.get(key)
    for country in _countries:
        if key in (fold(country["name"]), fold(country["dutch"])) or (english and country["name"] == english):
            boxes = [b for _, b in country["polygons"]]
            bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes)]
            return Place("country", country["name"], bbox, country["polygons"])
    city = CITY_ALIASES.get(key)
    best = None
    for pname, lat, lon, capital in _places:
        if fold(pname) == key or (city and pname == city):
            if best is None or capital:
                best = (pname, lat, lon)
    if best is None:
        return None
    pname, lat, lon = best
    dlat = CITY_RADIUS_KM / 111.0
    dlon = CITY_RADIUS_KM / (111.0 * max(0.2, math.cos(math.radians(lat))))
    return Place("city", pname, [lat - dlat, lon - dlon, lat + dlat, lon + dlon])


# Dutch display names for countries and cities that have a Dutch spelling (the aliases above
# are folded; these keep the accents)
_DUTCH_ACCENTS = {
    "belgie": "België", "italie": "Italië", "tsjechie": "Tsjechië", "roemenie": "Roemenië",
    "kroatie": "Kroatië", "slovenie": "Slovenië", "servie": "Servië", "albanie": "Albanië",
    "oekraine": "Oekraïne", "brazilie": "Brazilië", "argentinie": "Argentinië", "tunesie": "Tunesië",
    "indonesie": "Indonesië", "maleisie": "Maleisië", "australie": "Australië", "jordanie": "Jordanië",
    "israel": "Israël", "ijsland": "IJsland", "groot-brittannie": "Groot-Brittannië", "venetie": "Venetië", "genève": "Genève",
}


def _dutch(aliases: Dict[str, str]) -> Dict[str, str]:
    """English name -> the first Dutch alias for it, written with capitals and accents."""
    names: Dict[str, str] = {}
    for alias, english in aliases.items():
        if english in names or alias in ("vs", "usa", "amerika", "engeland", "schotland", "mnchen", "praag "):
            continue
        shown = _DUTCH_ACCENTS.get(alias) or " ".join(w[:1].upper() + w[1:] for w in alias.split(" "))
        names[english] = "-".join(p[:1].upper() + p[1:] for p in shown.split("-")) if "-" in shown else shown
    return names


def _nearest_coast(lat: float, lon: float, max_km: float = 30.0) -> Optional[str]:
    """The country whose outline passes closest (within max_km): the simplified outlines
    put harbours and beaches just outside the coast line."""
    pad = max_km / 111.0 * 2
    cos = max(0.2, math.cos(math.radians(lat)))
    best, best_km = None, max_km
    for c in _countries:
        for poly, (s, w, n, e) in c["polygons"]:
            if not (s - pad <= lat <= n + pad and w - pad / cos <= lon <= e + pad / cos):
                continue
            for x, y in poly[0]:
                km = math.hypot(y - lat, (x - lon) * cos) * 111.0
                if km < best_km:
                    best, best_km = c["code"], km
    return best


# ---------- a place name for every photo ----------
#
# backend/data/places_detail.json (scripts/build_map_regions.py) holds what the Kaart does not need
# but a single position does: the provinces as shapes (the same names as on the map), the coast line
# and every place of 15,000+ inhabitants. With numpy one lookup takes about a millisecond.

DETAIL = Path(__file__).parent / "data" / "places_detail.json"
KM_PER_DEG = 111.2
TOWN_KM = 30.0          # further from a town than this, the province names the place
COAST_KM = 2.0          # this close to the coast line: 'aan de kust'
COAST_TOWN_KM = 15.0    # on the coast and further from a town than this: the coast names the place
SHORE_KM = 1.0          # just outside the simplified coast (harbour, beach): still on land
OFFSHORE_KM = 30.0      # 'voor de kust van ...' up to here; beyond: 'op zee'
OPEN_SEA_KM = 150.0     # 'op zee, 80 km uit de kust van ...' up to here
DIRECTIONS = ("noorden", "noordoosten", "oosten", "zuidoosten", "zuiden", "zuidwesten", "westen", "noordwesten")

_detail: Optional[Dict[str, Any]] = None


def _load_detail() -> Dict[str, Any]:
    global _detail
    _load()
    with _lock:
        if _detail is not None:
            return _detail
        import numpy as np
        with open(DETAIL, encoding="utf-8") as f:
            raw = json.load(f)
        scale = float(raw["scale"])

        def ring(flat: List[int]):
            a = np.cumsum(np.asarray(flat, dtype=np.int64).reshape(-1, 2), axis=0) / scale
            return a if len(a) > 2 and (a[0] == a[-1]).all() else np.vstack([a, a[:1]])

        provinces, boxes = [], []
        for code, name, parts in raw["provinces"]:
            shapes = [[ring(r) for r in part] for part in parts]
            outer = np.vstack([part[0] for part in shapes])
            boxes.append([outer[:, 0].min(), outer[:, 1].min(), outer[:, 0].max(), outer[:, 1].max()])
            provinces.append((raw["codes"][code], name, shapes))
        a, b = [], []
        for flat in raw["coast"]:
            line = np.cumsum(np.asarray(flat, dtype=np.int64).reshape(-1, 2), axis=0) / scale
            a.append(line[:-1])
            b.append(line[1:])
        a, b = np.vstack(a), np.vstack(b)
        towns = raw["towns"]
        dutch_country = _dutch(COUNTRY_ALIASES)
        _detail = {
            "provinces": provinces, "boxes": np.asarray(boxes),
            "coast_a": a, "coast_b": b,
            "coast_lo": np.minimum(a, b), "coast_hi": np.maximum(a, b),
            "town_names": [t[0] for t in towns],
            "town_xy": np.asarray([[t[1] / scale, t[2] / scale] for t in towns]),
            "town_pop": np.asarray([t[3] for t in towns], dtype=np.float64),
            "town_code": [raw["codes"][t[4]] for t in towns],
            # Dutch country names: the common form ('Verenigde Staten') where the alias table has one
            "country_nl": {c["code"]: dutch_country.get(c["name"], c["dutch"]) for c in _countries},
        }
        return _detail


def _pip(x: float, y: float, ring) -> bool:
    xi, yi = ring[:-1, 0], ring[:-1, 1]
    xj, yj = ring[1:, 0], ring[1:, 1]
    cross = (yi > y) != (yj > y)
    import numpy as np
    with np.errstate(divide="ignore", invalid="ignore"):
        at = (xj - xi) * (y - yi) / (yj - yi) + xi
    return bool(np.count_nonzero(cross & (x < at)) % 2)


def _segment_km(lat: float, lon: float, a, b):
    """Distances in km from the point to the segments a[i]-b[i] (lon/lat rows), on a local flat map."""
    import numpy as np
    cos = max(0.05, math.cos(math.radians(lat)))
    ax, ay = (a[:, 0] - lon) * cos, a[:, 1] - lat
    bx, by = (b[:, 0] - lon) * cos, b[:, 1] - lat
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(length > 0, np.clip(-(ax * dx + ay * dy) / length, 0, 1), 0)
    return np.hypot(ax + t * dx, ay + t * dy) * KM_PER_DEG


def _province_at(d: Dict[str, Any], lat: float, lon: float) -> Optional[Tuple[str, str]]:
    import numpy as np
    box = d["boxes"]
    hits = np.nonzero((box[:, 0] <= lon) & (lon <= box[:, 2]) & (box[:, 1] <= lat) & (lat <= box[:, 3]))[0]
    for i in hits:
        code, name, shapes = d["provinces"][i]
        for part in shapes:
            if _pip(lon, lat, part[0]) and not any(_pip(lon, lat, hole) for hole in part[1:]):
                return code, name
    return None


def _nearest_province(d: Dict[str, Any], lat: float, lon: float, max_km: float) -> Optional[Tuple[str, str, float]]:
    import numpy as np
    pad_lat = max_km / KM_PER_DEG
    pad_lon = pad_lat / max(0.05, math.cos(math.radians(lat)))
    box = d["boxes"]
    near = np.nonzero((box[:, 0] - pad_lon <= lon) & (lon <= box[:, 2] + pad_lon)
                      & (box[:, 1] - pad_lat <= lat) & (lat <= box[:, 3] + pad_lat))[0]
    best = None
    for i in near:
        code, name, shapes = d["provinces"][i]
        for part in shapes:
            outer = part[0]
            km = float(_segment_km(lat, lon, outer[:-1], outer[1:]).min())
            if km <= max_km and (best is None or km < best[2]):
                best = (code, name, km)
    return best


def _coast_km(d: Dict[str, Any], lat: float, lon: float, max_km: float) -> Optional[float]:
    import numpy as np
    pad_lat = max_km / KM_PER_DEG
    pad_lon = pad_lat / max(0.05, math.cos(math.radians(lat)))
    lo, hi = d["coast_lo"], d["coast_hi"]
    near = (lo[:, 0] <= lon + pad_lon) & (hi[:, 0] >= lon - pad_lon) & (lo[:, 1] <= lat + pad_lat) & (hi[:, 1] >= lat - pad_lat)
    if not near.any():
        return None
    km = float(_segment_km(lat, lon, d["coast_a"][near], d["coast_b"][near]).min())
    return km if km <= max_km else None


def town_radius_km(pop_thousands: float) -> float:
    """How far from its centre a town still counts as 'in' it: 2 km at 15,000, 6 km at 1.5 million."""
    return 2.0 + 2.0 * math.log10(max(1.0, pop_thousands / 15.0))


def _town_near(d: Dict[str, Any], lat: float, lon: float, code: Optional[str],
               weigh: float = 0.3) -> Optional[Dict[str, Any]]:
    """The town that names a position: the largest one the position lies in, else the nearest within
    TOWN_KM, with larger towns weighing a bit heavier (a hamlet next to Faro is 'bij Faro'). At sea
    (weigh 0) simply the nearest: the harbour you see from the boat."""
    import numpy as np
    xy = d["town_xy"]
    cos = max(0.05, math.cos(math.radians(lat)))
    dx, dy = (xy[:, 0] - lon) * cos * KM_PER_DEG, (xy[:, 1] - lat) * KM_PER_DEG
    near = np.nonzero((np.abs(dy) <= TOWN_KM) & (np.abs(dx) <= TOWN_KM))[0]
    if code is not None:
        near = [i for i in near if d["town_code"][i] == code]   # a town across a border names nothing here
    best, best_score, inside = None, None, None
    for i in near:
        km = math.hypot(dx[i], dy[i])
        if km > TOWN_KM:
            continue
        pop = float(d["town_pop"][i])
        if km <= town_radius_km(pop):
            if inside is None or pop > float(d["town_pop"][inside[0]]):
                inside = (i, km)
            continue
        score = km / (pop / 15.0) ** weigh
        if best_score is None or score < best_score:
            best, best_score = (i, km), score
    pick = inside or best
    if pick is None:
        return None
    i, km = pick
    # direction from the town to the position, in eight compass points
    bearing = (math.degrees(math.atan2(-dx[i], -dy[i])) + 360.0) % 360.0
    return {"name": d["town_names"][i], "km": km, "inside": inside is not None,
            "direction": DIRECTIONS[int((bearing + 22.5) // 45) % 8], "code": d["town_code"][i]}


def _km_text(km: float) -> str:
    return f"{max(1, round(km))} km"


def describe_parts(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Where a position is, in Dutch and offline: {label, town, km, direction, region, country, coast, sea}.
    label is the one line the viewer and Pasha show:
      - in a town: 'Faro, Portugal'
      - near one (TOWN_KM): '12 km ten oosten van Faro, Portugal'
      - in the countryside: 'Gelderland, Nederland', or on the coast 'Aan de kust van Kerry, Ierland'
      - off the coast: 'Voor de kust bij Den Helder, Nederland' / 'Voor de kust van Bretagne, Frankrijk'
      - far out: 'Op zee, 80 km uit de kust van Portugal' or 'Op zee'
    None for a position that is no real place (0, 0 is what a broken GPS writes) or out of range.
    """
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
        return None
    d = _load_detail()
    names = d["country_nl"]
    found = _province_at(d, lat, lon)
    code, region, sea, offshore = (found[0], found[1], False, None) if found else (None, None, True, None)
    if found is None:
        # The province shapes are simplified to about a kilometre: a harbour or beach can fall just outside
        near = _nearest_province(d, lat, lon, OFFSHORE_KM)
        if near is not None:
            code, region, offshore = near
            sea = offshore > SHORE_KM
        else:
            for c in _countries:
                if Place("country", c["name"], [-90, -180, 90, 180], c["polygons"]).contains(lat, lon):
                    code, sea = c["code"], False
                    break
    coast_km = _coast_km(d, lat, lon, max(COAST_KM, OFFSHORE_KM))
    coast = coast_km is not None and coast_km <= COAST_KM
    country = names.get(code) if code else None
    if region and (fold(region) == fold(country or "") or not region.strip()):
        region = None
    town = _town_near(d, lat, lon, code, 0.0 if sea else 0.3) if code else None
    parts: Dict[str, Any] = {"town": town and town["name"], "km": town and round(town["km"], 1),
                             "direction": town and not town["inside"] and town["direction"] or None,
                             "region": region, "country": country, "coast": bool(coast or (sea and code)),
                             "sea": sea}
    tail = f", {country}" if country else ""
    if sea:
        if code and town:
            label = f"Voor de kust bij {town['name']}{tail}"
        elif code:
            label = f"Voor de kust van {region}{tail}" if region else f"Voor de kust van {country}"
        else:
            far = _nearest_coast(lat, lon, OPEN_SEA_KM)
            far_nl = names.get(far) if far else None
            km = _coast_km(d, lat, lon, OPEN_SEA_KM) if far_nl else None
            label = f"Op zee, {_km_text(km)} uit de kust van {far_nl}" if far_nl and km else "Op zee"
            parts["country"] = None
    elif town and town["inside"]:
        label = f"{town['name']}{tail}"
    elif town and not (coast and town["km"] > COAST_TOWN_KM):
        label = f"{_km_text(town['km'])} ten {town['direction']} van {town['name']}{tail}"
    elif coast:
        label = f"Aan de kust van {region}{tail}" if region else f"Aan de kust van {country}"
    elif region:
        label = f"{region}{tail}"
    elif country:
        label = country
    else:
        return None
    parts["label"] = label
    return parts


def describe(lat: float, lon: float) -> Optional[str]:
    """A short Dutch name for a position, offline (see describe_parts): 'Lissabon, Portugal',
    '12 km ten oosten van Faro, Portugal', 'Aan de kust van Zeeland, Nederland', ..."""
    parts = describe_parts(lat, lon)
    return parts["label"] if parts else None
