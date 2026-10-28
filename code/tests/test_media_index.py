"""
The media index (backend/media_index.py, migration 11): one row per photo, video and
audio file, kept up to date by the 'index' job, and the query layer on top of it.
"""
import sqlite3
import time

from backend.database import Database


def test_migration_11_adds_media_index_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 10, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO media_dates VALUES ('/v/RAW/a.jpg', 1, 2, 3.0, 'exif', 4.0)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('hashing', '{\"folder\": \"/v/RAW\"}', 'paused', 12, ?, ?, '{\"hashed_before\": 12}')",
                     (now, now))
        conn.execute("INSERT INTO job_results (job_id, encoding, data) VALUES (1, 'json+zlib', x'00')")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    assert "sqlite_sequence" in before and before["jobs"] and before["keurer_decisions"]

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 11
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        media_columns = {r[1] for r in conn.execute("PRAGMA table_info(media)")}
        assert {"id", "path", "folder", "name", "kind", "ext", "size", "mtime_ns", "inode", "birthtime",
                "taken", "taken_source", "width", "height", "orientation", "camera_make", "camera_model",
                "lens", "lat", "lon", "altitude", "duration", "indexed_at", "seen_at", "added_at",
                "missing"} <= media_columns
        assert {r[1] for r in conn.execute("PRAGMA table_info(media_folders)")} == {
            "path", "parent", "mtime_ns", "last_scanned"}
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_media_taken", "idx_media_folder", "idx_media_latlon", "idx_media_camera"} <= indexes
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v10-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_recent_keurer_decisions(1)[0]["dest_ino"] == 77
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    assert db.get_media_dates(["/v/RAW/a.jpg"]) == {"/v/RAW/a.jpg": (1, 2, 3.0, "exif")}
    db.close_all()


# ---------- helpers ----------

import os
import shutil
import sqlite3 as _sqlite3
from pathlib import Path

import pytest

from fixtures.vault_builder import PORTRAIT, STRAND, _photo


def _manager():
    from backend.routes.jobs import get_manager
    return get_manager()


def _index(client, full=False, expect="done"):
    r = client.post("/api/jobs", json={"type": "index", "params": {"full": full}})
    assert r.status_code in (200, 201), r.text
    job = _manager().wait(r.json()["job"]["id"], timeout=60)
    assert job["status"] == expect, job
    if expect != "done":
        return job
    return client.get(f"/api/jobs/{job['id']}/result").json()["result"]


def _row(config, rel):
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.row_factory = _sqlite3.Row
        row = conn.execute("SELECT * FROM media WHERE path = ?", (str(config.VAULT_ROOT / rel),)).fetchone()
    return dict(row) if row else None


def _paths(config, sql="SELECT path FROM media WHERE missing = 0"):
    root = str(config.VAULT_ROOT) + "/"
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        return {r[0][len(root):] for r in conn.execute(sql)}


# ---------- indexing ----------

def test_a_first_pass_indexes_every_media_file_with_its_metadata(client, config):
    assert client.get("/api/library/status").json()["ready"] is False
    result = _index(client)
    assert result["complete"] is True and result["errors"] == 0
    paths = _paths(config)
    assert STRAND in paths and PORTRAIT in paths
    assert "RAW/video/clip één.mp4" in paths and "RAW/audio/toon 🎵.mp3" in paths
    # Never: hidden files, non-media, the app's own folder, a symlink out of the vault
    assert not any(Path(p).name.startswith(".") for p in paths)
    assert not any(p.endswith(".txt") or p.endswith(".json") for p in paths)
    assert not any("link_to_outside" in p or ".datavault_cache" in p for p in paths)
    assert result["files"] == len(paths) == result["new"] == result["read"]

    strand = _row(config, STRAND)
    assert strand["kind"] == "photo" and strand["taken_source"] == "exif"
    assert (strand["width"], strand["height"], strand["camera_make"], strand["camera_model"]) == (800, 600, "Canon", "EOS 80D")
    assert strand["lat"] == pytest.approx(52.37, abs=1e-3) and strand["lon"] == pytest.approx(4.89, abs=1e-3)
    portrait = _row(config, PORTRAIT)
    assert (portrait["width"], portrait["height"], portrait["orientation"]) == (200, 400, 6)
    clip = _row(config, "RAW/video/clip één.mp4")
    if clip["size"]:
        assert clip["kind"] == "video" and clip["duration"] == pytest.approx(2.0, abs=0.2)
    assert _row(config, "RAW/misc/corrupt.jpg")["meta_status"] == "none"

    status = client.get("/api/library/status").json()
    assert status["ready"] is True and status["files"] == len(paths) and status["job"] is None
    assert status["photos"] + status["videos"] + status["audio"] == status["files"]


def test_an_unchanged_vault_lists_no_folder_and_reads_no_file(client, config):
    first = _index(client)
    again = _index(client)
    assert (again["new"], again["changed"], again["read"], again["folders_listed"]) == (0, 0, 0, 0)
    assert again["folders_unchanged"] == first["folders"] and again["files"] == first["files"]
    full = _index(client, full=True)
    assert (full["new"], full["changed"], full["read"]) == (0, 0, 0)
    assert full["folders_listed"] == first["folders"]


def test_only_the_changed_folder_is_listed_and_only_new_files_are_read(client, config):
    _index(client)
    _photo(config.VAULT_ROOT / "RAW/2022/bursts/nieuw.jpg", (1, 1, 1), "nieuw", "2024:01:02 03:04:05",
           gps=(48.85, 2.29))
    result = _index(client)
    assert (result["folders_listed"], result["new"], result["read"], result["changed"]) == (1, 1, 1, 0)
    row = _row(config, "RAW/2022/bursts/nieuw.jpg")
    assert row["taken_source"] == "exif" and row["lat"] == pytest.approx(48.85, abs=1e-3)


def test_a_file_edited_in_place_is_found_by_a_full_pass(client, config):
    _index(client)
    photo = config.VAULT_ROOT / "RAW/misc/UPPER.JPEG"
    folder_mtime = os.stat(photo.parent).st_mtime_ns
    _photo(photo, (9, 9, 9), "anders", "2011:11:11 11:11:11")       # rewritten in place
    os.utime(photo.parent, ns=(folder_mtime, folder_mtime))           # the folder looks untouched
    assert _index(client)["changed"] == 0                             # incremental: folder unchanged
    result = _index(client, full=True)
    assert result["changed"] == 1 and result["read"] == 1
    assert _row(config, "RAW/misc/UPPER.JPEG")["taken_source"] == "exif"


def test_a_removed_file_is_hidden_then_removed_after_the_pass(client, config):
    _index(client)
    target = config.VAULT_ROOT / "RAW/2022/bursts/burst_03.jpg"
    keep = target.read_bytes()
    target.unlink()  # (the test vault is a temporary copy)
    result = _index(client)
    assert result["removed"] == 1 and result["moved"] == 0
    assert _row(config, "RAW/2022/bursts/burst_03.jpg") is None
    target.write_bytes(keep)
    assert _index(client)["new"] == 1


def test_a_removed_folder_and_a_folder_that_became_hidden_leave_the_index(client, config):
    _index(client)
    shutil.rmtree(config.VAULT_ROOT / "RAW/misc/deep")
    (config.VAULT_ROOT / "RAW/2022/bursts").rename(config.VAULT_ROOT / "RAW/2022/.bursts")
    result = _index(client)
    paths = _paths(config)
    assert not any("deep/" in p or "bursts/" in p for p in paths)
    assert result["removed"] == 7 and result["errors"] == 0
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        folders = {r[0] for r in conn.execute("SELECT path FROM media_folders")}
    assert not any("/deep" in f or "bursts" in f for f in folders)


def test_an_unreadable_folder_keeps_its_rows_and_makes_the_pass_incomplete(client, config):
    _index(client)
    locked = config.VAULT_ROOT / "RAW/2022/bursts"
    before = {p for p in _paths(config) if p.startswith("RAW/2022/bursts/")}
    (locked / "extra.jpg").write_bytes((locked / "burst_00.jpg").read_bytes())
    os.chmod(locked, 0)
    try:
        result = _index(client, full=True)
        assert result["complete"] is False and result["errors"] >= 1
        assert {p for p in _paths(config) if p.startswith("RAW/2022/bursts/")} == before
        assert client.get("/api/library/status").json()["ready"] is True
    finally:
        os.chmod(locked, 0o755)
    result = _index(client)
    assert result["complete"] is True and result["new"] == 1


def test_a_file_that_cannot_be_read_gets_its_file_date_and_is_read_again(client, config):
    photo = config.VAULT_ROOT / STRAND
    os.chmod(photo, 0)
    try:
        result = _index(client)
        assert result["read_errors"] == 1 and result["complete"] is True
        row = _row(config, STRAND)
        assert row["meta_status"] == "error" and row["taken_source"] in ("birthtime", "mtime")
    finally:
        os.chmod(photo, 0o644)
    result = _index(client)
    assert result["changed"] == 1 and result["read_errors"] == 0
    assert _row(config, STRAND)["taken_source"] == "exif"


# ---------- moves ----------

def _rate(config, rel, rating=1300, votes=4):
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("INSERT OR REPLACE INTO file_ratings (path, rating, votes) VALUES (?, ?, ?)",
                     (str(config.VAULT_ROOT / rel), rating, votes))


def _rating(config, rel):
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        row = conn.execute("SELECT rating, votes FROM file_ratings WHERE path = ?",
                           (str(config.VAULT_ROOT / rel),)).fetchone()
    return tuple(row) if row else None


def test_a_move_outside_the_app_keeps_the_row_its_rating_and_keurer_keep(client, config):
    _index(client)
    src, dst = "RAW/2022/bursts/burst_01.jpg", "RAW/misc/burst_01 verplaatst.jpg"
    _rate(config, src)
    keep = client.post("/api/keurer/decide", json={
        "path": str(config.VAULT_ROOT / src), "folder": str(config.VAULT_ROOT / "RAW/2022/bursts"),
        "decision": "keep"})
    assert keep.status_code == 200, keep.text
    row_id = _row(config, src)["id"]
    os.rename(config.VAULT_ROOT / src, config.VAULT_ROOT / dst)   # as Finder would (same inode)

    result = _index(client)
    assert result["moved"] == 1 and result["removed"] == 0
    assert _row(config, src) is None
    moved = _row(config, dst)
    assert moved["id"] == row_id and moved["missing"] == 0 and moved["folder"] == str(config.VAULT_ROOT / "RAW/misc")
    assert _rating(config, dst) == (1300, 4) and _rating(config, src) is None
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        assert conn.execute("SELECT path, folder FROM keurer_decisions WHERE decision = 'keep'").fetchall() == [
            (str(config.VAULT_ROOT / dst), str(config.VAULT_ROOT / "RAW/misc"))]
    assert client.get("/api/keurer/stats").json()["today"] == 1


def test_a_copied_then_removed_file_is_a_move_only_with_a_known_content_hash(client, config):
    from backend.scanner import HASH_ALGO, hash_file_xxh3
    _index(client)
    pairs = [("RAW/2022/bursts/burst_02.jpg", "library/burst_02 kopie.jpg"),
             ("RAW/2022/bursts/burst_04.jpg", "library/burst_04 kopie.jpg")]
    # Only the first one's content hash was cached (a duplicate scan read it)
    first = config.VAULT_ROOT / pairs[0][0]
    st = os.stat(first)
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("INSERT INTO file_hashes (path, size, mtime, hash, algo, ino, mtime_ns, ctime_ns) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (str(first), st.st_size, st.st_mtime, hash_file_xxh3(first), HASH_ALGO,
                      st.st_ino, st.st_mtime_ns, st.st_ctime_ns))
    ids = {}
    for src, dst in pairs:
        _rate(config, src)
        ids[src] = _row(config, src)["id"]
        shutil.copyfile(config.VAULT_ROOT / src, config.VAULT_ROOT / dst)   # a new inode and birthtime
        (config.VAULT_ROOT / src).unlink()
    result = _index(client)
    assert result["moved"] == 1 and result["removed"] == 1
    assert _row(config, pairs[0][1])["id"] == ids[pairs[0][0]] and _rating(config, pairs[0][1]) == (1300, 4)
    # No known content: no guessing, the old rating stays where it was
    assert _row(config, pairs[1][1])["id"] != ids[pairs[1][0]] and _rating(config, pairs[1][1]) is None
    assert _rating(config, pairs[1][0]) == (1300, 4)


def test_a_move_in_the_app_updates_the_row_and_rating_at_once(client, config):
    _index(client)
    src = "RAW/2019/zomer vakantie/IMG_0002.JPG"
    _rate(config, src, 1200, 2)
    row_id = _row(config, src)["id"]
    r = client.post("/api/move-file", json={"source_path": str(config.VAULT_ROOT / src),
                                            "destination_folder": str(config.VAULT_ROOT / "library/Familie")})
    assert r.status_code == 200, r.text
    dst = "library/Familie/IMG_0002.JPG"
    row = _row(config, dst)
    assert row["id"] == row_id and row["missing"] == 0 and row["folder"] == str(config.VAULT_ROOT / "library/Familie")
    assert _row(config, src) is None and _rating(config, dst) == (1200, 2) and _rating(config, src) is None
    result = _index(client)
    assert (result["moved"], result["removed"], result["new"]) == (0, 0, 0)


def test_keurer_delete_hides_the_row_and_undo_brings_it_back_with_its_id(client, config):
    _index(client)
    src = "RAW/2019/zomer vakantie/IMG_0002.JPG"
    _rate(config, src)
    row_id = _row(config, src)["id"]
    stats_before = client.get("/api/keurer/stats").json()
    decided = client.post("/api/keurer/decide", json={
        "path": str(config.VAULT_ROOT / src), "folder": str(config.VAULT_ROOT / "RAW/2019/zomer vakantie"),
        "decision": "delete"}).json()
    assert decided["stats"]["total"] == stats_before["total"] + 1
    trashed = _row(config, "TRASH/IMG_0002.JPG")
    assert trashed["id"] == row_id and trashed["missing"] == 1
    assert src not in _paths(config)
    assert _rating(config, "TRASH/IMG_0002.JPG") == (1300, 4)

    undone = client.post("/api/keurer/undo", json={"id": decided["id"]})
    assert undone.status_code == 200, undone.text
    back = _row(config, src)
    assert back["id"] == row_id and back["missing"] == 0 and _rating(config, src) == (1300, 4)
    assert client.get("/api/keurer/stats").json()["total"] == stats_before["total"]


def test_a_file_that_stays_in_trash_leaves_the_index_after_a_pass(client, config):
    _index(client)
    src = "RAW/2019/zomer vakantie/IMG_0002.JPG"
    client.post("/api/keurer/decide", json={
        "path": str(config.VAULT_ROOT / src), "folder": str(config.VAULT_ROOT / "RAW/2019/zomer vakantie"),
        "decision": "delete"})
    assert _index(client)["removed"] == 1
    assert _row(config, "TRASH/IMG_0002.JPG") is None
    assert (config.VAULT_ROOT / "TRASH/IMG_0002.JPG").exists()   # the index never touches the file


def test_duplicates_move_and_undo_follow_in_the_index(client, config):
    from fixtures.vault_builder import STRAND_COPIES
    _index(client)
    copy = STRAND_COPIES[0]
    _rate(config, copy, 1111, 1)
    row_id = _row(config, copy)["id"]
    r = client.post("/api/duplicates/move", json={"groups": [
        {"keep": str(config.VAULT_ROOT / STRAND), "move": [str(config.VAULT_ROOT / copy)]}]})
    assert r.status_code == 200 and r.json()["moved"], r.text
    assert copy not in _paths(config)
    run_id = r.json()["run_id"]
    assert client.post("/api/duplicates/undo", json={"run_id": run_id}).status_code == 200
    back = _row(config, copy)
    assert back["id"] == row_id and back["missing"] == 0 and _rating(config, copy) == (1111, 1)


# ---------- jobs: resume, cancel, vault ----------

def test_a_paused_pass_resumes_without_reading_again_what_it_wrote(client, config, monkeypatch):
    from backend import media_index
    monkeypatch.setattr(media_index, "BATCH_FILES", 4)
    reads = []
    original = media_index.read_file
    pause_at = 9
    job_id = {}

    def slow_read(path, st):
        reads.append(path)
        if len(reads) == pause_at:
            _manager().pause(job_id["id"])
        return original(path, st)

    monkeypatch.setattr(media_index, "read_file", slow_read)
    r = client.post("/api/jobs", json={"type": "index", "params": {}})
    job_id["id"] = r.json()["job"]["id"]
    job = _manager().wait(job_id["id"], timeout=60)
    assert job["status"] == "paused", job
    partial = len(_paths(config))
    assert 0 < partial < 40
    reads.clear()
    _manager().resume(job_id["id"])
    job = _manager().wait(job_id["id"], statuses=("queued", "running"), timeout=60)
    assert job["status"] == "done", job
    result = client.get(f"/api/jobs/{job_id['id']}/result").json()["result"]
    assert result["files"] == len(_paths(config)) and len(reads) == result["files"] - partial


def test_a_cancelled_pass_leaves_a_consistent_index(client, config, monkeypatch):
    from backend import media_index
    monkeypatch.setattr(media_index, "BATCH_FILES", 3)
    original = media_index.read_file
    count = []
    job_id = {}

    def read(path, st):
        count.append(1)
        if len(count) == 5:
            _manager().cancel(job_id["id"])
        return original(path, st)

    monkeypatch.setattr(media_index, "read_file", read)
    job_id["id"] = client.post("/api/jobs", json={"type": "index", "params": {}}).json()["job"]["id"]
    assert _manager().wait(job_id["id"], timeout=60)["status"] == "cancelled"
    assert client.get("/api/library/status").json()["ready"] is False
    monkeypatch.setattr(media_index, "read_file", original)
    result = _index(client)
    assert result["complete"] and result["files"] == len(_paths(config))


def test_an_unplugged_vault_makes_the_pass_wait_and_finish_when_it_returns(client, config, monkeypatch):
    from backend import media_index
    monkeypatch.setattr(media_index, "BATCH_FILES", 3)
    _manager().vault_poll_interval = 0.1
    original = media_index.read_file
    gone = config.VAULT_ROOT.parent / "vault_los"
    count = []

    def read(path, st):
        count.append(1)
        if len(count) == 4:
            config.VAULT_ROOT.rename(gone)   # the disk is unplugged mid-pass
        return original(path, st)

    monkeypatch.setattr(media_index, "read_file", read)
    job_id = client.post("/api/jobs", json={"type": "index", "params": {}}).json()["job"]["id"]
    job = _manager().wait(job_id, statuses=("queued", "running"), timeout=30)
    assert job["status"] == "waiting_for_vault", job
    monkeypatch.setattr(media_index, "read_file", original)
    gone.rename(config.VAULT_ROOT)
    job = _manager().wait(job_id, statuses=("queued", "running", "waiting_for_vault"), timeout=30)
    assert job["status"] == "done", job
    result = client.get(f"/api/jobs/{job_id}/result").json()["result"]
    assert result["complete"] and result["files"] == len(_paths(config)) and result["removed"] == 0


def test_automatic_passes_keep_only_their_last_five_jobs(client, config):
    dup = client.post("/api/jobs", json={"type": "hashing", "params": {"folder": str(config.VAULT_ROOT / "library")}})
    _manager().wait(dup.json()["job"]["id"], timeout=60)
    for _ in range(7):
        _index(client)
    jobs = client.get("/api/jobs?limit=100").json()["jobs"]
    assert sum(1 for j in jobs if j["type"] == "index") == 5
    assert any(j["type"] == "hashing" for j in jobs)


def test_index_params_are_checked(client):
    assert client.post("/api/jobs", json={"type": "index", "params": {"full": "ja"}}).status_code == 400


def test_the_auto_indexer_runs_on_start_and_when_the_vault_returns(config_file, config, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import media_index
    from backend.main import create_app
    monkeypatch.setenv("DATAVAULT_AUTO_INDEX", "1")
    monkeypatch.setattr(media_index, "AUTO_POLL_SECONDS", 0.1)
    app = create_app(str(config_file))
    app.state.auto_index.poll = 0.1
    with TestClient(app) as c:
        manager = app.state.jobs
        manager.vault_poll_interval = 0.1

        def index_jobs():
            return [j for j in manager.list(limit=50, job_type="index")]

        deadline = time.monotonic() + 30
        while not (index_jobs() and index_jobs()[0]["status"] == "done") and time.monotonic() < deadline:
            time.sleep(0.05)
        first = index_jobs()
        assert len(first) == 1 and first[0]["status"] == "done" and first[0]["params"]["auto"] is True
        assert c.get("/api/library/status").json()["ready"] is True

        gone = config.VAULT_ROOT.parent / "vault_weg"
        config.VAULT_ROOT.rename(gone)
        time.sleep(0.5)
        gone.rename(config.VAULT_ROOT)
        deadline = time.monotonic() + 30
        while not (len(index_jobs()) == 2 and index_jobs()[0]["status"] == "done") and time.monotonic() < deadline:
            time.sleep(0.05)
        jobs = index_jobs()
        assert len(jobs) == 2 and jobs[0]["status"] == "done", jobs
    assert app.state.auto_index._thread is not None and not app.state.auto_index._thread.is_alive()
