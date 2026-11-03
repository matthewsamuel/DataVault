"""SQLite layer: one connection per thread, WAL, busy_timeout, no transaction left open (PERF-2, SCAN-DUP-DB-13)."""
import os
import shutil
import sqlite3
import threading

import pytest

from backend.database import Database, BUSY_TIMEOUT_MS


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "cache" / "datavault.db")
    yield database
    database.close_all()


def test_wal_mode_and_busy_timeout(db):
    with db._connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
    # Persistent in the file, so a plain connection (e.g. a backup tool) sees WAL too
    other = sqlite3.connect(str(db.db_path))
    assert other.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    other.close()


def test_one_connection_per_thread_reused_across_calls(db):
    first = db._thread_connection()
    db.update_rating("/v/a.jpg", 1010)
    db.get_all_ratings()
    assert db._thread_connection() is first

    seen = []
    thread = threading.Thread(target=lambda: seen.append(db._thread_connection()))
    thread.start()
    thread.join()
    assert seen[0] is not first

    # A thread that has ended gives its connection back the next time one is opened
    def open_one():
        db._thread_connection()
    thread = threading.Thread(target=open_one)
    thread.start()
    thread.join()
    assert any(t is thread for t, _ in db._open.values())
    db.release_thread()
    db._thread_connection()
    assert not any(t is thread for t, _ in db._open.values())


def test_an_error_rolls_back_and_releases_the_write_lock(db):
    with pytest.raises(RuntimeError):
        with db._connect() as conn:
            conn.execute("INSERT INTO file_ratings (path, rating) VALUES ('/v/x.jpg', 1)")
            raise RuntimeError("halverwege")
    assert db.get_all_ratings() == {}
    # Another connection can write at once: nothing is holding the lock
    other = sqlite3.connect(str(db.db_path), timeout=0)
    with other:
        other.execute("INSERT INTO file_ratings (path, rating) VALUES ('/v/y.jpg', 2)")
    other.close()
    assert db.get_all_ratings() == {"/v/y.jpg": 2}


def test_writers_on_many_threads_queue_instead_of_failing(db):
    errors = []

    def write(n):
        try:
            for i in range(50):
                db.update_rating(f"/v/{n}-{i}.jpg", i)
        except Exception as e:  # pragma: no cover - this is the failure being tested for
            errors.append(e)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(db.get_all_ratings()) == 400


def test_unplugged_database_is_an_operational_error_and_a_new_file_gets_a_new_connection(db, tmp_path):
    db.update_rating("/v/a.jpg", 1100)
    cache = db.db_path.parent
    away = tmp_path / "unplugged"
    os.rename(cache, away)
    try:
        # The open connection would still reach the renamed file; it must not be used
        with pytest.raises(sqlite3.OperationalError):
            db.get_all_ratings()
    finally:
        os.rename(away, cache)
    assert db.get_all_ratings() == {"/v/a.jpg": 1100}

    # 'Mounted again' as another file (another inode): a fresh connection reads that file
    db.close_all()
    copy = tmp_path / "copy.db"
    src = sqlite3.connect(str(db.db_path))
    dst = sqlite3.connect(str(copy))
    src.backup(dst)
    with dst:
        dst.execute("UPDATE file_ratings SET rating = 1200")
    src.close()
    dst.close()
    old = db._thread_connection()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(str(db.db_path) + suffix)  # the app's own database, never an archive file
        except FileNotFoundError:
            pass
    shutil.copy(copy, db.db_path)
    assert db.get_all_ratings() == {"/v/a.jpg": 1200}
    assert db._thread_connection() is not old
