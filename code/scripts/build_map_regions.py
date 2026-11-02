"""Bouwt de offline kaartdata in frontend/vendor/naturalearth/ opnieuw op.

Bronnen (eenmalig downloaden in een werkmap, niet in de repo):
  - Natural Earth (publiek domein), GitHub nvkelso/natural-earth-vector, map geojson/:
      ne_50m_admin_0_countries.geojson, ne_10m_admin_1_states_provinces.geojson,
      ne_10m_populated_places.geojson
  - GeoNames cities15000.txt (CC BY 4.0), https://download.geonames.org/export/dump/cities15000.zip
  - GeoNames cities1000.txt (CC BY 4.0), https://download.geonames.org/export/dump/cities1000.zip

Uitvoer:
  - countries-50m.geojson: landen met n (Nederlandse naam), e (de Engelse Natural Earth-naam, alleen
    als die anders is), a (ADM0_A3), l (het punt voor de landnaam: de pool van ontoegankelijkheid van
    het grootste deel, in Mercator zoals de kaart, dus Frankrijk zonder Frans-Guyana), z (vanaf welk
    zoomniveau Natural Earth de naam toont) en k (LABELRANK, lager = belangrijker); coördinaten op 2 decimalen.
  - regions/<A3>.json per land: de grenzen tussen provincies (lijnen, zonder de buitengrens),
    plekken voor de provincienamen en de grote steden.
    De kaart laadt zo'n bestand pas als het land aangeduid wordt of in beeld is bij ver inzoomen.
  - regions/towns/<A3>.json per land: de kleinere plaatsen (vanaf 1.000 inwoners) als [naam, lon, lat, zoom],
    grootste eerst. De kaart laadt ze pas vanaf zoom 8 en toont een plaats waar foto's genomen zijn eerder.
  - ../../../backend/data/places_detail.json (alleen voor de server, backend/places.describe): de
    provincies als vlakken (met hun soort: provincie, staat, regio, ...), de kustlijn en de plaatsen vanaf 15.000 inwoners, voor 'een plaatsnaam
    bij elke foto' ('12 km ten oosten van Faro, Portugal'). Coördinaten als gehele getallen in
    duizendsten van een graad, per ring het eerste punt en daarna de verschillen.

Gebruik: python3 scripts/build_map_regions.py <werkmap-met-bronnen> [uitvoermap] [map-voor-places_detail.json]
Vereist shapely (systeem-python3 heeft het; de venv van de app niet, en heeft het ook niet nodig).
"""

import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import linemerge, polylabel, unary_union

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
PROVINCE_NL = {('BEL', 'Brussels'): 'Brussel', ('FRA', 'Nord'): 'Nord',
               # Natural Earth vertaalt de Deense hoofdstadregio letterlijk ('Hoofdstad'); Nederlands gebruikt de Deense naam
               ('DNK', 'Hovedstaden'): 'Hovedstaden', ('DNK', 'Sjaælland'): 'Seeland',
               # één spelling voor de stad en haar regio, zoals de kaart de stad noemt
               ('MAR', 'Marrakech - Tensift - Al Haouz'): 'Marrakesh-Tensift-Al Haouz'}
# Zweedse län: de naam zonder 'län' (Natural Earth's Nederlandse naam is 'Stockholms län')
RAW_NAME = {'SWE'}
RAW_FIX = {('SWE', 'Orebro'): 'Örebro'}
# Wat voor gebied het is, voor 'provincie Gelderland' bij een foto (alleen de gewone soorten)
KIND_NL = {'Province': 'provincie', 'State': 'staat', 'Region': 'regio', 'District': 'district', 'County': 'graafschap',
           'Department': 'departement', 'Canton': 'kanton', 'Prefecture': 'prefectuur', 'Voivodeship|Province': 'woiwodschap'}
KIND_BY_COUNTRY = {'ESP': 'provincie', 'DEU': 'deelstaat', 'AUT': 'deelstaat', 'FRA': 'regio', 'ITA': 'regio', 'GBR': ''}
# Een fylke (Noorwegen) en een län (Zweden) heten in het Nederlands provincie, geen graafschap
KIND_COUNTY = {'NOR': 'provincie', 'SWE': 'provincie'}
CITY_NL = {
    'Brussels': 'Brussel', 'Antwerp': 'Antwerpen', 'Ghent': 'Gent', 'Bruges': 'Brugge', 'Ostend': 'Oostende',
    'Liège': 'Luik', 'Namur': 'Namen', 'Mons': 'Bergen', 'Tournai': 'Doornik', 'Mouscron': 'Moeskroen',
    'Arlon': 'Aarlen', 'Wavre': 'Waver', 'Louvain': 'Leuven', 'Courtrai': 'Kortrijk', 'Malines': 'Mechelen',
    'Almere Stad': 'Almere',    # GeoNames noemt het centrum zo; iedereen zegt Almere
}
# Tweetalige Brusselse gemeenten: GeoNames geeft de Franse naam, de kaart toont de Nederlandse
TOWN_NL = {('BEL', k): v for k, v in {
    'Uccle': 'Ukkel', 'Forest': 'Vorst', 'Woluwe-Saint-Lambert': 'Sint-Lambrechts-Woluwe',
    'Woluwe-Saint-Pierre': 'Sint-Pieters-Woluwe', 'Auderghem': 'Oudergem', 'Ixelles': 'Elsene',
    'Schaerbeek': 'Schaarbeek', 'Molenbeek-Saint-Jean': 'Sint-Jans-Molenbeek', 'Saint-Gilles': 'Sint-Gillis',
    'Berchem-Sainte-Agathe': 'Sint-Agatha-Berchem', 'Watermael-Boitsfort': 'Watermaal-Bosvoorde',
    'Saint-Josse-ten-Noode': 'Sint-Joost-ten-Node',
}.items()}
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
    for prefix in ('Provincie ', 'Province of ', 'Provinz ', 'Woiwodschap ', 'Changwat ', 'Kraj '):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return REGION_NL.get(name, name)


def _merc(lat):
    lat = max(-85.0, min(85.0, lat))
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def _unmerc(y):
    return math.degrees(2 * math.atan(math.exp(math.radians(y))) - math.pi / 2)


def true_area(g):
    """Oppervlakte in vierkante graden op de breedte van het vlak zelf (gelijkvlakkig genoeg om te vergelijken)."""
    return g.area * math.cos(math.radians(g.centroid.y))


def label_point(g, preferred=None):
    """Het punt voor een naam: de pool van ontoegankelijkheid van het grootste deel (naar echte oppervlakte),
    berekend in Mercator, want zo staat het op de kaart. Een land met overzeese delen krijgt zo zijn naam
    midden op het moederland. Een met de hand gekozen punt van Natural Earth (preferred) wint als het in
    dat deel ligt met minstens de helft van de ruimte rondom die de pool heeft (Chili: midden in het land,
    niet in de brede Atacama)."""
    main = max(getattr(g, 'geoms', [g]), key=true_area)
    ring = [(x, _merc(y)) for x, y in main.exterior.coords]
    holes = [[(x, _merc(y)) for x, y in h.coords] for h in main.interiors]
    flat = Polygon(ring, holes).buffer(0)
    if flat.geom_type == 'MultiPolygon':
        flat = max(flat.geoms, key=lambda x: x.area)
    span = max(flat.bounds[2] - flat.bounds[0], flat.bounds[3] - flat.bounds[1])
    pole = polylabel(flat, tolerance=max(0.005, span / 200))
    if preferred:
        hand = Point(preferred[0], _merc(preferred[1]))
        if flat.contains(hand) and flat.boundary.distance(hand) >= 0.5 * flat.boundary.distance(pole):
            return preferred
    return (pole.x, _unmerc(pole.y))


def build_countries(src, out):
    data = json.loads((src / 'ne_50m_admin_0_countries.geojson').read_text())
    feats = []
    iso2 = {}
    for f in data['features']:
        p = f['properties']
        a3 = p['ADM0_A3']
        props = {'n': p.get('NAME_NL') or p['NAME']}
        if props['n'] != p['NAME']:
            props['e'] = p['NAME']      # the English name: Pasha's place search and backend/data/country_zones.json
        props['a'] = a3
        hand = (p['LABEL_X'], p['LABEL_Y']) if p.get('LABEL_X') is not None else None
        lx, ly = label_point(shape(f['geometry']).buffer(0), hand)
        props['l'] = [round(lx, 2), round(ly, 2)]
        props['z'] = round(float(p.get('MIN_LABEL') or 5), 1)
        props['k'] = int(p.get('LABELRANK') or 9)
        feats.append({'type': 'Feature', 'properties': props,
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
    kinds = {}                                        # a3 -> naam -> 'provincie', 'staat', ... of ''
    for f in data['features']:
        p = f['properties']
        a3 = p['adm0_a3']
        g = shape(f['geometry']).buffer(0)
        key = DISSOLVE.get(a3)
        if key and p.get(key):
            name = clean_name(a3, p[key], None)
            label = None
        else:
            raw = RAW_FIX.get((a3, p['name']), p['name'])
            name = clean_name(a3, raw, None if a3 in RAW_NAME else p.get('name_nl'))
            label = (p['longitude'], p['latitude']) if p.get('longitude') is not None else None
        groups[a3][name].append((g, label))
        kind = KIND_COUNTY.get(a3) if p.get('type_en') == 'County' and a3 in KIND_COUNTY else None
        kinds.setdefault(a3, {})[name] = kind or KIND_BY_COUNTRY.get(a3, KIND_NL.get(p.get('type_en') or '', ''))
    result, shapes = {}, {}
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
                at = label_point(g)
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
        shapes[a3] = {name: (g, kinds[a3].get(name, '')) for name, g in polys.items()}
    return result, shapes


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


TOWN_MIN_POP = 15000
TOWN_NEAR_KM = 3          # dichter bij een grotere plaats: een wijk daarvan
DISTRICT_KM = 25          # zo ver kan een genummerd stadsdeel van zijn stad liggen
DETAIL_TOL = 0.01          # ~1 km: genoeg om te zeggen in welke provincie en hoe ver van de kust
DETAIL_SCALE = 1000        # coördinaten in duizendsten van een graad
ISLAND_MIN = 0.0005        # kleinere eilandjes (vierkante graden, ~6 km2) laat de kustlijn weg


MAP_TOWN_MIN_POP = 1000
MAP_TOWN_NEAR_KM = 1.5    # dichter bij een grotere plaats: een wijk of gehucht daarvan
MAP_TOWN_CELL = 0.05      # graden: het rooster om nabije plaatsen snel te vinden


def build_map_towns(src, iso2, cities):
    """De kleinere plaatsen voor de kaart: alles vanaf MAP_TOWN_MIN_POP inwoners dat niet al een stad op de kaart
    is of er vlak naast ligt, als [naam, lon, lat, zoom] (zoom 9, 10 of 11 naar inwoners), grootste eerst."""
    ne_nl = defaultdict(list)
    for f in json.loads((src / 'ne_10m_populated_places.geojson').read_text())['features']:
        p = f['properties']
        if p.get('NAME_NL'):
            names = {p.get(k) for k in ('NAME', 'NAMEASCII', 'NAME_EN', 'NAMEALT')} - {None, ''}
            ne_nl[p['ADM0_A3']].append(((p['LONGITUDE'], p['LATITUDE']), names, p['NAME_NL']))
    raw = defaultdict(list)
    with open(src / 'cities1000.txt', encoding='utf-8') as fh:
        for line in fh:
            c = line.rstrip('\n').split('\t')
            fcode, pop = c[7], int(c[14] or 0)
            a3 = iso2.get(c[8])
            if not a3 or fcode in SKIP_FCODES or pop < MAP_TOWN_MIN_POP:
                continue
            raw[a3].append((c[1], float(c[5]), float(c[4]), pop, {c[1], c[2], *c[3].split(',')}))
    result = {}
    for a3, rows in raw.items():
        grid = defaultdict(list)

        def near(at, dist):
            gx, gy = int(at[0] // MAP_TOWN_CELL), int(at[1] // MAP_TOWN_CELL)
            return any(km(at, k) < dist for x in (gx - 1, gx, gx + 1) for y in (gy - 1, gy, gy + 1) for k in grid[(x, y)])

        def keep(at):
            grid[(int(at[0] // MAP_TOWN_CELL), int(at[1] // MAP_TOWN_CELL))].append(at)

        for k in cities.get(a3, []):
            keep((k[1], k[2]))
        # de steden zelf: hun deelgemeenten binnen NEAR_KM horen erbij (de kaart toont alleen de stad)
        city_at = [(k[1], k[2]) for k in cities.get(a3, [])]
        kept = []
        for name, lon, lat, pop, names in sorted(rows, key=lambda r: -r[3]):
            at = (lon, lat)
            if near(at, MAP_TOWN_NEAR_KM) or any(km(at, c) < 3 for c in city_at):
                continue
            if re.search(r' \d{1,2}( |$)', name):
                continue
            keep(at)
            nl = next((n[2] for n in ne_nl.get(a3, []) if n[1] & names and km(at, n[0]) < 15), None)
            name = TOWN_NL.get((a3, name)) or CITY_NL.get(name, nl or name).removeprefix('District of ')
            zoom = 9 if pop >= 15000 else 10 if pop >= 5000 else 11
            kept.append([name, round(lon, 3), round(lat, 3), zoom])
        if kept:
            result[a3] = kept
    return result


def encode(coords):
    """Een ring of lijn als [x0, y0, dx1, dy1, ...] in duizendsten van een graad."""
    out, px, py = [], 0, 0
    for i, (x, y) in enumerate(coords):
        ix, iy = round(x * DETAIL_SCALE), round(y * DETAIL_SCALE)
        if i and ix == px and iy == py:
            continue
        out += [ix, iy] if i == 0 else [ix - px, iy - py]
        px, py = ix, iy
    return out


def build_towns(src, iso2, cities):
    """Plaatsen vanaf TOWN_MIN_POP inwoners per land, met de Nederlandse naam van de kaart waar die er is."""
    ne_nl = defaultdict(list)
    for f in json.loads((src / 'ne_10m_populated_places.geojson').read_text())['features']:
        p = f['properties']
        if p.get('NAME_NL'):
            names = {p.get(k) for k in ('NAME', 'NAMEASCII', 'NAME_EN', 'NAMEALT')} - {None, ''}
            ne_nl[p['ADM0_A3']].append(((p['LONGITUDE'], p['LATITUDE']), names, p['NAME_NL']))
    towns = defaultdict(list)       # a3 -> [naam, lon, lat, inwoners]
    with open(src / 'cities15000.txt', encoding='utf-8') as fh:
        for line in fh:
            c = line.rstrip('\n').split('\t')
            fcode, pop = c[7], int(c[14] or 0)
            a3 = iso2.get(c[8])
            if not a3 or fcode in SKIP_FCODES or pop < TOWN_MIN_POP:
                continue
            at = (float(c[5]), float(c[4]))
            known = next((k for k in cities.get(a3, []) if km(at, (k[1], k[2])) < 2), None)
            if known:
                name = known[0]
            else:
                names = {c[1], c[2], *c[3].split(',')}
                nl = next((n[2] for n in ne_nl.get(a3, []) if n[1] & names and km(at, n[0]) < 15), None)
                name = CITY_NL.get(c[1], nl or c[1]).removeprefix('District of ')
            towns[a3].append([name, at[0], at[1], pop])
    # Hoofdplaatsen die alleen Natural Earth kent (Aarlen), staan al op de kaart: die horen er ook bij
    for a3, kept in cities.items():
        for name, lon, lat, _zoom, _cap in kept:
            if not any(km((lon, lat), (t[1], t[2])) < 2 for t in towns[a3]):
                towns[a3].append([name, lon, lat, TOWN_MIN_POP])
    # Een kleinere plaats vlak naast een grotere is een wijk of deelgemeente (Porto de Mós bij Lagos)
    for a3, rows in towns.items():
        rows.sort(key=lambda t: -t[3])
        kept = []
        for t in rows:
            if any(km((t[1], t[2]), (k[1], k[2])) < TOWN_NEAR_KM for k in kept):
                continue
            # Een genummerd stadsdeel van een grotere stad ('Paris 16 Passy', 'Lyon 03'): die stad, geen eigen plaats
            if re.search(r' \d{1,2}( |$)', t[0]) and any(k[3] > t[3] and km((t[1], t[2]), (k[1], k[2])) < DISTRICT_KM for k in kept):
                continue
            kept.append(t)
        towns[a3] = kept
    return towns


def build_detail(shapes, towns, path):
    """backend/data/places_detail.json: provincies, kustlijn en plaatsen voor backend/places.describe."""
    codes = sorted(set(shapes) | set(towns))
    index = {a3: i for i, a3 in enumerate(codes)}
    provinces, wholes = [], []
    for a3 in sorted(shapes):
        for name, (g, kind) in shapes[a3].items():
            parts = []
            for part in getattr(g, 'geoms', [g]):
                if true_area(part) < ISLAND_MIN / 20:
                    continue
                part = part.simplify(DETAIL_TOL, preserve_topology=True)
                if part.is_empty or part.geom_type != 'Polygon':
                    continue
                parts.append([encode(part.exterior.coords)] + [encode(h.coords) for h in part.interiors])
            if parts:
                provinces.append([index[a3], name, parts, kind])
            wholes.append(g)
    land = unary_union(wholes)
    coast = []
    for part in getattr(land, 'geoms', [land]):
        if true_area(part) < ISLAND_MIN:
            continue
        for ring in [part.exterior, *part.interiors]:
            line = ring.simplify(DETAIL_TOL)
            if len(line.coords) >= 2:
                coast.append(encode(line.coords))
    town_rows = []
    for a3 in sorted(towns):
        for name, lon, lat, pop in sorted(towns[a3], key=lambda t: -t[3]):
            town_rows.append([name, round(lon * DETAIL_SCALE), round(lat * DETAIL_SCALE),
                              max(15, round(pop / 1000)), index[a3]])
    body = json.dumps({'scale': DETAIL_SCALE, 'codes': codes, 'provinces': provinces, 'coast': coast,
                       'towns': town_rows}, ensure_ascii=False, separators=(',', ':'))
    path.write_text(body)
    print(f'{len(provinces)} provincies, {len(coast)} kustlijnen, {len(town_rows)} plaatsen, '
          f'{len(body) / 1e6:.1f} MB in {path}')


def main():
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / 'frontend/vendor/naturalearth'
    iso2 = build_countries(src, out)
    provinces, shapes = build_provinces(src)
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
    towns_folder = folder / 'towns'
    towns_folder.mkdir(exist_ok=True)
    for old in towns_folder.glob('*.json'):
        old.unlink()
    total = 0
    towns = build_map_towns(src, iso2, cities)
    for a3, rows in sorted(towns.items()):
        body = json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
        (towns_folder / f'{a3}.json').write_text(body)
        total += len(body)
    print(f'{sum(map(len, towns.values()))} kleinere plaatsen in {len(towns)} landen, {total / 1e6:.1f} MB in {towns_folder}')
    detail = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(__file__).resolve().parent.parent / 'backend/data'
    build_detail(shapes, build_towns(src, iso2, cities), detail / 'places_detail.json')


if __name__ == '__main__':
    main()
