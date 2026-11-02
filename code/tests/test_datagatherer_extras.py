"""
DataGatherer step 3.9c: browser history (Chrome and Safari, read from a private copy of
the database, never in place) and the extra export sources, one connector file each:
Netflix, Snapchat, TikTok, Apple (privacy.apple.com, zips inside zips) and Apple Health
(streamed export.xml). Every importer is idempotent. All databases and exports are
synthetic (fake_exports.py); the real browser folders are never read in test mode.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

import fake_exports as fx
from backend.datagatherer import browser_history, registry


def _wait(app, job_id, timeout=60):
    return app.state.jobs.wait(job_id, timeout=timeout)


def _rows(app, sql, args=()):
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        return conn.execute(sql, args).fetchall()


def _count(app, source, kind=None):
    sql = "SELECT COUNT(*) FROM activity_events WHERE source = ?" + (" AND kind = ?" if kind else "")
    return _rows(app, sql, (source, kind) if kind else (source,))[0][0]


def _upload(client, app, source, path: Path, expect=(200, 201)):
    r = client.post(f"/api/datagatherer/sources/{source}/upload?name={path.name}", content=path.read_bytes())
    assert r.status_code in expect, r.text
    if r.status_code >= 400:
        return None
    assert _wait(app, r.json()["job"]["id"])["status"] == "done"
    result = app.state.jobs.result(r.json()["job"]["id"])
    if result["import_job"]:
        assert _wait(app, result["import_job"])["status"] == "done"
        result["import"] = app.state.jobs.result(result["import_job"])
    return result


def _sync(client, app, source, ok=True):
    r = client.post(f"/api/datagatherer/sources/{source}/sync")
    assert r.status_code in (200, 201), r.text
    final = _wait(app, r.json()["job"]["id"])
    assert (final["status"] == "done") is ok, final
    return final


def test_every_new_source_is_its_own_connector_file():
    folder = Path(registry.__file__).parent / "connectors"
    for source in ("google_timeline", "chrome_history", "safari_history", "netflix", "snapchat", "tiktok",
                   "apple_privacy", "apple_health"):
        assert (folder / f"{source}.py").is_file()
        connector = registry.get(source)
        assert connector is not None and not registry.validate(connector)
        assert "pasha" in [f.key for f in connector.settings]   # the assistant opt-in, off by default


def test_the_real_browser_folders_are_never_used_in_test_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("DATAVAULT_CHROME_DIR", raising=False)
    assert browser_history.chrome_root() is None and browser_history.safari_root() is None
    monkeypatch.setenv("DATAVAULT_CHROME_DIR", str(Path.home() / "Library"))
    assert browser_history.chrome_root() is None


def test_chrome_history_is_read_from_a_copy_once_and_incrementally(client, app, monkeypatch, tmp_path):
    root = tmp_path / "Chrome"
    history = fx.chrome_history_db(root / "Default" / "History", [
        (1563091200, "https://voorbeeld.be/", "Voorbeeld"), (1563094800, "https://ander.be/", "Ander")])
    fx.chrome_history_db(root / "Profile 1" / "History", [(1563098400, "https://werk.be/", "Werk")])
    (root / "System Profile").mkdir()
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "chrome_history")
    assert card["primary"]["action"] == "sync" and card["network"]["local"]
    # Not found yet (no folder): the card says so
    monkeypatch.setenv("DATAVAULT_CHROME_DIR", str(tmp_path / "geen"))
    assert client.post("/api/datagatherer/sources/chrome_history/sync").status_code == 409
    monkeypatch.setenv("DATAVAULT_CHROME_DIR", str(root))
    before = (history.read_bytes(), history.stat().st_mtime_ns)
    _sync(client, app, "chrome_history")
    assert _count(app, "chrome_history") == 3
    assert (history.read_bytes(), history.stat().st_mtime_ns) == before      # never opened in place
    assert not list((app.state.config.CACHE_FOLDER / "datagatherer-snapshots").iterdir())   # copy removed
    # Read to its end just now: current, though its newest visit is years old (Chrome that is hardly used)
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "chrome_history")
    assert card["status"] == "actueel" and "niets na 14 jul 2019" in card["next_step"], card
    _sync(client, app, "chrome_history")
    assert _count(app, "chrome_history") == 3
    conn = sqlite3.connect(str(history))
    conn.execute("INSERT INTO urls (url, title) VALUES ('https://nieuw.be/', 'Nieuw')")
    conn.execute("INSERT INTO visits (url, visit_time) VALUES (3, ?)", (int((1563200000 + 11644473600) * 1e6),))
    conn.commit()
    conn.close()
    _sync(client, app, "chrome_history")
    assert _count(app, "chrome_history") == 4
    row = _rows(app, "SELECT ts_ms, details FROM activity_events WHERE url = 'https://voorbeeld.be/'")[0]
    assert row[0] == 1563091200000 and json.loads(row[1])["profiel"] == "Default"


def test_safari_history_from_the_mac_without_redirects_and_a_clear_no_access_message(client, app, monkeypatch, tmp_path):
    root = tmp_path / "Safari"
    db = fx.safari_history_db(root / "History.db", [
        (1563091200.5, "https://safari.be/", "Safari", None), (1563091300, "https://doorgestuurd.be/", "Doorgestuurd", 1)])
    monkeypatch.setenv("DATAVAULT_SAFARI_DIR", str(root))
    _sync(client, app, "safari_history")
    assert _count(app, "safari_history") == 1
    assert _rows(app, "SELECT ts_ms FROM activity_events WHERE source = 'safari_history'")[0][0] == 1563091200500
    os.chmod(db, 0)
    try:
        final = _sync(client, app, "safari_history", ok=False)
        assert "Volledige schijftoegang" in final["error"]
        card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "safari_history")
        assert "Volledige schijftoegang" in card["last_error"]
    finally:
        os.chmod(db, 0o644)


def test_a_picked_copy_of_safari_history_is_archived_and_read_read_only(client, app, tmp_path):
    copy = fx.safari_history_db(tmp_path / "History.db", [(1563091200, "https://kopie.be/", "Kopie", None)])
    result = _upload(client, app, "safari_history", copy)
    assert _count(app, "safari_history") == 1
    archived = Path(result["path"])
    assert archived.is_file() and not Path(str(archived) + "-wal").exists() and not Path(str(archived) + "-shm").exists()
    not_safari = fx.chrome_history_db(tmp_path / "other" / "History.db", [(1, "https://x.be/", "x")])
    _upload(client, app, "safari_history", not_safari, expect=(400,))


def test_netflix_both_csv_exports_without_trailers_and_twice_adds_nothing(client, app, tmp_path):
    full = tmp_path / "ViewingActivity.csv"
    full.write_text(fx.NETFLIX_FULL)
    simple = tmp_path / "NetflixViewingHistory.csv"
    simple.write_text(fx.NETFLIX_SIMPLE)
    _upload(client, app, "netflix", full)
    assert _count(app, "netflix") == 2          # the trailer is left out
    _upload(client, app, "netflix", simple)
    assert _count(app, "netflix") == 4
    zipped = fx.write_zip(tmp_path / "netflix-report.zip", {"CONTENT_INTERACTION/ViewingActivity.csv": fx.NETFLIX_FULL})
    _upload(client, app, "netflix", zipped)
    assert _count(app, "netflix") == 4
    row = _rows(app, "SELECT details, end_ms - ts_ms FROM activity_events WHERE title LIKE 'Serie: Seizoen 1: Aflevering 1'")[0]
    assert json.loads(row[0])["profiel"] == "Matthew" and row[1] == 45 * 60 * 1000


def test_snapchat_memories_and_chats(client, app, tmp_path):
    path = fx.snapchat_zip(tmp_path / "mydata~1563091200.zip")
    _upload(client, app, "snapchat", path)
    assert _count(app, "snapchat", "memory") == 2 and _count(app, "snapchat", "chat") == 2
    details = [json.loads(r[0]) for r in _rows(app, "SELECT details FROM activity_events WHERE kind = 'memory' ORDER BY ts_ms")]
    assert details[0]["lat"] == 51.05 and "lat" not in details[1]          # 0,0 is no position
    assert not _rows(app, "SELECT 1 FROM activity_events WHERE details LIKE '%snapchat.com%'")   # links not kept
    _upload(client, app, "snapchat", fx.write_zip(tmp_path / "again.zip", {
        "json/memories_history.json": json.loads(json.dumps({"Saved Media": [
            {"Date": "2019-07-14 10:00:00 UTC", "Media Type": "Image", "Location": "Latitude, Longitude: 51.05, 3.72"}]}))}))
    assert _count(app, "snapchat") == 4


@pytest.mark.parametrize("layout", ["old", "new"])
def test_tiktok_both_json_layouts(client, app, tmp_path, layout):
    path = tmp_path / "user_data.json"
    path.write_text(json.dumps(fx.tiktok_old() if layout == "old" else fx.tiktok_new()))
    _upload(client, app, "tiktok", path)
    expected = {"old": {"video_watch": 1, "like": 1, "search": 1}, "new": {"video_watch": 2, "search": 1}}[layout]
    assert dict(_rows(app, "SELECT kind, COUNT(*) FROM activity_events WHERE source = 'tiktok' GROUP BY kind")) == expected


def test_apple_music_play_activity_from_a_zip_in_a_zip(client, app, tmp_path):
    path = fx.apple_privacy_zip(tmp_path / "Gegevens van Apple.zip")
    _upload(client, app, "apple_privacy", path)
    rows = _rows(app, "SELECT title, details FROM activity_events WHERE source = 'apple_privacy'")
    assert len(rows) == 1 and rows[0][0] == "Nummer A – Artiest A"          # the 0 ms play is left out
    assert not _rows(app, "SELECT 1 FROM spotify_plays")                     # the Spotify tables stay Spotify only


def test_apple_health_steps_per_day_workouts_and_a_dutch_file_name(client, app, tmp_path):
    path = fx.health_zip(tmp_path / "export.zip")
    result = _upload(client, app, "apple_health", path)
    steps = _rows(app, "SELECT title, details FROM activity_events WHERE kind = 'steps'")
    # 14 July: the source with the most steps that day (iPhone 1500, Watch 1200); 16 July is the export day
    assert len(steps) == 1 and json.loads(steps[0][1]) == {"stappen": 1500, "dag": "2019-07-14", "bron": "iPhone"}
    workout = _rows(app, "SELECT title, details FROM activity_events WHERE kind = 'workout'")[0]
    assert workout[0] == "Hardlopen" and json.loads(workout[1]) == {"soort": "Hardlopen", "duur_min": 30.0,
                                                                     "afstand": "5.20 km", "kcal": 350}
    again = fx.health_zip(tmp_path / "second" / "export.zip", xml_name="apple_health_export/export.xml")
    _upload(client, app, "apple_health", again)
    assert _count(app, "apple_health") == 2


def test_apple_health_memory_does_not_grow_with_the_export():
    """Every Record is dropped from the root after reading: 200k records stay in a few MB."""
    import io
    import tracemalloc
    from backend.datagatherer.connectors import apple_health

    def export(n):
        rec = ('<Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" '
               'startDate="2024-06-%02d 10:00:00 +0200" endDate="2024-06-%02d 10:05:00 +0200" value="12">'
               '<MetadataEntry key="x" value="y"/></Record>\n')
        body = "".join(rec % (1 + i % 28, 1 + i % 28) for i in range(n))
        return io.BytesIO(('<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="nl_BE">\n'
                           '<ExportDate value="2024-07-01 10:00:00 +0200"/>\n' + body + "</HealthData>\n").encode())

    stream = export(200_000)
    tracemalloc.start()
    try:
        events = list(apple_health.parse(stream))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(events) == 28 and events[0]["details"]["stappen"] > 0
    assert peak < 8 * 1024 * 1024, peak
