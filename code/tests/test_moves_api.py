"""File moves through the API: never overwrite, only regular files, only inside the vault."""
import os


def _move(client, src, dest_folder):
    return client.post("/api/move-file", json={"source_path": str(src), "destination_folder": str(dest_folder)})


def test_move_file_with_same_names_keeps_every_file(client, vault):
    target = vault / "library" / "Familie"
    names = []
    for i in range(3):
        src = vault / "RAW" / f"cam{i}" / "opa.jpg"
        src.parent.mkdir()
        src.write_bytes(f"cam {i}".encode())
        r = _move(client, src, target)
        assert r.status_code == 200, r.text
        names.append(os.path.basename(r.json()["new_path"]))
        assert not src.exists()
    assert names == ["opa_DUP_001.jpg", "opa_DUP_002.jpg", "opa_DUP_003.jpg"]
    assert (target / "opa.jpg").read_bytes()[:2] == b"\xff\xd8"  # the original opa.jpg is untouched
    assert sorted((target / n).read_bytes() for n in names) == [b"cam 0", b"cam 1", b"cam 2"]


def test_move_file_refuses_folders_symlinks_and_outside_paths(client, vault, tmp_path):
    folder = vault / "RAW" / "2019"
    r = _move(client, folder, vault / "library")
    assert r.status_code == 404
    assert folder.is_dir()

    link = vault / "RAW" / "misc" / "link.jpg"
    os.symlink(vault / "RAW" / "misc" / "UPPER.JPEG", link)
    assert _move(client, link, vault / "library").status_code == 404
    assert link.is_symlink()

    outside = tmp_path / "elders"
    outside.mkdir()
    r = _move(client, vault / "RAW" / "misc" / "UPPER.JPEG", outside)
    assert r.status_code == 403
    assert (vault / "RAW" / "misc" / "UPPER.JPEG").exists()

    r = _move(client, vault / "RAW" / "misc" / "UPPER.JPEG", vault / "RAW" / "misc")
    assert r.status_code == 400


def test_removed_dead_routes_are_gone(client, vault):
    """Broken or unused routes were removed; none of them may still move files."""
    photo = vault / "RAW" / "misc" / "UPPER.JPEG"
    for method, url, kwargs in [
        ("post", "/api/raw_duplicates/scan", {}),
        ("post", "/api/raw_duplicates/delete", {}),
        ("post", "/api/delete-to-trash", {"json": {"path": str(photo)}}),
        ("get", "/api/main-folders", {}),
    ]:
        r = getattr(client, method)(url, **kwargs)
        assert r.status_code in (404, 405), url
    assert photo.exists()
    assert not (vault / "TRASH" / "UPPER.JPEG").exists()
