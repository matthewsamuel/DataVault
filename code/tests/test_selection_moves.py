"""
The selection bar: many files moved to a folder or to the trash in one request, as one
action in the recovery center (Herstelcentrum) that puts them all back.
"""
import os

import pytest

INBOX = "RAW/inbox"


@pytest.fixture
def photos(vault):
    """Four small photos: IMG_0001.JPG .. IMG_0004.JPG."""
    folder = vault / INBOX
    folder.mkdir()
    files = []
    for i in range(1, 5):
        f = folder / f"IMG_{i:04d}.JPG"
        f.write_bytes(b"\xff\xd8\xff\xe0" + f"foto {i}".encode() * 40)
        files.append(f)
    return files


def _post(client, url, **body):
    r = client.post(url, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _entries(client):
    r = client.get("/api/recovery")
    assert r.status_code == 200, r.text
    return r.json()["entries"]


def test_move_many_files_is_one_action_that_undoes_them_all(client, vault, photos):
    target = vault / "library" / "Vakantie"
    data = {p: p.read_bytes() for p in photos}
    result = _post(client, "/api/move-files", paths=[str(p) for p in photos[:3]], destination_folder=str(target))
    assert result["success"] and result["error"] is None and result["skipped"] == []
    assert [m["path"] for m in result["moved"]] == [str(p) for p in photos[:3]]
    for p in photos[:3]:
        assert not p.exists() and (target / p.name).read_bytes() == data[p]
    assert photos[3].exists()

    [entry] = _entries(client)
    assert entry["source"] == "selection_move" and entry["label"] == "Selectie verplaatst"
    assert entry["title"] == "Verplaatst naar Vakantie" and entry["restorable"] == 3
    r = client.post(entry["undo"]["url"], json=entry["undo"]["body"])
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == 3
    assert all(p.read_bytes() == data[p] for p in photos)


def test_trash_moves_to_the_trash_folder_and_never_deletes(client, vault, config, photos):
    result = _post(client, "/api/trash-files", paths=[str(p) for p in photos[:2]])
    assert result["success"] and len(result["moved"]) == 2
    for p, m in zip(photos[:2], result["moved"]):
        assert not p.exists()
        assert os.path.dirname(m["new_path"]) == str(config.TRASH_FOLDER)
        assert os.path.exists(m["new_path"])

    [entry] = _entries(client)
    assert entry["source"] == "selection_trash" and entry["title"] == "Naar de prullenmand"
    assert client.post(entry["undo"]["url"], json=entry["undo"]["body"]).status_code == 200
    assert photos[0].exists() and photos[1].exists()


def test_name_clashes_get_a_new_name_and_nothing_is_overwritten(client, vault, photos):
    target = vault / "library"
    other = vault / "RAW" / "elders" / photos[0].name
    other.parent.mkdir()
    other.write_bytes(b"ander bestand")
    result = _post(client, "/api/move-files", paths=[str(photos[0]), str(other)], destination_folder=str(target))
    names = sorted(os.path.basename(m["new_path"]) for m in result["moved"])
    assert names == ["IMG_0001.JPG", "IMG_0001_DUP_001.JPG"]
    assert sorted((target / n).read_bytes()[:4] for n in names) == [b"ande", b"\xff\xd8\xff\xe0"]


def test_files_that_cannot_go_are_skipped_with_a_reason(client, vault, config, photos, tmp_path):
    outside = tmp_path / "buiten.jpg"
    outside.write_bytes(b"x")
    gone = vault / INBOX / "weg.jpg"
    already = vault / "library"
    result = _post(client, "/api/move-files", destination_folder=str(vault / INBOX),
                   paths=[str(photos[0]), str(outside), str(gone), str(photos[1])])
    assert result["moved"] == []
    reasons = {s["path"]: s["reason"] for s in result["skipped"]}
    assert reasons[str(photos[0])] == "Staat al in deze map"
    assert reasons[str(gone)] == "Bestand niet gevonden"
    assert "buiten het archief" in reasons[str(outside)]
    assert outside.exists() and photos[0].exists()
    assert _entries(client) == []   # nothing moved, nothing to undo

    trashed = _post(client, "/api/trash-files", paths=[str(photos[2])])["moved"][0]["new_path"]
    again = _post(client, "/api/trash-files", paths=[trashed, str(photos[3])])
    assert [s["reason"] for s in again["skipped"]] == ["Staat al in de prullenmand"]
    assert len(again["moved"]) == 1 and os.path.exists(trashed)
    assert already.is_dir()


def test_bad_requests_move_nothing(client, vault, photos):
    for url, body in [
        ("/api/move-files", {"paths": [], "destination_folder": str(vault / "library")}),
        ("/api/move-files", {"paths": "x", "destination_folder": str(vault / "library")}),
        ("/api/move-files", {"paths": [1], "destination_folder": str(vault / "library")}),
        ("/api/move-files", {"paths": [str(photos[0])]}),
        ("/api/move-files", {"paths": [str(photos[0])], "destination_folder": str(vault / "RAW/misc/notes.txt")}),
        ("/api/trash-files", {}),
        ("/api/trash-files", {"paths": [str(photos[0])] * 20001}),
    ]:
        assert client.post(url, json=body).status_code == 400, (url, body)
    r = client.post("/api/move-files", json={"paths": [str(photos[0])], "destination_folder": "/tmp"})
    assert r.status_code == 403
    assert all(p.exists() for p in photos)
