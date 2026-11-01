"""
Recovery center (Herstelcentrum): one list of every action that moved archive files, each
with its undo, and the shared recorder later features use (backend/recovery.py).
"""
import os
import sqlite3
import time

import pytest

from backend.database import Database

INBOX = "RAW/inbox"


@pytest.fixture(autouse=True)
def inbox(vault):
    """Five small photos to move around: IMG_0001.JPG .. IMG_0005.JPG."""
    folder = vault / INBOX
    folder.mkdir()
    for i in range(1, 6):
        (folder / f"IMG_{i:04d}.JPG").write_bytes(b"\xff\xd8\xff\xe0" + f"foto {i}".encode() * 40)
    return folder


def _list(client, **params):
    r = client.get("/api/recovery", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _undo(client, entry):
    return client.post(entry["undo"]["url"], json=entry["undo"]["body"])


def _move(client, src, dest_folder):
    r = client.post("/api/move-file", json={"source_path": str(src), "destination_folder": str(dest_folder)})
    assert r.status_code == 200, r.text
    return r.json()


def _decide(client, path, decision, **extra):
    r = client.post("/api/keurer/decide", json={"path": str(path), "folder": str(path.parent),
                                                "decision": decision, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_move_in_the_viewer_is_listed_and_undone(client, vault):
    src = vault / INBOX / "IMG_0002.JPG"
    data = src.read_bytes()
    ino = src.stat().st_ino
    moved = _move(client, src, vault / "library")
    assert moved["message"].startswith("Verplaatst naar library/")

    [entry] = _list(client)["entries"]
    assert entry["key"].startswith("action:") and entry["source"] == "viewer_move"
    assert entry["label"] == "Verplaatst in de viewer" and entry["title"] == "Verplaatst naar library"
    assert entry["undoable"] and entry["restorable"] == 1 and entry["reason"] is None
    assert entry["files"] == [{"from": str(src), "to": moved["new_path"], "name": "IMG_0002.JPG",
                               "state": "ok", "restorable": True, "reason": None}]
    assert entry["undo"]["url"] == "/api/recovery/undo"
    assert abs(entry["created_at"] - time.time()) < 60

    r = _undo(client, entry)
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == 1 and r.json()["skipped"] == [] and r.json()["remaining"] == 0
    assert src.read_bytes() == data and src.stat().st_ino == ino   # moved back, not copied
    assert not os.path.exists(moved["new_path"])
    assert _list(client)["entries"] == []
    assert _undo(client, entry).status_code == 404


def test_undo_never_overwrites_and_never_takes_back_another_file(client, vault):
    a = vault / INBOX / "IMG_0002.JPG"
    b = vault / INBOX / "IMG_0003.JPG"
    c = vault / INBOX / "IMG_0004.JPG"
    a_moved = _move(client, a, vault / "library")["new_path"]
    b_moved = _move(client, b, vault / "library")["new_path"]
    c_moved = _move(client, c, vault / "library")["new_path"]

    a.write_bytes(b"nieuw bestand op de oude plek")          # origin occupied
    os.rename(b_moved, vault / "RAW" / "elders.jpg")          # gone from where it was put
    os.rename(c_moved, vault / "RAW" / "c.jpg")               # ... and another file with its name
    with open(c_moved, "wb") as f:
        f.write(b"ander bestand")

    entries = {e["files"][0]["name"]: e for e in _list(client)["entries"]}
    assert [f["state"] for f in entries["IMG_0002.JPG"]["files"]] == ["occupied"]
    assert entries["IMG_0002.JPG"]["reason"] == (
        "Op de oorspronkelijke plek staat al een bestand IMG_0002.JPG; er wordt niets overschreven")
    assert entries["IMG_0003.JPG"]["reason"] == "IMG_0003.JPG staat niet meer in de map library"
    assert entries["IMG_0004.JPG"]["reason"] == "In de map library staat nu een ander bestand met de naam IMG_0004.JPG"
    for entry in entries.values():
        assert not entry["undoable"]
        r = _undo(client, entry)
        assert r.status_code == 409 and r.json()["error"]["message"] == entry["reason"]
    assert a.read_bytes() == b"nieuw bestand op de oude plek"
    assert open(c_moved, "rb").read() == b"ander bestand" and not c.exists()

    # Once the origin is free again, the first one can go back
    os.rename(a, vault / "RAW" / "opzij.jpg")
    assert _undo(client, entries["IMG_0002.JPG"]).status_code == 200
    assert a.exists() and not os.path.exists(a_moved)


def test_keurer_and_duplicate_actions_are_listed_newest_first_with_their_own_undo(client, vault, config, inbox):
    trashed = _decide(client, inbox / "IMG_0002.JPG", "delete")
    kept = _decide(client, inbox / "IMG_0003.JPG", "keep")
    moved = _decide(client, inbox / "IMG_0004.JPG", "move", destination_folder=str(vault / "library"))
    keep = vault / "library" / "orig.jpg"
    keep.write_bytes(b"dubbel" * 100)
    dups = []
    for i in range(3):
        dup = vault / "RAW" / "misc" / f"{i}_dup.jpg"
        dup.write_bytes(b"dubbel" * 100)
        dups.append(dup)
    r = client.post("/api/duplicates/move", json={"groups": [{"keep": str(keep), "move": [str(d) for d in dups]}],
                                                  "folder": str(vault / "RAW")})
    run_id = r.json()["run_id"]
    _move(client, vault / "RAW" / "misc" / "UPPER.JPEG", vault / "library")
    # Same clock second for the Keurer rows (CURRENT_TIMESTAMP): make the order explicit
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("UPDATE keurer_decisions SET decided_at = datetime('now', '-2 minutes') WHERE id = ?", (trashed["id"],))
        conn.execute("UPDATE keurer_decisions SET decided_at = datetime('now', '-1 minutes') WHERE id = ?", (moved["id"],))

    data = _list(client)
    keys = [e["key"] for e in data["entries"]]
    assert data["total"] == 4 and data["next_offset"] is None
    assert keys[2:] == [f"keurer:{moved['id']}", f"keurer:{trashed['id']}"]   # oldest last
    assert keys[0].startswith("action:") and keys[1] == f"duplicates:{run_id}"   # the viewer move came last
    assert f"keurer:{kept['id']}" not in keys                     # 'keep' moved nothing
    by_key = {e["key"]: e for e in data["entries"]}

    trash_entry = by_key[f"keurer:{trashed['id']}"]
    assert trash_entry["label"] == "Keurer" and trash_entry["title"] == "Naar de prullenmand"
    assert trash_entry["files"][0]["to"] == str(config.TRASH_FOLDER / "IMG_0002.JPG")
    assert trash_entry["undo"] == {"url": "/api/keurer/undo", "body": {"id": trashed["id"]}}
    assert by_key[f"keurer:{moved['id']}"]["title"] == "Verplaatst naar library"

    run_entry = by_key[f"duplicates:{run_id}"]
    assert run_entry["label"] == "Duplicaten" and run_entry["file_count"] == 3 and run_entry["restorable"] == 3
    assert run_entry["title"] == "3 duplicaten naar RAW_DUPLICATES" and run_entry["folder"] == str(vault / "RAW")
    assert run_entry["undo"] == {"url": "/api/duplicates/undo", "body": {"run_id": run_id}}

    # Pages: the same order, a page at a time
    first = _list(client, limit=3)
    assert [e["key"] for e in first["entries"]] == keys[:3] and first["next_offset"] == 3
    assert [e["key"] for e in _list(client, offset=3, limit=3)["entries"]] == keys[3:]

    # Each undo goes through the endpoint that owns the record
    assert _undo(client, trash_entry).status_code == 200
    assert (inbox / "IMG_0002.JPG").exists()
    assert _undo(client, run_entry).status_code == 200
    assert all(d.exists() for d in dups)
    assert {e["key"] for e in _list(client)["entries"]} == set(keys) - {trash_entry["key"], run_entry["key"]}


def test_a_keurer_file_that_is_gone_from_the_trash_says_why(client, vault, config):
    photo = vault / INBOX / "IMG_0002.JPG"
    _decide(client, photo, "delete")
    os.rename(config.TRASH_FOLDER / "IMG_0002.JPG", vault / "RAW" / "ergens.jpg")
    [entry] = _list(client)["entries"]
    assert not entry["undoable"] and entry["reason"] == "IMG_0002.JPG staat niet meer in de prullenmand"
    assert entry["files"][0]["state"] == "missing"
    assert _undo(client, entry).status_code == 410

    photo2 = vault / INBOX / "IMG_0003.JPG"
    _decide(client, photo2, "delete")
    photo2.write_bytes(b"nieuw")
    entry = next(e for e in _list(client)["entries"] if e["files"][0]["name"] == "IMG_0003.JPG")
    assert entry["files"][0]["state"] == "occupied" and not entry["undoable"]
    assert _undo(client, entry).status_code == 409


def test_old_keurer_decisions_without_a_recorded_place_are_left_out(client, vault, config):
    """Before migration 2 a Keurer delete did not record where the file went: it can never go back."""
    _decide(client, vault / INBOX / "IMG_0001.JPG", "delete")
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision) VALUES (?, ?, 'delete')",
                     (str(vault / INBOX / "oud.jpg"), str(vault / INBOX)))
    listed = _list(client)
    assert listed["total"] == 1
    assert [e["files"][0]["name"] for e in listed["entries"]] == ["IMG_0001.JPG"]


def test_a_duplicate_whose_old_place_is_taken_comes_back_beside_it(client, vault):
    keep = vault / "library" / "orig.jpg"
    keep.write_bytes(b"x" * 300)
    dup = vault / "RAW" / "misc" / "kopie.jpg"
    dup.write_bytes(b"x" * 300)
    client.post("/api/duplicates/move", json={"groups": [{"keep": str(keep), "move": [str(dup)]}]})
    dup.write_bytes(b"iets anders")
    [entry] = _list(client)["entries"]
    assert entry["undoable"] and entry["files"][0]["state"] == "beside"
    assert "kopie_RESTORED_001" in entry["files"][0]["reason"]
    assert _undo(client, entry).status_code == 200
    assert dup.read_bytes() == b"iets anders" and (dup.parent / "kopie_RESTORED_001.jpg").read_bytes() == b"x" * 300


def test_a_new_kind_of_action_plugs_in_with_the_recorder(client, vault, app):
    """What steps like near-duplicate 'keep best' or assistant moves do: no table, route or UI of their own."""
    from backend import recovery
    from backend.file_ops import ARCHIVE_LOCK, move_no_overwrite
    recovery.register_kind("test_kind", "Testactie")
    db = app.state.db
    files = [vault / INBOX / name for name in ("IMG_0002.JPG", "IMG_0003.JPG")]
    target = vault / "library"
    with ARCHIVE_LOCK:
        action = recovery.ActionRecorder(db, "test_kind", "Twee foto's opgeruimd", folder=str(vault / INBOX),
                                         details={"reden": "test"})
        moved = []
        for src in files:
            dst = move_no_overwrite(src, target / src.name)
            action.add(src, dst)
            moved.append(dst)
    [entry] = _list(client)["entries"]
    assert entry["label"] == "Testactie" and entry["title"] == "Twee foto's opgeruimd"
    assert entry["file_count"] == 2 and entry["restorable"] == 2 and entry["folder"] == str(vault / INBOX)

    # Partial: one origin is taken; the other goes back, the first stays listed with its reason
    files[0].write_bytes(b"bezet")
    r = _undo(client, entry)
    assert r.status_code == 200
    assert r.json()["restored"] == 1 and r.json()["remaining"] == 1
    assert r.json()["skipped"][0]["name"] == "IMG_0002.JPG"
    assert files[1].exists() and moved[0].exists()
    [left] = _list(client)["entries"]
    assert left["file_count"] == 1 and not left["undoable"] and "staat al een bestand" in left["reason"]


def test_the_undo_of_an_action_checks_every_path_in_full(client, vault, app, monkeypatch):
    """The list checks paths once per folder (FolderChecks); the undo checks each file's own
    paths again, as the README and FolderChecks promise."""
    from backend import recovery
    from backend.file_ops import ARCHIVE_LOCK, move_no_overwrite
    db = app.state.db
    files = [vault / INBOX / f"IMG_{i:04d}.JPG" for i in (1, 2, 3, 4)]
    with ARCHIVE_LOCK:
        action = recovery.ActionRecorder(db, "viewer_move", "Vier foto's")
        for src in files:
            action.add(src, move_no_overwrite(src, vault / "library" / src.name))

    checked = []
    real = recovery.is_in_vault

    def counted(path, *args, **kwargs):
        checked.append(str(path))
        return real(path, *args, **kwargs)
    monkeypatch.setattr(recovery, "is_in_vault", counted)

    [entry] = _list(client)["entries"]
    assert entry["restorable"] == 4 and len(checked) == 2   # one source and one target folder
    checked.clear()
    r = _undo(client, entry)
    assert r.status_code == 200 and r.json()["restored"] == 4
    assert sorted(checked) == sorted([str(f) for f in files] + [str(vault / "library" / f.name) for f in files])
    assert all(f.exists() for f in files)


def test_the_recorder_moves_the_file_back_when_it_cannot_record(client, vault, app, monkeypatch):
    src = vault / INBOX / "IMG_0002.JPG"
    db = app.state.db

    def broken(*args, **kwargs):
        raise sqlite3.IntegrityError("kapot")
    monkeypatch.setattr(db, "record_recovery_move", broken)
    r = client.post("/api/move-file", json={"source_path": str(src), "destination_folder": str(vault / "library")})
    assert r.status_code == 500 and "teruggezet" in r.json()["error"]["message"]
    assert src.exists() and not (vault / "library" / "IMG_0002.JPG").exists()


def test_records_that_point_outside_the_vault_are_refused(client, vault, app, tmp_path):
    outside = tmp_path / "buiten.jpg"
    outside.write_bytes(b"buiten")
    db = app.state.db
    st = os.lstat(outside)
    db.record_recovery_move(None, "viewer_move", "Verdacht", vault / "RAW" / "terug.jpg", outside, st.st_ino, st.st_size)
    [entry] = _list(client)["entries"]
    assert entry["files"][0]["state"] == "refused" and not entry["undoable"]
    assert _undo(client, entry).status_code == 409
    assert outside.exists() and not (vault / "RAW" / "terug.jpg").exists()


def test_bad_requests(client):
    for body in ({}, {"action_id": "1"}, {"action_id": True}, {"action_id": 0}, {"action_id": 2 ** 63}, []):
        assert client.post("/api/recovery/undo", json=body).status_code == 400, body
    assert client.post("/api/recovery/undo", json={"action_id": 12345}).status_code == 404
    assert client.get("/api/recovery", params={"offset": -1}).status_code == 422
    assert _list(client, limit=100000)["limit"] == 200


def test_migration_12_adds_recovery_tables_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 11, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{\"full\": true}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                     "VALUES ('/v/RAW/a.jpg', '/v/RAW', 'a.jpg', 'photo', '.jpg', 10, 1, 1, 1, 1)")
        conn.execute("INSERT INTO media_folders VALUES ('/v/RAW', '/v', 1, 1.0)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    assert before["sqlite_sequence"] and before["jobs"] and before["media"] and before["delete_operations"]

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 12
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        assert {r[1] for r in conn.execute("PRAGMA table_info(recovery_actions)")} == {
            "id", "kind", "title", "folder", "details", "created_at"}
        assert {r[1] for r in conn.execute("PRAGMA table_info(recovery_moves)")} == {
            "id", "action_id", "src_path", "dst_path", "dst_ino", "dst_size", "moved_at"}
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_recovery_moves_action", "idx_recovery_actions_created"} <= indexes
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v11-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_recent_keurer_decisions(1)[0]["dest_ino"] == 77
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    assert db.count_keurer_moves() >= 1 and db.count_delete_runs() >= 1
    # The new tables work, and an action id is never handed out twice
    first = db.record_recovery_move(None, "viewer_move", "x", "/v/a", "/v/b", 1, 2)
    db.delete_recovery_move(db.get_recovery_moves(first)[0]["id"])
    assert db.get_recovery_action(first) is None
    assert db.record_recovery_move(None, "viewer_move", "y", "/v/a", "/v/b", 1, 2) > first
    db.close_all()


def _add_run(config, ops, created_at, folder):
    """A duplicates run straight in the database: [(src, dst)]."""
    import uuid
    run_id = uuid.uuid4().hex
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("INSERT INTO delete_runs (run_id, total_files, created_at, folder) VALUES (?, ?, ?, ?)",
                     (run_id, len(ops), created_at, str(folder)))
        conn.executemany("INSERT INTO delete_operations (run_id, src_path, dst_path) VALUES (?, ?, ?)",
                         [(run_id, str(src), str(dst)) for src, dst in ops])
    return run_id


def test_large_duplicate_runs_list_fast_and_check_each_folder_once(client, vault, config, monkeypatch):
    """The list checks the shown files, and beyond those only until one can go back; the path
    checks (which resolve every folder on disk) run once per folder, not once per file."""
    from backend.routes import recovery as recovery_routes
    dups = config.DUPLICATES_FOLDER
    now = time.time()
    # 1. Every file is still in RAW_DUPLICATES (the usual case)
    (dups / "hier").mkdir(parents=True)
    present = []
    for i in range(600):
        dst = dups / "hier" / f"p{i}.jpg"
        dst.write_bytes(b"p")
        present.append((vault / "RAW" / "hier" / f"p{i}.jpg", dst))
    run_present = _add_run(config, present, now - 30, vault / "RAW")
    # 2. Every file is gone (RAW_DUPLICATES emptied by hand): every record has to be looked at
    gone = [(vault / "RAW" / "weg" / f"g{i}.jpg", dups / "weg" / f"g{i}.jpg") for i in range(4000)]
    run_gone = _add_run(config, gone, now - 20, vault / "RAW")
    # 3. Only the very last file is still there
    (dups / "laatste").mkdir()
    last = [(vault / "RAW" / "l" / f"l{i}.jpg", dups / "laatste" / f"l{i}.jpg") for i in range(1500)]
    last[-1][1].write_bytes(b"l")
    run_last = _add_run(config, last, now - 10, vault / "RAW")

    calls = {"is_inside": 0, "is_in_vault": 0}
    for name in calls:
        real = getattr(recovery_routes, name)

        def counted(*args, _real=real, _name=name, **kwargs):
            calls[_name] += 1
            return _real(*args, **kwargs)
        monkeypatch.setattr(recovery_routes, name, counted)

    started = time.monotonic()
    data = _list(client)
    seconds = time.monotonic() - started
    by_key = {e["key"]: e for e in data["entries"]}
    assert seconds < 3, seconds
    assert calls["is_inside"] <= 6 and calls["is_in_vault"] <= 6, calls

    p = by_key[f"duplicates:{run_present}"]
    assert p["file_count"] == 600 and p["undoable"] and p["checked"] == 20 and p["restorable"] == 20
    assert len(p["files"]) == 20 and all(f["state"] == "ok" for f in p["files"])
    g = by_key[f"duplicates:{run_gone}"]
    assert g["file_count"] == 4000 and g["checked"] == 4000 and not g["undoable"] and g["restorable"] == 0
    assert g["reason"] == "g0.jpg staat niet meer in RAW_DUPLICATES" and len(g["files"]) == 20
    la = by_key[f"duplicates:{run_last}"]
    assert la["undoable"] and la["restorable"] == 1 and la["checked"] == 1500
    assert [f["state"] for f in la["files"]] == ["missing"] * 20

    # The undo still checks every file itself
    r = client.post("/api/duplicates/undo", json={"run_id": run_last})
    assert r.status_code == 200 and r.json()["restored"] == 1
    assert last[-1][0].exists()


def test_folder_checks_still_check_names_that_could_change_the_answer(client, vault, config, tmp_path):
    """A symlink in RAW_DUPLICATES that points outside, and a record whose name is that of the
    vault's own TRASH folder, are refused although other files in the same folders are fine."""
    dups = config.DUPLICATES_FOLDER / "mix"
    dups.mkdir(parents=True)
    (dups / "a.jpg").write_bytes(b"a")
    outside = tmp_path / "buiten.jpg"
    outside.write_bytes(b"buiten")
    (dups / "link.jpg").symlink_to(outside)
    (dups / "t.jpg").write_bytes(b"t")
    ops = [(vault / "RAW" / "a.jpg", dups / "a.jpg"),
           (vault / "RAW" / "link.jpg", dups / "link.jpg"),
           (vault / config.TRASH_FOLDER.name, dups / "t.jpg")]
    run_id = _add_run(config, ops, time.time(), vault / "RAW")
    [entry] = _list(client)["entries"]
    assert entry["key"] == f"duplicates:{run_id}"
    assert [f["state"] for f in entry["files"]] == ["ok", "refused", "refused"]
    assert entry["restorable"] == 1
