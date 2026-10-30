"""
DataVault Dashboard - Offline place lookup for the assistant

Turns a place name ("Parijs", "Frankrijk") into an area on the map, using only the
Natural Earth data that is vendored for the Kaart view (frontend/vendor/naturalearth):
the country outlines and the list of capitals and larger cities. No network, no geocoder.

- A city is a box of CITY_RADIUS_KM around its position.
- A country is its outline: photos are matched with a point-in-polygon test, so a
  photo in Belgium does not count for the Netherlands just because the boxes overlap.

Names are matched without accents and case; the Dutch spelling of the common countries
and cities is in the alias tables below.
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
                countries.append({"name": feature["properties"]["n"], "polygons": rings})
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
        if fold(country["name"]) == key or (english and country["name"] == english):
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
    "israel": "Israël", "groot-brittannie": "Groot-Brittannië", "venetie": "Venetië", "genève": "Genève",
}
DESCRIBE_CITY_KM = 40.0


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
                    best, best_km = c["name"], km
    return best


def describe(lat: float, lon: float) -> Optional[str]:
    """
    A short Dutch name for a position, offline: the nearest larger city within
    DESCRIBE_CITY_KM and the country it lies in ('Lissabon, Portugal'), the country
    alone, or None at sea or outside the data.
    """
    _load()
    country = None
    for c in _countries:
        if Place("country", c["name"], [-90, -180, 90, 180], c["polygons"]).contains(lat, lon):
            country = c["name"]
            break
    if country is None:
        country = _nearest_coast(lat, lon)
    best, best_km = None, DESCRIBE_CITY_KM
    cos = max(0.2, math.cos(math.radians(lat)))
    for pname, plat, plon, _capital in _places:
        km = math.hypot(plat - lat, (plon - lon) * cos) * 111.0
        if km < best_km:
            best, best_km = pname, km
    country_nl = _dutch(COUNTRY_ALIASES).get(country, country) if country else None
    city_nl = _dutch(CITY_ALIASES).get(best, best) if best else None
    if city_nl and country_nl:
        return f"{city_nl}, {country_nl}"
    return country_nl or city_nl
