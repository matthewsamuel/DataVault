"""
One exclusion rule for every walk over the archive (backend/paths.py): never the
app's own folders, TRASH or RAW_DUPLICATES (by identity, not by name), no hidden
or '._' AppleDouble files and no symlinks. A user folder called 'dashboard' or
'trash' somewhere in the archive stays visible (GAP-8).
"""
import os
import time
from datetime import datetime

import pytest

from backend import paths
from fixtures.vault_builder import STRAND, STRAND_COPIES

DAY = datetime(2001, 2, 3, 12, 0, 0)
BIG = 3 * 1024 * 1024


def _plant(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    ts = time.mktime(DAY.timetuple())
    os.utime(path, (ts, ts))
    return path


@pytest.fixture
def planted(vault, config, monkeypatch):
    """Files with 'zoekmij' in the name, dated DAY, 3 MB each: one visible, the rest excluded."""
    jpeg = (vault / STRAND).read_bytes()
    data = jpeg + b"\0" * (BIG - len(jpeg))
    app_dir = vault / "dashboard"
    monkeypatch.setattr(paths, "APP_DIR", app_dir)
    hidden = {
        "trash": _plant(config.TRASH_FOLDER / "zoekmij trash.jpg", data),
        "dups": _plant(config.DUPLICATES_FOLDER / "misc" / "zoekmij dup.jpg", data),
        "cache": _plant(config.CACHE_FOLDER / "zoekmij cache.jpg", data),
        "app": _plant(app_dir / "code" / "frontend" / "zoekmij app.jpg", data),
        "appledouble": _plant(vault / "RAW" / "2019" / "._zoekmij.jpg", data),
        "dotfile": _plant(vault / "RAW" / "2019" / ".zoekmij verborgen.jpg", data),
        "dotfolder": _plant(vault / "RAW" / ".verborgen map" / "zoekmij.jpg", data),
    }
    link = vault / "RAW" / "2019" / "zoekmij link.jpg"
    os.symlink(vault / STRAND, link)
    hidden["symlink"] = link
    visible = {
        "dashboard": _plant(vault / "werk" / "dashboard" / "zoekmij screenshot.jpg", data),
        "trash_named": _plant(vault / "RAW" / "TRASH" / "zoekmij in eigen map.jpg", data),
    }
    return {"hidden": hidden, "visible": visible}


def _tree_paths(nodes):
    out = []
    for n in nodes:
        out.append(n["path"])
        out.extend(_tree_paths(n["children"]))
    return out


def _assert_only_visible(found, planted):
    found = {str(p) for p in found}
    for name, p in planted["hidden"].items():
        assert str(p) not in found, name
    for name, p in planted["visible"].items():
        assert str(p) in found, name


def test_folder_tree(client, vault, config, planted):
    tree = _tree_paths(client.get("/api/folders").json()["folders"])
    for excluded in [config.TRASH_FOLDER, config.DUPLICATES_FOLDER, config.CACHE_FOLDER,
                     vault / "dashboard", vault / "RAW" / ".verborgen map", vault / "RAW/misc/link_to_outside"]:
        assert str(excluded) not in tree
    assert str(vault / "werk" / "dashboard") in tree           # GAP-8
    assert str(vault / "RAW" / "TRASH") in tree                 # only the real TRASH is excluded


def test_timeline_folders_and_scan(client, vault, config, planted):
    tree = _tree_paths(client.get("/api/timeline/folders").json()["folders"])
    assert str(config.TRASH_FOLDER) not in tree and str(vault / "dashboard") not in tree
    assert str(vault / "werk" / "dashboard") in tree
    files = client.get("/api/timeline/scan", params={"folders": str(vault)}).json()["files"]
    _assert_only_visible([f["path"] for f in files], planted)


def test_viewer_listing(client, vault, config, planted):
    root = {i["path"] for i in client.get("/api/media", params={"folder": str(vault)}).json()["files"]}
    assert str(config.TRASH_FOLDER) not in root and str(config.DUPLICATES_FOLDER) not in root
    assert str(vault / "dashboard") not in root and str(vault / "werk") in root
    listed = {i["path"] for i in client.get("/api/media", params={"folder": str(vault / "RAW/2019")}).json()["files"]}
    assert not any("zoekmij" in p for p in listed)  # '._', dotfile and symlink are all hidden
    misc = {i["name"] for i in client.get("/api/media", params={"folder": str(vault / "RAW/misc")}).json()["files"]}
    assert "link_to_outside" not in misc and ".hidden.jpg" not in misc


def test_search_photos(client, planted):
    photos = client.get("/api/stats/search-photos", params={"query": "zoekmij", "limit": 50}).json()["photos"]
    _assert_only_visible([p["path"] for p in photos], planted)


def test_photos_by_date(client, planted):
    photos = client.get("/api/stats/photos-by-date", params={"date": DAY.strftime("%Y-%m-%d")}).json()["photos"]
    _assert_only_visible([p["path"] for p in photos], planted)


def test_largest_files(client, vault, planted):
    files = client.get("/api/stats/largest-files", params={"limit": 50}).json()["files"]
    _assert_only_visible([vault / f["path"] for f in files], planted)


def test_photo_on_this_day(client, vault, planted, monkeypatch):
    import backend.routes.stats as stats_mod

    class FakeNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return DAY.replace(year=2026)
    monkeypatch.setattr(stats_mod, "datetime", FakeNow)
    photos = client.get("/api/stats/photo-on-this-day").json()["photos"]
    found = {p["path"] for p in photos}
    for name, p in planted["hidden"].items():
        assert str(p) not in found, name
    assert found and found <= {str(p) for p in planted["visible"].values()}


def test_duplicates_scan_never_picks_hidden_or_excluded_files(client, vault, config, planted):
    """LIVE-PROBE-10: the hidden copy (.hidden.jpg) was chosen as the keeper of every visible copy."""
    for rel in ["RAW/2019/._strand.jpg", "RAW/a/.strand.jpg"]:
        _plant(vault / rel, (vault / STRAND).read_bytes())
    scan = client.post("/api/duplicates/scan_folder", params={"folder_path": str(vault)}).json()
    involved = {d["path"] for d in scan["duplicates"]} | {d["duplicate_of"] for d in scan["duplicates"]}
    assert not any("/." in p for p in involved), involved
    for p in planted["hidden"].values():
        assert str(p) not in involved
    for folder in (config.TRASH_FOLDER, config.DUPLICATES_FOLDER, config.CACHE_FOLDER):
        assert not any(p.startswith(str(folder)) for p in involved)
    visible_copies = {str(vault / STRAND)} | {str(vault / r) for r in STRAND_COPIES if "/." not in r}
    assert visible_copies <= involved


def test_keurer_lists_no_hidden_appledouble_or_symlinked_files(client, vault, planted):
    folder = vault / "RAW" / "2019"
    _plant(folder / "gewoon.jpg", (vault / STRAND).read_bytes())
    names = {f["name"] for f in client.get("/api/keurer/files", params={"folder": str(folder)}).json()["files"]}
    assert names == {"gewoon.jpg"}


def test_shows_skip_excluded_files(client, vault, planted):
    lib = vault / "library" / "Familie"
    _plant(lib / "._opa.jpg", b"junk")
    os.symlink(vault / STRAND, lib / "link.jpg")
    files = client.get("/api/shows/folder-content", params={"path": str(lib)}).json()
    assert {f["name"] for f in files} == {"opa.jpg"}
    stories = client.get("/api/shows/stories").json()
    for show in stories["shows"]:
        assert not show["preview"]["name"].startswith(".")
    for f in (stories["featured"] or {}).get("files", []):
        assert not f["name"].startswith(".") and f["name"] != "link.jpg"


def test_walk_archive_matches_os_walk_without_exclusions(config, vault):
    """On a folder with nothing to exclude, walk_archive is a drop-in for os.walk."""
    top = vault / "library"
    ours = sorted((r, sorted(d), sorted(f)) for r, d, f in paths.walk_archive(top, config))
    theirs = sorted((r, sorted(d), sorted(f)) for r, d, f in os.walk(top))
    assert ours == theirs


def test_walk_archive_allows_pruning(config, vault):
    seen = []
    for root, dirs, _ in paths.walk_archive(vault / "RAW", config):
        seen.append(root)
        dirs[:] = [d for d in dirs if d != "2019"]
    assert str(vault / "RAW" / "2019") not in seen and str(vault / "RAW" / "2020") in seen


def test_excluded_folder_matched_in_other_spelling(config, vault, monkeypatch):
    """The filter compares identities, so a config spelled differently still hides the folder."""
    config.TRASH_FOLDER.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "TRASH_FOLDER", vault / "RAW" / ".." / "TRASH")
    roots = [r for r, _, _ in paths.walk_archive(vault, config)]
    assert str(vault / "TRASH") not in roots
