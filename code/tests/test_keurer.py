"""Keurer decisions and server-side undo: the client only ever sends a decision id."""
import sqlite3

import pytest

from backend.database import Database
from test_foundation import _make_legacy_db as make_legacy_db

INBOX = "RAW/2019/zomer vakantie"


@pytest.fixture
def inbox(vault):
    return vault / INBOX


def _decide(client, path, decision, folder=None, **extra):
    body = {"path": str(path), "folder": str(folder or path.parent), "decision": decision, **extra}
    return client.post("/api/keurer/decide", json=body)


def _db(config):
    return sqlite3.connect(str(config.DB_PATH))


def test_delete_goes_to_trash_and_undo_by_id_restores(client, config, inbox):
    photo = inbox / "IMG_0002.JPG"
    data = photo.read_bytes()

    r = _decide(client, photo, "delete")
    assert r.status_code == 200, r.text
    body = r.json()
    trash = config.TRASH_FOLDER / "IMG_0002.JPG"
    assert body["dest_path"] == body["trash_path"] == str(trash)
    assert not photo.exists() and trash.read_bytes() == data
    assert body["stats"]["today"] == 1

    history = client.get("/api/keurer/history").json()["entries"]
    assert [e["id"] for e in history] == [body["id"]]
    assert history[0]["folder"] == str(inbox)
    assert history[0]["file"]["name"] == "IMG_0002.JPG"

    r = client.post("/api/keurer/undo", json={"id": body["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["restored_path"] == str(photo)
    assert r.json()["stats"]["today"] == 0
    assert photo.read_bytes() == data and not trash.exists()
    assert client.get("/api/keurer/history").json()["entries"] == []

    names = [f["name"] for f in client.get("/api/keurer/files", params={"folder": str(inbox)}).json()["files"]]
    assert "IMG_0002.JPG" in names


def test_same_name_from_two_folders_both_survive_in_trash(client, config, vault):
    a = vault / "RAW/x/IMG.jpg"
    b = vault / "RAW/y/IMG.jpg"
    for p, content in ((a, b"A"), (b, b"B")):
        p.parent.mkdir(parents=True)
        p.write_bytes(content)
    ida = _decide(client, a, "delete").json()["id"]
    idb = _decide(client, b, "delete").json()["id"]
    assert sorted(p.read_bytes() for p in config.TRASH_FOLDER.iterdir()) == [b"A", b"B"]

    assert client.post("/api/keurer/undo", json={"id": ida}).status_code == 200
    assert client.post("/api/keurer/undo", json={"id": idb}).status_code == 200
    assert a.read_bytes() == b"A" and b.read_bytes() == b"B"


def test_undo_never_takes_paths_from_the_client(client, vault, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("KEEP")
    legacy_body = {
        "path": str(vault / "RAW/misc/stolen.txt"), "folder": str(vault / "RAW/misc"),
        "decision": "delete", "trash_path": str(victim),
    }
    r = client.post("/api/keurer/undo", json=legacy_body)
    assert r.status_code == 400
    assert victim.read_text() == "KEEP"
    assert not (vault / "RAW/misc/stolen.txt").exists()

    for bad in ({"id": "1"}, {"id": True}, {"id": None}, {}):
        assert client.post("/api/keurer/undo", json=bad).status_code == 400
    assert client.post("/api/keurer/undo", json={"id": 9999}).status_code == 404


def test_undo_revalidates_paths_stored_in_the_database(client, config, inbox, tmp_path):
    """Even a tampered row cannot move files from or to outside the vault/TRASH."""
    photo = inbox / "IMG_0002.JPG"
    decision_id = _decide(client, photo, "delete").json()["id"]
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET")

    with _db(config) as conn:
        conn.execute("UPDATE keurer_decisions SET dest_path = ? WHERE id = ?", (str(outside), decision_id))
    assert client.post("/api/keurer/undo", json={"id": decision_id}).status_code == 403
    assert outside.read_text() == "SECRET" and not photo.exists()

    with _db(config) as conn:
        conn.execute("UPDATE keurer_decisions SET dest_path = ?, path = ? WHERE id = ?",
                     (str(config.TRASH_FOLDER / "IMG_0002.JPG"), str(tmp_path / "out.jpg"), decision_id))
    assert client.post("/api/keurer/undo", json={"id": decision_id}).status_code == 403
    assert (config.TRASH_FOLDER / "IMG_0002.JPG").exists()
    assert not (tmp_path / "out.jpg").exists()


def test_undo_refuses_when_original_place_is_taken(client, config, inbox):
    photo = inbox / "IMG_0002.JPG"
    decision_id = _decide(client, photo, "delete").json()["id"]
    photo.write_bytes(b"NEW CONTENT")

    r = client.post("/api/keurer/undo", json={"id": decision_id})
    assert r.status_code == 409
    assert photo.read_bytes() == b"NEW CONTENT"
    assert (config.TRASH_FOLDER / "IMG_0002.JPG").exists()
    # The decision stays, so nothing is lost and the counters stay right
    assert client.get("/api/keurer/stats").json()["today"] == 1


def test_undo_reports_missing_trash_file_instead_of_success(client, config, inbox):
    photo = inbox / "IMG_0002.JPG"
    decision_id = _decide(client, photo, "delete").json()["id"]
    # Simulate TRASH being emptied by hand (the app itself never deletes)
    elsewhere = config.VAULT_ROOT / "handmatig.jpg"
    (config.TRASH_FOLDER / "IMG_0002.JPG").rename(elsewhere)

    r = client.post("/api/keurer/undo", json={"id": decision_id})
    assert r.status_code == 410
    assert not photo.exists()
    assert client.get("/api/keurer/history").json()["entries"] == []


def test_move_and_undo_use_the_decisions_own_folder(client, vault, inbox):
    photo = inbox / "IMG_0002.JPG"
    dest = vault / "library" / "Familie"
    r = _decide(client, photo, "move", destination_folder=str(dest))
    assert r.status_code == 200, r.text
    assert r.json()["dest_path"] == str(dest / "IMG_0002.JPG")
    # Moves do not count as judgements (unchanged behaviour)
    assert r.json()["stats"]["today"] == 0

    # Meanwhile the user switched to another folder; undo still works on the original one
    keep_id = _decide(client, dest / "opa.jpg", "keep").json()["id"]
    history = client.get("/api/keurer/history").json()["entries"]
    assert [e["decision"] for e in history] == ["keep", "move"]

    move_id = history[1]["id"]
    r = client.post("/api/keurer/undo", json={"id": move_id})
    assert r.status_code == 200, r.text
    assert r.json()["folder"] == str(inbox)
    assert photo.exists() and not (dest / "IMG_0002.JPG").exists()

    assert client.post("/api/keurer/undo", json={"id": keep_id}).json()["restored_path"] is None
    files = client.get("/api/keurer/files", params={"folder": str(dest)}).json()["files"]
    assert "opa.jpg" in [f["name"] for f in files]


def test_move_into_same_name_never_overwrites(client, vault, inbox):
    dest = vault / "library" / "Familie"
    (dest / "IMG_0002.JPG").write_bytes(b"OCCUPIED")
    r = _decide(client, inbox / "IMG_0002.JPG", "move", destination_folder=str(dest))
    assert r.status_code == 200
    assert r.json()["dest_path"].endswith("IMG_0002_DUP_001.JPG")
    assert (dest / "IMG_0002.JPG").read_bytes() == b"OCCUPIED"


def test_decide_errors_are_reported_and_not_recorded(client, vault, inbox, tmp_path):
    missing = inbox / "bestaat niet.jpg"
    assert _decide(client, missing, "delete").status_code == 404
    # File must be directly in the folder that is being judged
    assert _decide(client, inbox / "IMG_0002.JPG", "keep", folder=vault / "RAW").status_code == 400
    # Folders and symlinks are never moved
    assert _decide(client, vault / "RAW/2019", "delete", folder=vault / "RAW").status_code == 404
    link = inbox / "link.jpg"
    link.symlink_to(inbox / "IMG_0002.JPG")
    assert _decide(client, link, "delete").status_code == 404
    assert link.is_symlink()
    # Move needs an existing folder inside the vault
    assert _decide(client, inbox / "IMG_0002.JPG", "move", destination_folder=str(tmp_path)).status_code == 403
    assert _decide(client, inbox / "IMG_0002.JPG", "move",
                   destination_folder=str(vault / "nieuw")).status_code == 404
    assert _decide(client, inbox / "IMG_0002.JPG", "move").status_code == 400

    assert client.get("/api/keurer/stats").json()["total"] == 0
    assert (inbox / "IMG_0002.JPG").exists()


def test_migration_keeps_old_keurer_rows(tmp_path):
    """Old-style rows (no dest_path) survive; old keeps are undoable, old deletes are refused safely."""
    db_path = tmp_path / "datavault.db"
    make_legacy_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision) VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete')")

    db = Database(db_path)
    stats = db.get_keurer_stats()
    assert stats["streak"] == 3 and stats["total"] == 61
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"

    recent = db.get_recent_keurer_decisions(limit=5)
    assert recent[0]["path"] == "/v/RAW/weg.jpg"
    assert recent[0]["dest_path"] is None
    assert len(db.get_keurer_decided_paths("/v/RAW")) == 61


def test_undo_of_legacy_delete_is_refused(client, config, vault):
    with _db(config) as conn:
        cur = conn.execute("INSERT INTO keurer_decisions (path, folder, decision) VALUES (?, ?, 'delete')",
                           (str(vault / "RAW/misc/oud.jpg"), str(vault / "RAW/misc")))
        legacy_id = cur.lastrowid
    assert client.get("/api/keurer/history").json()["entries"] == []
    r = client.post("/api/keurer/undo", json={"id": legacy_id})
    assert r.status_code == 409
