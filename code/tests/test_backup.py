"""
Automatic database backups and the way back (backend/backup.py, restore_backup.py).

- A backup is a consistent snapshot (SQLite backup API, WAL included, also while the app
  writes), compact (rebuildable caches emptied, schema kept), checked with
  integrity_check, and never overwrites anything.
- Due on start when the newest is older than 24 h; rotation keeps the newest per day for
  7 days and per week for 4 weeks, and only ever removes our own backup files.
- Restore only with the app stopped, and only after the live database was saved in full.
"""
import errno
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend import backup, migrations
from backend.config import Config


@pytest.fixture(autouse=True)
def no_other_servers(monkeypatch):
    """Other DataVault servers on this Mac (other checkouts) are not this test's app."""
    monkeypatch.setattr(backup, "running_servers", lambda: [])


def _fill(db):
    """User data and a bulky cache in the live database."""
    db.apply_vote("/v/library/a.jpg", 50)
    db.apply_vote("/v/library/b.jpg", -50)
    db.set_setting("chat_model", "qwen2.5:3b")
    with db._connect() as conn:
        conn.executemany(
            "INSERT INTO file_hashes (path, size, mtime, hash, algo) VALUES (?, 1, 1.0, ?, 'xxh3')",
            [(f"/v/RAW/h{i:05d}.jpg", os.urandom(32).hex()) for i in range(3000)])
        conn.executemany("INSERT INTO media_dates VALUES (?, 1, 2, 3.0, 'exif', 4.0)",
                         [(f"/v/RAW/d{i:05d}.jpg",) for i in range(2000)])
        conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                     "VALUES ('/v/library/a.jpg', '/v/library', 'a.jpg', 'photo', '.jpg', 10, 1, 1, 1, 1)")
        conn.execute("INSERT INTO media_folders VALUES ('/v/library', '/v', 1, 1.0)")


def _run(client, timeout=60):
    """'Nu back-uppen': POST /api/backup/run starts the backup job (202); wait for it. Returns
    (status, body): 200 and {"backup": result} when it is done, the job's error as
    {"error": {"message": ...}} with 500 when it failed, else the immediate refusal."""
    r = client.post("/api/backup/run")
    if r.status_code != 202:
        return r.status_code, r.json()
    job = r.json()["job"]
    assert job["type"] == "backup" and r.json()["status"]["folder"]
    deadline = time.time() + timeout
    while job["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(0.05)
        job = client.get(f"/api/jobs/{job['id']}").json()
    if job["status"] == "done":
        return 200, {"backup": client.get(f"/api/jobs/{job['id']}/result").json()["result"], "job": job}
    return 500, {"error": {"message": job["error"]}, "job": job}


class _open:
    """A backup (gzip or plain) opened read-only: with _open(path) as conn."""

    def __init__(self, path):
        self._plain = backup._plain(Path(path))

    def __enter__(self):
        plain = self._plain.__enter__()
        self._conn = sqlite3.connect(f"file:{plain}?mode=ro", uri=True)
        return self._conn

    def __exit__(self, *exc):
        self._conn.close()
        return self._plain.__exit__(*exc)


def test_a_backup_is_compact_checked_and_keeps_every_user_row(client, app, config):
    db = app.state.db
    _fill(db)
    code, body = _run(client)
    assert code == 200, body
    made = body["backup"]
    assert body["job"]["summary"]["size"] == made["size"] and body["job"]["processed"] == backup.STEPS
    path = Path(made["path"])
    assert path.parent == config.BACKUP_FOLDER and path.name.endswith("-handmatig.db.gz")
    assert made["integrity"] == "ok" and made["kind"] == "handmatig"
    assert made["schema_version"] == migrations.LATEST_VERSION
    assert made["emptied"] == {"file_hashes": 3000, "job_results": 0, "media_dates": 2000, "media_folders": 1,
                               "media_phash": 0, "semantic_embeddings": 0}
    assert made["size"] == path.stat().st_size < made["plain_size"] < made["live_size"]
    assert path.read_bytes()[:2] == b"\x1f\x8b"                                   # gzip
    assert not list(config.BACKUP_FOLDER.glob(".*partial"))
    with _open(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert dict(conn.execute("SELECT path, rating FROM file_ratings")) == {
            "/v/library/a.jpg": 1050, "/v/library/b.jpg": 950}
        assert conn.execute("SELECT value FROM app_settings WHERE key = 'chat_model'").fetchone()[0] == '"qwen2.5:3b"'
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1      # ids other data points at
        for table in ("file_hashes", "media_dates", "media_folders", "job_results"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert not Path(f"{path}-wal").exists()
    assert not list(config.BACKUP_FOLDER.glob("*.db"))           # nothing left unpacked

    status = client.get("/api/backup/status").json()
    assert status["folder"] == str(config.BACKUP_FOLDER) and status["count"] == 1
    assert status["last"]["name"] == path.name and status["last"]["size"] == made["size"]
    assert status["next_due_at"] == pytest.approx(status["last"]["created_at"] + 24 * 3600)
    assert status["auto"] is False and status["running"] is False and status["last_error"] is None
    assert "restore_backup.py" in status["restore_command"]


def test_a_backup_includes_what_is_still_in_the_wal(client, app, config):
    db = app.state.db
    with db._connect() as conn:
        conn.execute("PRAGMA wal_autocheckpoint = 0")
    for i in range(50):
        db.apply_vote(f"/v/library/w{i}.jpg", 50)
    assert os.path.getsize(f"{config.DB_PATH}-wal") > 0
    made = backup.make_backup(config)
    with _open(made["path"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM file_ratings").fetchone()[0] == 50


def test_a_backup_while_the_app_writes_is_a_consistent_snapshot(client, app, config):
    """A writer adds ratings two at a time in one transaction; a backup must never hold half of one."""
    db = app.state.db
    stop = threading.Event()

    def writer():
        n = 0
        while not stop.is_set():
            with db._connect() as conn:
                conn.execute("INSERT INTO file_ratings (path, rating, votes) VALUES (?, 1000, 1)", (f"/v/x/{n}a",))
                conn.execute("INSERT INTO file_ratings (path, rating, votes) VALUES (?, 1000, 1)", (f"/v/x/{n}b",))
            n += 1
        db.release_thread()
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        counts = []
        for i in range(6):
            made = backup.make_backup(config, keep_daily=100)
            with _open(made["path"]) as conn:
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                counts.append(conn.execute("SELECT COUNT(*) FROM file_ratings").fetchone()[0])
            time.sleep(1.05)   # one name per second
    finally:
        stop.set()
        thread.join()
    assert all(c % 2 == 0 for c in counts), counts
    assert counts == sorted(counts) and counts[-1] > 0


def _touch(folder, when, kind=""):
    name = f"datavault-{when:%Y%m%d-%H%M%S}{kind}.db.gz"
    (folder / name).write_bytes(b"x")
    return name


def test_rotation_keeps_seven_days_and_four_weeks_and_nothing_else_is_touched(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    start = datetime(2026, 10, 28, 9, 0, 0)
    names = {}
    for day in range(40):
        moment = start - timedelta(days=day)
        names[day] = [_touch(folder, moment), _touch(folder, moment + timedelta(hours=5), "-handmatig")]
    for d in range(8):
        (folder / f"datavault-voor-herstel-{start - timedelta(days=d):%Y%m%d-%H%M%S}.db.gz").write_bytes(b"y")
    others = ["notities.txt", "datavault.db", "datavault-2026.db.gz", "datavault-20261028-090000.db.gz.partial",
              ".datavault-20261028-090000.db.gz.1.2.partial"]
    for name in others:
        (folder / name).write_text("niet van ons")
    outside = tmp_path / "elders.db"
    outside.write_text("x")
    os.symlink(outside, folder / "datavault-20200101-000000.db.gz")

    removed = backup.rotate(folder, keep_daily=7, keep_weekly=4)
    left = {p.name for p in folder.iterdir()}
    # The automatic one of each of the last 7 days (not the handmatig one, 5 hours later) ...
    expected = {names[d][0] for d in range(7)}
    # ... the newest automatic one of each of the last 4 ISO weeks (2026-10-28 is a Wednesday)
    weeks = {}
    for day in range(40):
        moment = start - timedelta(days=day)
        weeks.setdefault(moment.isocalendar()[:2], names[day][0])
    expected |= set(list(weeks.values())[:4])
    # ... and the 5 newest made by hand, kept apart
    expected |= {names[d][1] for d in range(5)}
    voor_herstel = sorted(n for n in left if n.startswith("datavault-voor-herstel-"))
    assert len(voor_herstel) == 5
    assert left == expected | set(others) | {"datavault-20200101-000000.db.gz"} | set(voor_herstel)
    assert len(removed) == 80 - len(expected) + 3
    assert outside.read_text() == "x"


def test_due_on_start_when_the_newest_is_older_than_a_day(app, config):
    auto = backup.AutoBackup(config, app.state.db, interval=3600, first_delay=0)
    assert backup.is_due(config)
    made = auto.check()
    assert made is not None and made["kind"] == "auto" and not made["name"].endswith("-handmatig.db")
    assert auto.check() is None                       # the next check: not due
    assert not backup.is_due(config)
    assert backup.is_due(config, now=time.time() + 24 * 3600 + 1)
    # Only an old backup: due again
    os.unlink(made["path"])
    _touch(config.BACKUP_FOLDER, datetime.now() - timedelta(hours=25))
    assert backup.is_due(config)
    assert auto.check() is not None
    assert len(backup.list_backups(config.BACKUP_FOLDER)) == 2


def test_the_running_app_makes_one_on_start(config_file, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    monkeypatch.setenv("DATAVAULT_AUTO_BACKUP", "1")
    monkeypatch.setattr(backup, "FIRST_CHECK_DELAY", 0)
    app = create_app(str(config_file))
    app.state.auto_backup.first_delay = 0
    with TestClient(app) as c:
        c.headers["X-DataVault-Token"] = c.get("/api/session").json()["token"]
        deadline = time.time() + 20
        while time.time() < deadline and not backup.list_backups(app.state.config.BACKUP_FOLDER):
            time.sleep(0.1)
        status = c.get("/api/backup/status").json()
    assert status["auto"] is True and status["count"] == 1 and status["last"]["kind"] == "auto"


def test_failures_leave_nothing_behind_and_are_reported(client, app, config, monkeypatch):
    backup.make_backup(config)
    first = {p.name for p in config.BACKUP_FOLDER.iterdir()}
    time.sleep(1.05)
    monkeypatch.setattr(backup, "_integrity", lambda path: "*** in database main *** Page 3: corrupt")
    code, body = _run(client)
    assert code == 500 and "niet bewaard" in body["error"]["message"]
    monkeypatch.undo()
    monkeypatch.setattr(backup, "running_servers", lambda: [])

    class Usage:
        free = 10 * 1024 * 1024
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda path: Usage)
    code, body = _run(client)
    assert code == 507 and "Te weinig vrije ruimte" in body["error"]["message"]
    assert {p.name for p in config.BACKUP_FOLDER.iterdir()} == first
    status = client.get("/api/backup/status").json()
    assert "Te weinig vrije ruimte" in status["last_error"]["message"]

    with backup._lock:
        assert client.post("/api/backup/run").status_code == 409


def test_a_backup_folder_without_hard_links_gets_an_exclusive_copy(client, app, config, monkeypatch):
    # exFAT or FAT (a USB stick): os.link answers 'Operation not supported'
    def no_links(src, dst, *a, **k):
        raise OSError(errno.ENOTSUP, "Operation not supported")
    monkeypatch.setattr(backup.os, "link", no_links)
    _fill(app.state.db)
    made = backup.make_backup(config, manual=True)
    path = Path(made["path"])
    assert path.is_file() and backup.check_backup_file(path) == migrations.LATEST_VERSION
    assert sorted(p.name for p in config.BACKUP_FOLDER.iterdir()) == [path.name]   # no partial files left
    # The exclusive copy never overwrites: a name that is taken stays as it was
    other = config.BACKUP_FOLDER / "bezet.bin"
    other.write_bytes(b"van iemand anders")
    source = config.BACKUP_FOLDER / "bron.bin"
    source.write_bytes(b"nieuw")
    with pytest.raises(FileExistsError):
        backup._copy_new(source, other)
    assert other.read_bytes() == b"van iemand anders"
    # A copy that does not read back the same is removed again and reported
    real = backup._sha256
    calls = []
    monkeypatch.setattr(backup, "_sha256", lambda stream: calls.append(1) or (real(stream) + ("x" if len(calls) == 2 else "")))
    with pytest.raises(backup.BackupError, match="klopt niet"):
        backup._copy_new(source, config.BACKUP_FOLDER / "kopie.bin")
    assert not (config.BACKUP_FOLDER / "kopie.bin").exists()


def test_backups_made_by_hand_are_kept_apart_from_the_daily_rotation(client, app, config, tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    day = datetime(2026, 10, 28, 9, 0, 0)
    auto = _touch(folder, day - timedelta(hours=1))
    manual = [_touch(folder, day + timedelta(minutes=i), "-handmatig") for i in range(7)]
    backup.rotate(folder, keep_daily=7, keep_weekly=4)
    # The newest 5 by hand, and the automatic one of that day: a backup made by hand never
    # pushes it out
    assert {p.name for p in folder.iterdir()} == set(manual[2:]) | {auto}
    # Through the API: two backups by hand on one day both stay
    first = _run(client)[1]["backup"]["name"]
    time.sleep(1.05)
    second = _run(client)[1]["backup"]["name"]
    assert {b["name"] for b in backup.list_backups(config.BACKUP_FOLDER)} == {first, second}
    assert client.get("/api/backup/status").json()["keep_manual"] == backup.KEEP_MANUAL


def test_a_backup_by_hand_keeps_the_automatic_one_of_that_day(client, app, config):
    """'Nu back-uppen' after something went wrong must not remove this morning's automatic
    backup: that is the only one from before the change."""
    morning = backup.make_backup(config)
    assert morning["kind"] == "auto"
    time.sleep(1.05)   # one name per second
    code, body = _run(client)
    assert code == 200, body
    assert body["backup"]["removed"] == []
    names = {b["name"] for b in backup.list_backups(config.BACKUP_FOLDER)}
    assert names == {morning["name"], body["backup"]["name"]}
    # A day with only backups made by hand still has one in its day slot (beyond the 5 kept
    # apart), and an older day keeps its automatic one over a later one by hand
    folder = config.BACKUP_FOLDER
    old = datetime(2026, 9, 1, 9, 0, 0)
    only_manual = _touch(folder, old, "-handmatig")
    auto_before = _touch(folder, old - timedelta(days=1))
    manual_after = _touch(folder, old - timedelta(days=1) + timedelta(hours=3), "-handmatig")
    for i in range(6):
        _touch(folder, datetime.now() - timedelta(minutes=10 + i), "-handmatig")
    backup.rotate(folder, keep_daily=7, keep_weekly=4)
    left = {p.name for p in folder.iterdir()}
    assert only_manual in left and auto_before in left and manual_after not in left
    assert morning["name"] in left


def test_a_backup_folder_that_cannot_be_written_says_so_in_dutch(client, app, config):
    config.BACKUP_FOLDER.mkdir(parents=True, exist_ok=True)
    os.chmod(config.BACKUP_FOLDER, 0o555)
    try:
        code, body = _run(client)
    finally:
        os.chmod(config.BACKUP_FOLDER, 0o755)
    message = body["error"]["message"]
    assert code == 500
    assert message.startswith(f"De back-upmap {config.BACKUP_FOLDER} is niet schrijfbaar")
    assert "backup_folder" in message and "mislukt" not in message and "unable" not in message
    assert client.get("/api/backup/status").json()["last_error"]["message"] == message
    # sqlite and OS errors become Dutch too
    assert "bezet" in str(backup._reason(sqlite3.OperationalError("database is locked"), config.BACKUP_FOLDER))
    assert "vol" in str(backup._reason(OSError(errno.ENOSPC, "No space left on device"), config.BACKUP_FOLDER))
    assert "schrijfbaar" in str(backup._reason(OSError(errno.EACCES, "Permission denied"), config.BACKUP_FOLDER))


def test_the_restore_command_names_the_config_the_app_runs_with(client, app, config, monkeypatch):
    command = client.get("/api/backup/status").json()["restore_command"]
    assert command.endswith(f"restore_backup.py --config {config.config_path.resolve()}") or \
        command.endswith(f"restore_backup.py --config '{config.config_path.resolve()}'")
    assert f"--config" in command and "cd " in command
    # With code/config.json itself the option is left out
    monkeypatch.setattr(backup, "DEFAULT_CONFIG_PATH", Path(config.config_path))
    assert backup.restore_command(config).endswith("venv/bin/python restore_backup.py")


def test_the_status_says_when_the_running_app_checks_next(app, config):
    auto = backup.AutoBackup(config, app.state.db, interval=3600, first_delay=600)
    assert backup.status(config, auto_enabled=True)["next_check_at"] is None
    auto.start()
    try:
        status = backup.status(config, auto_enabled=True, next_check_at=auto.next_check_at)
        assert abs(status["next_check_at"] - (time.time() + 600)) < 5
        assert status["check_interval_minutes"] == 60 and status["next_due_at"] is None
    finally:
        auto.stop()
    assert backup.status(config, auto_enabled=False, next_check_at=1.0)["next_check_at"] is None


def test_with_the_vault_unplugged_the_status_still_answers(client, app, config, vault):
    backup.make_backup(config)
    hidden = vault.parent / "vault-weg"
    vault.rename(hidden)
    try:
        assert client.get("/api/backup/status").json()["count"] == 1
        r = client.post("/api/backup/run")
        assert r.status_code == 503, r.text
        assert not list(config.BACKUP_FOLDER.glob(".*partial"))
    finally:
        hidden.rename(vault)


def test_test_mode_refuses_a_backup_folder_outside_the_temp_dir(vault, tmp_path):
    import json
    from fixtures.vault_builder import write_config
    path = write_config(vault, tmp_path / "c.json")
    data = json.loads(path.read_text())
    data["backup_folder"] = str(Path.home() / "Library/Application Support/DataVault/backups")
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="backup_folder"):
        Config(str(path))
    del data["backup_folder"]
    path.write_text(json.dumps(data))
    assert Config(str(path)).BACKUP_FOLDER == (vault / ".datavault_cache/backups").resolve()


def test_a_backup_folder_with_a_tilde_is_in_the_home_folder(vault, tmp_path, monkeypatch):
    """The README writes the default as ~/Library/...: copied into config.json it must mean
    the home folder, not a folder named '~' below wherever the app was started."""
    import json
    from fixtures.vault_builder import write_config
    home = tmp_path / "thuis"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    path = write_config(vault, tmp_path / "c.json")
    data = json.loads(path.read_text())
    data["backup_folder"] = "~/Library/Application Support/DataVault/backups"
    path.write_text(json.dumps(data))
    config = Config(str(path))
    assert config.BACKUP_FOLDER == (home / "Library/Application Support/DataVault/backups").resolve()
    assert "~" not in str(config.BACKUP_FOLDER) and config.BACKUP_FOLDER_NOTE is None
    # A relative folder would depend on where the app was started: the default is used, and
    # Instellingen says why
    data["backup_folder"] = "backups"
    path.write_text(json.dumps(data))
    config = Config(str(path))
    assert config.BACKUP_FOLDER == (vault / ".datavault_cache/backups").resolve()
    assert "geen volledig pad" in config.BACKUP_FOLDER_NOTE and '"backups"' in config.BACKUP_FOLDER_NOTE
    assert backup.status(config)["folder_note"] == config.BACKUP_FOLDER_NOTE


# ==================== RESTORE ====================

def _rating(db_path, path):
    with _open(db_path) as conn:
        row = conn.execute("SELECT rating FROM file_ratings WHERE path = ?", (path,)).fetchone()
        return row[0] if row else None


def test_restore_is_refused_while_the_app_runs(client, app, config):
    app.state.db.apply_vote("/v/a.jpg", 50)
    made = backup.make_backup(config)
    app.state.db.apply_vote("/v/a.jpg", 50)
    with pytest.raises(backup.BackupError, match="Stop de app eerst"):
        backup.restore_backup(config, Path(made["path"]))
    assert _rating(config.DB_PATH, "/v/a.jpg") == 1100
    assert not [b for b in backup.list_backups(config.BACKUP_FOLDER) if b["kind"] == "voor-herstel"]


def test_restore_refuses_while_a_datavault_server_runs(app, config, monkeypatch):
    made = backup.make_backup(config)
    monkeypatch.setattr(backup, "running_servers", lambda: ["4242 python -m uvicorn backend.main:create_app"])
    with pytest.raises(backup.BackupError, match="DataVault draait nog"):
        backup.restore_backup(config, Path(made["path"]))


def test_restore_saves_the_live_database_first_and_puts_the_backup_back(config_file):
    from fastapi.testclient import TestClient
    from backend.database import Database
    from backend.main import create_app
    app = create_app(str(config_file))
    with TestClient(app):
        db = app.state.db
        config = app.state.config
        db.apply_vote("/v/a.jpg", 50)
        _fill(db)
        made = backup.make_backup(config)
        db.apply_vote("/v/a.jpg", 50)
        db.apply_vote("/v/new.jpg", 50)
    # The app is stopped now (its lock is released)
    import restore_backup
    assert restore_backup.main(["--config", str(config_file), made["name"], "--ja"]) == 0
    assert _rating(config.DB_PATH, "/v/a.jpg") == 1050 and _rating(config.DB_PATH, "/v/new.jpg") is None
    [saved] = [b for b in backup.list_backups(config.BACKUP_FOLDER) if b["kind"] == "voor-herstel"]
    assert _rating(saved["path"], "/v/a.jpg") == 1100 and _rating(saved["path"], "/v/new.jpg") == 1050
    with _open(saved["path"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM file_hashes").fetchone()[0] == 3000   # a full copy
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # The restored (compact) database opens and works; the emptied caches fill again
    db = Database(config.DB_PATH)
    assert db.get_rating("/v/library/a.jpg") == 1050 and db.get_setting("chat_model") == "qwen2.5:3b"
    with db._connect() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM file_hashes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    db.close_all()
    # ... and the safety copy goes back the same way
    assert restore_backup.main(["--config", str(config_file), saved["path"], "--ja"]) == 0
    assert _rating(config.DB_PATH, "/v/new.jpg") == 1050


def test_restore_refuses_damaged_or_foreign_files(app, config, tmp_path, capsys):
    import restore_backup
    bad = tmp_path / "kapot.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    other = tmp_path / "ander.db"
    sqlite3.connect(str(other)).execute("CREATE TABLE x (y)").connection.close()
    newer = tmp_path / "nieuwer.db"
    conn = sqlite3.connect(str(newer))
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO schema_version VALUES (9999, 'toekomst')")
    conn.commit()
    conn.close()
    made = backup.make_backup(config)
    cut = tmp_path / "afgebroken.db.gz"
    cut.write_bytes(Path(made["path"]).read_bytes()[:-100])
    for path in (bad, other, newer, cut, tmp_path / "bestaat-niet.db"):
        with pytest.raises(backup.BackupError):
            backup.check_backup_file(path)
        assert restore_backup.main(["--config", str(config.config_path), str(path), "--ja"]) == 2
    assert [p.name for p in config.BACKUP_FOLDER.iterdir()] == [made["name"]]   # no safety copy made


def test_the_restore_script_lists_the_backups(app, config, capsys):
    import restore_backup
    backup.make_backup(config)
    assert restore_backup.main(["--config", str(config.config_path)]) == 0
    out = capsys.readouterr().out
    assert str(config.BACKUP_FOLDER) in out and "datavault-" in out and "auto" in out


@pytest.mark.parametrize("folder", ["back-ups #2", "back-ups? 2", "back-ups %41"])
def test_checks_read_the_real_file_when_the_path_has_hash_question_mark_or_percent(tmp_path, folder):
    """Unquoted, a '#' or '?' cuts the SQLite URI short: the check would open (and create)
    an empty database at the shortened path and always say 'ok'."""
    where = tmp_path / folder
    where.mkdir()
    good = where / "goed #1.db"
    conn = sqlite3.connect(str(good))
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO schema_version VALUES (42, 'x')")
    conn.commit()
    conn.close()
    bad = where / "kapot #1.db"
    bad.write_bytes(good.read_bytes()[:100] + b"\xff" * (len(good.read_bytes()) - 100))
    before = sorted(p.name for p in tmp_path.rglob("*"))
    assert backup._integrity(good) == "ok"
    assert backup._schema_version(good) == 42
    try:
        verdict = backup._integrity(bad)
    except sqlite3.DatabaseError as e:
        verdict = str(e)
    assert verdict != "ok"
    assert sorted(p.name for p in tmp_path.rglob("*")) == before        # nothing created
    with pytest.raises(backup.BackupError):
        backup.check_backup_file(bad)


def test_a_backup_by_hand_runs_as_a_job_that_can_be_cancelled(client, app, config, monkeypatch):
    """PERF-4: 'Nu back-uppen' answers at once (202) with a job; its steps show in the dock and
    cancelling it leaves no backup and no temporary copy behind."""
    gate, reached = threading.Event(), threading.Event()
    real = backup._compress

    def slow(plain, packed):
        reached.set()
        gate.wait(10)
        return real(plain, packed)
    monkeypatch.setattr(backup, "_compress", slow)
    started = time.monotonic()
    r = client.post("/api/backup/run")
    assert r.status_code == 202 and time.monotonic() - started < 2
    job_id = r.json()["job"]["id"]
    assert reached.wait(10)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "running" and job["label"] == "Back-up maken"
    assert job["processed"] == 3 and job["total"] == backup.STEPS and job["message"].startswith("Comprimeren")
    # A second click while it runs: refused, not a second backup
    assert client.post("/api/backup/run").status_code == 409
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    gate.set()
    deadline = time.time() + 20
    while time.time() < deadline and job["status"] not in ("cancelled", "done", "failed"):
        time.sleep(0.05)
        job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "cancelled", job
    assert not backup.running()
    assert backup.list_backups(config.BACKUP_FOLDER) == []
    assert not [p for p in config.BACKUP_FOLDER.iterdir() if p.name.startswith(".")]
