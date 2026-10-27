"""The one move helper every archive move goes through: never overwrite, keep all metadata."""
import errno
import os
import stat
import subprocess
import sys

import pytest

from backend import file_ops
from backend.file_ops import (
    CrossDeviceError, DestinationExistsError, FileOperationError, files_identical, move_no_overwrite,
)

macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS file flags / xattr tool")


@pytest.fixture(params=["native", "link_unlink"])
def rename_method(request, monkeypatch):
    """Run the tests with renamex_np (macOS) and with the os.link fallback."""
    if request.param == "link_unlink":
        monkeypatch.setattr(file_ops, "_renamex_np", None)
    elif file_ops._renamex_np is None:
        pytest.skip("renamex_np not available on this platform")
    return request.param


def test_three_same_named_files_into_one_folder_all_survive(tmp_path, rename_method):
    target = tmp_path / "target"
    target.mkdir()
    results = []
    for i in range(3):
        src = tmp_path / f"camera{i}" / "IMG_0001.JPG"
        src.parent.mkdir()
        src.write_bytes(f"foto {i}".encode())
        results.append(move_no_overwrite(src, target / src.name))
        assert not src.exists()

    assert [p.name for p in results] == ["IMG_0001.JPG", "IMG_0001_DUP_001.JPG", "IMG_0001_DUP_002.JPG"]
    assert sorted(p.read_bytes() for p in target.iterdir()) == [b"foto 0", b"foto 1", b"foto 2"]


def test_existing_destination_is_never_overwritten(tmp_path, rename_method):
    src = tmp_path / "a" / "x.jpg"
    src.parent.mkdir()
    src.write_bytes(b"new")
    dst = tmp_path / "x.jpg"
    dst.write_bytes(b"KEEP ME")

    with pytest.raises(DestinationExistsError):
        move_no_overwrite(src, dst, rename_on_conflict=False)
    assert dst.read_bytes() == b"KEEP ME"
    assert src.read_bytes() == b"new"

    final = move_no_overwrite(src, dst, suffix_prefix="RESTORED")
    assert final.name == "x_RESTORED_001.jpg"
    assert dst.read_bytes() == b"KEEP ME"
    assert final.read_bytes() == b"new"


def test_exclusive_rename_refuses_existing_target(tmp_path, rename_method):
    """The primitive itself is exclusive, so a file appearing after a check cannot be clobbered."""
    src = tmp_path / "src.jpg"
    src.write_bytes(b"src")
    dst = tmp_path / "dst.jpg"
    dst.write_bytes(b"dst")
    with pytest.raises(OSError) as exc:
        file_ops._exclusive_rename(src, dst)
    assert exc.value.errno == errno.EEXIST
    assert src.read_bytes() == b"src" and dst.read_bytes() == b"dst"


def test_metadata_is_preserved(tmp_path, rename_method):
    src = tmp_path / "a" / "old photo.jpg"
    src.parent.mkdir()
    src.write_bytes(b"\xff\xd8 jpeg-ish")
    os.utime(src, (1_100_000_000, 1_200_000_000))
    has_xattr_tool = sys.platform == "darwin" and os.path.exists("/usr/bin/xattr")
    if has_xattr_tool:
        subprocess.run(["/usr/bin/xattr", "-w", "nl.datavault.test", "bewaar mij", str(src)], check=True)
    before = os.stat(src)

    final = move_no_overwrite(src, tmp_path / "b" / src.name)

    after = os.stat(final)
    assert after.st_ino == before.st_ino
    assert after.st_mtime == before.st_mtime == 1_200_000_000
    if hasattr(before, "st_birthtime"):
        assert after.st_birthtime == before.st_birthtime
    if has_xattr_tool:
        value = subprocess.run(["/usr/bin/xattr", "-p", "nl.datavault.test", str(final)],
                               capture_output=True, text=True, check=True).stdout.strip()
        assert value == "bewaar mij"


@macos_only
def test_locked_file_moves_and_keeps_its_flag(tmp_path, rename_method):
    src = tmp_path / "a" / "locked.jpg"
    src.parent.mkdir()
    src.write_bytes(b"locked")
    os.chflags(src, stat.UF_IMMUTABLE)
    final = None
    try:
        final = move_no_overwrite(src, tmp_path / "b" / "locked.jpg")
        assert not os.path.lexists(src)
        assert os.stat(final).st_flags & stat.UF_IMMUTABLE
        assert final.read_bytes() == b"locked"
    finally:
        for p in (src, final):
            if p is not None and os.path.lexists(p):
                os.chflags(p, 0)  # let pytest clean up tmp_path


@macos_only
def test_locked_file_gets_flag_back_when_move_fails(tmp_path):
    src = tmp_path / "locked.jpg"
    src.write_bytes(b"locked")
    dst = tmp_path / "taken.jpg"
    dst.write_bytes(b"taken")
    os.chflags(src, stat.UF_IMMUTABLE)
    try:
        with pytest.raises(DestinationExistsError):
            move_no_overwrite(src, dst, rename_on_conflict=False)
        assert os.stat(src).st_flags & stat.UF_IMMUTABLE
    finally:
        os.chflags(src, 0)


def test_directories_and_symlinks_are_refused(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "inside.jpg").write_bytes(b"x")
    with pytest.raises(FileOperationError):
        move_no_overwrite(folder, tmp_path / "elsewhere")
    assert (folder / "inside.jpg").exists()

    target = tmp_path / "real.jpg"
    target.write_bytes(b"real")
    link = tmp_path / "link.jpg"
    os.symlink(target, link)
    with pytest.raises(FileOperationError):
        move_no_overwrite(link, tmp_path / "moved_link.jpg")
    assert link.is_symlink() and target.exists()

    with pytest.raises(FileOperationError):
        move_no_overwrite(tmp_path / "missing.jpg", tmp_path / "x.jpg")


def test_cross_device_move_is_refused_without_copying(tmp_path, monkeypatch):
    src = tmp_path / "a.jpg"
    src.write_bytes(b"a")

    def exdev(s, d):
        raise OSError(errno.EXDEV, "Cross-device link")

    monkeypatch.setattr(file_ops, "_exclusive_rename", exdev)
    with pytest.raises(CrossDeviceError):
        move_no_overwrite(src, tmp_path / "b" / "a.jpg")
    assert src.read_bytes() == b"a"
    assert not (tmp_path / "b" / "a.jpg").exists()


def test_files_identical_compares_bytes(tmp_path):
    a, b, c, d = (tmp_path / n for n in "abcd")
    a.write_bytes(b"x" * 3_000_000)
    b.write_bytes(b"x" * 3_000_000)
    c.write_bytes(b"x" * 2_999_999 + b"y")
    d.write_bytes(b"x")
    assert files_identical(a, b)
    assert not files_identical(a, c)
    assert not files_identical(a, d)
