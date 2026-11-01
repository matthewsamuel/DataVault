"""
Album covers for Home's 'Spotify op deze dag' (backend/datagatherer/spotify_covers.py):
fetched only inside the Spotify sync (through the network guard, logged), stored once per
album in the cache folder, served from there; Home itself never goes online. Which tracks:
the ones Home can pick in the coming days, today's closest to now first. A rate limit or an
error stops the covers for that sync but never fails it.

Spotify is a fake (httpx.MockTransport); nothing reaches the network or the real Keychain.
"""
import json
import sqlite3
import time
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from backend.datagatherer import keychain, net, spotify_covers
from fake_services import FakeSpotify, track_item

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


class CoverSpotify(FakeSpotify):
    """FakeSpotify plus the track, search and image endpoints."""

    def __init__(self):
        super().__init__()
        self.tracks = {}            # id -> album id
        self.search = {}            # (track, artist) -> (uri, album id)
        self.track_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "api.spotify.com" and url.path.startswith("/v1/tracks/"):
            self.requests.append(request)
            if self.track_status != 200:
                return httpx.Response(self.track_status, headers={"Retry-After": "60"})
            album = self.tracks.get(url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"album": _album(album)}) if album else httpx.Response(404)
        if url.host == "api.spotify.com" and url.path == "/v1/search":
            self.requests.append(request)
            q = url.params.get("q", "")
            items = [{"uri": uri, "name": t, "artists": [{"name": a}], "album": _album(album)}
                     for (t, a), (uri, album) in self.search.items() if f"track:{t} artist:{a}" == q]
            return httpx.Response(200, json={"tracks": {"items": items}})
        if url.host == "i.scdn.co":
            self.requests.append(request)
            assert "Authorization" not in request.headers   # the token only goes to api.spotify.com
            return httpx.Response(200, content=JPEG, headers={"Content-Type": "image/jpeg"})
        return super().handler(request)


def _album(album_id):
    return {"id": album_id, "images": [{"url": f"https://i.scdn.co/image/{album_id}640", "width": 640},
                                       {"url": f"https://i.scdn.co/image/{album_id}300", "width": 300},
                                       {"url": f"https://i.scdn.co/image/{album_id}64", "width": 64}]}


@pytest.fixture(autouse=True)
def no_export_files(vault):
    """Without export files the dashboard reads the plays from the database (API plays too)."""
    for f in (vault / "data").rglob("*.json"):
        f.unlink()


@pytest.fixture
def fake():
    f = CoverSpotify()
    net.set_transport_factory(lambda: httpx.MockTransport(f.handler))
    yield f
    net.set_transport_factory(None)


def _connect():
    keychain.set("spotify", "client_id", "client-abc")
    keychain.set("spotify", "token", json.dumps({
        "access_token": "access-1", "refresh_token": "refresh-1", "expires_at": time.time() + 3000,
        "scope": "user-read-recently-played", "authorized_at": time.time()}))


def _sync(client, app):
    r = client.post("/api/datagatherer/sources/spotify/sync")
    assert r.status_code in (200, 201), r.text
    job_id = r.json()["job"]["id"]
    job = app.state.jobs.wait(job_id, timeout=30)
    return {**job, "summary": app.state.jobs.result(job_id) or {}}


def _last_year_now() -> float:
    """This moment one year ago (the same month and day: Home shows it today)."""
    now = datetime.now()
    try:
        then = now.replace(year=now.year - 1)
    except ValueError:              # 29 February
        then = now.replace(year=now.year - 4)
    return then.timestamp()


def _rows(app):
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        return {r[0]: r[1:] for r in conn.execute("SELECT key, file, status, album_id FROM spotify_covers")}


def test_the_sync_stores_todays_cover_and_home_shows_it_without_going_online(client, app, fake):
    _connect()
    ts = _last_year_now()
    item = track_item(ts, "spotify:track:abc", "Milkshake", "Kelis")
    item["track"]["album"] = _album("alb1")
    fake.items = [item]
    job = _sync(client, app)
    assert job["status"] == "done" and job["summary"]["covers"] == 1
    # The image came from i.scdn.co in the 300 px size, without the token
    images = [r for r in fake.requests if r.url.host == "i.scdn.co"]
    assert [str(r.url) for r in images] == ["https://i.scdn.co/image/alb1300"]
    assert (spotify_covers.folder(app.state.config) / "alb1.jpg").read_bytes() == JPEG

    seen = len(fake.requests)
    track = client.get("/api/stats/spotify-on-this-day").json()["track"]
    assert track["track"] == "Milkshake" and track["cover"] == "/api/spotify/cover/alb1.jpg"
    r = client.get(track["cover"])
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert len(fake.requests) == seen            # Home and the cover route never go online

    # A second sync: the cover is there, no new requests for it
    assert _sync(client, app)["summary"]["covers"] == 0
    assert len([r for r in fake.requests if r.url.host == "i.scdn.co"]) == 1
    # Every request is in the network log
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        hosts = {h for (h,) in conn.execute("SELECT host FROM dg_network_log WHERE source_id = 'spotify'")}
    assert "i.scdn.co" in hosts


def test_tracks_by_uri_or_by_search_one_file_per_album_and_unknown_tracks_wait_a_month(client, app, fake):
    _connect()
    db, config = app.state.db, app.state.config
    with db._connect() as conn:
        conn.execute("INSERT INTO spotify_plays (play_key, source, ts_ms, content, track, artist, track_uri, "
                     "first_seen_at) VALUES ('k1', 'export_extended', 0, 'track', 'Met URI', 'A', "
                     "'spotify:track:t1', 0)")
    fake.tracks = {"t1": "albX"}
    fake.search = {("Zonder URI", "B"): ("spotify:track:t2", "albX")}
    now = datetime(2025, 6, 10, 21, 0)
    plays = [SimpleNamespace(local=datetime(2020, 6, 10, 21, 5), track="Met URI", artist="A"),
             SimpleNamespace(local=datetime(2021, 6, 10, 8, 0), track="Zonder URI", artist="B"),
             SimpleNamespace(local=datetime(2019, 6, 11, 12, 0), track="Onbekend", artist="C"),
             SimpleNamespace(local=datetime(2025, 6, 10, 21, 0), track="Dit jaar", artist="D"),
             SimpleNamespace(local=datetime(2019, 7, 30, 12, 0), track="Te ver weg", artist="E")]
    assert spotify_covers.wanted(plays, now) == [("Met URI", "A"), ("Zonder URI", "B"), ("Onbekend", "C")]

    ctx = SimpleNamespace(db=db, config=config, check=lambda: None)
    with net.client(db, _connector(), "user") as http:
        added = spotify_covers.fetch(ctx, http, lambda: {"Authorization": "Bearer access-1"}, plays, now)
    assert added == 1                              # two tracks, one album: one file
    rows = _rows(app)
    assert rows[spotify_covers.key_of("Met URI", "A")] == ("albX.jpg", "ok", "albX")
    assert rows[spotify_covers.key_of("Zonder URI", "B")] == ("albX.jpg", "ok", "albX")
    assert rows[spotify_covers.key_of("Onbekend", "C")][1] == "none"
    assert spotify_covers.cover_file(db, config, "met uri", "a") == "albX.jpg"   # compared like the merge does

    # The unknown track is not asked again within a month; the others are done
    before = len(fake.requests)
    with net.client(db, _connector(), "user") as http:
        assert spotify_covers.fetch(ctx, http, lambda: {}, plays, now) == 0
    assert len(fake.requests) == before


def test_a_rate_limit_stops_the_covers_but_not_the_sync(client, app, fake):
    _connect()
    ts = _last_year_now()
    with app.state.db._connect() as conn:
        conn.execute("INSERT INTO spotify_plays (play_key, source, ts_ms, ms_played, content, track, artist, "
                     "track_uri, first_seen_at) VALUES ('k1', 'export_extended', ?, 200000, 'track', 'Oud', 'A', "
                     "'spotify:track:t1', 0)", (int(ts * 1000),))
    fake.tracks = {"t1": "albY"}
    fake.track_status = 429
    job = _sync(client, app)
    assert job["status"] == "done" and job["summary"]["covers"] == 0
    assert client.get("/api/stats/spotify-on-this-day").json()["track"]["cover"] is None
    fake.track_status = 200
    assert _sync(client, app)["summary"]["covers"] == 1


@pytest.mark.parametrize("name", ["../datavault.db", "x.png", "a b.jpg", ".hidden.jpg", "nope.jpg"])
def test_the_cover_route_serves_only_stored_cover_files(client, name):
    assert client.get(f"/api/spotify/cover/{name}").status_code == 404


def _connector():
    from backend.datagatherer.connectors.spotify import CONNECTOR
    return CONNECTOR
