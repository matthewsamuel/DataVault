"""
DataGatherer: the same website visit from Chrome's history on this Mac and from Google
Takeout (BrowserHistory.json and My Activity > Chrome) is shown once in Activiteit, with
both sources named. Both rows stay; the copy points at the shown row (duplicate_of,
migration 195). Two real visits of one URL a second apart stay two. In either import
order, and reading again changes nothing. Everything is synthetic (fake_exports.py).
"""
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest

import fake_exports as fx
from backend.datagatherer import activity_store

T = 1563091200            # 14 July 2019 08:00:00 UTC
DAY = datetime.fromtimestamp(T).strftime("%Y-%m-%d")


def _wait(app, job_id, timeout=60):
    return app.state.jobs.wait(job_id, timeout=timeout)


def _rows(app, sql, args=()):
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        return conn.execute(sql, args).fetchall()


def _iso(unix: float) -> str:
    return datetime.fromtimestamp(unix, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _chrome(tmp_path, monkeypatch):
    root = tmp_path / "Chrome"
    fx.chrome_history_db(root / "Default" / "History", [
        (T, "https://voorbeeld.be/", "Voorbeeld"),
        (T + 1.5, "https://voorbeeld.be/", "Voorbeeld"),            # a real reload 1.5 s later: its own visit
        (T + 3600.4, "https://ander.be/pad?x=1", "Ander"),
        (T + 7200, "https://alleen-hier.be/", "Alleen op de Mac"),
    ])
    monkeypatch.setenv("DATAVAULT_CHROME_DIR", str(root))


def _takeout(tmp_path):
    return fx.write_zip(tmp_path / "takeout-20240101T000000Z-001.zip", {
        "Takeout/Chrome/BrowserHistory.json": {"Browser History": [
            {"title": "Voorbeeld", "url": "https://voorbeeld.be/", "time_usec": T * 1000000},
            {"title": "Ander", "url": "https://ander.be/pad?x=1", "time_usec": (T + 3600) * 1000000},
            {"title": "Oud", "url": "https://alleen-takeout.be/", "time_usec": (T - 86400 * 400) * 1000000},
        ]},
        # My Activity > Chrome: whole seconds, Google's redirect, another host case, a fragment
        "Takeout/My Activity/Chrome/MyActivity.json": [
            {"header": "Chrome", "title": "Visited Voorbeeld", "time": _iso(T + 2),
             "titleUrl": "https://www.google.com/url?q=https://voorbeeld.be/&usg=AOvVaw"},
            {"header": "Chrome", "title": "Visited Ander", "time": _iso(T + 3601),
             "titleUrl": "https://ANDER.be/pad?x=1#boven"},
        ],
    })


def _sync_chrome(client, app):
    r = client.post("/api/datagatherer/sources/chrome_history/sync")
    assert r.status_code in (200, 201), r.text
    assert _wait(app, r.json()["job"]["id"])["status"] == "done"


def _upload_takeout(client, app, path):
    r = client.post(f"/api/datagatherer/sources/google_takeout/upload?name={path.name}", content=path.read_bytes())
    assert r.status_code in (200, 201), r.text
    result = app.state.jobs.result(_wait(app, r.json()["job"]["id"])["id"])
    if result["import_job"]:
        assert _wait(app, result["import_job"])["status"] == "done"


def _visits(client, **params):
    data = client.get("/api/activity/events", params={"day": DAY, "kind": "web_visit", **params}).json()
    return [e for e in data["events"] if e["kind"] == "web_visit"]


def test_url_matching_unwraps_redirects_and_ignores_case_and_fragments():
    n = activity_store.norm_url
    assert n("https://www.google.com/url?q=https://voorbeeld.be/&usg=x") == n("https://voorbeeld.be/") == n("HTTPS://Voorbeeld.BE")
    assert n("https://ander.be/pad?x=1#boven") == n("https://ander.be:443/pad?x=1")
    assert n("https://ander.be/pad?x=2") != n("https://ander.be/pad?x=1")
    assert n("https://ander.be/Pad") != n("https://ander.be/pad")              # paths keep their case
    assert n(None) is None


@pytest.mark.parametrize("order", ["chrome_first", "takeout_first"])
def test_a_visit_in_chrome_and_takeout_is_shown_once_with_both_sources(client, app, tmp_path, monkeypatch, order):
    _chrome(tmp_path, monkeypatch)
    takeout = _takeout(tmp_path)
    steps = [lambda: _sync_chrome(client, app), lambda: _upload_takeout(client, app, takeout)]
    for step in (steps if order == "chrome_first" else reversed(steps)):
        step()
    # Every row stays: 4 from Chrome, 3 + 2 from Takeout
    assert _rows(app, "SELECT source, COUNT(*) FROM activity_events WHERE kind = 'web_visit' GROUP BY source "
                      "ORDER BY source") == [("chrome_history", 4), ("google_takeout", 5)]
    shown = _visits(client)
    # voorbeeld (once, not three times), the reload, ander (once), alleen-hier; alleen-takeout is another day
    assert sorted(e["url"] for e in shown) == ["https://alleen-hier.be/", "https://ander.be/pad?x=1",
                                               "https://voorbeeld.be/", "https://voorbeeld.be/"]
    first = next(e for e in shown if e["url"] == "https://voorbeeld.be/" and e["ts"] == T)
    assert first["source"] == "chrome_history" and first["details"]["profiel"] == "Default"
    assert [a["source"] for a in first["also"]] == ["google_takeout"] and len(first["also"][0]["ids"]) == 2
    assert first["also"][0]["name"] == "Google Takeout" and first["also"][0]["icon"] == "fab fa-google"
    reload = next(e for e in shown if e["url"] == "https://voorbeeld.be/" and e["ts"] != T)
    assert reload["also"] == []
    ander = next(e for e in shown if "ander" in e["url"])
    assert ander["source"] == "chrome_history" and len(ander["also"][0]["ids"]) == 2
    # The day list and the kind counts count the visit once too
    day = next(d for d in client.get("/api/activity/days").json()["days"] if d["day"] == DAY)
    assert day["kinds"]["web_visit"] == 4
    facets = client.get("/api/activity/facets").json()
    assert next(k for k in facets["kinds"] if k["id"] == "web_visit")["count"] == 5      # + alleen-takeout
    # Only Takeout chosen: its visits are there, each once (a copy stands in for the row it copies)
    only = _visits(client, source="google_takeout")
    assert sorted(e["url"] for e in only) == ["https://ander.be/pad?x=1", "https://voorbeeld.be/"]
    assert {e["source"] for e in only} == {"google_takeout"}
    # Reading both again changes nothing
    links = _rows(app, "SELECT id, duplicate_of FROM activity_events ORDER BY id")
    _sync_chrome(client, app)
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        conn.execute("UPDATE dg_raw_imports SET status = 'pending'")
    r = client.post("/api/datagatherer/sources/google_takeout/import")
    assert _wait(app, r.json()["job"]["id"])["status"] == "done"
    assert _rows(app, "SELECT id, duplicate_of FROM activity_events ORDER BY id") == links


def test_takeouts_own_two_copies_are_one_without_chrome(client, app, tmp_path):
    _upload_takeout(client, app, _takeout(tmp_path))
    shown = _visits(client)
    # BrowserHistory.json and My Activity > Chrome have the same two visits: shown once, from BrowserHistory
    assert sorted(e["url"] for e in shown) == ["https://ander.be/pad?x=1", "https://voorbeeld.be/"]
    assert all(e["details"].get("bron") == "Takeout" for e in shown)


def test_two_visits_in_the_same_window_pair_one_to_one(app):
    db = app.state.db
    ev = lambda ts, src, n: activity_store.event("web_visit", ts, f"{src}-{n}", url="https://x.be/",
                                                 details={"bron": "Takeout"} if src == "t" else {"browser": "Chrome"})
    activity_store.insert(db, "chrome_history", None, [ev(T * 1000, "c", 1), ev(T * 1000 + 800, "c", 2)])
    activity_store.insert(db, "google_takeout", None, [ev(T * 1000 + 10, "t", 1), ev(T * 1000 + 790, "t", 2),
                                                       ev(T * 1000 + 6000, "t", 3)])
    assert activity_store.link_web_visits(db) == 2
    rows = dict(_rows(app, "SELECT event_key, duplicate_of FROM activity_events"))
    ids = dict(_rows(app, "SELECT event_key, id FROM activity_events"))
    assert rows["t-1"] == ids["c-1"] and rows["t-2"] == ids["c-2"] and rows["t-3"] is None
    assert activity_store.link_web_visits(db) == 0                         # idempotent
    # A range only touches clusters with a row inside it
    assert activity_store.link_web_visits(db, T * 1000 + 5000, T * 1000 + 7000) == 0


def test_a_visit_whose_other_copy_is_just_before_the_new_import_is_linked(client, app, tmp_path, monkeypatch):
    # My Activity rounds to whole seconds, so its copy is often a little earlier than Chrome's:
    # the cluster starts before the Chrome import's first visit, and is still linked
    _upload_takeout(client, app, fx.write_zip(tmp_path / "takeout-20240101T000000Z-001.zip", {
        "Takeout/My Activity/Chrome/MyActivity.json": [
            {"header": "Chrome", "title": "Visited Voorbeeld", "time": _iso(T), "titleUrl": "https://voorbeeld.be/"}]}))
    root = tmp_path / "Chrome"
    fx.chrome_history_db(root / "Default" / "History", [(T + 0.6, "https://voorbeeld.be/", "Voorbeeld")])
    monkeypatch.setenv("DATAVAULT_CHROME_DIR", str(root))
    _sync_chrome(client, app)
    shown = _visits(client)
    assert [(e["source"], [a["source"] for a in e["also"]]) for e in shown] == [("chrome_history", ["google_takeout"])]
    # And a range link touches a cluster that only ends inside it
    db = app.state.db
    with db._connect() as conn:
        conn.execute("UPDATE activity_events SET duplicate_of = NULL")
    assert activity_store.link_web_visits(db, T * 1000 + 500, T * 1000 + 700) == 1
    assert len(_visits(client)) == 1


def test_migration_195_links_what_is_stored_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from backend.database import Database
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 194, monkeypatch)
    now = time.time()
    events = [  # (source, kind, ts_ms, title, url, details, key)
        ("chrome_history", "web_visit", T * 1000, "Voorbeeld", "https://voorbeeld.be/", '{"browser":"Chrome"}', "c1"),
        ("google_takeout", "web_visit", T * 1000, "Voorbeeld", "https://voorbeeld.be/",
         '{"browser":"Chrome","bron":"Takeout"}', "t1"),
        ("google_takeout", "youtube_watch", T * 1000, "Kattenfilmpje", "https://www.youtube.com/watch?v=abc", None, "y1"),
        ("safari_history", "web_visit", T * 1000, "Voorbeeld", "https://voorbeeld.be/", '{"browser":"Safari"}', "s1"),
    ]
    with sqlite3.connect(str(db_path)) as conn:
        conn.executemany("INSERT INTO activity_events (source, kind, ts_ms, title, url, details, event_key, first_seen_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, 1.0)", events)
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{\"full\": true}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO elo_ratings VALUES ('/v/RAW/a.jpg', 1612.5, 80.0, 0.06, 1590, 7, 1.0)")
        conn.execute("INSERT INTO elo_skips (path_a, path_b, scope, created_at) VALUES ('/v/a.jpg', '/v/b.jpg', 'x', 1.0)")
        conn.execute("INSERT INTO msg_conversations (source, conv_key, updated_at) VALUES ('imessage', 'c', 1.0)")
        conn.execute("INSERT INTO msg_messages (source, conv_id, msg_key, origin, sent_at_ms, ts_precision, body, "
                     "first_seen_at) VALUES ('imessage', 1, 'G-1', 'imessage', 1, 'ms', 'Hoi', 1.0)")
        conn.execute("INSERT INTO dg_connectors (source_id, schedule_enabled, updated_at) VALUES ('spotify', 1, 1.0)")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name NOT IN ('schema_version', 'activity_events') "
                                             "AND name NOT LIKE 'msg_fts%'")]      # the search index's own pages
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
        events_before = conn.execute("SELECT * FROM activity_events ORDER BY id").fetchall()
    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert {195} <= {r[0] for r in conn.execute("SELECT version FROM schema_version")}
        for table, kept in before.items():
            if table in ("msg_messages",):        # later migrations may add columns: compare the old ones
                cols = len(kept[0]) if kept else 0
                assert [r[:cols] for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")] == kept, table
            else:
                assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == kept, table
        after = conn.execute("SELECT * FROM activity_events ORDER BY id").fetchall()
        assert [r[:-1] for r in after] == events_before                   # only the new column differs
        links = {r[0]: r[1] for r in conn.execute("SELECT event_key, duplicate_of FROM activity_events")}
        ids = {r[0]: r[1] for r in conn.execute("SELECT event_key, id FROM activity_events")}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert links == {"c1": None, "t1": ids["c1"], "y1": None, "s1": None}     # Safari is no copy of Chrome
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    db.close_all()
