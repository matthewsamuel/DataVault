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
