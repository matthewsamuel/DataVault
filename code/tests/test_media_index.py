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
        if pause_at and len(reads) == pause_at:
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
    pause_at = None
    _manager().resume(job_id["id"])
    job = _manager().wait(job_id["id"], statuses=("queued", "running"), timeout=60)
    assert job["status"] == "done", job
    result = client.get(f"/api/jobs/{job_id['id']}/result").json()["result"]
    assert result["files"] == len(_paths(config)) and len(reads) == result["files"] - partial
    _assert_index_matches_disk(client, config, set())


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
    _assert_index_matches_disk(client, config, set())


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
    _assert_index_matches_disk(client, config, set())


def _new_root_folders(config, count=12):
    """Many sibling folders at the vault root, each with two photos (they come early in the walk)."""
    added = set()
    for i in range(count):
        for j in range(2):
            rel = f"Map {i:02d}/foto {j}.jpg"
            _photo(config.VAULT_ROOT / rel, (i, j, 1), rel, f"2015:0{1 + j}:{10 + i} 12:00:00")
            added.add(rel)
    return added


def _assert_index_matches_disk(client, config, added):
    assert added <= _paths(config)
    full = _index(client, full=True)
    # A full pass that finds nothing new or changed: the index already held the disk
    assert (full["new"], full["changed"], full["read"], full["removed"]) == (0, 0, 0, 0), full
    assert _scan(client, config.VAULT_ROOT)["source"] == "index"


@pytest.mark.parametrize("how", ["cancel", "pause"])
def test_an_interrupted_pass_leaves_no_folder_out_of_the_index(client, config, monkeypatch, how):
    # A folder's row is only written once its whole subtree is in the index, so a
    # stored root row can never hide subfolders that the interrupted pass did not reach
    from backend import media_index
    monkeypatch.setattr(media_index, "_updater", lambda: None)
    _index(client)
    added = _new_root_folders(config)
    monkeypatch.setattr(media_index, "BATCH_FILES", 3)
    original = media_index.read_file
    count = []
    job_id = {}

    def read(path, st):
        count.append(1)
        if len(count) == 5:
            (_manager().cancel if how == "cancel" else _manager().pause)(job_id["id"])
        return original(path, st)

    monkeypatch.setattr(media_index, "read_file", read)
    job_id["id"] = client.post("/api/jobs", json={"type": "index", "params": {}}).json()["job"]["id"]
    job = _manager().wait(job_id["id"], timeout=60)
    assert job["status"] == ("cancelled" if how == "cancel" else "paused"), job
    assert not added <= _paths(config)
    # Not fresh: the timeline walks instead of answering from a partial index
    assert _scan(client, config.VAULT_ROOT)["source"] == "walk"
    monkeypatch.setattr(media_index, "read_file", original)
    if how == "cancel":
        result = _index(client)
    else:
        _manager().resume(job_id["id"])
        job = _manager().wait(job_id["id"], statuses=("queued", "running"), timeout=60)
        assert job["status"] == "done", job
        result = client.get(f"/api/jobs/{job_id['id']}/result").json()["result"]
    assert result["complete"] and result["files"] == len(_paths(config))
    _assert_index_matches_disk(client, config, added)


def test_a_cold_pass_cancelled_early_is_finished_by_the_next_incremental_pass(client, config, monkeypatch):
    from backend import media_index
    added = _new_root_folders(config)
    monkeypatch.setattr(media_index, "BATCH_FILES", 3)
    original = media_index.read_file
    count = []
    job_id = {}

    def read(path, st):
        count.append(1)
        if len(count) == 7:
            _manager().cancel(job_id["id"])
        return original(path, st)

    monkeypatch.setattr(media_index, "read_file", read)
    job_id["id"] = client.post("/api/jobs", json={"type": "index", "params": {}}).json()["job"]["id"]
    assert _manager().wait(job_id["id"], timeout=60)["status"] == "cancelled"
    monkeypatch.setattr(media_index, "read_file", original)
    result = _index(client)
    assert result["complete"] and result["files"] == len(_paths(config))
    _assert_index_matches_disk(client, config, added)


def test_a_new_folder_that_could_not_be_listed_is_tried_again(client, config, monkeypatch):
    from backend import media_index
    monkeypatch.setattr(media_index, "_updater", lambda: None)
    _index(client)
    new = config.VAULT_ROOT / "library/Nieuw"
    _photo(new / "a.jpg", (2, 2, 2), "a", "2019:05:05 05:05:05")
    os.chmod(new, 0)
    try:
        result = _index(client)
        assert result["complete"] is False and result["errors"] >= 1
        assert _scan(client, config.VAULT_ROOT / "library")["source"] == "walk"
    finally:
        os.chmod(new, 0o755)
    # Still not fresh after the folder became readable: its parent was never marked done
    assert _scan(client, config.VAULT_ROOT / "library")["source"] == "walk"
    result = _index(client)
    assert result["complete"] is True and result["new"] == 1
    assert "library/Nieuw/a.jpg" in _paths(config)
    assert _scan(client, config.VAULT_ROOT / "library")["source"] == "index"
    _assert_index_matches_disk(client, config, {"library/Nieuw/a.jpg"})


def test_an_index_from_the_older_walk_is_listed_once_in_full(client, config):
    # An index written before folder rows waited for their subtree may have a root
    # row that hides unvisited subfolders: the next pass lists every folder once
    from backend import media_index
    from backend.routes.library import db
    _index(client)
    lost = str(config.VAULT_ROOT / "library") + "/"
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("DELETE FROM media WHERE path >= ? AND path < ?", (lost, lost[:-1] + "0"))
        conn.execute("DELETE FROM media_folders WHERE path >= ? AND path < ?", (lost, lost[:-1] + "0"))
    info = media_index.state(db)
    info.pop("walk_version")
    db.set_setting(media_index.STATE_KEY, info)
    result = _index(client)
    assert result["complete"] and result["new"] > 0 and result["folders_unchanged"] == 0
    assert any(p.startswith("library/") for p in _paths(config))
    again = _index(client)
    assert (again["folders_listed"], again["read"]) == (0, 0)
    _assert_index_matches_disk(client, config, set())


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


# ---------- the timeline, on this day and photos by date from the index ----------

from datetime import datetime  # noqa: E402

from helpers import timeline_files  # noqa: E402
from test_exclusions import DAY, planted  # noqa: E402,F401


def _scan(client, *folders, **params):
    r = client.get("/api/timeline/scan", params={"folders": [str(f) for f in folders], **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_timeline_from_the_index_matches_the_walk(client, config):
    roots = [config.VAULT_ROOT / "RAW", config.VAULT_ROOT / "library"]
    walked = _scan(client, *roots)
    assert walked["source"] == "walk"
    _index(client)
    indexed = _scan(client, *roots)
    assert indexed["source"] == "index"
    for key in ("total", "rows", "date_index", "year_boundaries", "date_sources", "complete", "next_offset"):
        assert indexed[key] == walked[key], key


def test_the_timeline_pages_give_every_row_once(client, config):
    _index(client)
    whole = _scan(client, config.VAULT_ROOT)
    assert whole["source"] == "index" and whole["next_offset"] is None
    rows, offset, version = [], 0, None
    while offset is not None:
        page = _scan(client, config.VAULT_ROOT, offset=offset, limit=7)
        assert page["total"] == whole["total"] and page["date_index"] == whole["date_index"]
        assert page["offset"] == offset and len(page["rows"]) <= 7
        version = version or page["version"]
        assert page["version"] == version
        rows += page["rows"]
        offset = page["next_offset"]
    assert rows == whole["rows"]
    assert client.get("/api/timeline/scan", params={"folders": [str(config.VAULT_ROOT)], "limit": 0}).status_code == 422


def test_the_walk_pages_too_while_there_is_no_index(client, config):
    whole = _scan(client, config.VAULT_ROOT / "RAW")
    page = _scan(client, config.VAULT_ROOT / "RAW", offset=5, limit=5)
    assert page["source"] == "walk" and page["rows"] == whole["rows"][5:10] and page["next_offset"] == 10


def test_a_change_on_disk_makes_the_timeline_walk_and_ask_for_a_pass(client, config, monkeypatch):
    from backend import media_index
    asked = []
    monkeypatch.setattr(media_index, "_updater", lambda: asked.append(1))
    _index(client)
    assert _scan(client, config.VAULT_ROOT / "library")["source"] == "index" and not asked
    _photo(config.VAULT_ROOT / "library/Familie/nieuw.jpg", (5, 5, 5), "nieuw", "2012:12:12 12:00:00")
    data = _scan(client, config.VAULT_ROOT / "library")
    assert data["source"] == "walk" and asked
    assert str(config.VAULT_ROOT / "library/Familie/nieuw.jpg") in {f["path"] for f in timeline_files(data)}
    _index(client)
    data = _scan(client, config.VAULT_ROOT / "library")
    assert data["source"] == "index"
    assert str(config.VAULT_ROOT / "library/Familie/nieuw.jpg") in {f["path"] for f in timeline_files(data)}


def test_a_folder_that_could_not_be_listed_keeps_the_timeline_on_the_walk(client, config):
    _index(client)
    locked = config.VAULT_ROOT / "RAW/2022/bursts"
    os.chmod(locked, 0)
    try:
        _index(client, full=True)
    finally:
        os.chmod(locked, 0o755)
    assert _scan(client, config.VAULT_ROOT / "RAW")["source"] == "walk"
    assert _scan(client, config.VAULT_ROOT / "library")["source"] == "index"


def test_on_this_day_and_photos_by_date_come_from_the_index(client, config, monkeypatch):
    from backend.routes import stats
    now = datetime.now()
    try:
        day = now.replace(year=now.year - 7, hour=9, minute=0, second=0, microsecond=0)
    except ValueError:
        day = now.replace(year=now.year - 7, day=28, hour=9, minute=0, second=0, microsecond=0)
    folder = config.VAULT_ROOT / "library/Vandaag"
    _photo(folder / "jarig.jpg", (1, 1, 1), "x", day.strftime("%Y:%m:%d %H:%M:%S"))
    _photo(folder / "gisteren.jpg", (1, 1, 1), "x", "2001:01:01 01:00:00"
           if now.strftime("%m-%d") != "01-01" else "2001:02:02 01:00:00")
    _index(client)
    # The walk is never needed now: a budget of zero folders still finds it
    monkeypatch.setattr(stats, "_walk_for_dates", lambda *a, **k: pytest.fail("walked the disk"))
    data = client.get("/api/stats/photo-on-this-day").json()
    assert {p["name"] for p in data["photos"]} >= {"jarig.jpg"} and data["complete"] is True
    assert all(p["type"] in ("photo", "video") for p in data["photos"])
    only = client.get("/api/stats/photo-on-this-day", params={"allowed_folders": [str(config.VAULT_ROOT / "RAW")]}).json()
    assert "jarig.jpg" not in {p["name"] for p in only["photos"]}
    by_date = client.get("/api/stats/photos-by-date", params={"date": day.strftime("%Y-%m-%d")}).json()
    assert [p["name"] for p in by_date["photos"]] == ["jarig.jpg"] and by_date["photos"][0]["date_source"] == "exif"
    # A hit whose file is gone (or changed) since the pass is not shown
    (folder / "jarig.jpg").unlink()
    assert client.get("/api/stats/photos-by-date", params={"date": day.strftime("%Y-%m-%d")}).json()["photos"] == []


def test_the_index_never_shows_excluded_files(client, config, planted, monkeypatch):
    import backend.routes.stats as stats_mod
    result = _index(client)
    assert result["complete"]
    paths = {str(config.VAULT_ROOT / p) for p in _paths(config)}
    for name, p in planted["hidden"].items():
        assert str(p) not in paths, name
    for name, p in planted["visible"].items():
        assert str(p) in paths, name

    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return DAY.replace(year=2026)
    monkeypatch.setattr(stats_mod, "datetime", FakeNow)
    found = {p["path"] for p in client.get("/api/stats/photo-on-this-day").json()["photos"]}
    assert found and found <= {str(p) for p in planted["visible"].values()}


# ---------- the query layer ----------

def test_query_filters_sort_and_page(client, config):
    from backend import media_index
    _index(client)
    db = client.app.state.db
    root = str(config.VAULT_ROOT)
    _rate(config, STRAND, 1400, 5)
    _rate(config, "library/Familie/opa.jpg", 900, 3)

    def names(filters=None, **kw):
        return [r["name"] for r in media_index.query(db, filters, **kw)]

    everything = media_index.count(db)
    assert everything == len(_paths(config))
    assert set(names({"kind": "video"})) <= {"clip één.mp4", "kapot.mp4"}
    assert names({"kind": "audio"}) == ["toon 🎵.mp3"]
    assert set(names({"ext": [".png", "heic"]})) <= {"🎂 taart.png", "IMG_4001.heic", "fake.heic"}
    july = names({"date_from": datetime(2019, 7, 14).timestamp(), "date_to": datetime(2019, 7, 15).timestamp()})
    assert set(july) == {"strand 001.jpg", "IMG_0002.JPG", "strand 001 kopie.jpg", "dup van strand.jpg"}
    assert set(names({"folder": f"{root}/library"})) == set(names({"folders": [f"{root}/library"]}))
    assert names({"folder": f"{root}/RAW/misc", "recursive": False, "kind": "photo"}, sort="name")[:2] == [
        "UPPER.JPEG", "corrupt.jpg"]
    assert "diep bestand.jpeg" in names({"folder": f"{root}/RAW/misc"})
    assert set(names({"camera": "apple iphone 11"})) == {"koffie é à ü.jpg"}
    assert set(names({"camera": "iPhone 7"})) == {"portret.jpg"}
    gps = set(names({"has_gps": True}))
    assert {"strand 001.jpg", "IMG_0002.JPG"} <= gps and "opa.jpg" not in gps
    assert set(names({"bbox": (52, 4, 53, 5)})) == {"strand 001.jpg", "strand 001 kopie.jpg", "dup van strand.jpg"}
    assert names({"min_rating": 1000}) == ["strand 001.jpg"]
    rated = media_index.query(db, {"min_rating": 1000})[0]
    assert (rated["rating"], rated["votes"]) == (1400, 5)
    assert names({"orientation": "portrait"}) == ["portret.jpg"]
    assert "strand 001.jpg" in names({"orientation": "landscape"})
    by_size = media_index.query(db, sort="-size", limit=3)
    assert [r["size"] for r in by_size] == sorted([r["size"] for r in by_size], reverse=True)
    page1, page2 = names(sort="path", limit=5), names(sort="path", offset=5, limit=5)
    assert page1 + page2 == names(sort="path", limit=10)
    histogram = media_index.date_histogram(db, {"kind": "photo"})
    assert (2019, 7, 4) in histogram
    with pytest.raises(ValueError):
        media_index.query(db, sort="sql; drop")
    with pytest.raises(ValueError):
        media_index.query(db, {"orientation": "schuin"})


def test_library_media_route(client, config):
    assert client.get("/api/library/media").json() == {"ready": False, "total": 0, "offset": 0,
                                                         "limit": 100, "items": []}
    _index(client)
    data = client.get("/api/library/media", params={"kind": "photo", "date_from": "2019-07-14",
                                                    "date_to": "2019-07-14", "has_gps": True,
                                                    "folder": "RAW", "sort": "name"}).json()
    assert data["ready"] is True and data["total"] == 4
    assert [i["name"] for i in data["items"]][0] == "IMG_0002.JPG"
    assert client.get("/api/library/media", params={"folder": "../.."}).status_code == 403
    assert client.get("/api/library/media", params={"sort": "nope"}).status_code == 400
    assert client.get("/api/library/media", params={"orientation": "nope"}).status_code == 400
    assert client.get("/api/library/media", params={"date_from": "14-07-2019"}).status_code == 400
    assert client.get("/api/library/media", params={"kind": "doc"}).status_code == 400


def test_out_of_range_dates_and_huge_numbers_are_refused_or_clamped(client, config):
    _index(client)
    huge = str(10 ** 20)
    # The day after 9999-12-31 is no date: 400, never 500
    assert client.get("/api/library/media", params={"date_to": "9999-12-31"}).status_code == 400
    assert client.get("/api/stats/photos-by-date", params={"date": "9999-12-31"}).status_code == 400
    # Year 1 is a timestamp on some platforms and not on others (time zone): never a 500
    for name in ("date_from", "date_to"):
        assert client.get("/api/library/media", params={name: "0001-01-01"}).status_code in (200, 400)
    assert client.get("/api/stats/photos-by-date", params={"date": "0001-01-01"}).status_code in (200, 400)
    for name, date in (("date_from", "9999-12-31"), ("date_to", "9999-12-30")):
        assert client.get("/api/library/media", params={name: date}).status_code == 200
    data = client.get("/api/library/media", params={"offset": huge}).json()
    assert data["items"] == [] and data["offset"] == 2 ** 62
    assert client.get("/api/library/media", params={"min_rating": huge}).json()["total"] == 0
    assert client.get("/api/library/media", params={"min_rating": "-" + huge}).status_code == 200
    r = client.get("/api/timeline/scan", params={"folders": str(config.VAULT_ROOT), "offset": huge})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    page = client.get("/api/timeline/scan", params={"folders": str(config.VAULT_ROOT), "offset": 2 ** 62})
    assert page.status_code == 200 and page.json()["rows"] == [] and page.json()["next_offset"] is None


def test_an_automatic_pass_is_never_queued_twice_and_replaces_an_interrupted_one(client, config):
    from backend import media_index
    manager = _manager()
    first = client.post("/api/jobs", json={"type": "index", "params": {"auto": True}}).json()["job"]
    manager.wait(first["id"], timeout=60)
    # A restart left an automatic pass 'interrupted'
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("UPDATE jobs SET status = 'interrupted' WHERE id = ?", (first["id"],))
    auto = media_index.AutoIndexer(manager)
    assert auto.start_pass() is True
    jobs = manager.list(limit=10, job_type="index")
    assert manager.get(first["id"])["status"] == "cancelled"
    newest = jobs[0]
    assert newest["id"] != first["id"] and newest["params"] == {"full": False, "auto": True}
    # While that one is active, no second one
    if newest["status"] in ("queued", "running"):
        assert auto.start_pass() is False
    manager.wait(newest["id"], timeout=60)
    # A manual pass that was paused is left alone (the user resumes it)
    paused = client.post("/api/jobs", json={"type": "index", "params": {"full": True}}).json()["job"]
    manager.wait(paused["id"], timeout=60)
    with _sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("UPDATE jobs SET status = 'interrupted' WHERE id = ?", (paused["id"],))
    assert auto.start_pass() is True
    assert manager.get(paused["id"])["status"] == "interrupted"


def test_a_pass_that_changes_nothing_keeps_the_timeline_version(client, config):
    _index(client)
    before = _scan(client, config.VAULT_ROOT, limit=5)["version"]
    _index(client)
    _index(client, full=True)
    assert _scan(client, config.VAULT_ROOT, limit=5)["version"] == before
    _photo(config.VAULT_ROOT / "library/Familie/extra.jpg", (5, 5, 5), "x", "2012:12:12 12:00:00")
    _index(client)
    assert _scan(client, config.VAULT_ROOT, limit=5)["version"] != before
