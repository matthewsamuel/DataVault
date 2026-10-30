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
    # A harbour just outside the simplified coast line still gets its country
    assert places.describe(38.72, -9.14) == "Lissabon, Portugal"
    # Far from any larger city: the country alone; at sea: nothing
    assert places.describe(68.42, 27.41) == "Finland"
    assert places.describe(0.0, -30.0) is None
