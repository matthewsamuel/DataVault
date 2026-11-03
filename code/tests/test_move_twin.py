"""
'Bestand verplaatsen' when the destination already holds the same file: the viewer offers to
put this copy in the trash instead. Only a different, visible, byte-for-byte identical file
counts as the copy that stays, and the trash request checks all of that again itself.
"""
import os

ROME = "library/Reis Italië/rome.jpg"
FAMILIE = "library/Familie"


def _check(client, src, dest_folder):
    r = client.post("/api/move-check", json={"source_path": str(src), "destination_folder": str(dest_folder)})
    assert r.status_code == 200, r.text
    return r.json()["twin"]


def _trash(client, src, twin):
    return client.post("/api/move-twin-trash", json={"source_path": str(src), "twin_path": str(twin)})


def _untouched(src, data):
    assert src.is_file() and src.read_bytes() == data


def test_an_identical_copy_under_another_name_goes_to_the_trash_and_comes_back(client, vault):
    src = vault / ROME
    data = src.read_bytes()
    twin = vault / FAMILIE / "rome kopie.jpg"
    twin.write_bytes(data)

    assert _check(client, src, vault / FAMILIE) == {"path": str(twin), "name": "rome kopie.jpg"}

    r = _trash(client, src, twin)
    assert r.status_code == 200, r.text
    trashed = r.json()["trash_path"]
    assert not src.exists() and open(trashed, "rb").read() == data
    assert os.path.dirname(trashed) == str(vault / "TRASH")
    _untouched(twin, data)

    [entry] = client.get("/api/recovery").json()["entries"]
    assert entry["source"] == "move_twin_trash" and entry["label"] == "Dubbel bij verplaatsen"
    r = client.post(entry["undo"]["url"], json=entry["undo"]["body"])
    assert r.status_code == 200, r.text
    _untouched(src, data)
    _untouched(twin, data)


def test_the_same_name_is_found_first(client, vault):
    src = vault / ROME
    data = src.read_bytes()
    (vault / FAMILIE / "a.jpg").write_bytes(data)
    (vault / FAMILIE / "rome.jpg").write_bytes(data)
    assert _check(client, src, vault / FAMILIE)["name"] == "rome.jpg"


def test_the_folder_the_file_is_in_offers_nothing_and_the_file_itself_is_no_twin(client, vault):
    """The screenshot case: 'lock' chosen while the photo is in 'lock'. It is the only copy."""
    src = vault / ROME
    data = src.read_bytes()
    assert _check(client, src, src.parent) is None
    r = _trash(client, src, src)
    assert r.status_code == 409 and "hetzelfde bestand" in r.json()["detail"]
    _untouched(src, data)
    # Another spelling of the same path is the same file too
    r = _trash(client, src, str(src.parent) + "/./" + src.name)
    assert r.status_code == 409
    _untouched(src, data)


def test_a_hard_link_is_not_a_second_copy(client, vault):
    src = vault / ROME
    data = src.read_bytes()
    link = vault / FAMILIE / "rome.jpg"
    os.link(src, link)
    assert _check(client, src, vault / FAMILIE) is None
    assert _trash(client, src, link).status_code == 409
    _untouched(src, data)


def test_same_size_but_other_bytes_is_no_twin(client, vault):
    src = vault / ROME
    data = src.read_bytes()
    other = vault / FAMILIE / "rome.jpg"
    other.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    assert _check(client, src, vault / FAMILIE) is None
    r = _trash(client, src, other)
    assert r.status_code == 409 and "niet meer precies gelijk" in r.json()["detail"]
    _untouched(src, data)


def test_a_twin_that_changed_or_went_since_the_check_is_refused(client, vault):
    src = vault / ROME
    data = src.read_bytes()
    twin = vault / FAMILIE / "rome.jpg"
    twin.write_bytes(data)
    assert _check(client, src, vault / FAMILIE) is not None

    twin.write_bytes(data + b"x")
    assert _trash(client, src, twin).status_code == 409
    _untouched(src, data)

    twin.unlink()
    r = _trash(client, src, twin)
    assert r.status_code == 409 and "bestaat niet meer" in r.json()["detail"]
    _untouched(src, data)


def test_a_copy_in_the_trash_or_duplicates_never_counts(client, vault):
    src = vault / ROME
    data = src.read_bytes()
    for folder in ("TRASH", "RAW_DUPLICATES"):
        (vault / folder).mkdir(exist_ok=True)
        twin = vault / folder / "rome.jpg"
        twin.write_bytes(data)
        assert _check(client, src, vault / folder) is None
        r = _trash(client, src, twin)
        assert r.status_code == 409, folder
        _untouched(src, data)
        twin.unlink()


def test_a_hidden_copy_and_empty_files_never_count(client, vault):
    src = vault / ROME
    data = src.read_bytes()
    hidden = vault / FAMILIE / ".rome.jpg"
    hidden.write_bytes(data)
    assert _check(client, src, vault / FAMILIE) is None
    assert _trash(client, src, hidden).status_code == 409
    _untouched(src, data)

    empty = vault / "library/Reis Italië/leeg.jpg"
    empty.write_bytes(b"")
    (vault / FAMILIE / "leeg.jpg").write_bytes(b"")
    assert _check(client, empty, vault / FAMILIE) is None
    assert _trash(client, empty, vault / FAMILIE / "leeg.jpg").status_code == 409
    assert empty.is_file()


def test_a_symlink_twin_and_malformed_bodies_are_refused(client, vault):
    src = vault / ROME
    data = src.read_bytes()
    link = vault / FAMILIE / "rome.jpg"
    os.symlink(src, link)
    assert _check(client, src, vault / FAMILIE) is None
    assert _trash(client, src, link).status_code == 409
    _untouched(src, data)

    for body in ({}, {"source_path": str(src)}, {"source_path": 1, "twin_path": str(link)}):
        assert client.post("/api/move-twin-trash", json=body).status_code == 400
        assert client.post("/api/move-check", json=body).status_code == 400
    _untouched(src, data)
