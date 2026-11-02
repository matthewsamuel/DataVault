"""
DataGatherer step 3.9c: Google Takeout (Dutch and English names), location history in all
four formats, estimated positions for photos without GPS (time zones: DST, EXIF offset,
the zone of the place, videos in UTC), the Kaart endpoints, the Activiteit API, the own
watch folder, migration 110 and the assistant's day context. Everything is synthetic
(fake_exports.py); nothing reads real personal data or goes online.
"""
import json
import os
import shutil
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import fake_exports as fx
from backend.datagatherer import (activity_store, assistant_context, daemon, geotag, google_formats as gf,
                                  location_store, state, timezones)
from fixtures.vault_builder import DATA_OWNER


def _wait(app, job_id, timeout=60):
    return app.state.jobs.wait(job_id, timeout=timeout)


def _import(client, app, source):
    r = client.post(f"/api/datagatherer/sources/{source}/import")
    assert r.status_code in (200, 201), r.text
    job = r.json()["job"]
    assert _wait(app, job["id"])["status"] == "done"
    return app.state.jobs.result(job["id"])


def _rows(app, sql, args=()):
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        return conn.execute(sql, args).fetchall()


def _raw_google(vault: Path) -> Path:
    folder = vault / "data" / DATA_OWNER / "RAW_google"
    fx.dutch_takeout_folder(folder)
    fx.english_takeout_zip(folder / "takeout-20190901T000000Z-001.zip")
    return folder


def _snapshot(folder: Path):
    return sorted((str(p.relative_to(folder)), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in folder.rglob("*") if p.is_file())


# ---------- formats ----------

@pytest.mark.parametrize("path,expected", [
    ("Takeout/Mijn activiteit/YouTube/MijnActiviteit.json", "myactivity"),
    ("Takeout/My Activity/Search/MyActivity.html", "myactivity"),
    ("Takeout/YouTube en YouTube Music/geschiedenis/kijkgeschiedenis.json", "watch"),
    ("Takeout/YouTube and YouTube Music/history/search-history.html", "search"),
    ("Takeout/Chrome/BrowserHistory.json", "chrome"),
    ("Takeout/Agenda/Werk.ics", "calendar"),
    ("Takeout/Calendar/Work.ics", "calendar"),
    ("Takeout/Keep/Note.json", "keep"),
    ("Takeout/Locatiegeschiedenis/Records.json", "location"),
    ("Takeout/Tijdlijn/Semantic Location History/2019/2019_JULI.json", "location"),
    ("Takeout/Location History/Semantic Location History/2019/2019_JULY.json", "location"),
    ("Takeout/Google Foto's/Album/IMG_1.jpg.json", None),
    ("Takeout/Mail/All mail.mbox", None),
])
def test_takeout_files_are_recognised_by_dutch_and_english_names(path, expected):
    assert gf.route(path) == expected


def test_written_dates_in_dutch_and_english_become_utc():
    utc = lambda s: datetime.fromisoformat(s).timestamp() * 1000
    assert gf.parse_written_date("14 jul. 2019, 12:30:00 CEST") == utc("2019-07-14T10:30:00+00:00")
    assert gf.parse_written_date("Jul 14, 2019, 3:00:00 PM CEST") == utc("2019-07-14T13:00:00+00:00")
    assert gf.parse_written_date("1 mrt 2020, 08:00:00 CET") == utc("2020-03-01T07:00:00+00:00")
    assert gf.parse_written_date("1 okt 2020, 08:00:00 GMT+02:00") == utc("2020-10-01T06:00:00+00:00")
    # An unknown zone: Brussels with daylight saving time is assumed
    assert gf.parse_written_date("1 jan 2020, 08:00:00 XYZ") == utc("2020-01-01T07:00:00+00:00")
    assert gf.parse_written_date("geen datum") is None


def test_ics_handles_time_zones_all_day_folding_and_escapes():
    events = list(gf.parse_ics(fx.ICS.encode(), "Takeout/Agenda/Mijn agenda.ics"))
    assert [e["title"] for e in events] == ["Verjaardag, bij oma", "Vrije dag",
                                            "Een heel lange titel die over twee regels gevouwen is"]
    assert events[0]["ts_ms"] == datetime(2019, 7, 14, 12, 0).replace(tzinfo=ZoneInfo("UTC")).timestamp() * 1000
    assert events[0]["details"]["locatie"] == "Gent"
    assert events[1]["details"]["hele_dag"] is True
    assert events[2]["details"]["herhaalt"] is True


def test_location_formats_are_detected_by_content():
    for data, fmt in ((fx.records_json(), "records"), (fx.semantic_json(), "semantic"),
                      (fx.device_json(), "device"), (fx.ios_json(), "ios")):
        found, points, visits = location_store.parse(data)
        assert found == fmt and points, fmt
        assert location_store.looks_like_location_json(json.dumps(data).encode())
    _, points, visits = location_store.parse(fx.ios_json())
    assert visits[0]["lat"] == 51.1 and visits[0]["semantic_type"] == "Home"
    # durationMinutesOffsetFromStartTime: 15 minutes after the segment start
    start = location_store.parse_time("2024-03-31T11:00:00.000+02:00")
    assert (start + 15 * 60000, 51.15, 3.85, None) in points
    _, points, _ = location_store.parse(fx.device_json())
    assert (location_store.parse_time("2024-03-30T14:00:00.000+01:00"), 51.03, 3.75, 12.0) in points
    with pytest.raises(location_store.NotLocationHistory):
        location_store.parse({"Saved Media": []})
    assert location_store.parse_coord("geo:0,0") is None and location_store.parse_coord("91°, 3°") is None


# ---------- Takeout import ----------

def test_dutch_and_english_takeouts_are_read_in_place_without_duplicates(client, app, vault):
    folder = _raw_google(vault)
    before = _snapshot(folder)
    result = _import(client, app, "google_takeout")
    assert result["errors"] == 0 and result["imports"] == 1
    kinds = dict(_rows(app, "SELECT kind, COUNT(*) FROM activity_events WHERE source = 'google_takeout' GROUP BY kind"))
    # The video watched is in My Activity AND in kijkgeschiedenis, in the Dutch AND the English Takeout: once
    assert kinds["youtube_watch"] == 3       # abc, def (Maps HTML file has a YouTube cell), ghi
    assert kinds["youtube_search"] == 2      # fietsroutes, treinreis
    assert kinds["search"] == 1 and kinds["maps"] == 1 and kinds["web_visit"] == 2
    assert kinds["calendar"] == 3 and kinds["note"] == 1
    titles = {r[0] for r in _rows(app, "SELECT title FROM activity_events")}
    assert {"Kattenfilmpje", "weer Gent", "Gravensteen", "Fietsen in Gent", "Boodschappen"} <= titles
    points = _rows(app, "SELECT COUNT(*) FROM location_points WHERE origin = 'google_takeout'")[0][0]
    visits = _rows(app, "SELECT name FROM location_visits")
    assert points == 2 + 2 + 3 and visits == [("Gravensteen",)]
    # The archive is untouched (registered in place, zips never unpacked) and photo sidecars are skipped
    assert _snapshot(folder) == before
    assert not _rows(app, "SELECT 1 FROM activity_events WHERE title LIKE 'IMG_%'")
    # Twice: nothing new
    totals = _rows(app, "SELECT COUNT(*) FROM activity_events")[0][0], _rows(app, "SELECT COUNT(*) FROM location_points")[0][0]
    state.update_raw(app.state.db, _rows(app, "SELECT id FROM dg_raw_imports")[0][0], status="pending")
    _import(client, app, "google_takeout")
    assert (_rows(app, "SELECT COUNT(*) FROM activity_events")[0][0], _rows(app, "SELECT COUNT(*) FROM location_points")[0][0]) == totals
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "google_takeout")
    assert card["records"] > 0 and any("locatiepunt" in n["text"] for n in card["notes"])


def test_a_takeout_zip_from_downloads_is_copied_and_read(client, app, config, tmp_path):
    zip_path = fx.english_takeout_zip(tmp_path / "takeout-20240101T000000Z-001.zip")
    r = client.post("/api/datagatherer/sources/google_takeout/upload?name=takeout-20240101T000000Z-001.zip",
                    content=zip_path.read_bytes())
    assert r.status_code in (200, 201), r.text
    job = r.json()["job"]
    assert _wait(app, job["id"])["status"] == "done"
    result = app.state.jobs.result(job["id"])
    assert _wait(app, result["import_job"])["status"] == "done"
    assert _rows(app, "SELECT COUNT(*) FROM activity_events WHERE kind = 'calendar'")[0][0] == 3
    assert Path(result["path"]).is_file() and "Google" in result["path"]


# ---------- Tijdlijn from the phone ----------

@pytest.mark.parametrize("maker,name", [(fx.ios_json, "location-history.json"), (fx.device_json, "Timeline.json")])
def test_a_phone_timeline_export_is_read_once(client, app, tmp_path, maker, name):
    path = tmp_path / name
    path.write_text(json.dumps(maker()))
    for attempt in range(2):
        r = client.post(f"/api/datagatherer/sources/google_timeline/upload?name={name}", content=path.read_bytes())
        assert r.status_code in (200, 201), r.text
        assert _wait(app, r.json()["job"]["id"])["status"] == "done"
        result = app.state.jobs.result(r.json()["job"]["id"])
        if result["import_job"]:
            assert _wait(app, result["import_job"])["status"] == "done"
        count = _rows(app, "SELECT COUNT(*) FROM location_points")[0][0]
        if attempt == 0:
            first = count
    assert count == first > 0
    assert _rows(app, "SELECT COUNT(*) FROM location_visits")[0][0] == 1


def test_a_non_timeline_json_is_refused(client, tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"hello": "world"}))
    r = client.post("/api/datagatherer/sources/google_timeline/upload?name=other.json", content=path.read_bytes())
    assert r.status_code == 400


def test_an_own_watch_folder_is_followed_without_a_request(client, app, vault, tmp_path):
    folder = tmp_path / "iCloud" / "DataVault"
    folder.mkdir(parents=True)
    # Refused: not a folder, not a full path, inside the vault
    for bad in ("/bestaat/niet", "relatief/pad", str(vault / "data")):
        r = client.put("/api/datagatherer/sources/google_timeline/settings", json={"settings": {"watch_folder": bad}})
        assert r.status_code == 400, bad
    r = client.put("/api/datagatherer/sources/google_timeline/settings", json={"settings": {"watch_folder": str(folder)}})
    assert r.status_code == 200
    export = folder / "location-history.json"
    export.write_text(json.dumps(fx.ios_json()))
    os.utime(export, (time.time() - 3600, time.time() - 3600))
    assert not state.open_requests(app.state.db)
    gather = daemon.Daemon(app.state.config, app.state.db, lambda: app.state.jobs)
    started = gather.tick()
    assert started["watch"] == [str(export)]
    deadline = time.time() + 30
    while time.time() < deadline and not _rows(app, "SELECT COUNT(*) FROM location_points")[0][0]:
        time.sleep(0.1)
    assert _rows(app, "SELECT COUNT(*) FROM location_points")[0][0] > 0
    assert export.is_file()       # copied, never moved
    daemon.forget_seen()
    assert gather.tick()["watch"] == []     # already in the archive


# ---------- estimated positions for photos without GPS ----------

def _media(db, path, taken, source="exif", kind="photo", lat=None):
    now = time.time()
    with db._connect() as conn:
        cur = conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, taken, taken_source, lat, lon, "
                           "added_at, indexed_at, seen_at) VALUES (?, ?, ?, ?, '.jpg', 1, 1, ?, ?, ?, ?, ?, ?, ?)",
                           (path, str(Path(path).parent), Path(path).name, kind, taken, source, lat, lat, now, now, now))
        return cur.lastrowid


def _points(db, rows, origin="google_timeline"):
    location_store.insert(db, origin, None, "test", rows, [])


def _ms(text):
    return int(datetime.fromisoformat(text).timestamp() * 1000)


def _wall(text):
    """media.taken as media_dates stores it: the EXIF wall time read as this Mac's local time."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


def test_photos_without_gps_get_an_estimate_between_two_points(app):
    db = app.state.db
    _points(db, [(_ms("2019-07-14T07:50:00+00:00"), 51.05, 3.72, 20.0), (_ms("2019-07-14T08:10:00+00:00"), 51.05, 3.75, 20.0)])
    # 10:00 Brussels summer time = 08:00 UTC: halfway
    mid = _media(db, "/v/a.jpg", _wall("2019-07-14 10:00:00"))
    far = _media(db, "/v/b.jpg", _wall("2019-07-14 16:00:00"))            # no history near
    gps = _media(db, "/v/c.jpg", _wall("2019-07-14 10:00:00"), lat=50.0)  # has its own GPS
    stats = geotag.run(db, read_offset=lambda p: None)
    assert stats["estimated"] == 1
    est = geotag.inferred(db, mid)
    assert est["method"] == "interpolatie" and abs(est["lon"] - 3.735) < 1e-6 and est["tz_source"] == "locatie"
    assert est["tz"] == "Europe/Brussels" and 0.2 <= est["confidence"] <= 0.95
    assert geotag.inferred(db, far) is None and geotag.inferred(db, gps) is None


def test_the_dst_change_and_the_exif_offset_are_respected(app):
    db = app.state.db
    # 28 March 2021: Brussels goes from UTC+1 to UTC+2 at 02:00 local. 03:30 local = 01:30 UTC
    _points(db, [(_ms("2021-03-28T01:25:00+00:00"), 51.0, 4.0, 10.0), (_ms("2021-03-28T01:35:00+00:00"), 51.0, 4.1, 10.0),
                 # a point an hour earlier somewhere else: a wrong (winter) offset would land there
                 (_ms("2021-03-28T02:25:00+00:00"), 50.0, 5.0, 10.0), (_ms("2021-03-28T02:35:00+00:00"), 50.0, 5.1, 10.0)])
    after_dst = _media(db, "/v/dst.jpg", _wall("2021-03-28 03:30:00"))
    # A camera that wrote OffsetTimeOriginal +01:00 (still on winter time): 03:30+01:00 = 02:30 UTC
    with_offset = _media(db, "/v/offset.jpg", _wall("2021-03-28 03:30:00"))
    offsets = {"/v/offset.jpg": timedelta(hours=1)}
    geotag.run(db, read_offset=lambda p: offsets.get(p))
    assert abs(geotag.inferred(db, after_dst)["lon"] - 4.05) < 1e-6
    est = geotag.inferred(db, with_offset)
    assert est["tz_source"] == "exif" and est["tz"] == "+01:00" and abs(est["lon"] - 5.05) < 1e-6


def test_the_zone_of_the_place_is_used_far_from_home_and_videos_are_utc(app):
    db = app.state.db
    # New York in summer: UTC-4. 12:00 local = 16:00 UTC
    _points(db, [(_ms("2019-07-20T15:55:00+00:00"), 40.71, -74.0, 10.0), (_ms("2019-07-20T16:05:00+00:00"), 40.72, -74.0, 10.0)])
    photo = _media(db, "/v/ny.jpg", _wall("2019-07-20 12:00:00"))
    video = _media(db, "/v/ny.mov", _ms("2019-07-20T16:00:00+00:00") / 1000, source="video", kind="video")
    geotag.run(db, read_offset=lambda p: None)
    est = geotag.inferred(db, photo)
    assert est["tz"] == "America/New_York" and abs(est["lat"] - 40.715) < 1e-6
    assert geotag.inferred(db, video)["tz_source"] == "utc"
    assert timezones.zone_at(51.05, 3.72) == ("Europe/Brussels", "land")
    assert timezones.zone_at(-33.87, 151.21)[0] == "Australia/Sydney"
    # The Kaart's outlines carry Dutch names; the zones are still found by the English name.
    assert timezones.country_at(51.05, 3.72) == "België"
    assert timezones.zone_at(39.0, -77.5) == ("America/New_York", "land")


def test_every_country_outline_still_finds_its_time_zones():
    data = timezones._load()
    english = {shape[0] for shape in data["shapes"]}
    assert len(english) > 200 and len(english - set(data["countries"])) <= 1   # only Heard I. and McDonald Is. has none


def test_a_real_photo_offset_is_read_from_exif_and_the_file_is_never_written(tmp_path):
    from PIL import Image
    path = tmp_path / "offset.jpg"
    img = Image.new("RGB", (8, 8), "red")
    exif = Image.Exif()
    exif[0x0110] = "Testcamera"
    exif.get_ifd(0x8769)[0x9003] = "2021:03:28 03:30:00"
    exif.get_ifd(0x8769)[0x9011] = "+09:00"
    img.save(path, exif=exif)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert geotag.exif_offset(str(path)) == timedelta(hours=9)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert geotag.parse_offset("-05:30") == -timedelta(hours=5, minutes=30) and geotag.parse_offset("bad") is None


def test_the_map_shows_estimates_apart_and_the_viewer_can_ask(client, app, vault):
    db = app.state.db
    path = str(vault / "RAW" / "geschat.jpg")
    _points(db, [(_ms("2019-07-14T07:50:00+00:00"), 51.05, 3.72, 20.0), (_ms("2019-07-14T08:10:00+00:00"), 51.05, 3.75, 20.0)])
    mid = _media(db, path, _wall("2019-07-14 10:00:00"))
    geotag.run(db, read_offset=lambda p: None)
    data = client.get("/api/map/inferred").json()
    assert data["total"] == 1 and data["points"][0][0] == mid and len(data["points"][0]) == 6
    assert mid not in [p[0] for p in client.get("/api/map/points").json()["points"]]
    found = client.get("/api/map/locate", params={"path": path}).json()
    assert found["has_gps"] is False and found["inferred"]["source"] == "tijdlijn"
    assert found["inferred"]["place"] and found["inferred"]["place_parts"]["label"] == found["inferred"]["place"]
    summary = client.get("/api/location/summary").json()
    assert summary["inferred"] == 1 and summary["points"] == 2
    day = datetime.fromtimestamp(_ms("2019-07-14T08:00:00+00:00") / 1000).strftime("%Y-%m-%d")
    track = client.get("/api/location/track", params={"day": day}).json()
    assert len(track["points"]) == 2
    assert client.get("/api/location/track", params={"day": "14-07-2019"}).status_code == 400
    r = client.post("/api/location/geotag")
    assert r.status_code in (200, 201) and _wait(app, r.json()["job"]["id"])["status"] == "done"
    assert client.put("/api/location/settings", json={"max_gap_minutes": 0}).status_code == 400
    assert client.put("/api/location/settings", json={"max_gap_minutes": 45}).json()["max_gap_minutes"] == 45


# ---------- Activiteit API ----------

def test_the_activity_api_groups_per_day_and_filters(client, app, vault):
    _raw_google(vault)
    _import(client, app, "google_takeout")
    facets = client.get("/api/activity/facets").json()
    assert facets["sources"][0]["name"] == "Google Takeout"
    assert {"youtube_watch", "calendar"} <= {k["id"] for k in facets["kinds"]}
    days = client.get("/api/activity/days").json()["days"]
    assert days and days == sorted(days, key=lambda d: d["day"], reverse=True)
    only = client.get("/api/activity/days", params=[("kind", "calendar")]).json()["days"]
    assert sum(d["count"] for d in only) == 3
    day = only[-1]["day"]
    events = client.get("/api/activity/events", params=[("day", day), ("kind", "calendar")]).json()["events"]
    assert events and all(e["kind"] == "calendar" and e["source_name"] == "Google Takeout" for e in events)
    q = client.get("/api/activity/days", params={"q": "Kattenfilmpje"}).json()["days"]
    assert sum(d["count"] for d in q) == 1
    assert client.get("/api/activity/events", params={"day": "2019-02-30"}).status_code == 400


# ---------- the assistant ----------

def test_the_assistant_reads_a_day_only_when_allowed(client, app):
    db = app.state.db
    location_store.insert(db, "google_timeline", None, "semantic", [(_ms("2019-07-14T10:00:00+00:00"), 51.057, 3.721, None)],
                          [{"start": _ms("2019-07-14T10:00:00+00:00"), "end": _ms("2019-07-14T11:00:00+00:00"),
                            "lat": 51.057, "lon": 3.721, "name": "Gravensteen", "place_id": "x"}])
    as_data = lambda names, limit=25, n=80: json.dumps(names[:limit])
    assert assistant_context.day_in("Waar was ik op 14 juli 2019?") == date(2019, 7, 14)
    assert assistant_context.day_in("where was I on July 14, 2019") == date(2019, 7, 14)
    assert assistant_context.day_in("en op 2019-07-14?") == date(2019, 7, 14)
    assert assistant_context.day_in("wat deed ik gisteren", date(2020, 1, 2)) == date(2020, 1, 1)
    assert assistant_context.day_in("hoe gaat het") is None
    assert assistant_context.lines(db, "Waar was ik op 14 juli 2019?", as_data) == []     # off by default
    client.put("/api/datagatherer/sources/google_timeline/settings", json={"settings": {"pasha": True}})
    found = assistant_context.lines(db, "Waar was ik op 14 juli 2019?", as_data)
    assert found and "Gravensteen" in found[0] and "14 juli 2019" in found[0]
    from backend.routes import chat
    assert "Gravensteen" in chat.history_context("Waar was ik op 14 juli 2019?")
    assert chat.history_context("Hallo") == ""


# ---------- migration ----------

def test_migration_110_adds_tables_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from backend.database import Database
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 61, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{\"full\": true}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                     "VALUES ('/v/RAW/a.jpg', '/v/RAW', 'a.jpg', 'photo', '.jpg', 10, 1, 1, 1, 1)")
        conn.execute("INSERT INTO spotify_plays (play_key, source, ts_ms, content, first_seen_at) "
                     "VALUES ('x|1', 'export_extended', 1, 'track', 1)")
        conn.execute("INSERT INTO dg_connectors (source_id, schedule_enabled, updated_at) VALUES ('spotify', 1, 1.0)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 110
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {"activity_events", "location_points", "location_visits", "media_location_inferred"} <= names
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    assert state.schedule_enabled(db, "spotify")
    db.close_all()


def test_my_activity_as_html_and_as_json_is_one_record(app):
    """Takeout HTML has whole seconds, its JSON milliseconds: the same video is one row."""
    html = [e for e in gf.parse_activity_html(fx.MY_ACTIVITY_HTML.encode(), None) if e["title"] == "Fietsen in Gent"]
    entry = {"header": "YouTube", "title": "Watched Fietsen in Gent", "titleUrl": "https://www.youtube.com/watch?v=def",
             "time": "2019-07-14T13:00:00.417Z", "products": ["YouTube"]}
    json_events = list(gf.parse_activity_json(json.dumps([entry]).encode(), "YouTube", "youtube_watch"))
    assert len(html) == 1 and html[0]["ts_ms"] == 1563109200000 and json_events[0]["ts_ms"] == 1563109200417
    assert html[0]["key"] == json_events[0]["key"]
    activity_store.insert(app.state.db, "google_takeout", None, html)
    assert activity_store.insert(app.state.db, "google_takeout", None, json_events)["added"] == 0


def test_migration_135_rekeys_my_activity_per_second_and_keeps_every_other_row(tmp_path, monkeypatch):
    import hashlib
    from backend import migrations
    from backend.database import Database
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db

    def old_key(*parts):
        return hashlib.sha1("\x1f".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:24]

    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 110, monkeypatch)
    url = "https://www.youtube.com/watch?v=def"
    rows = [  # (source, kind, ts_ms, title, url, details, key)
        ("google_takeout", "youtube_watch", 1563109200000, "Fietsen in Gent", url, None,                       # HTML
         old_key("youtube_watch", 1563109200000, url)),
        ("google_takeout", "youtube_watch", 1563109200417, "Fietsen in Gent", url, '{"kanaal": "Kanaal Y"}',  # JSON
         old_key("youtube_watch", 1563109200417, url)),
        ("google_takeout", "search", 1563094800000, "weer Gent", None, None, old_key("search", 1563094800000, "weer Gent")),
        ("google_takeout", "web_visit", 1563091200123, "Voorbeeld", "https://voorbeeld.be/",
         '{"browser": "Chrome", "bron": "Takeout"}', old_key("web_visit", 1563091200123, "https://voorbeeld.be/")),
        ("google_takeout", "calendar", 1563091200000, "Etentje", None, None, "cal-1"),
        ("netflix", "video_watch", 1563091200000, "Documentaire", None, None, "nf-1"),
    ]
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany("INSERT INTO activity_events (source, kind, ts_ms, title, url, details, event_key, first_seen_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, 1.0)", rows)
        conn.execute("INSERT INTO location_points (ts_ms, lat, lon, source, origin) VALUES (1, 51.0, 3.7, 'records', 'x')")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT IN ('schema_version', 'activity_events')")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 135
        for table, kept in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == kept, table
        after = {(r[0], r[1]): r[2:] for r in conn.execute(
            "SELECT source, kind, ts_ms, details, event_key FROM activity_events ORDER BY id")}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(after) == 5                                     # the HTML copy of the video is gone, nothing else
    assert after[("google_takeout", "youtube_watch")][:2] == (1563109200417, '{"kanaal": "Kanaal Y"}')
    assert after[("google_takeout", "youtube_watch")][2] == activity_store.key("youtube_watch", 1563109200, url)
    assert after[("google_takeout", "search")][2] == activity_store.key("search", 1563094800, "weer Gent")
    assert after[("google_takeout", "web_visit")][2] == rows[3][6]            # Chrome's own history keeps its key
    assert after[("google_takeout", "calendar")][2] == "cal-1" and after[("netflix", "video_watch")][2] == "nf-1"
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    db.close_all()


def _big_records(n):
    locations = [{"latitudeE7": 510500000 + i, "longitudeE7": 37200000 + i, "accuracy": 10 + i % 7,
                  "timestamp": f"2019-07-14T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}Z",
                  "activity": [{"timestamp": "2019-07-14T00:00:00Z", "activity": [{"type": "STILL", "confidence": 90}]}]}
                 for i in range(n)]
    locations.insert(3, {"latitudeE7": "x"})          # junk in between is skipped, as in parse()
    return {"locations": locations}


def test_a_large_records_json_is_streamed_with_the_same_points(tmp_path):
    import io as _io
    data = _big_records(500)
    for text in (json.dumps(data, indent=2), "﻿" + json.dumps(data)):   # pretty (Google's shape) and compact
        blob = text.encode("utf-8")
        _, whole, _ = location_store.parse_bytes(blob)
        for chunk in (7, 64, 4096):                       # objects cut at every possible place
            streamed = list(location_store.iter_records(lambda: _io.BytesIO(blob), chunk=chunk))
            assert streamed == whole and len(streamed) == 500
    with pytest.raises(location_store.NotLocationHistory):
        list(location_store.iter_records(lambda: _io.BytesIO(json.dumps(data)[:-500].encode()), chunk=64))
    with pytest.raises(location_store.NotLocationHistory):
        list(location_store.iter_records(lambda: _io.BytesIO(b'{"timelineObjects": []}')))


def test_a_records_json_over_the_read_limit_is_still_read_and_a_skipped_file_is_on_the_card(client, app, tmp_path,
                                                                                             monkeypatch):
    from backend.datagatherer import raw_store
    monkeypatch.setattr(raw_store, "MAX_MEMBER_BYTES", 20_000)     # stand-in for 1 GB
    monkeypatch.setattr(location_store, "STREAM_FROM", 10_000)
    zip_path = fx.write_zip(tmp_path / "takeout-20240101T000000Z-001.zip", {
        "Takeout/Location History/Records.json": json.dumps(_big_records(300), indent=2),
        "Takeout/Mijn activiteit/Zoeken/MyActivity.json": "[" + ",".join(
            json.dumps({"header": "Zoeken", "title": f"Gezocht naar iets {i} " + "x" * 200,
                        "time": "2019-07-14T09:00:00.000Z", "products": ["Zoeken"]}) for i in range(200)) + "]",
    })
    r = client.post(f"/api/datagatherer/sources/google_takeout/upload?name={zip_path.name}", content=zip_path.read_bytes())
    assert r.status_code in (200, 201), r.text
    result = app.state.jobs.result(_wait(app, r.json()["job"]["id"])["id"])
    assert _wait(app, result["import_job"])["status"] == "done"
    assert _rows(app, "SELECT COUNT(*) FROM location_points")[0][0] == 300
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "google_takeout")
    warning = [n for n in card["notes"] if n["tone"] == "warning"]
    assert warning and "MyActivity.json" in warning[0]["text"] and "Niet alles" in warning[0]["text"]
