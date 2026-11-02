"""Bouwt de offline kaartdata in frontend/vendor/naturalearth/ opnieuw op.

Bronnen (eenmalig downloaden in een werkmap, niet in de repo):
  - Natural Earth (publiek domein), GitHub nvkelso/natural-earth-vector, map geojson/:
      ne_50m_admin_0_countries.geojson, ne_10m_admin_1_states_provinces.geojson,
      ne_10m_populated_places.geojson
  - GeoNames cities15000.txt (CC BY 4.0), https://download.geonames.org/export/dump/cities15000.zip

Uitvoer:
  - countries-50m.geojson: landen met n (Nederlandse naam) en a (ADM0_A3), coördinaten op 2 decimalen.
  - regions/<A3>.json per land: de grenzen tussen provincies (lijnen, zonder de buitengrens),
    plekken voor de provincienamen en de grote steden.
    De kaart laadt zo'n bestand pas als het land aangeduid wordt of in beeld is bij ver inzoomen.

Gebruik: python3 scripts/build_map_regions.py <werkmap-met-bronnen> [uitvoermap]
Vereist shapely (systeem-python3 heeft het; de venv van de app niet, en heeft het ook niet nodig).
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import linemerge, unary_union

# Landen met heel veel kleine eenheden: samenvoegen tot de regio's die mensen kennen
DISSOLVE = {'FRA': 'region', 'ITA': 'region', 'GBR': 'geonunit', 'SVN': 'region', 'LVA': 'region',
            'MLT': 'region', 'PHL': 'region'}
REGION_NL = {
    'Toscana': 'Toscane', 'Lombardia': 'Lombardije', 'Piemonte': 'Piëmont', 'Sicily': 'Sicilië',
    'Sicilia': 'Sicilië', 'Sardegna': 'Sardinië', 'Apulia': 'Apulië', 'Campania': 'Campanië',
    'Calabria': 'Calabrië', 'Umbria': 'Umbrië', 'Liguria': 'Ligurië', 'Abruzzo': 'Abruzzen',
    'Trentino-Alto Adige': 'Trentino-Zuid-Tirol', 'Corse': 'Corsica', 'Guyane française': 'Frans-Guyana',
    'England': 'Engeland', 'Scotland': 'Schotland', 'Northern Ireland': 'Noord-Ierland',
}
PROVINCE_NL = {('BEL', 'Brussels'): 'Brussel', ('FRA', 'Nord'): 'Nord'}
CITY_NL = {
    'Brussels': 'Brussel', 'Antwerp': 'Antwerpen', 'Ghent': 'Gent', 'Bruges': 'Brugge', 'Ostend': 'Oostende',
    'Liège': 'Luik', 'Namur': 'Namen', 'Mons': 'Bergen', 'Tournai': 'Doornik', 'Mouscron': 'Moeskroen',
    'Arlon': 'Aarlen', 'Wavre': 'Waver', 'Louvain': 'Leuven', 'Courtrai': 'Kortrijk', 'Malines': 'Mechelen',
}
SKIP_FCODES = {'PPLX', 'PPLH', 'PPLQ', 'PPLW', 'PPLCH', 'PPLR'}
CITY_MIN_POP = 50000
PROVINCE_SEAT_MIN_POP = 15000
CITY_MAX = 120
NEAR_KM = 8          # een kleinere stad zo dicht bij een grotere is een deelgemeente of voorstad


def rnd(coords, digits):
    """Rondt geneste coördinaten af en laat punten weg die daardoor dubbel worden."""
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], digits), round(coords[1], digits)]
    out = [rnd(c, digits) for c in coords]
    if out and isinstance(out[0][0], (int, float)):
        out = [p for i, p in enumerate(out) if i == 0 or p != out[i - 1]]
    return out


def geom(g, digits):
    m = mapping(g)
    return {'type': m['type'], 'coordinates': rnd(json.loads(json.dumps(m['coordinates'])), digits)}


def km(a, b):
    dx = (a[0] - b[0]) * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(dx, a[1] - b[1]) * 111.2


def clean_name(a3, raw, nl):
    if (a3, raw) in PROVINCE_NL:
        return PROVINCE_NL[(a3, raw)]
    name = (nl or raw or '').strip()
    for prefix in ('Provincie ', 'Province of ', 'Provinz '):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return REGION_NL.get(name, name)


def build_countries(src, out):
    data = json.loads((src / 'ne_50m_admin_0_countries.geojson').read_text())
    feats = []
    iso2 = {}
    for f in data['features']:
        p = f['properties']
        a3 = p['ADM0_A3']
        feats.append({'type': 'Feature', 'properties': {'n': p.get('NAME_NL') or p['NAME'], 'a': a3},
                      'geometry': {'type': f['geometry']['type'], 'coordinates': rnd(f['geometry']['coordinates'], 2)}})
        code = p.get('ISO_A2_EH') or p.get('ISO_A2')
        if code and code != '-99':
            iso2.setdefault(code, a3)
    (out / 'countries-50m.geojson').write_text(
        json.dumps({'type': 'FeatureCollection', 'features': feats}, ensure_ascii=False, separators=(',', ':')))
    return iso2


def build_provinces(src):
    data = json.loads((src / 'ne_10m_admin_1_states_provinces.geojson').read_text())
    groups = defaultdict(lambda: defaultdict(list))   # a3 -> naam -> [(geometrie, labelpunt)]
    for f in data['features']:
        p = f['properties']
        a3 = p['adm0_a3']
        g = shape(f['geometry']).buffer(0)
        key = DISSOLVE.get(a3)
        if key and p.get(key):
            name = clean_name(a3, p[key], None)
            label = None
        else:
            name = clean_name(a3, p['name'], p.get('name_nl'))
            label = (p['longitude'], p['latitude']) if p.get('longitude') is not None else None
        groups[a3][name].append((g, label))
    result = {}
    for a3, units in groups.items():
        polys = {name: unary_union([g for g, _ in parts]) for name, parts in units.items()}
        whole = unary_union(list(polys.values()))
        minx, miny, maxx, maxy = whole.bounds
        tol = max(0.004, math.hypot(maxx - minx, maxy - miny) / 900)
        digits = 3 if tol < 0.02 else 2
        labels = []
        for name, g in polys.items():
            pts = [lab for _, lab in units[name] if lab]
            if len(pts) == 1 and g.buffer(tol).contains(shape({'type': 'Point', 'coordinates': pts[0]})):
                at = pts[0]
            else:
                # het grootste deel van de regio, zodat de naam niet op een eilandje belandt
                main = max(getattr(g, 'geoms', [g]), key=lambda x: x.area)
                rp = main.representative_point()
                at = (rp.x, rp.y)
            labels.append([name, round(at[0], 3), round(at[1], 3), round(g.area, 4)])
        borders = None
        if len(polys) > 1:
            inner = unary_union([g.boundary for g in polys.values()]).difference(whole.boundary.buffer(tol / 2))
            inner = inner.simplify(tol, preserve_topology=False)
            if not inner.is_empty:
                if inner.geom_type in ('MultiLineString', 'GeometryCollection'):
                    lines = [x for x in getattr(inner, 'geoms', []) if x.geom_type == 'LineString']
                    inner = linemerge(lines) if lines else inner
                borders = geom(inner, digits) if inner.geom_type in ('LineString', 'MultiLineString') else None
        labels.sort(key=lambda x: -x[3])           # grote provincies eerst: die krijgen bij weinig plek voorrang
        result[a3] = {'borders': borders, 'labels': [x[:3] for x in labels]}
    return result


def build_cities(src, iso2):
    ne = defaultdict(list)       # a3 -> [(lon/lat, namen, Nederlandse naam, hoofdplaats van een provincie, inwoners)]
    for f in json.loads((src / 'ne_10m_populated_places.geojson').read_text())['features']:
        p = f['properties']
        names = {p.get(k) for k in ('NAME', 'NAMEASCII', 'NAME_EN', 'NAMEALT')} - {None, ''}
        ne[p['ADM0_A3']].append(((p['LONGITUDE'], p['LATITUDE']), names, p.get('NAME_NL'),
                                 p['FEATURECLA'] == 'Admin-1 capital', int(p.get('POP_MAX') or 0)))
    raw = defaultdict(list)
    with open(src / 'cities15000.txt', encoding='utf-8') as fh:
        for line in fh:
            c = line.rstrip('\n').split('\t')
            fcode, pop = c[7], int(c[14] or 0)
            a3 = iso2.get(c[8])
            if not a3 or fcode in SKIP_FCODES:
                continue
            if pop < CITY_MIN_POP and not (fcode == 'PPLA' and pop >= PROVINCE_SEAT_MIN_POP) and fcode != 'PPLC':
                continue
            raw[a3].append((c[1], float(c[5]), float(c[4]), fcode, pop, {c[1], c[2], *c[3].split(',')}))
    result = {}
    for a3 in set(raw) | set(ne):
        cities = raw.get(a3, [])
        # Hoofdplaatsen van provincies die GeoNames te klein vond (Aarlen) komen uit Natural Earth
        for at, names, nl, seat, pop in ne.get(a3, []):
            if seat and not any(km(at, (c[1], c[2])) < NEAR_KM for c in cities):
                cities.append((nl or min(names), at[0], at[1], 'PPLA', pop, names))
        cities.sort(key=lambda x: (x[3] != 'PPLC', -x[4]))
        kept = []
        for name, lon, lat, fcode, pop, names in cities:
            if len(kept) >= CITY_MAX:
                break
            if fcode != 'PPLC' and any(km((lon, lat), (k[1], k[2])) < NEAR_KM for k in kept):
                continue
            # Nederlandse naam van Natural Earth, alleen als het echt dezelfde plaats is
            nl = next((n[2] for n in ne.get(a3, []) if n[2] and n[1] & names and km((lon, lat), n[0]) < 15), None)
            name = CITY_NL.get(name, nl or name)
            zoom = 4 if pop >= 1e6 else 5 if pop >= 5e5 else 6 if pop >= 2e5 else 7 if pop >= 1e5 else 8
            if fcode in ('PPLA', 'PPLC'):
                zoom = min(zoom, 7)
            kept.append([name, round(lon, 3), round(lat, 3), zoom, 1 if fcode == 'PPLC' else 0])
        if kept:
            result[a3] = kept
    return result


def main():
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / 'frontend/vendor/naturalearth'
    iso2 = build_countries(src, out)
    provinces = build_provinces(src)
    cities = build_cities(src, iso2)
    folder = out / 'regions'
    folder.mkdir(exist_ok=True)
    for old in folder.glob('*.json'):
        old.unlink()
    total = 0
    for a3 in sorted(set(provinces) | set(cities)):
        p = provinces.get(a3, {'borders': None, 'labels': []})
        body = json.dumps({**p, 'cities': cities.get(a3, [])}, ensure_ascii=False, separators=(',', ':'))
        (folder / f'{a3}.json').write_text(body)
        total += len(body)
    print(f'{len(provinces)} landen met provincies, {len(cities)} met steden, {total / 1e6:.1f} MB in {folder}')


if __name__ == '__main__':
    main()
