"""
Unplugging the archive disk while the app runs, and plugging it back in (NIET_GEDAAN: 'een blijvende
test voor loskoppelen en terugplaatsen van de schijf').

The disk is simulated by renaming the vault folder: for the app that is exactly an unplugged T7
(VAULT_ROOT and the database on it are gone). One whole-app scenario, with the automatic index
passes on, like the real app:

- a job that is half-way waits for the archive ('waiting_for_vault') and is never 'done' with
  partial work;
- every route the page polls keeps answering (no 500), archive routes answer 503
  vault_unavailable, and nothing new can be started or written;
- nothing writes: the vault folder is not made again at its old place (a mkdir there would
  fill the Mac's own disk under /Volumes) and not one file in the unplugged vault changes;
- once the disk is back, the job finishes from where it was, the automatic index pass runs
  again, the archive itself is unchanged and everything the user had (a rating, a setting) is
  still there.

The browser half of this (the banner, the job dock, the toast and the reload) is in
tests/ui/smoke_webkit.py.
"""
import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from fixtures.vault_builder import STRAND

CACHE = ".datavault_cache"
GONE = "Archief niet bereikbaar (schijf losgekoppeld?)"


def _photos(folder: Path, n: int):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (64, 48), (i * 9 % 255, 90, 200 - i * 5 % 200)).save(folder / f"foto_{i:02d}.jpg")
    return sorted(folder.iterdir())


def _tree(root: Path, cache: bool):
    """{relative path: (size, mtime_ns)} of every file below root, with or without the app's cache."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        if not cache and rel_dir.parts[:1] == (CACHE,):
            dirnames[:] = []
            continue
        for name in filenames:
            st = os.lstat(Path(dirpath) / name)
            found[str(rel_dir / name)] = (st.st_size, st.st_mtime_ns)
    return found


def _until(check, timeout=20.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError(f"timed out after {timeout:.0f} s waiting for {what}")


class Gate:
    """Holds the 8th thumbnail of a job until the test has pulled the disk out."""

    def __init__(self, monkeypatch):
        from backend import thumbnail
        self.calls, self.unplug_now, self.go_on = [], threading.Event(), threading.Event()
        real = thumbnail.render_photo_thumbnail

        def gated(file_path, output_path):
            self.calls.append(file_path)
            if len(self.calls) == 8:
                self.unplug_now.set()
                self.go_on.wait(20)
            return real(file_path, output_path)

        # Before the app is made: the job type imports the function when it is built
        monkeypatch.setattr(thumbnail, "render_photo_thumbnail", gated)


@pytest.fixture
def gate(monkeypatch):
    return Gate(monkeypatch)


@pytest.fixture
def live_app(gate, config_file, monkeypatch):
    """The app with its automatic index passes on (conftest turns them off), polling fast."""
    from fastapi.testclient import TestClient
    from backend.main import create_app
    monkeypatch.setenv("DATAVAULT_AUTO_INDEX", "1")
    app = create_app(str(config_file))
    app.state.jobs.vault_poll_interval = 0.05
    app.state.auto_index.poll = 0.05
    with TestClient(app) as c:
        c.headers["X-DataVault-Token"] = c.get("/api/session").json()["token"]
        yield app, c


def _index_jobs(client):
    return [j for j in client.get("/api/jobs", params={"limit": 100}).json()["jobs"] if j["type"] == "index"]


def test_unplug_mid_job_and_plug_back_in(live_app, gate, vault):
    from backend import thumbnail
    app, client = live_app
    config = app.state.config
    jobs = app.state.jobs

    # The app's own first index pass (on start) is done before the disk goes
    first = _until(lambda: [j for j in _index_jobs(client) if j["status"] == "done"], what="the first index pass")

    # What the user has: a rating and a setting
    photo = (vault / STRAND).resolve()
    assert client.post("/api/shows/vote", json={"path": str(photo), "vote": "up"}).status_code == 200
    app.state.db.set_setting("unplug_test", {"blijft": True})

    # A thumbnail job that is half-way when the disk goes
    photos = _photos(vault / "Los", 24)
    r = client.post("/api/jobs", json={"type": "thumbnails", "params": {"folder": str(vault / "Los")}})
    assert r.status_code == 201, r.text
    job_id = r.json()["job"]["id"]
    assert gate.unplug_now.wait(20), jobs.get(job_id)

    archive_before = _tree(vault, cache=False)
    root = config.VAULT_ROOT
    away = root.with_name("vault_losgekoppeld")
    os.rename(root, away)
    try:
        gate.go_on.set()
        job = jobs.wait(job_id, statuses=("queued", "running"), timeout=20)
        assert job["status"] == "waiting_for_vault", job
        assert "schijf" in job["message"] and job["processed"] == 0 and job["total"] is None

        # Everything the page polls keeps answering; the archive routes say why they cannot
        health = client.get("/api/health").json()
        assert health["vault_available"] is False and health["vault_message"] == GONE
        listed = client.get("/api/jobs")
        assert listed.status_code == 200 and job_id in [j["id"] for j in listed.json()["jobs"]]
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "waiting_for_vault"
        library = client.get("/api/library/status")
        assert library.status_code < 500 or library.json()["error"]["code"] == "vault_unavailable", library.text
        for response in (client.get("/api/folders"),
                         client.post("/api/jobs", json={"type": "thumbnails", "params": {}}),
                         client.post("/api/shows/vote", json={"path": str(photo), "vote": "up"})):
            assert response.status_code == 503, response.text
            assert response.json()["error"]["code"] == "vault_unavailable"

        # Nothing writes: not at the old place (that would be the Mac's own disk) and not in the
        # unplugged vault (what the transition itself closed is settled before the first look)
        time.sleep(0.3)
        frozen = _tree(away, cache=True)
        for _ in range(20):                      # 20 polls of the job, the index and the page
            client.get("/api/health")
            client.get("/api/jobs")
            client.get("/api/library/status")
            time.sleep(0.05)
        assert not root.exists(), "something made the vault folder again while the disk was gone"
        assert _tree(away, cache=True) == frozen, "a file in the unplugged vault changed"
        assert jobs.get(job_id)["status"] == "waiting_for_vault"
        assert _index_jobs(client) == []        # no index pass starts without the disk
    finally:
        gate.go_on.set()
        if away.exists() and not root.exists():
            os.rename(away, root)

    # Back: the job goes on from where it was and is done, complete, without placeholders
    job = jobs.wait(job_id, statuses=("queued", "running", "waiting_for_vault"), timeout=30)
    assert job["status"] == "done", job
    assert job["summary"] == {"folder": str(root / "Los"), "files": 24, "made": 24,
                              "undecodable": 0, "errors": 0, "complete": True}
    assert all(thumbnail.get_thumbnail_cache_path(p, config.THUMB_CACHE_PATH).exists() for p in photos)
    assert not [p for p in photos if thumbnail.is_placeholder(thumbnail.get_thumbnail_cache_path(p, config.THUMB_CACHE_PATH))]

    # ... the page sees the archive again, and the automatic index pass runs once more
    assert client.get("/api/health").json()["vault_available"] is True
    assert client.get("/api/folders").status_code == 200
    again = _until(lambda: [j for j in _index_jobs(client)
                            if j["id"] != first[0]["id"] and j["status"] == "done"], timeout=30,
                   what="an index pass after the disk came back")
    assert again

    # The archive itself is untouched, and what the user had is still there
    assert _tree(root, cache=False) == archive_before
    with app.state.db._connect() as conn:
        assert conn.execute("SELECT rating, votes FROM file_ratings WHERE path = ?",
                            (str(photo),)).fetchone() == (1050, 1)
    assert app.state.db.get_setting("unplug_test") == {"blijft": True}
    assert client.post("/api/shows/vote", json={"path": str(photo), "vote": "up"}).json()["new_rating"] == 1100


def test_unplugged_while_idle_every_polled_route_answers_and_nothing_is_made(live_app, vault):
    """The page polls health, jobs and the library status while the disk is gone (every 3 s)."""
    app, client = live_app
    root = app.state.config.VAULT_ROOT
    _until(lambda: [j for j in _index_jobs(client) if j["status"] == "done"], what="the first index pass")
    away = root.with_name("vault_weg")
    os.rename(root, away)
    try:
        time.sleep(0.2)
        frozen = _tree(away, cache=True)
        for route in ("/api/health", "/api/jobs", "/api/library/status", "/api/folders",
                      "/api/duplicates/runs", "/api/keurer/history", "/api/albums"):
            for _ in range(3):
                r = client.get(route)
                assert r.status_code < 500 or r.json()["error"]["code"] == "vault_unavailable", (route, r.text)
        assert not root.exists()
        assert _tree(away, cache=True) == frozen
    finally:
        if away.exists() and not root.exists():
            os.rename(away, root)
    assert _until(lambda: client.get("/api/folders").status_code == 200, what="the archive back")
