"""Background jobs: start, progress, cancel, pause, resume, interrupted, waiting for the vault (step 2.3)."""
import os
import sqlite3
import threading
import time

import pytest
from PIL import Image

from backend import jobs, scanner
from backend.database import Database


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
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
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
        "folder": str(folder), "files": 5, "made": 5, "errors": 0, "complete": True}
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
