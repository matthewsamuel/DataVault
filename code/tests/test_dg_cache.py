"""
PERF-2, PERF-3, PERF-6: what the DataGatherer tab, the Spotify chip and the Kaart day list
count over whole tables is cached until the data changes (state.data_signature), and a
change of the data (an import, a sync, a raw import that is read) shows at once.
"""
import time

from backend.datagatherer import activity_store, cards, location_store, registry, spotify_store, state
from backend.datagatherer.base import GatherContext

DAY = 86_400_000
T0 = 1_600_000_000_000


def _points(start, n):
    return [(start + i * 60_000, 52.0 + i * 1e-4, 5.0, 10.0) for i in range(n)]


def test_location_days_are_cached_until_points_are_added(app, monkeypatch):
    db = app.state.db
    location_store.insert(db, "google_timeline", None, "records", _points(T0, 5), [])
    calls = []
    real = location_store._days
    monkeypatch.setattr(location_store, "_days", lambda d: calls.append(1) or real(d))
    first = location_store.days(db)
    assert sum(d["count"] for d in first) == 5
    assert location_store.days(db) == first and len(calls) == 1
    # New points (an import bumps data_version in the same transaction): counted again
    location_store.insert(db, "google_timeline", None, "records", _points(T0 + 10 * DAY, 3), [])
    again = location_store.days(db)
    assert len(calls) == 2 and sum(d["count"] for d in again) == 8 and len(again) == len(first) + 1
    # The same points once more add nothing: no new count needed
    location_store.insert(db, "google_timeline", None, "records", _points(T0, 5), [])
    assert location_store.days(db) == again and len(calls) == 2


def test_activity_days_cache_keeps_filters_apart(app):
    db = app.state.db
    rows = [{"kind": "search", "ts_ms": T0 + i * DAY, "title": f"zoek {i}", "key": f"k{i}"} for i in range(3)]
    activity_store.insert(db, "google_takeout", None, rows)
    assert len(activity_store.days(db)) == 3
    assert activity_store.days(db, kinds=["youtube"]) == []
    activity_store.insert(db, "google_takeout", None, [{"kind": "youtube", "ts_ms": T0, "title": "film", "key": "y"}])
    assert activity_store.days(db, kinds=["youtube"])[0]["count"] == 1
    assert activity_store.days(db)[-1]["count"] == 2


def test_card_coverage_is_counted_once_per_data_version(app, monkeypatch):
    db, config = app.state.db, app.state.config
    connector = registry.get("google_timeline")
    gctx = GatherContext(config, db, connector)
    calls = []
    real = type(connector).coverage
    monkeypatch.setattr(type(connector), "coverage", lambda self, ctx: calls.append(1) or real(self, ctx))
    state.forget_cached()
    assert cards.coverage(connector, gctx).records == 0
    cards.card(config, db, connector)
    assert len(calls) == 1
    location_store.insert(db, "google_timeline", None, "records", _points(T0, 4), [])
    assert cards.coverage(connector, gctx).records == 4 and len(calls) == 2
    # A raw import that is registered or read changes the signature too
    before = state.data_signature(db)
    with db._connect() as conn:
        conn.execute("INSERT INTO dg_raw_imports (source_id, kind, path, origin, received_at, status) "
                     "VALUES ('google_timeline', 'file', '/x/Records.json', 'upload', ?, 'pending')", (time.time(),))
    assert state.data_signature(db) != before
    cards.coverage(connector, gctx)
    assert len(calls) == 3


def test_the_spotify_chip_follows_new_plays(app):
    db = app.state.db
    assert spotify_store.coverage(db)["plays"] == 0
    with db._connect() as conn:
        conn.execute("INSERT INTO spotify_plays (play_key, source, ts_ms, ms_played, content, track, artist, first_seen_at) "
                     "VALUES ('a', 'export_extended', ?, 60000, 'track', 'Nummer', 'Artiest', 1)", (T0,))
        state.bump_version(conn, spotify_store.SOURCE_ID)
    assert spotify_store.coverage(db)["plays"] == 1


def test_the_tab_warms_its_cache_in_the_background(app, monkeypatch):
    db, config = app.state.db, app.state.config
    state.forget_cached()
    cards.warm(config, db)
    calls = []
    for connector in registry.connectors():
        monkeypatch.setattr(type(connector), "coverage", lambda self, ctx: calls.append(self.id))
    for connector in registry.connectors():
        cards.coverage(connector, GatherContext(config, db, connector))
    assert calls == []
