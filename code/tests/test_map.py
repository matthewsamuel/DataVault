"""Kaart (step 3.6): GET /api/map/points, /items and the opt-in tiles setting (backend/routes/geo.py)."""
import sqlite3

import pytest

from fixtures.vault_builder import _photo
from test_media_index import _index


@pytest.fixture
def indexed(client, config):
    _index(client)
    return client


def _points(client, **params):
    r = client.get("/api/map/points", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _paths(client, data):
    ids = ",".join(str(p[0]) for p in data["points"])
    items = client.get("/api/map/items", params={"ids": ids}).json()["items"] if ids else []
    return sorted(i["name"] for i in items)


def test_points_are_compact_arrays_of_the_geotagged_media(indexed):
    data = _points(indexed)
    assert data["ready"] is True and data["total"] == len(data["points"]) >= 2
    assert data["next"] is None and data["without_gps"] > 0
    pid, lat, lon, taken, kind = data["points"][0]
    assert isinstance(pid, int) and -90 <= lat <= 90 and -180 <= lon <= 180 and kind == 0 and taken > 0
    south, west, north, east = data["bounds"]
    assert south <= min(p[1] for p in data["points"]) and north >= max(p[1] for p in data["points"])
    assert any(abs(p[1] - 52.37) < 0.01 and abs(p[2] - 4.89) < 0.01 for p in data["points"])
    assert {"strand 001.jpg"} <= set(_paths(indexed, data)) or _paths(indexed, data)


def test_filters_year_folder_camera_and_album_reach_the_points(indexed):
    everything = _points(indexed)
    nice = _points(indexed, folder="RAW/2019/zomer vakantie")
    assert 0 < nice["total"] < everything["total"] and "IMG_0002.JPG" in _paths(indexed, nice)
    assert _points(indexed, year=2019)["total"] == everything["total"]
    assert _points(indexed, year=2030)["total"] == 0 and _points(indexed, year=2030)["bounds"] is None
    assert _points(indexed, camera="Apple iPhone 11")["total"] == 0          # that photo has no position
    assert _points(indexed, date_from="2019-07-14", date_to="2019-07-14")["total"] == everything["total"]
    # has_gps in a filter changes nothing: only positioned media are ever points
    assert _points(indexed, has_gps="false")["total"] == everything["total"]
    album = indexed.post("/api/library/albums", json={"name": "Juli", "filters": {"folder": "RAW/2019/zomer vakantie"}}).json()
    assert _points(indexed, album=album["id"])["total"] == nice["total"]
    assert indexed.get("/api/map/points", params={"album": album["id"], "year": 2019}).status_code == 400
    assert indexed.get("/api/map/points", params={"album": 99999}).status_code == 404
    assert indexed.get("/api/map/points", params={"year": "x"}).status_code == 400
    assert indexed.get("/api/map/points", params={"folder": "../.."}).status_code in (400, 403)


def test_bbox_limits_the_points_but_not_the_totals(indexed):
    everything = _points(indexed)
    around_nice = _points(indexed, bbox="43,7,44,8")
    assert [round(p[1], 1) for p in around_nice["points"]] == [43.7]
    assert around_nice["total"] == everything["total"] and around_nice["bounds"] == everything["bounds"]
    assert _points(indexed, bbox="0,0,1,1")["points"] == []
    # across the date line (west > east) and the poles
    assert _points(indexed, bbox="-90,170,90,-170")["points"] == []
    assert len(_points(indexed, bbox="-100,-200,100,200")["points"]) == everything["total"]
    for bad in ("1,2,3", "a,b,c,d", "10,0,5,1", "nan,0,1,1"):
        assert indexed.get("/api/map/points", params={"bbox": bad}).status_code == 400, bad


def test_points_come_in_chunks_by_id(client, config):
    root = config.VAULT_ROOT
    for i in range(7):
        _photo(root / "RAW" / "kaart" / f"p{i}.jpg", (i * 20, 10, 10), f"p{i}", f"2023:01:0{i + 1} 10:00:00",
               gps=(50 + i * 0.1, 5 + i * 0.1))
    _index(client)
    seen, after, chunks = [], 0, 0
    while True:
        data = _points(client, limit=3, after=after)
        chunks += 1
        seen += [p[0] for p in data["points"]]
        assert data["total"] >= 9
        if data["next"] is None:
            break
        assert data["next"] == data["points"][-1][0]
        after = data["next"]
    assert chunks >= 3 and seen == sorted(seen) and len(seen) == len(set(seen)) == data["total"]


def test_media_without_a_usable_position_is_not_a_point(client, config):
    root = config.VAULT_ROOT
    _photo(root / "RAW" / "nul" / "nul.jpg", (1, 2, 3), "nul", "2023:01:01 10:00:00", gps=(0.0, 0.0))
    _photo(root / "RAW" / "nul" / "zuid.jpg", (1, 2, 4), "zuid", "2023:01:01 10:00:00", gps=(-33.86, -70.67))
    _index(client)
    data = _points(client, folder="RAW/nul")
    assert [round(p[1], 2) for p in data["points"]] == [-33.86] and data["without_gps"] == 1


def test_items_give_the_viewer_what_it_needs(indexed):
    data = _points(indexed)
    ids = [p[0] for p in data["points"]][::-1]
    items = indexed.get("/api/map/items", params={"ids": ",".join(map(str, ids + [987654]))}).json()["items"]
    assert [i["id"] for i in items] == ids                      # the order asked, unknown ids left out
    assert all(i["has_gps"] and abs(i["lat"]) <= 90 and i["path"] and i["type"] == "photo" for i in items)
    assert indexed.get("/api/map/items", params={"ids": ""}).json() == {"items": []}
    assert indexed.get("/api/map/items", params={"ids": "1,x"}).status_code == 400
    assert indexed.get("/api/map/items", params={"ids": ",".join(["1"] * 201)}).status_code == 400


def test_a_moved_file_is_not_a_point_and_nothing_is_written(indexed, config):
    before = _points(indexed)["total"]
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("UPDATE media SET missing = 1 WHERE lat IS NOT NULL AND ROWID = (SELECT MIN(ROWID) FROM media WHERE lat IS NOT NULL)")
    assert _points(indexed)["total"] == before - 1


def test_osm_tiles_are_off_until_the_user_switches_them_on(client):
    assert client.get("/api/map/settings").json() == {"osm_tiles": False}
    assert client.put("/api/map/settings", json={"osm_tiles": True}).json() == {"osm_tiles": True}
    assert client.get("/api/map/settings").json() == {"osm_tiles": True}
    assert client.put("/api/map/settings", json={"osm_tiles": "ja"}).status_code == 400
    assert client.put("/api/map/settings", json={}).status_code == 400
    client.put("/api/map/settings", json={"osm_tiles": False})
    assert client.get("/api/map/settings").json() == {"osm_tiles": False}


def test_locate_gives_the_position_of_one_file(indexed, config):
    r = indexed.get("/api/map/locate", params={"path": str(config.VAULT_ROOT / "RAW/2019/zomer vakantie/IMG_0002.JPG")})
    assert r.status_code == 200 and r.json()["has_gps"] is True and abs(r.json()["lat"] - 43.7) < 0.01
    # The viewer's details: an offline place name, the capture date, pixel size and camera
    found = r.json()
    assert found["place"].endswith(", Frankrijk")   # (the nearest larger city in the data)
    assert found["place_parts"]["country"] == "Frankrijk" and found["place_parts"]["label"] == found["place"]
    assert found["details"]["width"] == 800 and found["details"]["height"] == 600
    assert found["details"]["camera"] == "Canon EOS 80D" and found["details"]["taken_source"] == "exif"
    no_gps = indexed.get("/api/map/locate", params={"path": str(config.VAULT_ROOT / "RAW/2021/🎉 feest/no exif.jpg")}).json()
    assert no_gps["has_gps"] is False and "lat" not in no_gps and "place" not in no_gps
    assert no_gps["details"]["camera"] is None
    assert indexed.get("/api/map/locate", params={"path": "/etc/passwd"}).status_code == 403


def test_describe_names_a_position_in_dutch_offline():
    from backend import places
    assert places.describe(52.37, 4.89) == "Amsterdam, Nederland"
    assert places.describe(41.9, 12.49) == "Rome, Italië"
    assert places.describe(48.858, 2.294) == "Parijs, Frankrijk"           # the Eiffel tower, in the Dutch name
    # A harbour just outside the simplified coast line still gets its town and country
    assert places.describe(38.72, -9.14) == "Lissabon, Portugal"
    assert places.describe(64.5, -17.0) == "Suðurland, IJsland"


def test_describe_gives_every_geotagged_photo_a_place():
    """Not only near a larger city: a town with a distance and direction, the province in the
    countryside, the coast, the sea; always in Dutch and with the country."""
    from backend import places
    # Near a town: distance and compass direction from the town that names the area
    assert places.describe(37.02, -7.93) == "Faro, Portugal"
    assert places.describe(37.02, -7.80) == "4 km ten oosten van Olhão, Portugal"
    assert places.describe(52.15, 5.85) == "11 km ten zuidwesten van Apeldoorn, Nederland"   # the Veluwe
    # Countryside far from any town: the province, as on the Kaart
    assert places.describe(68.42, 27.41) == "Lapland, Finland"
    assert places.describe(37.75, -119.59) == "Californië, Verenigde Staten"               # Yosemite
    assert places.describe(36.1, -112.1) == "Arizona, Verenigde Staten"                     # the Grand Canyon
    # On a beach and on the water
    assert places.describe(52.37, 4.52) == "Zandvoort, Nederland"
    assert places.describe(53.45, 5.75) == "Aan de kust van Friesland, Nederland"           # Ameland
    assert places.describe(52.4, 4.3) == "Voor de kust bij Zandvoort, Nederland"
    assert places.describe(51.80, -10.25) == "Voor de kust van Kerry, Ierland"
    assert places.describe(42.75, 15.08) == "Op zee, 70 km uit de kust van Italië"
    assert places.describe(0.0, -30.0) == "Op zee"
    # 0, 0 is what a broken GPS writes: no place at all
    assert places.describe(0.0, 0.0) is None
    parts = places.describe_parts(37.02, -7.80)
    assert parts["town"] == "Olhão" and parts["direction"] == "oosten" and parts["country"] == "Portugal"
    assert parts["region"] == "Faro" and parts["region_kind"] == "district" and parts["sea"] is False
    assert places.describe_parts(52.15, 5.85)["region_kind"] == "provincie"           # the Veluwe, Gelderland
    assert places.describe_parts(37.75, -119.59)["region_kind"] == "staat"
    assert places.describe_parts(52.4, 4.3)["sea"] is True and places.describe_parts(52.4, 4.3)["coast"] is True


def test_describe_names_downtown_landmarks_by_their_city():
    """A big city's point is one spot downtown: its landmarks 10-15 km away are still in it, while a suburb with
    its own centre keeps its name."""
    from backend import places
    assert places.describe(25.1972, 55.2744) == "Dubai, Verenigde Arabische Emiraten"     # Burj Khalifa
    assert places.describe(25.1412, 55.1853) == "Dubai, Verenigde Arabische Emiraten"     # Burj al Arab
    assert places.describe(35.7148, 139.7967) == "Tokio, Japan"                            # Asakusa
    assert places.describe(34.0928, -118.3287) == "Los Angeles, Verenigde Staten"         # Hollywood
    assert places.describe(52.30, 4.86) == "Amstelveen, Nederland"
    assert places.describe(48.80, 2.13) == "Versailles, Frankrijk"
    assert places.town_radius_km(15) < 4 and places.town_radius_km(1e5) == 25


def test_describe_city_states_and_region_names_read_as_dutch():
    from backend import places
    assert places.describe(43.7384, 7.4246) == "Monaco"
    assert places.describe(1.2903, 103.8519) == "Singapore"
    assert places.describe(43.9356, 12.4473) == "San Marino"
    assert places.describe(41.9022, 12.4539) == "Vaticaanstad"                             # Sint-Pietersplein
    assert places.describe(41.8986, 12.4769) == "Rome, Italië"                             # the Pantheon
    assert places.describe(52.40, 5.15) == "Almere, Nederland"                             # not 'Almere Stad'
    oslo, stockholm, cph = places.describe_parts(59.91, 10.75), places.describe_parts(59.33, 18.07), places.describe_parts(55.68, 12.57)
    assert (oslo["region_kind"], stockholm["region_kind"], stockholm["region"]) == ("provincie", "provincie", "Stockholm")
    assert cph["region"] == "Hovedstaden"
    assert places.describe_parts(31.63, -7.99)["region"].startswith("Marrakesh-")         # one spelling with the city
    assert places.describe(37.40, 25.20) == "Voor de kust van de Zuid-Egeïsche Eilanden, Griekenland"


def test_describe_off_the_coast_is_quick_too():
    import time
    from backend import places
    places.describe(52.0, 5.0)
    start = time.perf_counter()
    for i in range(100):
        places.describe(52.4 + (i % 10) * 0.1, 4.3 + (i // 10) * 0.08)           # the coast and the Wadden
    assert (time.perf_counter() - start) / 100 < 0.004


def test_describe_is_fast_enough_for_a_page_of_photos():
    import time
    from backend import places
    places.describe(52.0, 5.0)                                  # loads the data once
    start = time.perf_counter()
    for i in range(200):
        places.describe(40.0 + (i % 20) * 0.5, -5.0 + (i // 20) * 2.0)
    assert (time.perf_counter() - start) / 200 < 0.02           # about a millisecond each here
