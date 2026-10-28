"""
Albums (step 3.3): the library filters (backend/library_filters.py) and GET
/api/library/browse, 'on this day' per year (GET /api/library/on-this-day), and the
smart albums (migration 20, backend/smart_albums.py, /api/library/albums).
"""
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from backend import library_filters
from backend.database import Database
from fixtures.vault_builder import PORTRAIT, STRAND, _photo
from test_media_index import _index


def _files(vault: Path):
    """Every file and folder of the archive with its size and mtime (the app's own cache aside)."""
    out = {}
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d != ".datavault_cache"]
        for name in dirs + files:
            path = Path(root) / name
            st = os.lstat(path)
            out[str(path)] = (st.st_size, st.st_mtime_ns)
    return out


def _browse(client, **params):
    r = client.get("/api/library/browse", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _names(data):
    return sorted(item["name"] for item in data["items"])


@pytest.fixture
def indexed(client, config):
    _index(client)
    return client


# ---------- migration ----------

def test_migration_20_and_21_add_smart_albums_and_keep_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 12, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision) VALUES ('/v/RAW/a.jpg', '/v/RAW', 'keep')")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{\"full\": true}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                     "VALUES ('/v/RAW/a.jpg', '/v/RAW', 'a.jpg', 'photo', '.jpg', 10, 1, 1, 1, 1)")
        conn.execute("INSERT INTO media_folders VALUES ('/v/RAW', '/v', 1, 1.0)")
        conn.execute("INSERT INTO recovery_actions (kind, title, folder, details, created_at) "
                     "VALUES ('viewer_move', 'x', '/v/RAW', '{}', 1.0)")
        conn.execute("INSERT INTO recovery_moves (action_id, src_path, dst_path, dst_ino, dst_size, moved_at) "
                     "VALUES (1, '/v/RAW/b.jpg', '/v/RAW/c/b.jpg', 5, 6, 1.0)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    assert before["sqlite_sequence"] and before["jobs"] and before["media"] and before["recovery_moves"]

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 21
        assert {r[0] for r in conn.execute("SELECT version FROM schema_version")} >= {20, 21}
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        assert {r[1] for r in conn.execute("PRAGMA table_info(smart_albums)")} == {
            "id", "name", "filters", "sort", "icon", "builtin", "hidden", "position", "created_at", "updated_at"}
        # The migration adds no rows (the default albums come from the app, once)
        assert conn.execute("SELECT COUNT(*) FROM smart_albums").fetchone()[0] == 0
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert "idx_keurer_path" in indexes
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v12-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_recent_keurer_decisions(2)[1]["dest_ino"] == 77
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    assert db.get_recovery_moves(1)[0]["dst_path"] == "/v/RAW/c/b.jpg"
    db.close_all()


# ---------- the filter format ----------

def test_clean_gives_one_normal_form_and_refuses_what_it_does_not_know():
    clean = library_filters.clean
    assert clean(None) == {} and clean({}) == {}
    assert clean({"kind": ["video", "photo", "photo"], "ext": ["JPG", ".heic", "jpg"], "year": "2019",
                  "month": 7, "has_gps": "true", "recursive": True, "folder": "RAW/2019/",
                  "camera": "  Canon EOS 80D ", "min_rating": "1050", "keurer": "keep", "orientation": "portrait",
                  "capture_date": "known", "date_from": "2019-01-01", "date_to": "2019-12-31", "period": "this_year",
                  "camera_contains": "iPhone"}) == {
        "kind": ["photo", "video"], "ext": [".heic", ".jpg"], "year": 2019, "month": 7, "has_gps": True,
        "folder": "RAW/2019", "camera": "Canon EOS 80D", "min_rating": 1050, "keurer": "keep",
        "orientation": "portrait", "capture_date": "known", "date_from": "2019-01-01", "date_to": "2019-12-31",
        "period": "this_year", "camera_contains": "iPhone"}
    # All three kinds is no filter; empty values drop out; recursive only matters with a folder
    assert clean({"kind": ["photo", "video", "audio"], "ext": [], "camera": "", "recursive": False}) == {}
    assert clean({"folder": "RAW", "recursive": "false"}) == {"folder": "RAW", "recursive": False}
    assert clean({"has_gps": False}) == {"has_gps": False}
    for bad in ({"colour": "red"}, {"kind": "document"}, {"ext": "j.pg"}, {"ext": "<b>"}, {"year": 99},
                {"year": "20x9"}, {"month": 13}, {"month": True}, {"period": "forever"}, {"has_gps": "maybe"},
                {"keurer": "delete"}, {"orientation": "round"}, {"capture_date": "soon"},
                {"date_from": "14-07-2019"}, {"date_to": "2019-02-30"}, {"date_from": "0001-01-01"},
                {"date_from": "2020-01-02", "date_to": "2020-01-01"}, {"camera": "x" * 300},
                {"camera": "a\nb"}, {"folder": 5}, {"min_rating": 10 ** 12}, ["kind"]):
        with pytest.raises(library_filters.FilterError):
            clean(bad)


def test_filters_from_an_address_repeat_or_comma_separate_kinds():
    params = [("kind", "photo"), ("kind", "video"), ("ext", "jpg,png"), ("year", "2019"), ("sort", "name")]
    assert library_filters.from_params(params, reserved=("sort",)) == {
        "kind": ["photo", "video"], "ext": [".jpg", ".png"], "year": 2019}
    with pytest.raises(library_filters.FilterError):
        library_filters.from_params([("year", "2019"), ("year", "2020")])


def test_date_keys_are_local_days_and_combine_into_one_range():
    start, end = library_filters.date_range({"year": 2019, "month": 12})
    assert (start, end) == (datetime(2019, 12, 1).timestamp(), datetime(2020, 1, 1).timestamp())
    start, end = library_filters.date_range({"date_from": "2019-07-14", "date_to": "2019-07-14"})
    assert end - start in (86400, 82800, 90000)       # one local day (23 or 25 h on a DST change)
    # Every date key applies: the range is where they overlap
    start, end = library_filters.date_range({"year": 2019, "date_from": "2019-06-01", "date_to": "2020-03-01"})
    assert (start, end) == (datetime(2019, 6, 1).timestamp(), datetime(2020, 1, 1).timestamp())
    today = date(2024, 3, 15)
    assert library_filters.date_range({"period": "this_year"}, today) == (
        datetime(2024, 1, 1).timestamp(), datetime(2025, 1, 1).timestamp())
    assert library_filters.date_range({"period": "last_year"}, today) == (
        datetime(2023, 1, 1).timestamp(), datetime(2024, 1, 1).timestamp())
    assert library_filters.date_range({"period": "this_month"}, date(2024, 12, 9)) == (
        datetime(2024, 12, 1).timestamp(), datetime(2025, 1, 1).timestamp())
    assert library_filters.date_range({"period": "last_30_days"}, today) == (
        datetime(2024, 2, 15).timestamp(), datetime(2024, 3, 16).timestamp())


# ---------- browsing ----------

def test_browse_combines_filters(indexed, config, app):
    client = indexed
    everything = _browse(client, limit=1000)
    assert everything["ready"] is True and everything["total"] == len(everything["items"]) > 20
    item = next(i for i in everything["items"] if i["name"] == "strand 001.jpg")
    assert item["type"] == "photo" and item["date_source"] == "exif" and item["has_gps"] is True
    assert item["timestamp"] == datetime(2019, 7, 14, 19, 30).timestamp() and item["camera"] == "Canon EOS 80D"

    assert _names(_browse(client, year=2019)) == ["IMG_0002.JPG", "dup van strand.jpg", "strand 001 kopie.jpg",
                                                  "strand 001.jpg"]
    # Folder: relative to the archive, recursive by default; recursive=false is only the folder itself
    assert _names(_browse(client, year=2019, folder="RAW/2019")) == ["IMG_0002.JPG", "strand 001.jpg"]
    assert _browse(client, folder="RAW/2019", recursive="false")["total"] == 0
    assert _names(_browse(client, folder="RAW/2019/zomer vakantie", recursive="false")) == [
        "IMG_0002.JPG", "strand 001.jpg"]
    assert _names(_browse(client, has_gps="true", folder="RAW/misc")) == ["strand 001 kopie.jpg"]
    iphones = _names(_browse(client, camera_contains="iphone"))
    assert {"koffie é à ü.jpg", "portret.jpg"} <= set(iphones) and all(n.endswith((".jpg", ".heic")) for n in iphones)
    assert _names(_browse(client, camera="Apple iPhone 11")) == ["koffie é à ü.jpg"]
    assert _names(_browse(client, camera="iphone 7", orientation="portrait")) == ["portret.jpg"]
    assert _browse(client, camera="iphone 7", orientation="landscape")["total"] == 0
    # kind is any of; ext too
    videos = _browse(client, kind="video")
    assert videos["total"] >= 1 and {i["type"] for i in videos["items"]} == {"video"}
    both = _browse(client, kind=["video", "audio"])
    assert {i["type"] for i in both["items"]} <= {"video", "audio"} and both["total"] > videos["total"]
    assert _names(_browse(client, ext="png")) == ["🎂 taart.png"]
    assert _names(_browse(client, ext=["png", ".jpeg"], folder="RAW/misc")) == ["UPPER.JPEG", "diep bestand.jpeg"]
    # No capture date: the date of the file; never a photo with EXIF
    undated = _browse(client, capture_date="missing", kind="photo", limit=1000)
    assert "🎂 taart.png" in _names(undated) and "no exif.jpg" in _names(undated)
    assert all(i["date_source"] in ("birthtime", "mtime", None) for i in undated["items"])
    dated = _browse(client, capture_date="known", limit=1000)
    assert dated["total"] + _browse(client, capture_date="missing")["total"] == everything["total"]
    # Month of any year
    assert _names(_browse(client, month=10, kind="photo", capture_date="known")) == ["diep bestand.jpeg", "koffie é à ü.jpg", "portret.jpg"]
    assert _names(_browse(client, month=10, year=2018)) == ["diep bestand.jpeg"]


def test_rating_and_keurer_filters_and_sorts(indexed, config, app):
    client = indexed
    from backend.routes import albums
    db = albums.db
    root = config.VAULT_ROOT
    strand, portrait, koffie = (str(root / rel) for rel in (STRAND, PORTRAIT, "RAW/2020/Café Ümlaut/koffie é à ü.jpg"))
    db.apply_vote(strand, 150)
    db.apply_vote(portrait, 50)
    db.apply_vote(koffie, -50)
    best = _browse(client, min_rating=1050, sort="-rating")
    assert [i["name"] for i in best["items"]] == ["strand 001.jpg", "portret.jpg"]
    assert best["items"][0]["rating"] == 1150
    ranked = _browse(client, sort="-rating", limit=4)["items"]
    assert [i["name"] for i in ranked[:3]] == ["strand 001.jpg", "portret.jpg", "koffie é à ü.jpg"]
    assert "rating" not in ranked[3]                 # never voted on: after every rated file
    assert [i["name"] for i in _browse(client, sort="rating", limit=3)["items"]] == [
        "koffie é à ü.jpg", "portret.jpg", "strand 001.jpg"]

    db.record_keurer_decision(strand, str(Path(strand).parent), "keep")
    db.record_keurer_decision(portrait, str(Path(portrait).parent), "keep")
    db.record_keurer_decision(str(root / "RAW/2019/zomer vakantie/weg.jpg"), str(Path(strand).parent), "delete",
                              dest_path=str(root / "TRASH/weg.jpg"))
    assert _names(_browse(client, keurer="keep")) == ["portret.jpg", "strand 001.jpg"]
    undecided = _browse(client, keurer="undecided", folder="RAW/2019")
    assert _names(undecided) == ["IMG_0002.JPG"]
    assert _browse(client, keurer="undecided")["total"] == _browse(client)["total"] - 2

    by_name = [i["name"] for i in _browse(client, sort="name", folder="RAW/2022/bursts")["items"]]
    assert by_name == sorted(by_name) and by_name[0] == "burst_00.jpg"
    assert [i["name"] for i in _browse(client, sort="-name", folder="RAW/2022/bursts")["items"]] == by_name[::-1]
    sizes = [i["size"] for i in _browse(client, sort="-size", limit=1000)["items"]]
    assert sizes == sorted(sizes, reverse=True)
    dates = [i["timestamp"] for i in _browse(client, sort="taken", capture_date="known", limit=1000)["items"]]
    assert dates == sorted(dates)


def test_browse_pages_and_versions_one_selection(indexed, config):
    client = indexed
    whole = _browse(client, sort="name", limit=1000)
    paged = []
    for offset in range(0, whole["total"], 7):
        page = _browse(client, sort="name", offset=offset, limit=7)
        assert page["total"] == whole["total"] and page["version"] == whole["version"]
        paged += [i["path"] for i in page["items"]]
    assert paged == [i["path"] for i in whole["items"]]
    # A new file in the selection changes its version; another selection keeps its own
    other = _browse(client, folder="library")["version"]
    _photo(config.VAULT_ROOT / "RAW/2019/zomer vakantie/nieuw.jpg", (1, 2, 3), "nieuw", "2019:07:15 10:00:00")
    _index(client)
    assert _browse(client, sort="name", limit=1)["version"] != whole["version"]
    assert _browse(client, folder="library")["version"] == other


def test_browse_refuses_bad_input(indexed, config):
    client = indexed
    for params in ({"colour": "red"}, {"kind": "doc"}, {"year": "twee"}, {"sort": "random"}, {"sort": "path"},
                   {"date_from": "2019-13-01"}, {"month": 0}, {"keurer": "maybe"}, {"year": ["2019", "2020"]}):
        r = client.get("/api/library/browse", params=params)
        assert r.status_code == 400, (params, r.text)
        assert r.json()["detail"]
    assert client.get("/api/library/browse", params={"folder": "../.."}).status_code == 403
    assert client.get("/api/library/browse", params={"folder": str(config.CACHE_FOLDER)}).status_code == 403
    assert client.get("/api/library/browse", params={"album": 999}).status_code == 404
    assert client.get("/api/library/browse", params={"offset": -1}).json()["offset"] == 0
    assert client.get("/api/library/browse", params={"limit": 10 ** 6}).json()["limit"] == 1000
    # A folder inside the vault that does not exist is simply empty
    assert _browse(client, folder="RAW/bestaat niet")["total"] == 0


def test_before_the_first_pass_browse_says_it_is_not_ready(client):
    data = _browse(client)
    assert data["ready"] is False and data["total"] == 0 and data["items"] == []


def test_year_and_day_edges_follow_local_wall_clock_time_like_the_timeline(client, config, monkeypatch):
    # A photo at 23:30 on New Year's Eve belongs to that year and day where it was taken,
    # whatever its UTC time: in Los Angeles it is already 1 January in UTC
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        edge = config.VAULT_ROOT / "RAW/rand"
        _photo(edge / "oud.jpg", (1, 1, 1), "oud", "2019:12:31 23:30:00")
        _photo(edge / "nieuw.jpg", (2, 2, 2), "nieuw", "2020:01:01 00:15:00")
        _photo(edge / "schrikkel.jpg", (3, 3, 3), "29 feb", "2020:02:29 23:59:59")
        _index(client)
        assert _names(_browse(client, folder="RAW/rand", year=2019)) == ["oud.jpg"]
        assert _names(_browse(client, folder="RAW/rand", date_to="2019-12-31")) == ["oud.jpg"]
        assert _names(_browse(client, folder="RAW/rand", date_from="2020-01-01", date_to="2020-01-01")) == ["nieuw.jpg"]
        assert _names(_browse(client, folder="RAW/rand", year=2020, month=2)) == ["schrikkel.jpg"]
        assert _names(_browse(client, folder="RAW/rand", date_from="2020-02-29", date_to="2020-02-29")) == [
            "schrikkel.jpg"]
        assert _names(_browse(client, folder="RAW/rand", month=12)) == ["oud.jpg"]
        # The same years as the timeline gives for that folder
        scan = client.get("/api/timeline/scan", params={"folders": str(edge)}).json()
        timeline = {int(y): sum(m.values()) for y, m in scan["date_index"].items()}
        assert timeline == {2019: 1, 2020: 2}
        facet = dict(map(tuple, client.get("/api/library/facets").json()["years"]))
        assert facet[2019] == _browse(client, year=2019)["total"]
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_facets_list_years_cameras_and_types(indexed):
    data = indexed.get("/api/library/facets").json()
    years = dict(map(tuple, data["years"]))
    assert years[2019] == 4 and years[2022] >= 5 and list(years) == sorted(years, reverse=True)
    cameras = {c["label"]: c for c in data["cameras"]}
    assert cameras["Apple iPhone 11"]["value"] == "Apple iPhone 11" and cameras["Apple iPhone 11"]["count"] == 1
    assert cameras["Canon EOS 80D"]["value"] == "Canon EOS 80D"
    exts = {(e["kind"], e["ext"]): e["count"] for e in data["exts"]}
    assert exts[("photo", ".png")] == 1 and exts[("photo", ".jpg")] > 10


# ---------- on this day ----------

def _otd(client, monkeypatch, today, **params):
    from backend.routes import albums
    monkeypatch.setattr(albums, "_today", lambda: today)
    r = client.get("/api/library/on-this-day", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_on_this_day_groups_per_year_newest_first(indexed, config, monkeypatch):
    client = indexed
    data = _otd(client, monkeypatch, date(2026, 10, 27))
    assert (data["month"], data["day"], data["ready"]) == (10, 27, True)
    assert [(g["year"], g["years_ago"], g["total"]) for g in data["years"]] == [(2020, 6, 1), (2018, 8, 1)]
    assert data["years"][0]["items"][0]["name"] == "koffie é à ü.jpg"
    # Another day, asked for (the year is ignored); the hits of that day in every earlier year
    data = _otd(client, monkeypatch, date(2026, 10, 27), date="2026-07-14")
    assert [(g["year"], g["total"]) for g in data["years"]] == [(2019, 4)]
    assert [i["name"] for i in data["years"][0]["items"]] == [
        "strand 001.jpg", "dup van strand.jpg", "strand 001 kopie.jpg", "IMG_0002.JPG"]
    # Only photos and videos, by capture time within the year; per_year caps the items, not the total
    capped = _otd(client, monkeypatch, date(2026, 10, 27), date="07-14", per_year=1)
    assert capped["years"][0]["total"] == 4 and len(capped["years"][0]["items"]) == 1
    assert capped["total"] == 4
    # This year itself is not 'on this day' (it is today, or still to come)
    _photo(config.VAULT_ROOT / "RAW/2026/dit jaar.jpg", (5, 5, 5), "nu", "2026:07:14 09:00:00")
    _index(client)
    assert [g["year"] for g in _otd(client, monkeypatch, date(2026, 10, 27), date="07-14")["years"]] == [2019]
    # Limited to the folders chosen for 'op deze dag' on Home
    limited = _otd(client, monkeypatch, date(2026, 10, 27), date="07-14", allowed_folders=["RAW/misc"])
    assert [i["name"] for g in limited["years"] for i in g["items"]] == ["strand 001 kopie.jpg"]
    nothing = _otd(client, monkeypatch, date(2026, 10, 27), date="07-14", allowed_folders=["../buiten"])
    assert nothing["years"] == [] and nothing["total"] == 0
    for bad in ("13-01", "02-30", "juli", "2019-07", "7-14-2019"):
        assert client.get("/api/library/on-this-day", params={"date": bad}).status_code == 400, bad


def test_on_this_day_and_29_february(client, config, monkeypatch):
    leap = config.VAULT_ROOT / "RAW/schrikkel"
    _photo(leap / "2020.jpg", (1, 1, 1), "a", "2020:02:29 12:00:00")
    _photo(leap / "2016.jpg", (2, 2, 2), "b", "2016:02:29 08:00:00")
    _photo(leap / "28feb.jpg", (3, 3, 3), "c", "2019:02:28 08:00:00")
    _photo(leap / "1maart.jpg", (4, 4, 4), "d", "2021:03:01 08:00:00")
    _index(client)
    groups = lambda data: [(g["year"], [i["name"] for i in g["items"]]) for g in data["years"]]
    # 29 February can always be asked for, also in a year that does not have it
    assert groups(_otd(client, monkeypatch, date(2026, 5, 1), date="02-29")) == [(2020, ["2020.jpg"]),
                                                                                (2016, ["2016.jpg"])]
    # In a year without 29 February, 28 February also shows the photos of the 29th
    assert groups(_otd(client, monkeypatch, date(2026, 2, 28))) == [(2020, ["2020.jpg"]), (2019, ["28feb.jpg"]),
                                                                    (2016, ["2016.jpg"])]
    # In a leap year the 29th is its own day
    assert groups(_otd(client, monkeypatch, date(2028, 2, 28))) == [(2019, ["28feb.jpg"])]
    assert groups(_otd(client, monkeypatch, date(2028, 2, 29))) == [(2020, ["2020.jpg"]), (2016, ["2016.jpg"])]
    assert groups(_otd(client, monkeypatch, date(2026, 3, 1))) == [(2021, ["1maart.jpg"])]


# ---------- smart albums ----------

def _albums(client):
    r = client.get("/api/library/albums")
    assert r.status_code == 200, r.text
    return r.json()


def test_default_albums_come_once_with_counts(indexed, config, app):
    client = indexed
    data = _albums(client)
    by_key = {a["builtin"]: a for a in data["albums"]}
    assert list(by_key) == ["best", "videos", "undated", "located", "this_year", "iphone"]
    assert by_key["videos"]["count"] == _browse(client, kind="video")["total"] >= 1
    assert by_key["located"]["count"] == 4
    assert by_key["iphone"]["count"] == _browse(client, camera_contains="iPhone")["total"] >= 2
    assert by_key["undated"]["count"] == _browse(client, capture_date="missing")["total"]
    assert by_key["best"]["count"] == 0 and by_key["best"]["sort"] == "-rating"
    assert data["all"] == _browse(client)["total"] and data["ready"] is True
    # Opening one is a live query on the index
    assert _browse(client, album=by_key["iphone"]["id"])["total"] == by_key["iphone"]["count"]
    assert client.get("/api/library/browse", params={"album": by_key["iphone"]["id"], "year": 2020}).status_code == 400
    # Deleted defaults do not come back by themselves on the next start
    assert client.delete(f"/api/library/albums/{by_key['iphone']['id']}").status_code == 200
    from backend.routes import albums
    albums.init_router(config, albums.db)
    assert "iphone" not in {a["builtin"] for a in _albums(client)["albums"]}
    # ... only when asked: deleted ones are added again, hidden ones shown again
    client.patch(f"/api/library/albums/{by_key['videos']['id']}", json={"hidden": True})
    restored = client.post("/api/library/albums/defaults").json()
    assert restored["added"] == 1
    assert [(a["builtin"], a["hidden"]) for a in restored["albums"]] == [
        ("best", False), ("videos", False), ("undated", False), ("located", False), ("this_year", False),
        ("iphone", False)]


def test_album_crud_never_touches_a_file(indexed, config, vault):
    client = indexed
    before = _files(vault)
    r = client.post("/api/library/albums", json={"name": "  Zomer   2019 ", "sort": "name",
                                                 "filters": {"year": 2019, "folder": str(config.VAULT_ROOT / "RAW"),
                                                             "kind": ["photo"]}})
    assert r.status_code == 201, r.text
    album = r.json()
    assert album["name"] == "Zomer 2019" and album["builtin"] is None and album["hidden"] is False
    assert album["filters"] == {"year": 2019, "folder": "RAW", "kind": ["photo"]}   # stored relative
    assert album["count"] == 4 and album["icon"] == "fa-images"
    page = _browse(client, album=album["id"])
    assert page["sort"] == "name" and [i["name"] for i in page["items"]] == sorted(i["name"] for i in page["items"])
    assert client.get(f"/api/library/albums/{album['id']}").json()["count"] == 4

    # Changing it: name, filter, icon, hidden; a key not given stays
    r = client.patch(f"/api/library/albums/{album['id']}", json={"filters": {"year": 2019, "has_gps": True,
                                                                             "folder": "RAW/2019"},
                                                                "icon": "fa-umbrella-beach"})
    assert r.status_code == 200, r.text
    changed = r.json()
    assert changed["name"] == "Zomer 2019" and changed["count"] == 2 and changed["icon"] == "fa-umbrella-beach"
    assert changed["updated_at"] >= album["updated_at"]
    assert client.patch(f"/api/library/albums/{album['id']}", json={"hidden": True}).json()["hidden"] is True
    assert album["id"] not in {a["id"] for a in client.get("/api/library/albums",
                                                            params={"with_hidden": False}).json()["albums"]}

    # Refused: no name, a name that is taken, an unknown filter key, outside the vault, bad sort or icon
    for body, status in (({"name": " ", "filters": {}}, 400), ({"name": "zomer 2019"}, 409),
                         ({"name": "Rood", "filters": {"colour": "red"}}, 400),
                         ({"name": "Rood", "filters": {"folder": "../.."}}, 403),
                         ({"name": "Rood", "filters": {"folder": str(config.CACHE_FOLDER)}}, 403),
                         ({"name": "Rood", "sort": "random"}, 400), ({"name": "Rood", "icon": "fa-x onclick"}, 400),
                         ({"name": "x" * 81}, 400), ({"name": 5}, 400)):
        r = client.post("/api/library/albums", json=body)
        assert r.status_code == status, (body, r.text)
    assert client.patch(f"/api/library/albums/{album['id']}", json={"name": "Video's"}).status_code == 409
    assert client.patch(f"/api/library/albums/{album['id']}", json={"colour": 1}).status_code == 400
    assert client.patch("/api/library/albums/9999", json={"name": "x"}).status_code == 404
    assert client.post("/api/library/albums", json=["x"]).status_code in (400, 422)

    # Deleting returns what it was, so the page can put it back
    r = client.delete(f"/api/library/albums/{album['id']}")
    assert r.status_code == 200 and r.json()["deleted"]["name"] == "Zomer 2019"
    assert client.get(f"/api/library/albums/{album['id']}").status_code == 404
    assert client.delete(f"/api/library/albums/{album['id']}").status_code == 404
    back = client.post("/api/library/albums", json={k: r.json()["deleted"][k] for k in ("name", "filters", "sort", "icon")})
    assert back.status_code == 201 and back.json()["count"] == 2
    assert _files(vault) == before


def test_an_album_is_live_and_its_count_follows_the_index(indexed, config):
    client = indexed
    album = client.post("/api/library/albums", json={"name": "Bursts", "filters": {"folder": "RAW/2022/bursts"}}).json()
    n = album["count"]
    _photo(config.VAULT_ROOT / "RAW/2022/bursts/burst_09.jpg", (9, 9, 9), "9", "2022:05:01 12:00:09")
    _index(client)
    assert next(a for a in _albums(client)["albums"] if a["id"] == album["id"])["count"] == n + 1
    assert _browse(client, album=album["id"])["total"] == n + 1
    # A folder that is gone gives an empty album, not an error
    gone = client.post("/api/library/albums", json={"name": "Weg", "filters": {"folder": "RAW/weg"}}).json()
    assert gone["count"] == 0 and gone["error"] is None


def test_album_list_is_fast_with_many_rows(client, config, app):
    """The sidebar counts on 20,000 index rows: the first list and a cached one."""
    from backend.routes import albums
    db = albums.db
    now = time.time()
    root = str(config.VAULT_ROOT)
    rows = [(f"{root}/RAW/perf/{i // 500}/f{i}.jpg", f"{root}/RAW/perf/{i // 500}", f"f{i}.jpg",
             "video" if i % 10 == 0 else "photo", ".jpg", 1000 + i, i, now - i * 3600,
             "exif" if i % 3 else "mtime", 52.0 if i % 4 == 0 else None, now, now, now) for i in range(20000)]
    with db._connect() as conn:
        conn.executemany("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, taken, taken_source, lat, "
                         "added_at, indexed_at, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    db.set_setting("media_index", {"last_pass_at": now})
    started = time.perf_counter()
    data = _albums(client)
    first = time.perf_counter() - started
    assert data["all"] == 20000
    assert {a["builtin"]: a["count"] for a in data["albums"]}["videos"] == 2000
    started = time.perf_counter()
    _albums(client)
    again = time.perf_counter() - started
    assert first < 2.0 and again < 0.5, (first, again)
    started = time.perf_counter()
    page = _browse(client, kind="photo", keurer="undecided", sort="-rating", limit=200)
    assert page["total"] == 18000 and time.perf_counter() - started < 2.0


def test_camera_text_is_matched_literally(indexed):
    # % and _ in the text are characters, not wildcards
    assert _browse(indexed, camera_contains="iPhone")["total"] >= 2
    assert _browse(indexed, camera_contains="%")["total"] == 0
    assert _browse(indexed, camera_contains="i_hone")["total"] == 0


# ---------- review fixes ----------

def test_deep_pages_keep_the_order_of_one_full_sort(tmp_path):
    """query() sorts the ids and reads the rows of the page afterwards: same order, ties included."""
    from backend import media_index
    db = Database(tmp_path / "datavault.db")
    now = time.time()
    rows = [(f"/v/RAW/{i % 7}/f{i:03d}.jpg", f"/v/RAW/{i % 7}", f"f{i % 40:03d}.jpg", "photo", ".jpg",
             1000 + i % 5, i, now - (i % 9) * 3600, "exif", now, now - i, now) for i in range(300)]
    with db._connect() as conn:
        conn.executemany("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, taken, taken_source, "
                         "added_at, indexed_at, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.executemany("INSERT INTO file_ratings (path, rating, votes) VALUES (?, ?, 1)",
                         [(rows[i][0], 1000 + 50 * (i % 3)) for i in range(0, 300, 4)])
        columns = ", ".join(f"m.{c}" for c in media_index.ROW_COLUMNS)
        for sort, order in media_index.SORTS.items():
            if sort == "random":
                continue
            expected = [r[0] for r in conn.execute(
                f"SELECT m.path FROM media m LEFT JOIN file_ratings r ON r.path = m.path WHERE m.missing = 0 "
                f"ORDER BY {order}")]
            paged = [row["path"] for offset in range(0, 300, 23)
                     for row in media_index.query(db, {}, sort=sort, offset=offset, limit=23)]
            assert paged == expected, sort
            # Ratings come along with the rows of the page
            rated = media_index.query(db, {}, sort="-rating", limit=1)[0]
            assert rated["rating"] == 1100 and rated["votes"] == 1
    assert media_index.query(db, {}, sort="name", offset=10 ** 6) == []
    assert media_index.query(db, {}, limit=0) == []
    n, version = media_index.selection_info(db, {"kind": "photo"})
    assert n == 300 and version == media_index.selection_version(db, {"kind": "photo"})
    db.close_all()


def test_a_huge_album_id_is_refused_not_a_server_error(indexed):
    for method in ("get", "patch", "delete"):
        kwargs = {"json": {"name": "x"}} if method == "patch" else {}
        for album_id in ("99999999999999999999999", "-1"):
            r = getattr(indexed, method)(f"/api/library/albums/{album_id}", **kwargs)
            assert r.status_code == 422, (method, album_id, r.text)
    assert indexed.get(f"/api/library/albums/{2 ** 62}").status_code == 404


def test_names_stay_unique_also_for_restored_defaults_and_beyond_ascii(indexed):
    client = indexed
    videos = next(a for a in _albums(client)["albums"] if a["builtin"] == "videos")
    assert client.delete(f"/api/library/albums/{videos['id']}").status_code == 200
    assert client.post("/api/library/albums", json={"name": "video's", "filters": {"year": 2019}}).status_code == 201
    restored = client.post("/api/library/albums/defaults").json()
    assert restored["added"] == 1
    names = [a["name"] for a in restored["albums"]]
    assert "Video's (2)" in names and len({n.casefold() for n in names}) == len(names)
    # Case is ignored beyond ASCII too
    assert client.post("/api/library/albums", json={"name": "Ünïcode"}).status_code == 201
    assert client.post("/api/library/albums", json={"name": "ünïcode"}).status_code == 409
    other = client.post("/api/library/albums", json={"name": "Anders"}).json()
    assert client.patch(f"/api/library/albums/{other['id']}", json={"name": "ÜNÏCODE"}).status_code == 409
    assert client.patch(f"/api/library/albums/{other['id']}", json={"name": "anders"}).status_code == 200


def test_on_this_day_and_dit_jaar_leave_out_files_dated_only_by_the_file(client, config, monkeypatch):
    day = config.VAULT_ROOT / "RAW/dag"
    _photo(day / "echt.jpg", (1, 1, 1), "exif", "2020:07:14 10:00:00")
    copied = _photo(day / "kopie zonder exif.jpg", (2, 2, 2), "geen exif")
    stamp = datetime(2021, 7, 14, 12, 0).timestamp()
    os.utime(copied, (stamp, stamp))
    _index(client)
    # The copy really is dated 14 July 2021 by its file date (else this test proves nothing)
    assert _names(_browse(client, folder="RAW/dag", date_from="2021-07-14", date_to="2021-07-14")) == [
        "kopie zonder exif.jpg"]
    data = _otd(client, monkeypatch, date(2026, 10, 28), date="07-14", allowed_folders=["RAW/dag"])
    assert [(g["year"], [i["name"] for i in g["items"]]) for g in data["years"]] == [(2020, ["echt.jpg"])]
    # The default 'Dit jaar' counts real capture dates only
    this_year = next(a for a in _albums(client)["albums"] if a["builtin"] == "this_year")
    assert this_year["filters"] == {"period": "this_year", "capture_date": "known"}
    assert all(i["date_source"] in ("exif", "video") for i in _browse(client, album=this_year["id"])["items"])


def test_an_unchanged_dit_jaar_from_before_is_updated_once(client, config, app):
    import json
    from backend import smart_albums
    from backend.routes import albums
    db = albums.db
    with db._connect() as conn:
        conn.execute("UPDATE smart_albums SET filters = ? WHERE builtin = 'this_year'",
                     (json.dumps({"period": "this_year"}),))
        conn.execute("UPDATE smart_albums SET filters = ? WHERE builtin = 'videos'",
                     (json.dumps({"kind": ["video"], "year": 2019}),))
    db.set_setting(smart_albums.DEFAULTS_KEY, 1)
    assert smart_albums.ensure_defaults(db) is False
    by_key = {a["builtin"]: a for a in _albums(client)["albums"]}
    assert by_key["this_year"]["filters"] == {"period": "this_year", "capture_date": "known"}
    assert by_key["videos"]["filters"] == {"kind": ["video"], "year": 2019}
    assert len(by_key) == 6 and db.get_setting(smart_albums.DEFAULTS_KEY) == smart_albums.DEFAULTS_VERSION
    # A user who changed 'Dit jaar' back keeps it that way (the upgrade ran once)
    with db._connect() as conn:
        conn.execute("UPDATE smart_albums SET filters = ? WHERE builtin = 'this_year'",
                     (json.dumps({"period": "this_year"}),))
    smart_albums.ensure_defaults(db)
    assert {a["builtin"]: a for a in _albums(client)["albums"]}["this_year"]["filters"] == {"period": "this_year"}


def test_a_folders_list_filters_like_the_on_this_day_choice(indexed, config):
    clean = library_filters.clean
    assert clean({"folders": ["RAW/2019/", "library", "RAW/2019"]}) == {"folders": ["RAW/2019", "library"]}
    assert clean({"folders": "RAW,library", "recursive": False}) == {"folders": ["RAW,library"], "recursive": False}
    with pytest.raises(library_filters.FilterError):
        clean({"folder": "RAW", "folders": ["library"]})
    with pytest.raises(library_filters.FilterError):
        clean({"folders": [f"RAW/{i}" for i in range(301)]})
    client = indexed
    both = _browse(client, folders=["RAW/2019", "RAW/misc"])
    assert both["total"] == _browse(client, folder="RAW/2019")["total"] + _browse(client, folder="RAW/misc")["total"]
    assert client.get("/api/library/browse", params={"folders": ["RAW", "../.."]}).status_code == 403
    album = client.post("/api/library/albums", json={"name": "Twee mappen",
                                                     "filters": {"folders": [str(config.VAULT_ROOT / "RAW/misc"),
                                                                             "RAW/2019"]}}).json()
    assert album["filters"] == {"folders": ["RAW/2019", "RAW/misc"]} and album["count"] == both["total"]


def test_each_on_this_day_year_opens_exactly_its_own_files(client, config, monkeypatch):
    leap = config.VAULT_ROOT / "RAW/schrikkel"
    _photo(leap / "2020a.jpg", (1, 1, 1), "a", "2020:02:28 12:00:00")
    _photo(leap / "2020b.jpg", (2, 2, 2), "b", "2020:02:29 12:00:00")
    _photo(leap / "2019.jpg", (3, 3, 3), "c", "2019:02:28 08:00:00")
    _photo(config.VAULT_ROOT / "library/elders/2020c.jpg", (4, 4, 4), "d", "2020:02:28 09:00:00")
    _index(client)
    for folders in ([], ["RAW/schrikkel"]):
        data = _otd(client, monkeypatch, date(2026, 2, 28), allowed_folders=folders)
        assert [g["year"] for g in data["years"]] == [2020, 2019]
        for group in data["years"]:
            spec = group["filters"]
            assert ("folders" in spec) == bool(folders)
            page = _browse(client, **spec)
            assert page["total"] == group["total"], (folders, group["year"], spec)
            assert {i["path"] for i in page["items"]} == {i["path"] for i in group["items"]}
        assert data["years"][0]["filters"]["date_to"] == "2020-02-29"
        assert data["years"][1]["filters"]["date_to"] == "2019-02-28"
    # In a leap year 28 February is one day only
    data = _otd(client, monkeypatch, date(2028, 2, 28))
    assert data["years"][0]["filters"]["date_to"] == "2020-02-28" and data["years"][0]["total"] == 2


def test_a_folder_with_a_comma_survives_the_address_and_home_matches_the_screen(client, config, monkeypatch):
    folder = config.VAULT_ROOT / "RAW/Reis, Italië"
    _photo(folder / "a.jpg", (1, 1, 1), "a", "2019:07:14 12:00:00")
    _index(client)
    assert library_filters.from_params([("folders", "RAW/Reis, Italië"), ("kind", "photo,video")]) == {
        "folders": ["RAW/Reis, Italië"], "kind": ["photo", "video"]}
    page = _browse(client, folders=["RAW/Reis, Italië"])
    assert page["total"] == 1 and page["filters"]["folders"] == ["RAW/Reis, Italië"]
    data = _otd(client, monkeypatch, date(2026, 7, 14), allowed_folders=[str(folder)])
    group = data["years"][0]
    assert _browse(client, **group["filters"])["total"] == group["total"] == 1


def test_many_home_folders_still_open_in_the_screen(client, config, monkeypatch):
    folders = [str(config.VAULT_ROOT / f"RAW/map{i}") for i in range(80)]
    assert library_filters.clean({"folders": folders})["folders"]


def test_home_widget_leaves_out_file_dated_files_like_the_screen(client, config, monkeypatch):
    now = datetime.now()
    legacy = config.VAULT_ROOT / "library/WhatsApp/IMG-old.jpg"
    _photo(legacy, (9, 9, 9), "w")
    stamp = datetime(now.year - 2, now.month, min(now.day, 28), 12).timestamp()
    os.utime(legacy, (stamp, stamp))
    _index(client)
    shown = {p["name"] for p in client.get("/api/stats/photo-on-this-day").json()["photos"]}
    assert "IMG-old.jpg" not in shown


def test_creating_an_album_refuses_unknown_fields_like_updating(client):
    r = client.post("/api/library/albums", json={"name": "Diep", "filters": {"folder": "RAW"}, "extra": 1})
    assert r.status_code == 400 and "Onbekend veld: extra" in r.json()["detail"]["error"] if isinstance(r.json().get("detail"), dict) else r.status_code == 400
