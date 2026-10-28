"""
DataGatherer for Spotify and Last.fm: the export importer (Extended Streaming History and
Account data, URI-based keys, importing twice adds nothing), the merged plays and what
supersedes what (never deleted), the Spotify dashboard reading them (the same answers as
from the files), OAuth with PKCE and state, token refresh with rotation, the 6-month
expiry, 429 Retry-After, recently-played without trusting 'after', and Last.fm paging.

Spotify and Last.fm are fakes (fake_services.py over httpx.MockTransport); every export is
synthetic. Nothing here reaches the network or the real Keychain.
"""
import base64
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from backend.datagatherer import keychain, net, oauth, registry, spotify_store, state
from backend.datagatherer.base import GatherContext
from fake_services import (FakeLastFm, FakeSpotify, extended_play, legacy_play, track_item, write_export_zip)
from fixtures.vault_builder import SPOTIFY_FOLDER

T0 = 1_700_000_000          # 2023-11-14, a fixed moment for the synthetic plays


def _wait(app, job_id, timeout=30):
    return app.state.jobs.wait(job_id, timeout=timeout)


def _upload(client, app, path: Path, name=None):
    """Upload an export like the browser does; wait for its copy job and the import it starts.
    Returns the copy job's result ({created, raw_import_id, import_job, ...})."""
    r = client.post(f"/api/datagatherer/sources/spotify/upload?name={name or path.name}", content=path.read_bytes())
    assert r.status_code in (200, 201), r.text
    job = r.json()["job"]
    assert job["type"] == "datagatherer.watch"
    assert _wait(app, job["id"])["status"] == "done"
    result = app.state.jobs.result(job["id"])
    if result["import_job"]:
        result["import"] = _wait(app, result["import_job"])
    return result


def _plays(app, source=None, merged_only=False):
    sql = "SELECT source, ts_ms, track, ms_played, superseded, superseded_by FROM spotify_plays"
    sql += f" WHERE source = '{source}'" if source else ""
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        rows = conn.execute(sql + " ORDER BY ts_ms, id").fetchall()
    return [r for r in rows if not merged_only or r[4] == 0]


def _api_rows(app):
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        return conn.execute("SELECT ts_ms, track_uri, superseded, superseded_by FROM spotify_api_plays ORDER BY ts_ms").fetchall()


@pytest.fixture
def fake_spotify():
    fake = FakeSpotify()
    net.set_transport_factory(lambda: httpx.MockTransport(fake.handler))
    return fake


def _connected(authorized_at=None, expires_at=None, refresh="refresh-1", access="access-1"):
    now = time.time()
    keychain.set("spotify", "client_id", "client-abc")
    keychain.set("spotify", "token", json.dumps({
        "access_token": access, "refresh_token": refresh, "expires_at": expires_at or now + 3000,
        "scope": "user-read-recently-played", "authorized_at": authorized_at or now}))


def _sync(client, app):
    r = client.post("/api/datagatherer/sources/spotify/sync")
    assert r.status_code in (200, 201), r.text
    return _wait(app, r.json()["job"]["id"])


# ---------- the export importer ----------

def test_importing_the_same_export_twice_adds_nothing(client, app, tmp_path):
    plays = [extended_play(T0 + i * 300, f"Nummer {i % 7}", f"Band {i % 3}", uri=f"spotify:track:{i % 7}")
             for i in range(120)]
    plays.append(extended_play(T0 + 99_000, None, None, ms=1_200_000, episode_name="Aflevering",
                               episode_show_name="Podcast", spotify_episode_uri="spotify:episode:1"))
    zip_path = write_export_zip(tmp_path / "dl" / "my_spotify_data.zip", extended=plays,
                                account=[legacy_play(T0 + 30 * 86400, "Alleen account", "X")],
                                video=[extended_play(T0 + 500, "Videoclip", "Band 0", uri=None)])
    first = _upload(client, app, zip_path)
    assert first["created"] is True
    count = len(_plays(app))
    assert count == 123                                  # 120 + episode + legacy + video
    contents = {r[0] for r in _plays(app)}
    assert contents == {"export_extended", "export_account"}
    # Same bytes again: not stored again, nothing new
    again = _upload(client, app, zip_path, name="my_spotify_data (1).zip")
    assert again["created"] is False and again["raw_import_id"] == first["raw_import_id"]
    assert len(_plays(app)) == count
    # And read again from scratch (as if the zip changed its parser version): still nothing new
    connector = registry.get("spotify")
    raw = state.get_raw(app.state.db, first["raw_import_id"])
    stats = connector.import_raw(GatherContext(app.state.config, app.state.db, connector), raw)
    assert stats["added"] == 0 and stats["seen"] == 123
    # Privacy: IP address and user agent stay in the raw zip only
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        dump = "\n".join(conn.iterdump())
    assert "10.0.0.1" not in dump


def test_a_renamed_track_in_a_newer_export_is_still_one_play(client, app, tmp_path):
    old = write_export_zip(tmp_path / "a" / "my_spotify_data.zip",
                           extended=[extended_play(T0, "Oude titel", "Band", uri="spotify:track:x")])
    new = write_export_zip(tmp_path / "b" / "my_spotify_data.zip",
                           extended=[extended_play(T0, "Nieuwe titel (Remastered)", "Band", uri="spotify:track:x"),
                                     extended_play(T0 + 400, "Later", "Band", uri="spotify:track:y")])
    _upload(client, app, old)
    _upload(client, app, new)
    assert [r[2] for r in _plays(app)] == ["Oude titel", "Later"]


def test_a_broken_export_is_marked_failed_and_the_rest_still_reads(client, app, tmp_path):
    import zipfile
    bad = tmp_path / "bad" / "my_spotify_data.zip"
    bad.parent.mkdir()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("x/Streaming_History_Audio_2020.json", "[{\"ts\": ")
    done = _upload(client, app, bad)
    job = done["import"]
    assert job["status"] == "done" and job["summary"]["complete"] is False and job["summary"]["errors"] == 1
    raw = state.get_raw(app.state.db, done["raw_import_id"])
    assert raw["status"] == "failed" and "geen geldig JSON" in raw["error"]
    assert client.post("/api/datagatherer/sources/spotify/upload?name=notes.txt", content=b"hallo").status_code == 400
    assert client.post("/api/datagatherer/sources/spotify/upload?name=x.zip", content=b"").status_code == 400


def test_odd_numbers_skip_one_play_and_an_unexpected_error_fails_one_export(client, app, tmp_path, monkeypatch):
    """F-F5: Infinity, NaN or an absurd ms_played skips that play; a crash in one export marks it
    failed (never stuck in 'importing') and the job still finishes."""
    import zipfile
    odd = tmp_path / "odd" / "my_spotify_data.zip"
    odd.parent.mkdir()
    good = json.dumps(extended_play(T0, "Goed", "Band", uri="spotify:track:goed"))
    with zipfile.ZipFile(odd, "w") as zf:
        zf.writestr("x/Streaming_History_Audio_2023.json", "[" + ", ".join([
            '{"ts": "2023-11-14T22:13:20Z", "ms_played": Infinity, "master_metadata_track_name": "Oneindig"}',
            '{"ts": "2023-11-14T22:13:21Z", "ms_played": NaN, "master_metadata_track_name": "Geen getal"}',
            '{"ts": "2023-11-14T22:13:22Z", "ms_played": 1e300, "master_metadata_track_name": "Te lang"}',
            good]) + "]")
        zf.writestr("x/StreamingHistory_music_0.json",
                    '[{"endTime": "2023-11-14 22:13", "msPlayed": -Infinity, "trackName": "Min", "artistName": "B"}]')
    done = _upload(client, app, odd)
    assert done["import"]["status"] == "done" and done["import"]["summary"]["errors"] == 0
    assert [r[2] for r in _plays(app)] == ["Goed"]
    # An unexpected error while reading: that export is 'failed' with a Dutch reason
    from backend.datagatherer.connectors import spotify as spotify_connector
    other = write_export_zip(tmp_path / "other" / "my_spotify_data.zip",
                             extended=[extended_play(T0 + 99, "Later", "Band", uri="spotify:track:l")])

    def boom(*args, **kwargs):
        raise RuntimeError("stuk")

    monkeypatch.setattr(spotify_connector.Spotify, "import_raw", boom)
    done = _upload(client, app, other)
    job = done["import"]
    assert job["status"] == "done" and job["summary"]["errors"] == 1 and job["summary"]["complete"] is False
    raw = state.get_raw(app.state.db, done["raw_import_id"])
    assert raw["status"] == "failed" and "Onverwachte fout" in raw["error"]


def test_a_loose_history_file_can_be_uploaded_and_the_upload_runs_as_a_job(client, app, tmp_path):
    """F-F3 and F-F7: a bare StreamingHistory*.json or Streaming_History_Audio_*.json is recognised
    by its real name; copying runs as a job; the staged copy in the cache folder is removed."""
    from backend.datagatherer import jobs as gather_jobs
    staging = gather_jobs.upload_staging(app.state.config)
    account = tmp_path / "StreamingHistory_music_0.json"
    account.write_text(json.dumps([legacy_play(T0, "Los", "Band")]))
    extended = tmp_path / "Streaming_History_Audio_2023_1.json"
    extended.write_text(json.dumps([extended_play(T0 + 3600, "Ook los", "Band", uri="spotify:track:los")]))
    for path in (account, extended):
        done = _upload(client, app, path)
        assert done["created"] is True and done["import"]["status"] == "done"
        raw = state.get_raw(app.state.db, done["raw_import_id"])
        assert raw["origin"] == "upload" and Path(raw["path"]).name == path.name and raw["status"] == "imported"
    assert {r[2] for r in _plays(app)} >= {"Los", "Ook los"}
    assert list(staging.iterdir()) == []
    # Refused before any job: not an export, a control character in the name, an empty body
    r = client.post("/api/datagatherer/sources/spotify/upload?name=StreamingHistory_x.json", content=b'{"geen": "lijst"}')
    assert r.status_code == 400 and "geen export van Spotify" in r.json()["error"]["message"]
    assert client.post("/api/datagatherer/sources/spotify/upload?name=%00.zip", content=b"PK").status_code == 400
    assert client.post("/api/datagatherer/sources/spotify/upload?name=a%0Ab.json", content=b"[]").status_code == 400
    assert list(staging.iterdir()) == []
    # A staged upload can only be named, never a path elsewhere
    for params in ({"source": "spotify", "upload": "../x", "name": "a.zip"},
                   {"source": "spotify", "upload": "upload-x", "name": "../../etc/passwd"}):
        assert client.post("/api/jobs", json={"type": "datagatherer.watch", "params": params}).status_code == 400


# ---------- the dashboard reads the merged plays ----------

@pytest.fixture
def export(vault):
    """The test vault's Spotify folder, emptied: each test writes its own export files."""
    for f in (vault / "data").rglob("*.json"):
        f.unlink()
    return vault / SPOTIFY_FOLDER


def _write(folder, name, plays):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(plays))


def _old_style(ts, track, artist, ms=200000):
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)), "ms_played": ms,
            "master_metadata_track_name": track, "master_metadata_album_artist_name": artist, "platform": "ios"}


def test_the_dashboard_gives_the_same_answers_from_the_database_as_from_the_files(client, app, export):
    from backend.routes import spotify
    base = 1_680_000_000
    extended = [_old_style(base + i * 3600 * 7, f"Track {i % 9}", f"Artiest {i % 4}", ms=1000 + (i * 7919) % 239000)
                for i in range(400)]
    extended += [_old_style(base + 10, "Herhaal", "X"), _old_style(base + 250, "Herhaal", "X"),
                 _old_style(base + 500, "Herhaal", "X"), _old_style(base + 900, "Kort", "Y", ms=500)]
    legacy = [legacy_play(base + i * 3600 * 7, f"Track {i % 9}", f"Artiest {i % 4} feat. Z") for i in range(0, 400, 3)]
    legacy += [legacy_play(base - 86400 * 40 + i * 600, "Alleen legacy", "Oud") for i in range(5)]
    _write(export, "Streaming_History_Audio_2023.json", extended)
    _write(export.parent / "Spotify Account Data", "StreamingHistory_music_0.json", legacy)
    years = ["all"] + [str(y) for y in client.get("/api/spotify/years").json()["years"]]
    before = {y: client.get("/api/spotify/stats", params={"year": y}).json() for y in years}
    assert not spotify._from_database()

    r = client.post("/api/datagatherer/sources/spotify/import")
    assert _wait(app, r.json()["job"]["id"])["status"] == "done"
    assert spotify._from_database()
    after = {y: client.get("/api/spotify/stats", params={"year": y}).json() for y in years}
    for y in years:
        assert after[y] == before[y], y
    assert client.get("/api/stats/spotify-on-this-day").status_code == 200
    # Both folders were registered in place (nothing copied or moved)
    raws = state.list_raw(app.state.db, "spotify")
    assert sorted(r["origin"] for r in raws) == ["existing", "existing"]
    assert all(r["is_folder"] for r in raws)
    # Superseded Account-data plays are marked, never deleted
    legacy_rows = _plays(app, "export_account")
    assert len(legacy_rows) == len(legacy) and sum(r[4] for r in legacy_rows) == 134
    # Import again: nothing new, the same answers
    r = client.post("/api/datagatherer/sources/spotify/import")
    assert _wait(app, r.json()["job"]["id"])["summary"]["added"] == 0
    assert client.get("/api/spotify/stats", params={"year": "all"}).json() == before["all"]


def test_the_coverage_chip_says_since_when_listening_time_is_estimated(client, app, export, fake_spotify):
    from datetime import datetime
    _write(export, "Streaming_History_Audio_2023.json", [_old_style(T0 + i * 600, f"T{i}", "A") for i in range(10)])
    assert client.get("/api/spotify/coverage").json()["mode"] == "files"
    _wait(app, client.post("/api/datagatherer/sources/spotify/import").json()["job"]["id"])
    _connected()
    after_export = T0 + 20 * 86400
    fake_spotify.items = [track_item(after_export + i * 200, f"spotify:track:n{i}", f"Nieuw {i}", "B") for i in range(3)]
    assert _sync(client, app)["status"] == "done"
    cov = client.get("/api/spotify/coverage").json()
    assert cov["mode"] == "database" and cov["sources"] == {"export_extended": 10, "api": 3}
    # Estimated from the first API play on; the 20 days between the export and it are a hole (UX-2)
    assert cov["estimated_since"] == pytest.approx(after_export + 0.123)
    assert cov["last"] == pytest.approx(after_export + 400 + 0.123)
    assert [(h["from"], h["until"]) for h in cov["holes"]] == [
        (pytest.approx(T0 + 9 * 600), pytest.approx(after_export + 0.123))]
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "spotify")
    assert [s["estimated"] for s in card["coverage"]["segments"]] == [False, True]
    assert card["coverage"]["segments"][1]["from"] == pytest.approx(after_export + 0.123)
    assert card["data_until"] == pytest.approx(cov["last"])
    assert card["status"] == "gat" and card["status_label"] == "Gat van 19 dagen" and card["tone"] == "warning"
    assert card["primary"]["action"] == "request_export" and "ontbreken gegevens tussen" in card["next_step"]
    # API plays that follow the export closely leave no hole: the card is current again
    fake_spotify.items = [track_item(T0 + 9 * 600 + 86400 * k, f"spotify:track:b{k}", f"Brug {k}", "B")
                          for k in range(1, 20, 2)]
    assert _sync(client, app)["status"] == "done"
    cov = client.get("/api/spotify/coverage").json()
    assert cov["holes"] == [] and cov["estimated_since"] == pytest.approx(T0 + 9 * 600 + 86400 + 0.123)
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "spotify")
    assert card["status"] != "gat" and card["primary"]["action"] == "sync"
    # The chat (Pasha) never gets the API plays
    from backend.routes import spotify
    assert spotify.parse_spotify_data(for_assistant=True)["total_plays"] == 10
    assert spotify.parse_spotify_data()["total_plays"] == 23


def test_the_dashboard_keeps_the_files_until_every_export_file_is_read(client, app, tmp_path, vault, fake_spotify):
    """F-F1: an upload, a first sync or a stopped first import never shrinks the lifetime statistics."""
    from backend.routes import spotify
    total = client.get("/api/spotify/stats", params={"year": "all"}).json()["total_plays"]
    assert total == 300
    # One new play through an upload: read into the database, but the existing folder is not
    one = write_export_zip(tmp_path / "up" / "my_spotify_data.zip", account=[legacy_play(1_720_000_000, "Nieuw", "Band")])
    _upload(client, app, one)
    assert spotify_store.has_plays(app.state.db) and not spotify._from_database()
    cov = client.get("/api/spotify/coverage").json()
    assert cov["mode"] == "files" and cov["waiting_for_import"] is True and cov["plays"] == total
    assert client.get("/api/spotify/stats", params={"year": "all"}).json()["total_plays"] == total
    # A first sync of the API: the same
    _connected()
    fake_spotify.items = [track_item(1_730_000_000, "spotify:track:api", "Api", "Band")]
    assert _sync(client, app)["status"] == "done"
    assert client.get("/api/spotify/stats", params={"year": "all"}).json()["total_plays"] == total
    # Inlezen reads the existing folder: now the database has everything (and more)
    assert _wait(app, client.post("/api/datagatherer/sources/spotify/import").json()["job"]["id"])["status"] == "done"
    assert spotify._from_database()
    assert client.get("/api/spotify/coverage").json()["mode"] == "database"
    assert client.get("/api/spotify/stats", params={"year": "all"}).json()["total_plays"] == total + 2
    # An import that was stopped halfway ('importing') or a file changed after reading: the files again
    folder = state.raw_by_path(app.state.db, str(vault / SPOTIFY_FOLDER))
    state.update_raw(app.state.db, folder["id"], status="importing")
    assert not spotify._from_database()
    assert client.get("/api/spotify/stats", params={"year": "all"}).json()["total_plays"] == total
    state.update_raw(app.state.db, folder["id"], status="imported")
    assert spotify._from_database()
    later = time.time() + 60
    os.utime(vault / SPOTIFY_FOLDER / "Streaming_History_Audio_2023.json", (later, later))
    assert not spotify._from_database()


# ---------- superseding ----------

def test_api_and_lastfm_plays_are_superseded_by_a_later_export_never_deleted(client, app, tmp_path, fake_spotify):
    _connected()
    fake_spotify.items = [track_item(T0 + i * 200, f"spotify:track:{i}", f"Nummer {i}", "Band") for i in range(5)]
    assert _sync(client, app)["summary"]["added"] == 5
    merged = lambda: len(spotify_store.dashboard_plays(app.state.db))  # noqa: E731
    assert merged() == 5
    db = app.state.db
    spotify_store.insert_plays(db, [spotify_store.lastfm_row(
        {"artist": {"#text": "Band"}, "name": "Nummer 2", "date": {"uts": str(int(T0 + 400 - 180))}}, time.time()),
        spotify_store.lastfm_row({"artist": {"#text": "Ander"}, "name": "Alleen Last.fm",
                                  "date": {"uts": str(int(T0 + 5000))}}, time.time())], source_id="lastfm")
    spotify_store.reconcile(db)
    lastfm = _plays(app, "lastfm")
    assert [(r[2], r[4], (r[5] or "").split(":")[0]) for r in lastfm] == [
        ("Nummer 2", 1, "spotify_api_plays"), ("Alleen Last.fm", 0, "")]
    assert merged() == 6
    # The export arrives: it covers the API plays (and the Last.fm one in its period)
    export = write_export_zip(tmp_path / "e" / "my_spotify_data.zip", extended=[
        extended_play(T0 + i * 200 + 3, f"Nummer {i}", "Band", uri=f"spotify:track:{i}") for i in range(5)]
        + [extended_play(T0 + 6000, "Laatste", "Band", uri="spotify:track:z")])
    _upload(client, app, export)
    assert [r[2] for r in _api_rows(app)] == [1] * 5          # kept, marked
    assert all(r[3].split(":")[0] in ("export", "spotify_plays") for r in _api_rows(app))
    assert _plays(app, "lastfm")[1][4] == 1
    assert merged() == 6                                       # the export's 6 plays, nothing twice
    sources = {r[0] for r in _plays(app, merged_only=True)}
    assert sources == {"export_extended"}


def test_reconcile_is_the_same_whatever_the_order(client, app, tmp_path, fake_spotify):
    _connected()
    fake_spotify.items = [track_item(T0 + 50_000, "spotify:track:late", "Laat", "Band")]
    _sync(client, app)
    first = (_api_rows(app), _plays(app))
    spotify_store.reconcile(app.state.db)
    spotify_store.reconcile(app.state.db)
    assert (_api_rows(app), _plays(app)) == first


# ---------- OAuth: PKCE and state ----------

def test_pkce_verifier_and_s256_challenge():
    verifier = oauth.new_verifier()
    assert 43 <= len(verifier) <= 128 and all(c.isalnum() or c in "-_" for c in verifier)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert oauth.challenge(verifier) == expected
    assert oauth.new_verifier() != verifier
    assert oauth.redirect_uri(8000, "spotify") == "http://127.0.0.1:8000/api/datagatherer/spotify/callback"


def test_the_state_is_single_use_bound_to_its_source_and_expires(monkeypatch):
    state_value, _ = oauth.begin("spotify", "http://127.0.0.1:1/cb")
    assert oauth.finish("lastfm", state_value) is None                 # another source
    state_value, _ = oauth.begin("spotify", "http://127.0.0.1:1/cb")
    assert oauth.finish("spotify", state_value)[1] == "http://127.0.0.1:1/cb"
    assert oauth.finish("spotify", state_value) is None                # used up
    assert oauth.finish("spotify", None) is None
    old, _ = oauth.begin("spotify", "x")
    real = time.monotonic
    monkeypatch.setattr(oauth.time, "monotonic", lambda: real() + oauth.STATE_TTL + 1)
    assert oauth.finish("spotify", old) is None


def test_connecting_spotify_end_to_end(client, app, fake_spotify):
    r = client.put("/api/datagatherer/sources/spotify/secrets", json={"client_id": "client-abc"})
    assert r.status_code == 200
    detail = client.get("/api/datagatherer/sources/spotify").json()
    assert detail["setup"]["redirect_uri"].startswith("http://127.0.0.1") and "localhost" not in detail["setup"]["redirect_uri"]
    assert any(detail["setup"]["redirect_uri"] in step for step in detail["setup"]["steps"])
    assert any("User Management" in s for s in detail["setup"]["steps"]) and "Premium" in " ".join(detail["setup"]["steps"])
    connect = client.post("/api/datagatherer/sources/spotify/connect").json()
    url = urlsplit(connect["authorize_url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "accounts.spotify.com" and q["code_challenge_method"] == "S256"
    assert q["client_id"] == "client-abc" and q["scope"] == "user-read-recently-played"
    assert q["redirect_uri"] == connect["redirect_uri"] and len(q["state"]) >= 32
    # A forged state is refused and nothing is exchanged
    page = client.get("/api/datagatherer/spotify/callback", params={"state": "vals", "code": "good-code"})
    assert page.status_code == 200 and "Koppeling verlopen" in page.text
    assert not fake_spotify.requests
    fake_spotify.items = [track_item(T0, "spotify:track:1", "Eén", "Band")]
    page = client.get("/api/datagatherer/spotify/callback", params={"state": q["state"], "code": "good-code"})
    assert "Spotify is verbonden" in page.text
    exchange = parse_qs(fake_spotify.requests[0].content.decode())
    assert exchange["code_verifier"][0] and "client_secret" not in exchange
    assert oauth.challenge(exchange["code_verifier"][0]) == q["code_challenge"]
    token = json.loads(keychain.get("spotify", "token"))
    assert token["refresh_token"] == "refresh-1" and token["authorized_at"] <= time.time()
    job = next(j for j in app.state.jobs.list(5) if j["type"] == "datagatherer.sync.spotify")
    assert _wait(app, job["id"])["status"] == "done"
    assert len(_api_rows(app)) == 1
    # The state was used up: the same callback again does nothing
    assert "Koppeling verlopen" in client.get("/api/datagatherer/spotify/callback",
                                              params={"state": q["state"], "code": "good-code"}).text
    # Refused consent
    q2 = {k: v[0] for k, v in parse_qs(urlsplit(client.post("/api/datagatherer/sources/spotify/connect").json()["authorize_url"]).query).items()}
    assert "Niet verbonden" in client.get("/api/datagatherer/spotify/callback",
                                          params={"state": q2["state"], "error": "access_denied"}).text
    # Every request is in the network log, with its host
    hosts = {e["host"] for e in client.get("/api/datagatherer/network-log?source=spotify").json()["entries"]}
    assert hosts == {"accounts.spotify.com", "api.spotify.com"}


# ---------- tokens: refresh, rotation, expiry ----------

def test_a_rotated_refresh_token_is_always_stored(client, app, fake_spotify):
    _connected(expires_at=time.time() - 10)
    fake_spotify.rotate_to = "refresh-2"
    assert _sync(client, app)["status"] == "done"
    token = json.loads(keychain.get("spotify", "token"))
    assert token["refresh_token"] == "refresh-2" and token["access_token"] == fake_spotify.access_token
    # The old refresh token no longer works at the fake; the stored one does
    token["expires_at"] = 0
    keychain.set("spotify", "token", json.dumps(token))
    assert _sync(client, app)["status"] == "done"


def test_an_expired_access_token_is_refreshed_once_and_retried(client, app, fake_spotify):
    _connected()
    fake_spotify.expire_access = True
    fake_spotify.items = [track_item(T0, "spotify:track:1", "Eén", "Band")]
    assert _sync(client, app)["summary"]["added"] == 1


def test_invalid_grant_asks_to_connect_again(client, app, fake_spotify):
    _connected(expires_at=time.time() - 10)
    fake_spotify.token_error = "invalid_grant"
    job = _sync(client, app)
    assert job["status"] == "failed" and "opnieuw verbinden" in job["error"].lower()
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "spotify")
    assert card["status"] == "opnieuw_verbinden" and card["primary"]["action"] == "reconnect"
    assert card["primary"]["label"] == "Opnieuw verbinden"
    # The schedule does not keep trying
    from backend.datagatherer import daemon
    client.put("/api/datagatherer/sources/spotify/settings", json={"schedule_enabled": True})
    assert daemon.Daemon(app.state.config, app.state.db, lambda: app.state.jobs).schedule_tick(time.time() + 9999) == []


def test_a_warning_two_weeks_before_the_six_month_expiry(client, app):
    _connected(authorized_at=time.time() - 175 * 86400)
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "spotify")
    assert any("verloopt over 7 dagen" in n["text"] for n in card["notes"])
    _connected(authorized_at=time.time() - 30 * 86400)
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "spotify")
    assert not any("verloopt" in n["text"] for n in card["notes"])


def test_a_short_retry_after_is_waited_out_and_a_long_one_ends_the_run(client, app, fake_spotify):
    _connected()
    fake_spotify.retry_after = 1
    fake_spotify.items = [track_item(T0, "spotify:track:1", "Eén", "Band")]
    start = time.monotonic()
    assert _sync(client, app)["summary"]["added"] == 1
    assert time.monotonic() - start >= 0.9
    fake_spotify.retry_after = 600
    job = _sync(client, app)
    assert job["status"] == "failed" and "600 seconden" in job["error"]
    st = state.get_state(app.state.db, "spotify")
    assert st["retry_after"] > time.time() + 500


# ---------- recently played ----------

def test_recently_played_always_fetches_50_and_dedupes(client, app, fake_spotify):
    _connected()
    fake_spotify.items = [track_item(T0 + 1000 - i * 200, f"spotify:track:{i}", f"N{i}", "B") for i in range(3)]
    assert _sync(client, app)["summary"]["added"] == 3
    assert all(r.url.params.get("limit") == "50" and "after" not in r.url.params
               for r in fake_spotify.requests if r.url.host == "api.spotify.com")
    # A play from an offline phone arrives later, with an OLDER time than the newest stored
    fake_spotify.items.append(track_item(T0 - 5000, "spotify:track:offline", "Offline", "B"))
    job = _sync(client, app)
    assert job["summary"]["added"] == 1 and job["summary"]["seen"] == 4
    assert len(_api_rows(app)) == 4


def test_more_than_50_new_plays_flags_a_possible_gap(client, app, fake_spotify):
    _connected()
    fake_spotify.items = [track_item(T0, "spotify:track:old", "Oud", "B")]
    _sync(client, app)
    fake_spotify.items = [track_item(T0 + 100_000 - i * 200, f"spotify:track:{i}", f"N{i}", "B") for i in range(50)]
    job = _sync(client, app)
    assert job["summary"]["gap_possible"] is True
    card = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "spotify")
    assert any("meer dan 50" in n["text"] for n in card["notes"])


def test_a_403_says_what_to_check(client, app, fake_spotify):
    _connected()
    fake_spotify.recent_status = 403
    job = _sync(client, app)
    assert job["status"] == "failed" and "User Management" in job["error"]


# ---------- Last.fm ----------

@pytest.fixture
def fake_lastfm():
    scrobbles = [(T0 + i * 240, f"Band {i % 5}", f"Nummer {i}") for i in range(450)]
    fake = FakeLastFm(scrobbles)
    fake.now_playing = ("Band", "Nu bezig")
    net.set_transport_factory(lambda: httpx.MockTransport(fake.handler))
    return fake


def _lastfm_ready(client):
    keychain.set("lastfm", "api_key", "lastfm-key-123")
    client.put("/api/datagatherer/sources/lastfm/settings", json={"settings": {"username": "iemand"}})


def _lastfm_sync(client, app):
    r = client.post("/api/datagatherer/sources/lastfm/sync")
    assert r.status_code in (200, 201), r.text
    return _wait(app, r.json()["job"]["id"])


def test_lastfm_pages_through_the_history_then_only_asks_for_what_is_new(client, app, fake_lastfm):
    _lastfm_ready(client)
    job = _lastfm_sync(client, app)
    assert job["status"] == "done" and job["summary"]["added"] == 450
    assert len(fake_lastfm.requests) == 3                       # 200 + 200 + 50, 'now playing' skipped
    assert all(r.url.params["limit"] == "200" and "from" not in r.url.params for r in fake_lastfm.requests)
    rows = _plays(app, "lastfm")
    assert len(rows) == 450 and all(r[3] == spotify_store.LASTFM_DEFAULT_MS for r in rows)
    fake_lastfm.requests.clear()
    fake_lastfm.scrobbles.append((T0 + 450 * 240 + 600, "Band", "Nieuw"))
    job = _lastfm_sync(client, app)
    assert job["summary"]["added"] == 1 and len(fake_lastfm.requests) == 1
    assert int(fake_lastfm.requests[0].url.params["from"]) == T0 + 449 * 240 + 1
    # The API key never ends up in the network log (query strings are not logged)
    log = client.get("/api/datagatherer/network-log?source=lastfm").json()["entries"]
    assert log and "lastfm-key-123" not in json.dumps(log)


def test_lastfm_errors_are_explained(client, app, fake_lastfm):
    _lastfm_ready(client)
    fake_lastfm.error = 17
    job = _lastfm_sync(client, app)
    assert job["status"] == "failed" and "zichtbaar" in job["error"]
    fake_lastfm.error = 10
    assert "API-sleutel" in _lastfm_sync(client, app)["error"]
    assert state.get_state(app.state.db, "lastfm")["needs_reconnect"] == 1


def test_a_single_scrobble_object_is_read_too(client, app):
    fake = FakeLastFm([(T0, "Band", "Eenling")])
    net.set_transport_factory(lambda: httpx.MockTransport(fake.handler))
    _lastfm_ready(client)
    assert _lastfm_sync(client, app)["summary"]["added"] == 1
