"""
Undo of a duplicates run checks that the file in RAW_DUPLICATES is still the one that was
moved there: same inode and size as recorded right after the move (migration 171). A file
that was changed or swapped since stays where it is, recorded, with its reason in the
Herstelcentrum. Runs from before migration 171 (no inode and size) keep the old checks.
"""
import os
import sqlite3
import time

from backend.database import Database
from test_duplicates_runs import _group, _move, _runs, _migrate_to
from test_foundation import _make_legacy_db


def _moved(client, vault, name, data):
    keep, dups = _group(vault, name, data, copies=1)
    r = _move(client, [(keep, dups)])
    assert r.status_code == 200, r.text
    [moved] = r.json()["moved"]
    return r.json()["run_id"], dups[0], moved["dst_path"]


def _recovery_entry(client, run_id):
    entries = client.get("/api/recovery").json()["entries"]
    return next(e for e in entries if e["key"] == f"duplicates:{run_id}")


def test_a_move_records_inode_and_size_of_the_moved_file(client, vault, app):
    run_id, src, dst = _moved(client, vault, "a.jpg", b"inhoud a" * 40)
    [op] = app.state.db.get_delete_operations(run_id)
    st = os.lstat(dst)
    assert (op["dst_ino"], op["dst_size"]) == (st.st_ino, st.st_size) == (op["dst_ino"], 320)


def test_a_swapped_file_is_not_restored_and_the_reason_is_shown(client, vault, app):
    run_id, src, dst = _moved(client, vault, "b.jpg", b"inhoud b" * 40)
    # Someone moved the file away and put another one, of the same size, under its name
    aside = dst + ".weg"
    os.rename(dst, aside)
    with open(dst, "wb") as f:
        f.write(b"ANDERE b" * 40)

    [run] = _runs(client)
    assert (run["restorable"], run["changed"], run["remaining"]) == (0, 1, 1)
    entry = _recovery_entry(client, run_id)
    assert not entry["undoable"] and entry["files"][0]["state"] == "other"
    assert "ander bestand" in entry["reason"] and "b.jpg" in entry["reason"]

    r = client.post("/api/duplicates/undo", json={"run_id": run_id})
    assert r.status_code == 410
    assert "veranderd of vervangen" in r.json()["error"]["message"]
    assert not src.exists()
    assert open(dst, "rb").read() == b"ANDERE b" * 40 and os.path.exists(aside)
    assert app.state.db.get_delete_run(run_id)["remaining"] == 1      # still recorded
    # undo_last passes it over as well
    assert client.post("/api/raw_duplicates/undo_last").status_code == 404
    assert not src.exists()

    # Once the moved file is back under its name, the undo works again
    os.rename(dst, dst + ".ander")
    os.rename(aside, dst)
    r = client.post("/api/duplicates/undo", json={"run_id": run_id})
    assert r.status_code == 200 and r.json()["restored"] == 1 and r.json()["changed"] == []
    assert src.read_bytes() == b"inhoud b" * 40


def test_a_file_changed_in_place_is_not_restored(client, vault):
    run_id, src, dst = _moved(client, vault, "c.jpg", b"inhoud c" * 40)
    ino = os.lstat(dst).st_ino
    with open(dst, "ab") as f:
        f.write(b"erbij")
    assert os.lstat(dst).st_ino == ino

    entry = _recovery_entry(client, run_id)
    assert entry["files"][0]["state"] == "changed" and not entry["undoable"]
    assert "veranderd" in entry["files"][0]["reason"] or "veranderd" in entry["reason"]
    assert client.post("/api/duplicates/undo", json={"run_id": run_id}).status_code == 410
    assert not src.exists() and os.path.getsize(dst) == 325


def test_a_mixed_run_restores_the_unchanged_files_and_reports_the_rest(client, vault, app):
    groups = [_group(vault, f"m{i}.jpg", f"mix {i}".encode() * 60, copies=1) for i in range(2)]
    r = _move(client, groups)
    run_id = r.json()["run_id"]
    moved = {m["src_path"]: m["dst_path"] for m in r.json()["moved"]}
    changed_src = str(groups[0][1][0])
    with open(moved[changed_src], "ab") as f:
        f.write(b"!")

    [run] = _runs(client)
    assert (run["restorable"], run["changed"]) == (1, 1)
    entry = _recovery_entry(client, run_id)
    assert entry["undoable"] and entry["restorable"] == 1
    assert {f["state"] for f in entry["files"]} == {"ok", "changed"}

    r = client.post("/api/duplicates/undo", json={"run_id": run_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"] == 1 and body["skipped"] == 1
    [changed] = body["changed"]
    assert changed["src_path"] == changed_src and changed["state"] == "changed"
    assert "niet teruggezet" in changed["reason"]
    assert groups[1][1][0].exists() and not groups[0][1][0].exists()
    assert body["run"]["remaining"] == 1                       # the changed one stays recorded
    entry = _recovery_entry(client, run_id)
    assert entry["files"][0]["state"] == "changed" and entry["reason"]


def test_a_run_from_before_migration_171_keeps_the_old_checks(client, vault, config, app):
    """No inode and size recorded: a changed file still goes back (never over another file)."""
    config.ensure_duplicates_folder()
    src = vault / "RAW" / "misc" / "oud.jpg"
    dst = config.DUPLICATES_FOLDER / "misc" / "oud.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"oud" * 30)
    run_id = app.state.db.record_delete_operation(None, src, dst)
    [op] = app.state.db.get_delete_operations(run_id)
    assert op["dst_ino"] is None and op["dst_size"] is None
    with open(dst, "ab") as f:
        f.write(b"later")

    [run] = _runs(client)
    assert (run["restorable"], run["changed"]) == (1, 0)
    assert _recovery_entry(client, run_id)["files"][0]["state"] == "ok"
    r = client.post("/api/duplicates/undo", json={"run_id": run_id})
    assert r.status_code == 200 and r.json()["restored"] == 1 and r.json()["changed"] == []
    assert src.read_bytes() == b"oud" * 30 + b"later"


def test_migration_171_adds_the_columns_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 170, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        assert 171 not in migrations.get_applied_versions(conn)
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO recovery_actions (kind, title, created_at) VALUES ('viewer_move', 'x', 1)")
        conn.execute("INSERT INTO elo_comparisons (path_a, path_b, winner, rating_a_before, rating_a_after, "
                     "rating_b_before, rating_b_after, games_a_before, games_b_before, existed_a, existed_b, "
                     "k_a, k_b, scope, created_at) VALUES ('/v/a.jpg', '/v/b.jpg', 'a', 1000, 1016, 1000, 984, "
                     "0, 0, 1, 1, 32, 32, '/v', 1.0)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    assert before["delete_operations"] and before["file_ratings"] and before["keurer_decisions"]

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert 171 in migrations.get_applied_versions(conn)
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 171
        for table, rows in before.items():
            now_rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            if table == "delete_operations":
                now_rows = [row[:-2] for row in now_rows]            # the two new columns, NULL
                assert all(row[-2:] == (None, None) for row in conn.execute(
                    "SELECT * FROM delete_operations"))
            assert now_rows == rows, table
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    [op] = db.get_delete_operations("run-1")
    assert op["dst_path"] == "/v/RAW_DUPLICATES/b.jpg" and op["dst_ino"] is None
    # New moves are recorded with their fingerprint
    run = db.record_delete_operation(None, "/v/RAW/c.jpg", "/v/RAW_DUPLICATES/c.jpg", dst_ino=5, dst_size=9)
    assert db.get_delete_operations(run)[0]["dst_ino"] == 5
    db.close_all()
