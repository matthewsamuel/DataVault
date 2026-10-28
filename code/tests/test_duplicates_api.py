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


def _pair(vault, name, data):
    """An original in library/ and an identical copy in RAW/misc/."""
    keeper = vault / "library" / f"orig_{name}"
    dup = vault / "RAW" / "misc" / name
    keeper.write_bytes(data)
    dup.write_bytes(data)
    return keeper, dup


def test_undo_last_passes_over_a_run_whose_file_is_gone(client, vault):
    k1, d1 = _pair(vault, "d1.jpg", b"eerste")
    k2, d2 = _pair(vault, "d2.jpg", b"tweede")
    assert _delete_single(client, d1, k1).status_code == 200
    assert _delete_single(client, d2, k2).status_code == 200
    moved2 = vault / "RAW_DUPLICATES" / "misc" / "d2.jpg"
    elsewhere = vault / "library" / "handmatig_d2.jpg"
    os.rename(moved2, elsewhere)  # the user tidied RAW_DUPLICATES by hand (test vault)

    r = client.post("/api/raw_duplicates/undo_last")
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == 1
    assert d1.read_bytes() == b"eerste"  # the older run is reachable again

    r = client.post("/api/raw_duplicates/undo_last")
    assert r.status_code == 404  # nothing left to restore, and no endless 'restored: 0'

    # If the file comes back to RAW_DUPLICATES, its run can still be undone
    os.rename(elsewhere, moved2)
    r = client.post("/api/raw_duplicates/undo_last")
    assert r.status_code == 200 and r.json()["restored"] == 1
    assert d2.read_bytes() == b"tweede"


def test_undo_last_keeps_records_of_files_it_could_not_restore(client, vault, monkeypatch):
    from backend.routes import duplicates

    k1, d1 = _pair(vault, "d1.jpg", b"eerste")
    assert _delete_single(client, d1, k1).status_code == 200
    real_move = duplicates.move_no_overwrite

    def failing_move(*args, **kwargs):
        raise duplicates.FileOperationError("schijf vergrendeld")

    monkeypatch.setattr(duplicates, "move_no_overwrite", failing_move)
    r = client.post("/api/raw_duplicates/undo_last").json()
    assert r["restored"] == 0 and r["errors"]

    monkeypatch.setattr(duplicates, "move_no_overwrite", real_move)
    r = client.post("/api/raw_duplicates/undo_last")
    assert r.status_code == 200 and r.json()["restored"] == 1
    assert d1.read_bytes() == b"eerste"
