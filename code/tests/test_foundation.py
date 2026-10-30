"""Config safety net, schema migrations and app start-up."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend import migrations
from backend.config import Config
from backend.database import Database

# Schema exactly as created by database.py before migrations existed
LEGACY_SCHEMA = """
CREATE TABLE file_hashes (path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL,
    sha256 TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE delete_runs (run_id TEXT PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_files INTEGER DEFAULT 0);
CREATE TABLE delete_operations (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    src_path TEXT NOT NULL, dst_path TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES delete_runs(run_id));
CREATE TABLE file_ratings (path TEXT PRIMARY KEY, rating INTEGER DEFAULT 1000,
    votes INTEGER DEFAULT 0, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE show_settings (path TEXT PRIMARY KEY, enabled BOOLEAN DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE keurer_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL,
    folder TEXT NOT NULL, decision TEXT NOT NULL, decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(path, folder));
CREATE INDEX idx_keurer_folder ON keurer_decisions(folder);
CREATE INDEX idx_keurer_date ON keurer_decisions(decided_at);
"""


def _utc_string_for_local_noon(days_ago: int) -> str:
    local_noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return local_noon.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _make_legacy_db(path, with_keurer=True):
    conn = sqlite3.connect(str(path))
    schema = LEGACY_SCHEMA if with_keurer else LEGACY_SCHEMA.split("CREATE TABLE keurer_decisions")[0]
    conn.executescript(schema)
    conn.execute("INSERT INTO file_ratings (path, rating, votes) VALUES ('/v/RAW/a.jpg', 1150, 3)")
    conn.execute("INSERT INTO show_settings (path, enabled) VALUES ('/v/library/Oud', 0)")
    conn.execute("INSERT INTO delete_runs (run_id, total_files) VALUES ('run-1', 1)")
    conn.execute("INSERT INTO delete_operations (run_id, src_path, dst_path) "
                 "VALUES ('run-1', '/v/RAW/b.jpg', '/v/RAW_DUPLICATES/b.jpg')")
    conn.execute("INSERT INTO file_hashes (path, size, mtime, sha256) VALUES ('/v/RAW/a.jpg', 10, 1.5, 'abc')")
    if with_keurer:
        # Three consecutive days with the daily goal of 20 reached -> streak 3
        for day in range(3):
            for i in range(20):
                conn.execute(
                    "INSERT INTO keurer_decisions (path, folder, decision, decided_at) VALUES (?, ?, ?, ?)",
                    (f"/v/RAW/d{day}_{i}.jpg", "/v/RAW", "keep", _utc_string_for_local_noon(day)),
                )
    conn.commit()
    conn.close()


def test_testing_mode_refuses_vault_outside_tempdir(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "vault_root": "/Volumes/SomeDisk/vault",
        "raw_folder": "/Volumes/SomeDisk/vault/RAW",
        "cache_folder": "/Volumes/SomeDisk/vault/.datavault_cache",
    }))
    with pytest.raises(RuntimeError, match="refusing to run tests"):
        Config(str(cfg))


def test_testing_mode_refuses_default_config(monkeypatch):
    """Even without an explicit path, the real code/config.json is rejected in test mode."""
    from backend import config as config_module
    if not config_module.DEFAULT_CONFIG_PATH.exists():
        pytest.skip("no local config.json")
    with pytest.raises(RuntimeError):
        Config()


def test_config_env_override(config_file, monkeypatch):
    monkeypatch.setenv("DATAVAULT_CONFIG", str(config_file))
    assert Config().config_path == config_file


def test_fresh_database_gets_latest_schema_without_backup(tmp_path):
    db_path = tmp_path / "cache" / "datavault.db"
    Database(db_path)
    conn = sqlite3.connect(str(db_path))
    assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION
    conn.close()
    assert not (db_path.parent / migrations.BACKUP_DIR_NAME).exists()


def test_legacy_database_keeps_ratings_streak_and_undo_data(tmp_path):
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)

    db = Database(db_path)

    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_folder_status("/v/library/Oud") is False
    run = db.get_last_delete_run()
    assert run["run_id"] == "run-1"
    assert [(op["src_path"], op["dst_path"]) for op in db.get_delete_operations("run-1")] == [
        ("/v/RAW/b.jpg", "/v/RAW_DUPLICATES/b.jpg")
    ]
    stats = db.get_keurer_stats()
    assert stats["streak"] == 3
    assert stats["today"] == 20
    assert stats["total"] == 60

    conn = sqlite3.connect(str(db_path))
    assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION
    conn.close()

    backups = list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v0-*.db"))
    assert len(backups) == 1
    backup = sqlite3.connect(str(backups[0]))
    assert backup.execute("SELECT rating FROM file_ratings").fetchone()[0] == 1150
    backup.close()


def test_database_older_than_keurer_gets_table_added(tmp_path):
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path, with_keurer=False)
    db = Database(db_path)
    assert db.get_keurer_stats()["total"] == 0
    assert db.get_rating("/v/RAW/a.jpg") == 1150


def test_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    Database(db_path)
    assert migrations.migrate(db_path) == []
    # No second backup when nothing is pending
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).iterdir())) == 1


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    db_path = tmp_path / "datavault.db"
    Database(db_path)

    def broken(conn):
        conn.execute("CREATE TABLE half_done (x INTEGER)")
        raise ValueError("boom")

    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS + [(999, "broken", broken)])
    with pytest.raises(ValueError):
        migrations.migrate(db_path)
    conn = sqlite3.connect(str(db_path))
    assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='half_done'").fetchone() is None
    conn.close()


def test_app_starts_on_test_vault(client, vault):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["vault_available"] is True
    assert body["vault_path"] == str(vault)


# The first-parent history of phase 3 merged migrations out of order: step 3.9a shipped
# 1-12, 20, 21, 30, 31, 60, 61, and only later merges added 40 and 50 below 61.
_OUT_OF_ORDER_SHIPPED = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 20, 21, 30, 31, 60, 61}


def test_migrations_merged_below_the_highest_version_still_run(tmp_path, monkeypatch):
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    full = list(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", [m for m in full if m[0] in _OUT_OF_ORDER_SHIPPED])
    assert migrations.migrate(db_path) == [v for v in sorted(_OUT_OF_ORDER_SHIPPED) if v > 0]
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                 "VALUES ('/v/RAW/a.jpg', '/v/RAW', 'a.jpg', 'photo', '.jpg', 10, 1, 1.0, 1.0, 1.0)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    applied = migrations.migrate(db_path)
    assert 40 in applied and 50 in applied
    assert applied == [m[0] for m in full if m[0] not in _OUT_OF_ORDER_SHIPPED]

    conn = sqlite3.connect(str(db_path))
    versions = {r[0] for r in conn.execute("SELECT version FROM schema_version")}
    assert versions == {m[0] for m in full}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"elo_comparisons", "semantic_embeddings"} <= tables
    # Every row from before survives the late migrations
    assert conn.execute("SELECT rating, votes FROM file_ratings WHERE path='/v/RAW/a.jpg'").fetchone() == (1150, 3)
    assert conn.execute("SELECT enabled FROM show_settings").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM delete_operations").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM keurer_decisions").fetchone() == (60,)
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone() == (1,)
    conn.close()
    # Nothing left to do afterwards, and the app's own start-up agrees
    assert migrations.migrate(db_path) == []
    db = Database(db_path)
    assert db.get_rating("/v/RAW/a.jpg") == 1150


def test_a_db_with_a_gap_gets_a_backup_before_the_gap_is_filled(tmp_path, monkeypatch):
    db_path = tmp_path / "datavault.db"
    Database(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM schema_version WHERE version IN (40, 50)")
    conn.commit()
    conn.close()
    assert migrations.migrate(db_path) == [40, 50]
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v*.db"))) == 1

