"""delete_single only moves a real duplicate while its kept original still exists and is identical."""
import os

from fixtures.vault_builder import STRAND, STRAND_COPIES


def _delete_single(client, path, duplicate_of=None):
    params = {"file_path": str(path)}
    if duplicate_of is not None:
        params["duplicate_of"] = str(duplicate_of)
    return client.post("/api/duplicates/delete_single", params=params)


def test_delete_single_moves_identical_duplicate_and_undo_restores(client, vault):
    keeper = vault / STRAND
    dup = vault / STRAND_COPIES[0]
    data = dup.read_bytes()
    r = _delete_single(client, dup, keeper)
    assert r.status_code == 200, r.text
    moved = vault / "RAW_DUPLICATES" / "misc" / dup.name
    assert not dup.exists() and moved.read_bytes() == data and keeper.exists()

    r = client.post("/api/raw_duplicates/undo_last")
    assert r.json()["restored"] == 1
    assert dup.read_bytes() == data and not moved.exists()


def test_delete_single_uses_keeper_from_last_scan(client, vault):
    folder = vault / "RAW"
    scan = client.post("/api/duplicates/scan_folder", params={"folder_path": str(folder)}).json()
    dup = scan["duplicates"][0]
    r = _delete_single(client, dup["path"])  # old API shape: only file_path
    assert r.status_code == 200, r.text
    assert r.json()["duplicate_of"] == dup["duplicate_of"]


def test_delete_single_refuses_folders_and_symlinks(client, vault):
    raw = vault / "RAW"
    assert _delete_single(client, raw, vault / STRAND).status_code == 400
    assert _delete_single(client, vault / "RAW/2019", vault / STRAND).status_code == 400
    assert raw.is_dir() and (vault / "RAW/2019").is_dir()
    assert not (vault / "RAW_DUPLICATES_DUP_001").exists()

    link = vault / "RAW/misc/link.jpg"
    os.symlink(vault / STRAND_COPIES[0], link)
    assert _delete_single(client, link, vault / STRAND).status_code == 400
    assert link.is_symlink()
    assert _delete_single(client, vault / "RAW/misc/link_to_outside", vault / STRAND).status_code == 403


def test_delete_single_refuses_protected_folders(client, vault):
    for folder in ("RAW_DUPLICATES", "TRASH", ".datavault_cache"):
        f = vault / folder / "x.jpg"
        f.parent.mkdir(exist_ok=True)
        f.write_bytes((vault / STRAND).read_bytes())
        assert _delete_single(client, f, vault / STRAND).status_code == 400, folder
        assert f.exists()


def test_delete_single_needs_an_existing_identical_original(client, vault):
    dup = vault / STRAND_COPIES[0]
    keeper = vault / STRAND

    # No keeper given and no scan -> refuse
    assert _delete_single(client, dup).status_code == 409
    # Not a duplicate at all
    assert _delete_single(client, dup, vault / "RAW/misc/UPPER.JPEG").status_code == 409
    # Keeper is the file itself
    assert _delete_single(client, dup, dup).status_code == 409
    # Keeper differs by a single byte (same size)
    other = vault / "RAW/misc/bijna.jpg"
    data = bytearray(keeper.read_bytes())
    data[-1] ^= 0xFF
    other.write_bytes(bytes(data))
    assert _delete_single(client, dup, other).status_code == 409
    # Keeper was moved away in the meantime (e.g. by the Keurer)
    keeper.rename(vault / "elders.jpg")
    assert _delete_single(client, dup, keeper).status_code == 409
    # Keeper outside the vault
    assert _delete_single(client, dup, vault.parent / f"{vault.name}_outside" / "secret.jpg").status_code == 403

    assert dup.exists()
    assert not any((vault / "RAW_DUPLICATES").rglob("*.jpg"))
    assert client.post("/api/raw_duplicates/undo_last").status_code == 404  # no empty runs were created


def test_delete_single_never_overwrites_in_raw_duplicates(client, vault):
    dup = vault / STRAND_COPIES[0]
    occupied = vault / "RAW_DUPLICATES" / "misc" / dup.name
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"EARLIER DUPLICATE")
    r = _delete_single(client, dup, vault / STRAND)
    assert r.status_code == 200
    assert r.json()["was_conflict"] is True
    assert occupied.read_bytes() == b"EARLIER DUPLICATE"
    assert r.json()["dst_path"].endswith("strand 001 kopie_DUP_001.jpg")
