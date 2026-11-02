"""Moving duplicates as one undo run per user action, undo per run, and migrations 6 and 7."""
import hashlib
import os
import sqlite3
import stat

import pytest

from backend.database import Database
from fixtures.vault_builder import STRAND, STRAND_COPIES
from test_foundation import _make_legacy_db as make_legacy_db


def _move(client, groups, run_id=None, folder=None):
    body = {"groups": [{"keep": str(k), "move": [str(p) for p in m]} for k, m in groups]}
    if run_id is not None:
        body["run_id"] = run_id
    if folder is not None:
        body["folder"] = str(folder)
    return client.post("/api/duplicates/move", json=body)


def _state(path):
    st = os.stat(path)
    return (hashlib.sha256(path.read_bytes()).hexdigest(), st.st_mtime_ns,
            getattr(st, "st_birthtime", None), st.st_ino)


def _group(vault, name, data, copies=2, ts=None):
    """An original in library/ and identical copies in RAW/misc/."""
    keep = vault / "library" / f"orig_{name}"
    keep.write_bytes(data)
    dups = []
    for i in range(copies):
        dup = vault / "RAW" / "misc" / f"{i}_{name}"
        dup.write_bytes(data)
        if ts:
            os.utime(dup, (ts + i, ts + i))
        dups.append(dup)
    return keep, dups


def _runs(client):
    return client.get("/api/duplicates/runs").json()["runs"]


def test_batch_in_chunks_is_one_run_and_undo_restores_content_and_dates(client, vault):
    groups = [_group(vault, f"g{i}.jpg", f"inhoud {i}".encode() * 50, ts=1300000000 + i * 10) for i in range(3)]
    before = {p: _state(p) for _, dups in groups for p in dups}

    r = _move(client, groups[:1], folder=vault / "RAW")
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert run_id and len(r.json()["moved"]) == 2 and r.json()["failed"] == []
    r = _move(client, groups[1:], run_id=run_id, folder=vault / "RAW")  # the next chunk of the same action
    assert r.json()["run_id"] == run_id and len(r.json()["moved"]) == 4

    [run] = _runs(client)
    assert run["run_id"] == run_id and run["total_files"] == 6 and run["remaining"] == 6
    assert run["restorable"] == 6 and run["folder"] == str(vault / "RAW") and run["created_at"]
    assert not any(p.exists() for p in before)
    assert all(keep.exists() for keep, _ in groups)

    r = client.post("/api/duplicates/undo", json={"run_id": run_id})
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == 6 and r.json()["errors"] == [] and r.json()["run"] is None
    for path, state in before.items():
        assert _state(path) == state, path  # same bytes, mtime, birthtime and inode
    assert _runs(client) == []
    assert client.post("/api/duplicates/undo", json={"run_id": run_id}).status_code == 404


def test_per_group_override_keeps_the_copy_the_user_chose(client, vault):
    scan = client.post("/api/duplicates/scan_folder", params={"folder_path": str(vault / "RAW")}).json()
    [group] = [g for g in scan["groups"] if str(vault / STRAND) in {c["path"] for c in g["copies"]}]
    default = group["copies"][group["keep"]]["path"]
    chosen = next(c["path"] for c in group["copies"] if c["path"] != default)
    others = [c["path"] for c in group["copies"] if c["path"] != chosen]

    r = _move(client, [(chosen, others)])
    assert r.status_code == 200 and len(r.json()["moved"]) == len(others) and not r.json()["failed"]
    assert os.path.exists(chosen)
    assert not any(os.path.exists(p) for p in others)
    assert {m["keep"] for m in r.json()["moved"]} == {chosen}


def test_byte_mismatch_is_refused_and_creates_no_run(client, vault):
    keep, [dup] = _group(vault, "m.jpg", b"precies gelijk" * 40, copies=1)
    data = bytearray(keep.read_bytes())
    data[-1] ^= 0xFF  # same size, one byte different: what a stale scan would still call identical
    keep.write_bytes(bytes(data))

    r = _move(client, [(keep, [dup])])
    assert r.status_code == 200
    assert r.json()["run_id"] is None and r.json()["moved"] == []
    [failed] = r.json()["failed"]
    assert failed["status"] == 409 and "identical" in failed["detail"]
    assert dup.exists() and _runs(client) == []


def test_a_failed_action_never_blocks_undo_of_an_earlier_one(client, vault):
    """SECURITY-8 / SCAN-DUP-DB-4 / LIVE-PROBE-8: an empty run made every older run unreachable."""
    keep, [dup] = _group(vault, "a.jpg", b"eerste" * 40, copies=1)
    first = _move(client, [(keep, [dup])]).json()["run_id"]
    gone = vault / "RAW" / "misc" / "bestaat niet.jpg"
    r = _move(client, [(keep, [gone]), (vault / "nergens.jpg", [dup])])
    assert r.json()["run_id"] is None and len(r.json()["failed"]) == 2
    assert [run["run_id"] for run in _runs(client)] == [first]

    r = client.post("/api/raw_duplicates/undo_last")
    assert r.status_code == 200 and r.json()["run_id"] == first and r.json()["restored"] == 1
    assert dup.exists()


def test_legacy_empty_runs_are_never_listed_and_never_block_undo(client, vault, config):
    keep, [dup] = _group(vault, "e.jpg", b"leeg" * 40, copies=1)
    run_id = _move(client, [(keep, [dup])]).json()["run_id"]
    with sqlite3.connect(str(config.DB_PATH)) as conn:  # what the old code left behind after a failed move
        conn.execute("INSERT INTO delete_runs (run_id, timestamp, total_files) VALUES ('leeg', '2099-01-01', 0)")
    assert [run["run_id"] for run in _runs(client)] == [run_id]
    assert client.post("/api/duplicates/undo", json={"run_id": "leeg"}).status_code == 404
    assert client.post("/api/raw_duplicates/undo_last").json()["run_id"] == run_id


def test_newest_run_is_by_sequence_not_by_clock(client, vault, monkeypatch):
    """GAP-5: after the switch to winter time the newer run had the 'older' local timestamp."""
    from backend import database
    k1, [d1] = _group(vault, "z1.jpg", b"zomertijd" * 40, copies=1)
    k2, [d2] = _group(vault, "z2.jpg", b"wintertijd" * 40, copies=1)
    first = _move(client, [(k1, [d1])]).json()["run_id"]

    class Earlier(database.datetime):
        @classmethod
        def now(cls, tz=None):
            return database.datetime(2000, 1, 1, 2, 10)

    monkeypatch.setattr(database, "datetime", Earlier)
    monkeypatch.setattr(database.time, "time", lambda: 946689000.0)
    second = _move(client, [(k2, [d2])]).json()["run_id"]
    monkeypatch.undo()

    assert [run["run_id"] for run in _runs(client)] == [second, first]
    r = client.post("/api/raw_duplicates/undo_last").json()
    assert r["run_id"] == second and d2.exists() and not d1.exists()


def test_undo_never_overwrites_a_file_at_the_original_place(client, vault):
    keep, [dup] = _group(vault, "o.jpg", b"origineel" * 40, copies=1)
    run_id = _move(client, [(keep, [dup])]).json()["run_id"]
    dup.write_bytes(b"NIEUW BESTAND")  # something new took the original place
    r = client.post("/api/duplicates/undo", json={"run_id": run_id}).json()
    assert r["restored"] == 1
    assert dup.read_bytes() == b"NIEUW BESTAND"
    restored = dup.with_name("0_o_RESTORED_001.jpg")
    assert r["files"] == [{"src_path": str(dup), "restored_path": str(restored)}]
    assert restored.read_bytes() == b"origineel" * 40


def test_undo_of_a_run_whose_files_are_gone_is_410_and_keeps_the_record(client, vault, config):
    keep, [dup] = _group(vault, "g.jpg", b"weg" * 40, copies=1)
    r = _move(client, [(keep, [dup])]).json()
    moved = r["moved"][0]["dst_path"]
    elsewhere = vault / "library" / "handmatig.jpg"
    os.rename(moved, elsewhere)
    assert client.post("/api/duplicates/undo", json={"run_id": r["run_id"]}).status_code == 410
    [run] = _runs(client)
    assert run["restorable"] == 0 and run["remaining"] == 1
    os.rename(elsewhere, moved)
    assert client.post("/api/duplicates/undo", json={"run_id": r["run_id"]}).json()["restored"] == 1


def test_move_body_is_validated(client, vault):
    keep = str(vault / STRAND)
    dup = str(vault / STRAND_COPIES[0])
    bad = [
        [], {"groups": []}, {"groups": "x"}, {"groups": [keep]},
        {"groups": [{"keep": 1, "move": [dup]}]}, {"groups": [{"keep": keep, "move": dup}]},
        {"groups": [{"keep": keep, "move": []}]}, {"groups": [{"keep": keep, "move": [None]}]},
        {"groups": [{"keep": keep, "move": [dup]}], "run_id": 5},
        {"groups": [{"keep": keep, "move": [dup]}], "folder": ["x"]},
        {"groups": [{"keep": keep, "move": [dup] * 201}]},
    ]
    for body in bad:
        assert client.post("/api/duplicates/move", json=body).status_code == 400, body
    for body in ({}, {"run_id": 3}, []):
        assert client.post("/api/duplicates/undo", json=body).status_code == 400, body
    assert client.post("/api/duplicates/undo", json={"run_id": "onbekend"}).status_code == 404
    assert os.path.exists(dup)


def test_move_refuses_paths_outside_the_vault_and_protected_folders(client, vault, config):
    keep = vault / STRAND
    outside = vault.parent / f"{vault.name}_outside" / "secret.jpg"
    trash = config.TRASH_FOLDER / "x.jpg"
    trash.parent.mkdir(exist_ok=True)
    trash.write_bytes(keep.read_bytes())
    r = _move(client, [(keep, [outside, trash, vault / "RAW"]), (outside, [vault / STRAND_COPIES[0]])]).json()
    assert [f["status"] for f in r["failed"]] == [403, 400, 400, 403]
    assert r["moved"] == [] and trash.exists() and (vault / STRAND_COPIES[0]).exists()


def test_a_hidden_copy_never_stays_in_place_of_a_visible_one(client, vault):
    """The UI never offers a hidden keeper, but the server must refuse one too."""
    hidden = vault / "RAW" / "misc" / ".hidden.jpg"
    visible = vault / STRAND
    assert hidden.read_bytes() == visible.read_bytes()
    r = _move(client, [(hidden, [visible])])
    assert r.status_code == 200 and r.json()["run_id"] is None and r.json()["moved"] == []
    [failed] = r.json()["failed"]
    assert failed["status"] == 409 and "verborgen" in failed["detail"]

    in_dot_folder = vault / "RAW" / ".verstopt" / "kopie.jpg"
    in_dot_folder.parent.mkdir()
    in_dot_folder.write_bytes(visible.read_bytes())
    assert _move(client, [(in_dot_folder, [visible])]).json()["failed"][0]["status"] == 409
    assert client.post("/api/duplicates/delete_single", params={
        "file_path": str(visible), "duplicate_of": str(hidden)}).status_code == 409
    assert visible.exists() and _runs(client) == []

    if hasattr(stat, "UF_HIDDEN") and hasattr(os, "chflags"):
        flagged = vault / "RAW" / "misc" / "vlag.jpg"
        flagged.write_bytes(visible.read_bytes())
        os.chflags(flagged, stat.UF_HIDDEN)
        assert _move(client, [(flagged, [visible])]).json()["failed"][0]["status"] == 409
        assert visible.exists()

    # The other way round is fine: a hidden copy may go while the visible one stays
    r = _move(client, [(visible, [hidden])]).json()
    assert len(r["moved"]) == 1 and visible.exists() and not hidden.exists()


@pytest.fixture
def unreadable():
    """chmod 000 a file for the test, readable again afterwards so tmp cleanup works."""
    made = []

    def make(path):
        os.chmod(path, 0)
        made.append(path)
        return path

    yield make
    for path in made:
        if os.path.lexists(path):
            os.chmod(path, 0o644)


def test_an_unreadable_copy_refuses_only_that_file(client, vault, unreadable):
    """DUP-MOVE-OSERROR-500: one unreadable copy aborted the whole request with a 500."""
    keep, dups = _group(vault, "lees.jpg", b"leesbaar" * 40, copies=3)
    unreadable(dups[1])
    if os.access(dups[1], os.R_OK):
        pytest.skip("running as a user that can read mode 000 files")

    r = _move(client, [(keep, dups)])
    assert r.status_code == 200, r.text
    assert [m["path"] for m in r.json()["moved"]] == [str(dups[0]), str(dups[2])]
    [failed] = r.json()["failed"]
    assert failed["path"] == str(dups[1]) and failed["status"] == 409
    assert "Kon het bestand niet lezen" in failed["detail"] and "niets verplaatst" in failed["detail"]
    assert dups[1].exists() and not dups[0].exists() and not dups[2].exists()
    [run] = _runs(client)
    assert run["run_id"] == r.json()["run_id"] and run["total_files"] == 2


def test_an_unreadable_keeper_refuses_its_group_and_the_rest_still_runs(client, vault, unreadable):
    k1, [d1] = _group(vault, "k1.jpg", b"bewaard een" * 40, copies=1)
    k2, [d2] = _group(vault, "k2.jpg", b"bewaard twee" * 40, copies=1)
    unreadable(k1)
    if os.access(k1, os.R_OK):
        pytest.skip("running as a user that can read mode 000 files")

    r = _move(client, [(k1, [d1]), (k2, [d2])])
    assert r.status_code == 200, r.text
    assert [f["status"] for f in r.json()["failed"]] == [409] and r.json()["failed"][0]["path"] == str(d1)
    assert [m["path"] for m in r.json()["moved"]] == [str(d2)]
    assert d1.exists() and k1.exists()
    r = client.post("/api/duplicates/delete_single", params={"file_path": str(d1), "duplicate_of": str(k1)})
    assert r.status_code == 409 and "niet lezen" in r.json()["detail"]


def test_a_read_error_or_a_vanished_file_is_a_per_file_refusal(client, vault, monkeypatch):
    import errno
    from backend.routes import duplicates
    keep, dups = _group(vault, "io.jpg", b"leesfout" * 40, copies=3)
    real = duplicates.files_identical
    calls = []

    def flaky(a, b):
        calls.append(a)
        if len(calls) == 1:
            raise OSError(errno.EIO, "Input/output error")
        if len(calls) == 2:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory")
        return real(a, b)

    monkeypatch.setattr(duplicates, "files_identical", flaky)
    r = _move(client, [(keep, dups)])
    assert r.status_code == 200, r.text
    assert [(f["status"], f["path"]) for f in r.json()["failed"]] == [(409, str(dups[0])), (409, str(dups[1]))]
    assert "Input/output error" in r.json()["failed"][0]["detail"]
    assert [m["path"] for m in r.json()["moved"]] == [str(dups[2])]
    assert dups[0].exists() and dups[1].exists()


def test_a_disk_unplugged_mid_batch_answers_what_was_moved(client, vault, monkeypatch):
    import errno
    from backend.routes import duplicates
    keep, dups = _group(vault, "weg.jpg", b"halverwege" * 40, copies=2)
    away = vault.with_name("vault_unplugged")
    real = duplicates.files_identical
    calls = []

    def unplug_on_second(a, b):
        calls.append(a)
        if len(calls) == 2:
            os.rename(vault, away)
            raise OSError(errno.ENXIO, "Device not configured")
        return real(a, b)

    monkeypatch.setattr(duplicates, "files_identical", unplug_on_second)
    try:
        r = _move(client, [(keep, dups)])
    finally:
        if away.exists():
            os.rename(away, vault)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert run_id and [m["path"] for m in r.json()["moved"]] == [str(dups[0])]
    [failed] = r.json()["failed"]
    assert failed["status"] == 503 and failed["detail"] == "Archief niet bereikbaar (schijf losgekoppeld?)"
    monkeypatch.undo()
    assert client.post("/api/duplicates/undo", json={"run_id": run_id}).json()["restored"] == 1
    assert dups[0].exists() and dups[1].exists()


def test_a_failed_undo_record_puts_the_file_back(client, vault, monkeypatch):
    from backend.routes import duplicates
    keep, [dup] = _group(vault, "r.jpg", b"record" * 40, copies=1)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(duplicates.db, "record_delete_operation", broken)
    r = _move(client, [(keep, [dup])]).json()
    assert r["failed"][0]["status"] == 500 and r["moved"] == []
    assert dup.read_bytes() == b"record" * 40
    assert not any((vault / "RAW_DUPLICATES").rglob("*r.jpg"))


def test_a_run_undone_between_chunks_starts_a_new_run(client, vault):
    k1, [d1] = _group(vault, "c1.jpg", b"chunk een" * 40, copies=1)
    k2, [d2] = _group(vault, "c2.jpg", b"chunk twee" * 40, copies=1)
    run_id = _move(client, [(k1, [d1])]).json()["run_id"]
    client.post("/api/duplicates/undo", json={"run_id": run_id})
    r = _move(client, [(k2, [d2])], run_id=run_id).json()
    assert r["run_id"] and r["run_id"] != run_id
    assert [run["run_id"] for run in _runs(client)] == [r["run_id"]]


def test_unplugged_vault_answers_503_and_undo_works_once_it_is_back(client, vault):
    """DUP-UNPLUGGED-500: the database lives on the vault disk; unplugged gave an SQLite 500."""
    keep, [dup] = _group(vault, "u.jpg", b"losgekoppeld" * 40, copies=1)
    run_id = _move(client, [(keep, [dup])]).json()["run_id"]
    away = vault.with_name("vault_unplugged")
    os.rename(vault, away)
    try:
        for response in (
            client.get("/api/duplicates/runs"),
            client.post("/api/duplicates/undo", json={"run_id": run_id}),
            client.post("/api/raw_duplicates/undo_last"),
            client.get("/api/keurer/history"),
        ):
            assert response.status_code == 503, response.text
            assert response.json()["detail"] == "Archief niet bereikbaar (schijf losgekoppeld?)"
    finally:
        os.rename(away, vault)
    r = client.post("/api/duplicates/undo", json={"run_id": run_id})
    assert r.status_code == 200 and r.json()["restored"] == 1
    assert dup.read_bytes() == b"losgekoppeld" * 40


def test_other_database_errors_stay_a_500(client, vault, monkeypatch):
    from backend.routes import duplicates

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: delete_runs")

    monkeypatch.setattr(duplicates.db, "get_delete_runs", broken)
    r = client.get("/api/duplicates/runs")
    assert r.status_code == 500 and "no such table" in r.json()["detail"]


# ==================== MIGRATIONS 6 AND 7 ====================

def _migrate_to(db_path, version, monkeypatch):
    from backend import migrations
    monkeypatch.setattr(migrations, "MIGRATIONS", [m for m in migrations.MIGRATIONS if m[0] <= version])
    migrations.migrate(db_path)
    monkeypatch.undo()


def test_migration_6_tags_the_hash_cache_and_keeps_all_user_data(tmp_path, monkeypatch):
    from backend import migrations
    db_path = tmp_path / "datavault.db"
    make_legacy_db(db_path)
    _migrate_to(db_path, 5, monkeypatch)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        keurer_before = conn.execute("SELECT * FROM keurer_decisions ORDER BY id").fetchall()

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION
        columns = {row[1] for row in conn.execute("PRAGMA table_info(file_hashes)")}
        assert {"hash", "algo", "ino", "mtime_ns", "ctime_ns"} <= columns and "sha256" not in columns
        assert conn.execute("SELECT path, size, mtime, hash, algo FROM file_hashes").fetchall() == [
            ("/v/RAW/a.jpg", 10, 1.5, "abc", None)]
        assert conn.execute("SELECT * FROM keurer_decisions ORDER BY id").fetchall() == keurer_before
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v5-*.db"))) == 1
    # The untagged row is never used as a hash ('unknown' algorithm), so the file is rehashed
    assert db.get_cached_hashes("/v/RAW", "xxh3_128") == {}
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"


def test_migration_7_orders_runs_by_insertion_and_keeps_every_run(tmp_path, monkeypatch):
    from backend import migrations
    db_path = tmp_path / "datavault.db"
    make_legacy_db(db_path)
    _migrate_to(db_path, 6, monkeypatch)
    with sqlite3.connect(str(db_path)) as conn:
        # Two later runs whose local timestamps went backwards (winter time), and an empty one
        conn.execute("INSERT INTO delete_runs (run_id, timestamp, total_files) VALUES ('run-2', '2026-10-25 02:50:00.5', 1)")
        conn.execute("INSERT INTO delete_runs (run_id, timestamp, total_files) VALUES ('run-3', '2026-10-25 02:10:00', 1)")
        conn.execute("INSERT INTO delete_runs (run_id, timestamp, total_files) VALUES ('run-leeg', '2026-10-25 02:20:00', 0)")
        for run in ("run-2", "run-3"):
            conn.execute("INSERT INTO delete_operations (run_id, src_path, dst_path) VALUES (?, ?, ?)",
                         (run, f"/v/RAW/{run}.jpg", f"/v/RAW_DUPLICATES/{run}.jpg"))
        runs_before = conn.execute("SELECT run_id, timestamp, total_files FROM delete_runs ORDER BY rowid").fetchall()
        ops_before = conn.execute("SELECT id, run_id, src_path, dst_path, timestamp FROM delete_operations ORDER BY id").fetchall()
        keurer_before = conn.execute("SELECT * FROM keurer_decisions ORDER BY id").fetchall()

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION
        assert conn.execute("SELECT run_id, timestamp, total_files FROM delete_runs ORDER BY seq").fetchall() == runs_before
        assert conn.execute("SELECT id, run_id, src_path, dst_path, timestamp FROM delete_operations ORDER BY id").fetchall() == ops_before
        assert conn.execute("SELECT * FROM keurer_decisions ORDER BY id").fetchall() == keurer_before
        assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'delete_runs'").fetchone()[0] == 4
        created = conn.execute("SELECT created_at FROM delete_runs WHERE run_id = 'run-2'").fetchone()[0]
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'idx_delops_run'").fetchone()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert created is not None
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v6-*.db"))) == 1

    # Newest by insertion, not by the text timestamp; the empty run is never listed
    assert [r["run_id"] for r in db.get_delete_runs()] == ["run-3", "run-2", "run-1"]
    assert db.get_last_delete_run()["run_id"] == "run-3"
    new = db.record_delete_operation(None, "/v/RAW/n.jpg", "/v/RAW_DUPLICATES/n.jpg")
    assert db.get_delete_runs()[0]["run_id"] == new and db.get_delete_runs()[0]["seq"] == 5
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3


def test_migration_7_never_reuses_a_sequence_number(tmp_path):
    db = Database(tmp_path / "datavault.db")
    a = db.record_delete_operation(None, "/v/a.jpg", "/v/RAW_DUPLICATES/a.jpg")
    [op] = db.get_delete_operations(a)
    db.delete_delete_operation(op["id"])  # undone: the run is gone
    b = db.record_delete_operation(None, "/v/b.jpg", "/v/RAW_DUPLICATES/b.jpg")
    assert db.get_delete_run(b)["seq"] == 2
