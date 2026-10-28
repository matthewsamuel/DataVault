"""
Protected-folder guards hold for every spelling of a path, and one shared lock
keeps checks and moves together (so two requests can never move both copies away).
"""
import os
import sqlite3
import threading
import unicodedata

import pytest
from fastapi import HTTPException

from backend import file_ops
from backend.file_ops import ProtectedPathError, is_inside, move_no_overwrite, same_folder
from fixtures.vault_builder import STRAND as STRAND_PATH, STRAND_COPIES

STRAND_COPY = STRAND_COPIES[0]


def _case_insensitive(folder) -> bool:
    probe = folder / "CaseProbe"
    probe.mkdir()
    try:
        return os.path.exists(folder / "caseprobe")
    finally:
        probe.rmdir()


@pytest.fixture
def case_insensitive(vault):
    if not _case_insensitive(vault):
        pytest.skip("temp volume is case-sensitive; case variants are different folders there")


@pytest.fixture
def no_protected_paths():
    """Unit tests of the helper start from an empty registry and leave it as they found it."""
    saved = file_ops._protected_paths
    file_ops.protect_paths([])
    yield
    file_ops._protected_paths = saved


# --- is_inside / same_folder -------------------------------------------------

def test_is_inside_exact_and_outside(tmp_path):
    folder = tmp_path / "TRASH"
    folder.mkdir()
    assert is_inside(folder / "a.jpg", folder)
    assert is_inside(folder, folder)
    assert is_inside(folder / "new" / "deeper" / "x.jpg", folder)  # not existing yet
    assert not is_inside(tmp_path / "TRASH_old" / "a.jpg", folder)
    assert not is_inside(tmp_path / "other" / ".." / "x.jpg", folder)
    assert is_inside(tmp_path / "other" / ".." / "TRASH" / "x.jpg", folder)


def test_is_inside_sees_through_symlinked_folders(tmp_path):
    folder = tmp_path / "cache"
    folder.mkdir()
    (tmp_path / "alias").symlink_to(folder)
    assert is_inside(tmp_path / "alias" / "datavault.db", folder)


def test_is_inside_catches_case_variants(tmp_path):
    if not _case_insensitive(tmp_path):
        pytest.skip("temp volume is case-sensitive")
    folder = tmp_path / ".datavault_cache"
    folder.mkdir()
    (folder / "datavault.db").write_bytes(b"db")
    assert is_inside(tmp_path / ".DATAVAULT_CACHE" / "datavault.db", folder)
    assert is_inside(tmp_path / ".DataVault_Cache" / "nieuw" / "x.jpg", folder)
    assert is_inside(tmp_path / ".DATAVAULT_CACHE" / "datavault.db", folder / "datavault.db")
    assert same_folder(tmp_path / ".DATAVAULT_CACHE", folder)


def test_is_inside_catches_unicode_variants(tmp_path):
    nfc = unicodedata.normalize("NFC", "prullenbak é")
    nfd = unicodedata.normalize("NFD", "prullenbak é")
    folder = tmp_path / nfc
    folder.mkdir()
    if not os.path.exists(tmp_path / nfd):
        pytest.skip("volume is not normalisation-insensitive")
    assert is_inside(tmp_path / nfd / "x.jpg", folder)


def test_is_inside_for_a_folder_that_does_not_exist_yet_ignores_case(tmp_path):
    # Errs on the side of "inside": once created, TRASH and trash may be one folder
    assert is_inside(tmp_path / "trash" / "x.jpg", tmp_path / "TRASH")
    assert not is_inside(tmp_path / "trashcan" / "x.jpg", tmp_path / "TRASH")


# --- move_no_overwrite refuses the app's own folders ---------------------------

def test_move_refuses_protected_source_and_destination(tmp_path, no_protected_paths):
    cache = tmp_path / ".datavault_cache"
    cache.mkdir()
    db = cache / "datavault.db"
    db.write_bytes(b"live db")
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(b"foto")
    file_ops.protect_paths([cache, db])

    with pytest.raises(ProtectedPathError):
        move_no_overwrite(db, tmp_path / "TRASH" / "datavault.db")
    with pytest.raises(ProtectedPathError):
        move_no_overwrite(photo, cache / "foto.jpg")
    assert db.read_bytes() == b"live db" and photo.exists()
    assert not (tmp_path / "TRASH").exists()


def test_move_refuses_moving_a_file_onto_another_spelling_of_itself(tmp_path, no_protected_paths):
    if not _case_insensitive(tmp_path):
        pytest.skip("temp volume is case-sensitive")
    (tmp_path / "Familie").mkdir()
    photo = tmp_path / "Familie" / "opa.jpg"
    photo.write_bytes(b"opa")
    with pytest.raises(file_ops.FileOperationError):
        move_no_overwrite(photo, tmp_path / "FAMILIE" / "opa.jpg")
    assert sorted(os.listdir(tmp_path / "Familie")) == ["opa.jpg"]


# --- API: case variants of protected folders are refused --------------------

def test_move_file_refuses_cache_in_other_case(client, config, vault, case_insensitive):
    probe = config.CACHE_FOLDER / "probe.jpg"
    probe.write_bytes(b"probe")
    for src, dest in [
        (vault / ".DATAVAULT_CACHE" / "probe.jpg", vault / "library"),
        (vault / ".DATAVAULT_CACHE" / "datavault.db", vault / "library"),
        (vault / STRAND_PATH, vault / ".Datavault_Cache" / "thumbs"),
    ]:
        r = client.post("/api/move-file", json={"source_path": str(src), "destination_folder": str(dest)})
        assert r.status_code == 403, (src, r.text)
    assert probe.exists() and (vault / STRAND_PATH).exists()


def test_keurer_cannot_trash_the_live_database_via_other_case(client, config, vault, case_insensitive):
    r = client.post("/api/keurer/decide", json={
        "path": str(vault / ".DATAVAULT_CACHE" / "datavault.db"),
        "folder": str(vault / ".DATAVAULT_CACHE"),
        "decision": "delete",
    })
    assert r.status_code == 403, r.text
    assert not (config.TRASH_FOLDER / "datavault.db").exists()
    conn = sqlite3.connect(str(config.DB_PATH))
    assert conn.execute("SELECT COUNT(*) FROM keurer_decisions").fetchone()[0] == 0
    conn.close()
    assert client.get("/api/keurer/stats").status_code == 200


def test_keurer_trash_check_and_same_folder_check_ignore_case(client, config, vault, case_insensitive):
    config.TRASH_FOLDER.mkdir(exist_ok=True)
    in_trash = config.TRASH_FOLDER / "weg.jpg"
    in_trash.write_bytes(b"weg")
    r = client.post("/api/keurer/decide", json={
        "path": str(vault / "trash" / "weg.jpg"), "folder": str(vault / "trash"), "decision": "delete",
    })
    assert r.status_code == 400, r.text
    assert in_trash.exists() and not (config.TRASH_FOLDER / "weg_DUP_001.jpg").exists()

    photo = vault / STRAND_PATH
    r = client.post("/api/keurer/decide", json={
        "path": str(photo), "folder": str(photo.parent), "decision": "move",
        "destination_folder": str(vault / "raw" / "2019" / "ZOMER VAKANTIE"),
    })
    assert r.status_code == 400, r.text
    assert photo.exists() and not photo.with_name("strand 001_DUP_001.jpg").exists()


def test_delete_single_refuses_protected_folders_in_other_case(client, config, vault, case_insensitive):
    data = (vault / STRAND_PATH).read_bytes()
    inside = config.DUPLICATES_FOLDER / "casetest" / "x.jpg"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(data)
    r = client.post("/api/duplicates/delete_single", params={
        "file_path": str(vault / "raw_duplicates" / "casetest" / "x.jpg"),
        "duplicate_of": str(vault / STRAND_PATH),
    })
    assert r.status_code == 400, r.text
    assert inside.exists()

    config.TRASH_FOLDER.mkdir(exist_ok=True)
    (config.TRASH_FOLDER / "k.jpg").write_bytes(data)
    dup = vault / STRAND_COPY
    r = client.post("/api/duplicates/delete_single", params={
        "file_path": str(dup), "duplicate_of": str(vault / "trash" / "k.jpg"),
    })
    assert r.status_code == 409, r.text
    assert dup.exists()


# --- One shared lock: check and move cannot be split by another request --------

def _call(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        return 200
    except HTTPException as e:
        return e.status_code


def test_mutual_delete_single_never_moves_both_copies(client, vault):
    from backend.routes.duplicates import delete_single_duplicate

    folder = vault / "library" / "pair"
    folder.mkdir()
    for trial in range(8):
        a, b = folder / f"a{trial}.jpg", folder / f"b{trial}.jpg"
        data = os.urandom(2_000_000)
        a.write_bytes(data)
        b.write_bytes(data)
        barrier = threading.Barrier(2)
        codes = {}

        def run(src, keep):
            barrier.wait()
            codes[src.name] = _call(delete_single_duplicate, file_path=str(src), duplicate_of=str(keep))

        threads = [threading.Thread(target=run, args=pair) for pair in ((a, b), (b, a))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(codes.values()) == [200, 409], codes
        assert a.exists() != b.exists(), f"trial {trial}: exactly one copy must stay"


def test_keurer_delete_of_the_original_waits_for_delete_single(client, vault, monkeypatch):
    """While delete_single holds the lock, the Keurer cannot move the original away."""
    from backend.routes import duplicates, keurer

    keeper = vault / STRAND_PATH
    dup = vault / STRAND_COPY
    in_compare = threading.Event()
    release = threading.Event()
    real_identical = duplicates.files_identical

    def slow_identical(x, y):
        in_compare.set()
        release.wait(5)
        return real_identical(x, y)

    monkeypatch.setattr(duplicates, "files_identical", slow_identical)
    results = {}
    t1 = threading.Thread(target=lambda: results.__setitem__("dup", _call(
        duplicates.delete_single_duplicate, file_path=str(dup), duplicate_of=str(keeper))))
    t1.start()
    assert in_compare.wait(5)
    t2 = threading.Thread(target=lambda: results.__setitem__("keurer", _call(
        keurer.decide_file, {"path": str(keeper), "folder": str(keeper.parent), "decision": "delete"})))
    t2.start()
    t2.join(0.3)
    assert t2.is_alive(), "Keurer move must wait for the duplicates check-and-move"
    release.set()
    t1.join(5)
    t2.join(5)
    assert results == {"dup": 200, "keurer": 200}
    # The duplicate went to RAW_DUPLICATES first (original still there at that moment),
    # then the Keurer trashed the original: both copies are still in the archive.
    assert not dup.exists() and not keeper.exists()
    assert (vault / "TRASH" / keeper.name).exists()
