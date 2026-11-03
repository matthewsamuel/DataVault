"""Background jobs: start, progress, cancel, pause, resume, interrupted, waiting for the vault (step 2.3)."""
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from backend import jobs, scanner
from backend.database import Database
from helpers import kept_rows


def _start(client, job_type, **params):
    r = client.post("/api/jobs", json={"type": job_type, "params": params})
    assert r.status_code in (200, 201), r.text
    return r.json()["job"]


def _job(client, job_id):
    return client.get(f"/api/jobs/{job_id}").json()


def _wait(client, job_id, until=("done", "failed", "cancelled", "paused", "interrupted"), timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = _job(client, job_id)
        if job["status"] in until:
            return job
        time.sleep(0.03)
    raise AssertionError(f"job {job_id} still {_job(client, job_id)['status']}")


def _copies(folder, n=30, size=3000):
    """n byte-identical copies in folder (all one duplicate group)."""
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (folder / f"kopie_{i:03d}.jpg").write_bytes(b"k" * size)
    return folder


class Gate:
    """Lets the hashing threads through one at a time when asked (or all at once)."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.open = threading.Event()
        self.hashed = threading.Semaphore(0)
        real = scanner.hash_file_xxh3

        def gated(path):
            self.calls.append(str(path))
            self.hashed.release()
            self.open.wait(20)
            return real(path)

        monkeypatch.setattr(scanner, "hash_file_xxh3", gated)

    def wait_calls(self, n, timeout=10):
        for _ in range(n):
            assert self.hashed.acquire(timeout=timeout)


def _hash_rows(config, folder):
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        return conn.execute("SELECT COUNT(*) FROM file_hashes WHERE path LIKE ? AND algo = ?",
                            (f"{folder}/%", scanner.HASH_ALGO)).fetchone()[0]


# ==================== MIGRATION 8 ====================

def test_migration_8_adds_jobs_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 7, monkeypatch)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                  for t in ("keurer_decisions", "delete_runs", "delete_operations", "file_ratings",
                            "show_settings", "file_hashes", "sqlite_sequence")}

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 8
        for table, rows in before.items():
            assert kept_rows(conn, table, rows) == rows, table
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        assert {"id", "type", "params", "status", "processed", "total", "checkpoint", "summary"} <= columns
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'idx_delops_run'").fetchone()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v7-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_recent_keurer_decisions(1)[0]["dest_ino"] == 77
    db.close_all()


# ==================== LIFECYCLE ====================

def test_scan_job_runs_in_the_background_and_its_result_is_kept(client, vault):
    folder = _copies(vault / "Achtergrond", n=4)
    job = _start(client, "dup_scan", folder=str(folder))
    assert job["type"] == "dup_scan" and job["label"] == "Duplicaten zoeken" and job["target"] == "Achtergrond"
    assert job["params"] == {"folder": str(folder), "rule": "oldest", "prefer_folder": None}
    job = _wait(client, job["id"])
    assert job["status"] == "done" and job["processed"] == job["total"] == 4
    assert job["summary"] == {"folder": str(folder), "groups": 1, "duplicates_found": 3,
                              "total_files": 4, "complete": True, "errors": 0}
    body = client.get(f"/api/jobs/{job['id']}/result").json()
    [group] = body["result"]["groups"]
    assert len(group["copies"]) == 4 and body["job"]["id"] == job["id"]
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]][0] == job["id"]
    # The old progress endpoint follows the job
    progress = client.get("/api/raw_duplicates/progress").json()
    assert progress["status"] == "complete" and progress["job_id"] == job["id"]


def test_a_stored_scan_leaves_out_copies_that_were_moved_since(client, vault):
    folder = _copies(vault / "Later", n=3)
    job = _wait(client, _start(client, "dup_scan", folder=str(folder))["id"])
    keep, *move = [c["path"] for c in client.get(f"/api/jobs/{job['id']}/result").json()["result"]["groups"][0]["copies"]]
    r = client.post("/api/duplicates/move", json={"groups": [{"keep": keep, "move": move[:1]}]})
    assert len(r.json()["moved"]) == 1
    result = client.get(f"/api/jobs/{job['id']}/result").json()["result"]
    assert [c["path"] for c in result["groups"][0]["copies"]] == [keep, move[1]]
    assert result["duplicates_found"] == 1 and result["stale_removed"] == 1
    client.post("/api/duplicates/move", json={"groups": [{"keep": keep, "move": move[1:]}]})
    assert client.get(f"/api/jobs/{job['id']}/result").json()["result"]["groups"] == []


def test_job_requests_are_checked(client, vault, config):
    assert client.post("/api/jobs", json={"type": "rm -rf", "params": {}}).status_code == 400
    assert client.post("/api/jobs", json=["dup_scan"]).status_code == 400
    assert client.post("/api/jobs", json={"type": "dup_scan", "params": "RAW"}).status_code == 400
    assert client.post("/api/jobs", json={"type": "dup_scan", "params": {}}).status_code == 400
    assert client.post("/api/jobs", json={"type": "dup_scan", "params": {"folder": "/etc"}}).status_code == 403
    assert client.post("/api/jobs", json={"type": "thumbnails", "params": {"folder": "../.."}}).status_code == 403
    assert client.post("/api/jobs", json={"type": "dup_scan", "params": {"folder": str(config.CACHE_FOLDER)}}).status_code == 403
    config.DUPLICATES_FOLDER.mkdir(exist_ok=True)
    assert client.post("/api/jobs", json={"type": "dup_scan", "params": {"folder": str(config.DUPLICATES_FOLDER)}}).status_code == 400
    assert client.post("/api/jobs", json={"type": "dup_scan", "params": {"folder": "RAW", "rule": "x"}}).status_code == 400
    assert client.post("/api/jobs", json={"type": "hashing", "params": {"folder": "bestaat-niet"}}).status_code == 404
    assert client.get("/api/jobs/0").status_code == 400
    assert client.get(f"/api/jobs/{2**63}").status_code == 400
    assert client.get("/api/jobs/987").status_code == 404
    assert client.post("/api/jobs/987/resume").status_code == 404
    assert client.get("/api/jobs").json()["jobs"] == []

    done = _wait(client, _start(client, "hashing", folder="RAW")["id"])
    assert client.post(f"/api/jobs/{done['id']}/resume").status_code == 409
    assert client.post(f"/api/jobs/{done['id']}/cancel").status_code == 409
    assert client.get(f"/api/jobs/{done['id']}/result").status_code == 200


def test_the_same_job_twice_is_one_job_and_others_of_its_type_wait(client, vault, monkeypatch):
    gate = Gate(monkeypatch)
    a = _copies(vault / "A", n=6)
    b = _copies(vault / "B", n=2)
    first = _start(client, "dup_scan", folder=str(a))
    gate.wait_calls(1)
    again = client.post("/api/jobs", json={"type": "dup_scan", "params": {"folder": str(a)}})
    assert again.status_code == 200 and again.json()["created"] is False and again.json()["job"]["id"] == first["id"]
    second = _start(client, "dup_scan", folder=str(b))
    time.sleep(0.2)
    assert _job(client, second["id"])["status"] == "queued"
    assert _job(client, first["id"])["status"] == "running"
    gate.open.set()
    assert _wait(client, first["id"])["status"] == "done"
    assert _wait(client, second["id"])["status"] == "done"


def test_progress_is_written_to_the_database_only_now_and_then(client, vault, monkeypatch):
    folder = _copies(vault / "Veel", n=400, size=500)
    manager = client.app.state.jobs
    writes = []
    real = manager._db_write
    monkeypatch.setattr(manager, "_db_write", lambda row: (writes.append(row["status"]), real(row)))
    job = _wait(client, _start(client, "dup_scan", folder=str(folder))["id"])
    assert job["status"] == "done" and job["processed"] == 400
    assert len(writes) <= 8, writes  # start, a few progress writes, done: never one per file


# ==================== CANCEL, PAUSE, RESUME ====================

def test_cancel_stops_the_scan_and_keeps_what_was_hashed(client, vault, config, monkeypatch):
    gate = Gate(monkeypatch)
    folder = _copies(vault / "Stop", n=40)
    job = _start(client, "dup_scan", folder=str(folder))
    gate.wait_calls(4)
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["stopping"] is True
    gate.open.set()
    job = _wait(client, job["id"])
    assert job["status"] == "cancelled" and job["finished_at"]
    # Only the few running when it stopped were hashed (bounded submission), and they are cached
    assert len(gate.calls) < 40
    assert 0 < _hash_rows(config, folder) == len(gate.calls)
    assert client.get(f"/api/jobs/{job['id']}/result").status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 409
    assert client.get("/api/raw_duplicates/progress").json()["status"] == "cancelled"


def test_pause_then_resume_continues_from_the_hash_cache(client, vault, config, monkeypatch):
    gate = Gate(monkeypatch)
    folder = _copies(vault / "Pauze", n=40)
    job = _start(client, "dup_scan", folder=str(folder))
    gate.wait_calls(4)
    client.post(f"/api/jobs/{job['id']}/pause")
    gate.open.set()
    job = _wait(client, job["id"])
    assert job["status"] == "paused" and job["can_resume"] and not job["finished_at"]
    first_run = list(gate.calls)
    assert 0 < len(first_run) < 40

    job = client.post(f"/api/jobs/{job['id']}/resume").json()
    assert job["status"] in ("queued", "running")
    job = _wait(client, job["id"])
    assert job["status"] == "done" and job["checkpoint"]["runs"] == 2
    # The resumed run hashed only what the first one had not
    second_run = gate.calls[len(first_run):]
    assert sorted(first_run + second_run) == sorted(str(p) for p in folder.iterdir())
    [group] = client.get(f"/api/jobs/{job['id']}/result").json()["result"]["groups"]
    assert len(group["copies"]) == 40


def test_jobs_left_running_are_interrupted_at_start_and_can_be_resumed(client, vault, config_file):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    folder = _copies(vault / "Herstart", n=3)
    manager = client.app.state.jobs
    with manager.db._connect() as conn:
        conn.execute(
            "INSERT INTO jobs (type, params, status, processed, total, created_at, updated_at) "
            "VALUES ('dup_scan', ?, 'running', 1, 3, 1, 1)",
            (f'{{"folder": "{folder}", "rule": "oldest", "prefer_folder": null}}',),
        )
        job_id = conn.execute("SELECT MAX(id) FROM jobs").fetchone()[0]

    with TestClient(create_app(str(config_file))) as other:
        other.headers["X-DataVault-Token"] = other.get("/api/session").json()["token"]
        job = _job(other, job_id)
        assert job["status"] == "interrupted" and job["can_resume"] and "Onderbroken" in job["message"]
        other.post(f"/api/jobs/{job_id}/resume")
        job = _wait(other, job_id)
        assert job["status"] == "done" and job["summary"]["duplicates_found"] == 2


def test_server_shutdown_leaves_a_running_job_interrupted(app, vault, monkeypatch):
    from fastapi.testclient import TestClient
    gate = Gate(monkeypatch)
    folder = _copies(vault / "Afsluiten", n=20)
    with TestClient(app) as c:
        c.headers["X-DataVault-Token"] = c.get("/api/session").json()["token"]
        job = _start(c, "dup_scan", folder=str(folder))
        gate.wait_calls(2)
        threading.Timer(0.3, gate.open.set).start()
    # Lifespan shutdown stopped the worker at its next check
    with app.state.db._connect() as conn:
        status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()[0]
    assert status == "interrupted"


# ==================== VAULT UNPLUGGED ====================

def test_unplugged_vault_makes_the_job_wait_and_it_finishes_when_back(client, vault, config, monkeypatch):
    client.app.state.jobs.vault_poll_interval = 0.05
    gate = Gate(monkeypatch)
    folder = _copies(vault / "Los", n=30)
    job = _start(client, "dup_scan", folder=str(folder))
    gate.wait_calls(3)
    away = vault.with_name("vault_los")
    os.rename(vault, away)
    try:
        gate.open.set()
        job = _wait(client, job["id"], until=("waiting_for_vault", "done", "failed"))
        assert job["status"] == "waiting_for_vault", job
        time.sleep(0.3)
        job = _job(client, job["id"])
        assert job["status"] == "waiting_for_vault" and "schijf" in job["message"]
        assert job["processed"] == 0 and job["total"] is None  # never shown as (nearly) finished
        # The dock still lists it while the database (on the vault disk) is gone
        assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [job["id"]]
    finally:
        os.rename(away, vault)
    job = _wait(client, job["id"])
    assert job["status"] == "done"
    result = client.get(f"/api/jobs/{job['id']}/result").json()["result"]
    assert result["complete"] is True and result["errors"] == 0
    assert len(result["groups"][0]["copies"]) == 30


def test_a_cancelled_wait_for_the_vault_stops_at_once(client, vault, monkeypatch):
    client.app.state.jobs.vault_poll_interval = 30
    gate = Gate(monkeypatch)
    folder = _copies(vault / "Wacht", n=10)
    job = _start(client, "dup_scan", folder=str(folder))
    gate.wait_calls(1)
    away = vault.with_name("vault_wacht")
    os.rename(vault, away)
    try:
        gate.open.set()
        assert _wait(client, job["id"], until=("waiting_for_vault",))["status"] == "waiting_for_vault"
        client.post(f"/api/jobs/{job['id']}/cancel")
        assert _wait(client, job["id"], until=("cancelled",), timeout=3)["status"] == "cancelled"
    finally:
        os.rename(away, vault)
    # The final state could not be written while the disk was gone; it is once it is back
    assert _job(client, job["id"])["status"] == "cancelled"
    with sqlite3.connect(str(vault / ".datavault_cache" / "datavault.db")) as conn:
        assert conn.execute("SELECT status FROM jobs WHERE id = ?", (job["id"],)).fetchone()[0] == "cancelled"


# ==================== HASHING AND THUMBNAILS ====================

def test_hashing_job_fills_the_cache_so_a_scan_reads_nothing_again(client, vault, monkeypatch):
    folder = vault / "Index"
    _copies(folder / "a", n=2, size=700)
    (folder / "uniek.jpg").write_bytes(b"u" * 999)
    job = _wait(client, _start(client, "hashing", folder=str(folder))["id"])
    assert job["status"] == "done" and job["summary"] == {
        "folder": str(folder), "files": 3, "hashed": 3, "errors": 0, "complete": True}
    gate = Gate(monkeypatch)
    gate.open.set()
    scan = _wait(client, _start(client, "dup_scan", folder=str(folder))["id"])
    assert scan["summary"]["duplicates_found"] == 1 and gate.calls == []


def test_thumbnail_job_makes_missing_thumbnails_once(client, vault, config):
    folder = vault / "Duimnagels"
    folder.mkdir()
    for i in range(5):
        Image.new("RGB", (64, 48), (i * 40, 90, 120)).save(folder / f"foto_{i}.jpg")
    (folder / "lied.mp3").write_bytes(b"ID3")
    job = _wait(client, _start(client, "thumbnails", folder=str(folder))["id"])
    assert job["status"] == "done" and job["summary"] == {
        "folder": str(folder), "files": 5, "made": 5, "undecodable": 0, "errors": 0, "complete": True}
    thumbs = sorted(p.name for p in config.THUMB_CACHE_PATH.iterdir())
    assert len(thumbs) == 5 and not any(name.startswith(".") for name in thumbs)  # no temporary files left
    again = _wait(client, _start(client, "thumbnails", folder=str(folder))["id"])
    assert again["summary"]["made"] == 0
    assert client.get(f"/api/jobs/{again['id']}/result").json()["result"]["cached"] == 5
    # The viewer gets the same thumbnail the job made
    r = client.get("/api/thumb", params={"path": str(folder / "foto_0.jpg")})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_thumbnail_job_for_the_whole_vault_by_default(client, vault):
    job = _wait(client, _start(client, "thumbnails")["id"], timeout=60)
    assert job["status"] == "done" and job["params"] == {"folder": str(vault.resolve())}
    assert job["target"] == "hele archief" and job["message"].startswith("Klaar: ")
    assert job["summary"]["files"] > 10


def _photos(folder, n):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (64, 48), (i * 9 % 255, 90, 200 - i * 5 % 200)).save(folder / f"foto_{i:02d}.jpg")
    return sorted(folder.iterdir())


def _placeholders(paths, thumb_dir):
    from backend import thumbnail
    return [p for p in paths
            if thumbnail.is_placeholder(thumbnail.get_thumbnail_cache_path(p, thumb_dir))]


def test_an_unreadable_photo_is_an_error_and_never_gets_a_placeholder(client, vault, config):
    from backend import thumbnail
    folder = vault / "Kapot"
    good = _photos(folder, 3)
    (folder / "ruis.jpg").write_bytes(os.urandom(3000))
    locked = folder / "op_slot.jpg"
    Image.new("RGB", (30, 30), (1, 2, 3)).save(locked)
    os.chmod(locked, 0)
    # A placeholder an earlier run (or a request during an outage) left for a real photo
    stale = thumbnail.get_thumbnail_cache_path(good[0], config.THUMB_CACHE_PATH)
    config.ensure_cache_dirs()
    assert thumbnail.create_placeholder_thumbnail(stale)
    try:
        job = _wait(client, _start(client, "thumbnails", folder=str(folder))["id"])
    finally:
        os.chmod(locked, 0o644)
    # ruis.jpg reads fine but is no picture: counted apart, not as a failure (R23-2)
    assert job["status"] == "done" and job["summary"] == {
        "folder": str(folder), "files": 5, "made": 3, "undecodable": 1, "errors": 1, "complete": False}
    assert "1 mislukt" in job["message"] and "1 onleesbaar" in job["message"]
    result = client.get(f"/api/jobs/{job['id']}/result").json()["result"]
    assert [Path(e["path"]).name for e in result["error_samples"]] == ["op_slot.jpg"]
    assert [Path(e["path"]).name for e in result["undecodable_samples"]] == ["ruis.jpg"]
    assert _placeholders(good, config.THUMB_CACHE_PATH) == []
    for bad in (folder / "ruis.jpg", locked):
        assert not thumbnail.get_thumbnail_cache_path(bad, config.THUMB_CACHE_PATH).exists()


def test_the_viewer_never_caches_a_placeholder_for_a_photo_it_cannot_read(client, vault, config):
    from backend import thumbnail
    photo = _photos(vault / "Kijker", 1)[0]
    os.chmod(photo, 0)
    try:
        r = client.get("/api/thumb", params={"path": str(photo)})
    finally:
        os.chmod(photo, 0o644)
    assert r.status_code == 200 and r.content == thumbnail._placeholder_bytes(False)
    assert not thumbnail.get_thumbnail_cache_path(photo, config.THUMB_CACHE_PATH).exists()
    r = client.get("/api/thumb", params={"path": str(photo)})
    assert r.status_code == 200 and r.content != thumbnail._placeholder_bytes(False)


def test_thumbnail_job_with_the_cache_outside_the_vault_waits_out_an_unplug(tmp_path, vault, monkeypatch):
    """R23-1: an unplugged vault mid-job never ends 'done' with placeholders for real photos."""
    import json
    from fastapi.testclient import TestClient
    from backend import thumbnail
    from backend.main import create_app
    from fixtures.vault_builder import write_config
    config_path = write_config(vault, tmp_path / "buiten.json")
    data = json.loads(config_path.read_text())
    data["cache_folder"] = str(tmp_path / "cache_buiten")  # README: /Volumes/T7/.datavault_cache
    config_path.write_text(json.dumps(data))
    photos = _photos(vault / "Los", 24)

    calls = []
    unplug_now = threading.Event()
    go_on = threading.Event()
    real = thumbnail.render_photo_thumbnail

    def gated(file_path, output_path):
        calls.append(file_path)
        if len(calls) == 8:
            unplug_now.set()
            go_on.wait(20)
        return real(file_path, output_path)

    monkeypatch.setattr(thumbnail, "render_photo_thumbnail", gated)
    app = create_app(str(config_path))
    with TestClient(app) as c:
        c.headers["X-DataVault-Token"] = c.get("/api/session").json()["token"]
        app.state.jobs.vault_poll_interval = 0.05
        config = app.state.config
        job = _start(c, "thumbnails", folder=str(vault / "Los"))
        assert unplug_now.wait(20)
        root = config.VAULT_ROOT
        away = root.with_name("vault_weg")
        os.rename(root, away)
        try:
            go_on.set()
            job = _wait(c, job["id"], until=("waiting_for_vault", "done", "failed"))
            assert job["status"] == "waiting_for_vault", job
        finally:
            os.rename(away, root)
        job = _wait(c, job["id"])
        assert job["status"] == "done", job
        # Counted over both runs: what the first run made is not 'already there'
        assert job["summary"] == {"folder": str(root / "Los"), "files": 24, "made": 24,
                                  "undecodable": 0, "errors": 0, "complete": True}
        assert job["message"] == "Klaar: 24 nieuw, 0 waren er al"
        assert _placeholders(photos, config.THUMB_CACHE_PATH) == []
        assert all(thumbnail.get_thumbnail_cache_path(p, config.THUMB_CACHE_PATH).exists() for p in photos)


def test_a_resumed_hashing_job_counts_what_its_first_run_hashed(client, vault, config, monkeypatch):
    gate = Gate(monkeypatch)
    folder = vault / "Tellen"
    folder.mkdir()
    for i in range(40):
        (folder / f"f{i:02d}.jpg").write_bytes(bytes([i]) * 2000)
    job = _start(client, "hashing", folder=str(folder))
    gate.wait_calls(4)
    client.post(f"/api/jobs/{job['id']}/pause")
    gate.open.set()
    job = _wait(client, job["id"])
    assert job["status"] == "paused"
    first = _hash_rows(config, folder)
    assert 0 < first < 40
    client.post(f"/api/jobs/{job['id']}/resume")
    job = _wait(client, job["id"])
    assert job["status"] == "done" and job["summary"]["hashed"] == 40
    assert client.get(f"/api/jobs/{job['id']}/result").json()["result"]["from_cache"] == 0


def test_a_failed_thumbnail_write_leaves_no_partial_file(tmp_path, monkeypatch):
    from backend import thumbnail
    src = tmp_path / "foto.jpg"
    Image.new("RGB", (40, 40), (200, 10, 10)).save(src)
    cache = tmp_path / "thumbs"
    out = thumbnail.get_thumbnail_cache_path(src, cache)

    def broken_save(self, fp, *args, **kwargs):
        with open(fp, "wb") as f:
            f.write(b"\xff\xd8 half")
        raise OSError("disk full")

    monkeypatch.setattr(Image.Image, "save", broken_save)
    assert thumbnail.generate_photo_thumbnail(src, out) is False
    assert not out.exists() and list(cache.iterdir()) == []


# ==================== BOUNDED SUBMISSION ====================

def test_bounded_map_never_holds_more_than_its_window():
    submitted = 0
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def items():
        nonlocal submitted
        for i in range(500):
            submitted += 1
            yield i

    def work(i):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.0005)
        with lock:
            in_flight -= 1
        if i == 7:
            raise ValueError("zeven")
        return i * 2

    consumed = 0
    results = {}
    for item, value, error in jobs.bounded_map(work, items(), workers=4, window=16):
        consumed += 1
        assert submitted - consumed <= 16
        results[item] = error if error else value
    assert peak <= 4 and len(results) == 500
    assert isinstance(results[7], ValueError) and results[8] == 16

    # Stopping early: nothing more is started
    gen = jobs.bounded_map(work, range(10_000), workers=2, window=4)
    next(gen)
    gen.close()


# ==================== RESUME WHILE A WORKER IS STILL ENDING ====================

def _test_manager(client, run):
    manager = jobs.JobManager(client.app.state.db, client.app.state.config,
                              [jobs.JobType("proef", "Proef", prepare=lambda p: p, run=run)])
    return manager


def test_resume_while_the_paused_worker_is_still_cleaning_up_keeps_the_new_run(client, monkeypatch):
    """R23-2: the old worker's cleanup must not drop the run resume() just started."""
    release = threading.Event()

    def run(ctx):
        while not release.is_set():
            ctx.check()
            time.sleep(0.01)
        return {"klaar": True}

    manager = _test_manager(client, run)
    written, resumed = threading.Event(), threading.Event()
    real = manager._finish_stopped

    def slow_finish(live):
        real(live)              # PAUSED is in the database...
        written.set()
        resumed.wait(5)         # ...but the worker has not left _live yet

    monkeypatch.setattr(manager, "_finish_stopped", slow_finish)
    job, _ = manager.start("proef", {})
    assert manager.wait(job["id"], statuses=(jobs.QUEUED,), timeout=5)["status"] == "running"
    old = manager._live[job["id"]]
    manager.pause(job["id"])
    assert written.wait(5)
    assert manager.resume(job["id"])["status"] in (jobs.QUEUED, jobs.RUNNING)
    resumed.set()
    old.thread.join(5) if old.thread else None
    time.sleep(0.1)
    new = manager._live.get(job["id"])
    assert new is not None and new is not old and new.thread is not None
    assert manager.get(job["id"])["status"] == jobs.RUNNING
    # Still one run of this type: a second job of it waits
    other, _ = manager.start("proef", {"ander": 1})
    time.sleep(0.1)
    assert manager.get(other["id"])["status"] == jobs.QUEUED
    assert manager.cancel(job["id"])["stopping"] is True
    assert manager.wait(job["id"], statuses=(jobs.QUEUED, jobs.RUNNING), timeout=5)["status"] == jobs.CANCELLED
    release.set()
    assert manager.wait(other["id"], statuses=(jobs.QUEUED, jobs.RUNNING), timeout=5)["status"] == jobs.DONE
    manager.shutdown()


def test_cancel_while_the_paused_worker_is_still_cleaning_up_closes_the_job(client, monkeypatch):
    def run(ctx):
        while True:
            ctx.check()
            time.sleep(0.01)

    manager = _test_manager(client, run)
    written, go_on = threading.Event(), threading.Event()
    real = manager._finish_stopped
    monkeypatch.setattr(manager, "_finish_stopped", lambda live: (real(live), written.set(), go_on.wait(5)))
    job, _ = manager.start("proef", {})
    manager.wait(job["id"], statuses=(jobs.QUEUED,), timeout=5)
    manager.pause(job["id"])
    assert written.wait(5)
    assert manager.cancel(job["id"])["status"] == jobs.CANCELLED
    go_on.set()
    time.sleep(0.2)
    assert manager.get(job["id"])["status"] == jobs.CANCELLED and job["id"] not in manager._live
    manager.shutdown()


def test_a_job_whose_result_was_pruned_says_so(client, vault, monkeypatch):
    """R23-3: the dock offers 'Bekijk' only while the result is still stored."""
    manager = client.app.state.jobs
    monkeypatch.setattr(manager.types["dup_scan"], "results_to_keep", 2)
    folder = _copies(vault / "Snoei", n=2)
    ids = [_wait(client, _start(client, "dup_scan", folder=str(folder))["id"])["id"] for _ in range(3)]
    listed = {j["id"]: j for j in client.get("/api/jobs?type=dup_scan").json()["jobs"]}
    assert [listed[i]["has_result"] for i in ids] == [False, True, True]
    assert all(listed[i]["status"] == "done" for i in ids)
    assert _job(client, ids[0])["has_result"] is False and _job(client, ids[2])["has_result"] is True
    assert client.get(f"/api/jobs/{ids[0]}/result").status_code == 410
    assert client.get(f"/api/jobs/{ids[2]}/result").status_code == 200
    running = _start(client, "hashing", folder=str(folder))
    assert running["has_result"] is False


# ==================== THUMBNAILS: RENAMED VAULT, UNDECODABLE FILES ====================

class ThumbGate:
    """Holds a photo render after decoding, just before its thumbnail is written."""

    def __init__(self, monkeypatch, at):
        from PIL import ImageOps
        self.calls = 0
        self.lock = threading.Lock()
        self.reached = threading.Event()
        self.open = threading.Event()
        real = ImageOps.exif_transpose

        def gated(img, *args, **kwargs):
            with self.lock:
                self.calls += 1
                hold = self.calls >= at
            if hold:
                self.reached.set()
                self.open.wait(20)
            return real(img, *args, **kwargs)

        monkeypatch.setattr(ImageOps, "exif_transpose", gated)


def test_a_thumbnail_write_never_recreates_a_renamed_vault(config):
    """R23-1: only the thumbnail folder itself may be made, never the folders above it."""
    from backend import thumbnail
    config.ensure_cache_dirs()
    root = config.VAULT_ROOT
    out = config.THUMB_CACHE_PATH / "abc.jpg"
    away = root.with_name("vault_weg")
    os.rename(root, away)
    try:
        with pytest.raises(FileNotFoundError):
            with thumbnail._atomic_output(out) as tmp:
                tmp.write_bytes(b"x")
        assert thumbnail.create_placeholder_thumbnail(out) is False
        assert config.ensure_cache_dirs() is False
        assert not root.exists()
    finally:
        os.rename(away, root)
    # With the vault there, a missing thumbnail folder is made again
    for p in config.THUMB_CACHE_PATH.iterdir():
        p.unlink()
    config.THUMB_CACHE_PATH.rmdir()
    assert thumbnail.create_placeholder_thumbnail(out) is True and out.is_file()


def test_thumbnail_job_with_the_cache_inside_a_renamed_vault_waits_and_never_recreates_it(
        tmp_path, vault, monkeypatch):
    """R23-1: renders in flight when the vault is renamed must not build an empty vault at the old path."""
    from fastapi.testclient import TestClient
    from backend import thumbnail
    from backend.main import create_app
    from fixtures.vault_builder import write_config
    photos = _photos(vault / "Los", 24)
    gate = ThumbGate(monkeypatch, at=6)
    app = create_app(str(write_config(vault, tmp_path / "binnen.json")))
    with TestClient(app) as c:
        c.headers["X-DataVault-Token"] = c.get("/api/session").json()["token"]
        app.state.jobs.vault_poll_interval = 0.05
        config = app.state.config
        job = _start(c, "thumbnails", folder=str(vault / "Los"))
        assert gate.reached.wait(20)
        root = config.VAULT_ROOT
        away = root.with_name("vault_weg")
        os.rename(root, away)
        try:
            gate.open.set()
            job = _wait(c, job["id"], until=("waiting_for_vault", "done", "failed"))
            assert job["status"] == "waiting_for_vault", job
            time.sleep(0.3)
            assert not root.exists(), sorted(str(p) for p in root.rglob("*"))
        finally:
            if not root.exists():
                os.rename(away, root)
        job = _wait(c, job["id"])
        assert job["status"] == "done", job
        assert job["summary"]["made"] == 24 and job["summary"]["complete"] is True, job
        assert job["message"] == "Klaar: 24 nieuw, 0 waren er al"
        assert all(thumbnail.get_thumbnail_cache_path(p, config.THUMB_CACHE_PATH).exists() for p in photos)


def test_thumbnails_made_twice_after_a_pause_are_counted_once(client, vault, config, monkeypatch):
    """R23-1: 'nieuw' never adds up to more than the number of files."""
    from backend import thumbnail
    photos = _photos(vault / "Twee", 12)
    gate = ThumbGate(monkeypatch, at=4)
    job = _start(client, "thumbnails", folder=str(vault / "Twee"))
    assert gate.reached.wait(20)
    client.post(f"/api/jobs/{job['id']}/pause")
    gate.open.set()
    job = _wait(client, job["id"])
    assert job["status"] == "paused"
    made = [p for p in photos if thumbnail.get_thumbnail_cache_path(p, config.THUMB_CACHE_PATH).exists()]
    assert made
    for p in made:  # lost in between (e.g. written somewhere they do not count)
        thumbnail.get_thumbnail_cache_path(p, config.THUMB_CACHE_PATH).unlink()
    client.post(f"/api/jobs/{job['id']}/resume")
    job = _wait(client, job["id"])
    assert job["status"] == "done" and job["summary"]["made"] == 12, job
    result = client.get(f"/api/jobs/{job['id']}/result").json()["result"]
    assert result["made"] + result["cached"] == 12
    assert job["message"] == "Klaar: 12 nieuw, 0 waren er al"


def _short_clip(path, seconds):
    import subprocess
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=duration={seconds}:size=64x48:rate=10", "-pix_fmt", "yuv420p", str(path)],
                   check=True, stdin=subprocess.DEVNULL)
    return path


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg ontbreekt")
def test_a_video_shorter_than_a_second_gets_its_first_frame(tmp_path):
    """R23-2: no frame at 1 s means the first frame, not a failure."""
    from backend import thumbnail
    clip = _short_clip(tmp_path / "kort.mp4", 0.5)
    out = tmp_path / "thumbs" / "kort.jpg"
    out.parent.mkdir()
    thumbnail.render_video_thumbnail(clip, out)
    with Image.open(out) as img:
        assert img.size[0] > 0
    assert not thumbnail.is_placeholder(out)


def test_undecodable_files_are_counted_apart_and_do_not_make_a_run_incomplete(client, vault, config):
    """R23-2: a corrupt photo is not 'mislukt' on every run; a read error still is."""
    import shutil
    folder = vault / "Gemengd"
    good = _photos(folder, 2)
    (folder / "ruis.jpg").write_bytes(os.urandom(3000))
    (folder / "leeg.jpg").write_bytes(b"")
    (folder / "nep.heic").write_bytes(b"not a heic")
    expected_bad = 3
    if shutil.which("ffmpeg"):
        _short_clip(folder / "kort.mp4", 0.5)
        (folder / "kapot.mp4").write_bytes(b"\x00" * 4096)
        expected_bad += 1
    files = len(list(folder.iterdir()))
    for run in range(2):
        job = _wait(client, _start(client, "thumbnails", folder=str(folder))["id"])
        assert job["status"] == "done", job
        assert job["summary"]["errors"] == 0 and job["summary"]["complete"] is True, job
        assert job["summary"]["undecodable"] == expected_bad, job
        assert f"{expected_bad} onleesbaar" in job["message"] and "mislukt" not in job["message"]
        result = client.get(f"/api/jobs/{job['id']}/result").json()["result"]
        assert result["made"] + result["cached"] + result["undecodable"] == files
        names = sorted(Path(e["path"]).name for e in result["undecodable_samples"])
        assert "ruis.jpg" in names and "kort.mp4" not in names
    # A file that cannot be read at all stays an error
    locked = folder / "op_slot.jpg"
    Image.new("RGB", (30, 30), (1, 2, 3)).save(locked)
    os.chmod(locked, 0)
    try:
        job = _wait(client, _start(client, "thumbnails", folder=str(folder))["id"])
    finally:
        os.chmod(locked, 0o644)
    assert job["summary"]["errors"] == 1 and job["summary"]["complete"] is False
    assert "1 mislukt" in job["message"]


def test_the_scan_message_counts_in_proper_dutch():
    from backend.job_types import _scan_message
    one = {"groups": [{}], "duplicates_found": 1, "errors": 1}
    many = {"groups": [{}, {}], "duplicates_found": 3, "errors": 0}
    assert _scan_message(one) == "Klaar: 1 groep, 1 duplicaat (1 leesfout: onvolledig)"
    assert _scan_message(many) == "Klaar: 2 groepen, 3 duplicaten"


def test_shutdown_waits_until_a_worker_has_fully_ended(client, monkeypatch):
    """
    A worker that has finished its job still prunes old jobs on its own connection. Shutdown must wait
    for that, else close_all() closes the connection under it (a segmentation fault in sqlite3).
    """
    manager = _test_manager(client, lambda ctx: {"klaar": True})
    pruning, go_on = threading.Event(), threading.Event()
    real = manager._prune

    def slow_prune(job_type):
        pruning.set()
        go_on.wait(5)
        real(job_type)

    monkeypatch.setattr(manager, "_prune", slow_prune)
    job, _ = manager.start("proef", {})
    assert pruning.wait(5)
    worker = next(iter(manager._workers))
    assert job["id"] not in manager._live            # the job itself is done and gone from _live
    threading.Timer(0.3, go_on.set).start()
    started = time.monotonic()
    manager.shutdown()
    assert time.monotonic() - started >= 0.25, "shutdown did not wait for the worker's clean-up"
    assert not worker.is_alive() and not manager._workers
    assert manager.get(job["id"])["status"] == jobs.DONE
