"""
Shows: which files a show plays and which folders become a show.

- A folder show plays every photo and video below its folder exactly once, in a
  rating-weighted random order (never with repeats, never only the first 500 files).
- Feeling Lucky is a rating-weighted sample without repeats from ALL library files.
- Audio never ends up in a slideshow.
- The library folder itself is not a show; a folder with only subfolders (and a
  .DS_Store) is one, with a preview from below; one unreadable folder does not end
  the list.
"""
import os
import random
from collections import Counter
from pathlib import Path

import pytest

from backend.routes import shows


def _fill(folder: Path, count: int, ext: str = ".jpg", prefix: str = "f") -> list:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        p = folder / f"{prefix}{i:04d}{ext}"
        p.write_bytes(b"x" * (i + 1))   # the slideshow never decodes them here
        paths.append(p)
    return paths


def _content(client, folder: Path):
    r = client.get("/api/shows/folder-content", params={"path": str(folder)})
    assert r.status_code == 200, r.text
    return r.json()


def test_folder_show_plays_every_file_once(client, vault):
    folder = vault / "library" / "Groot"
    made = {p.name for p in _fill(folder, 40)}
    for _ in range(5):
        files = _content(client, folder)
        names = [f["name"] for f in files]
        assert len(names) == len(set(names)) == 40      # no repeats, none missing
        assert set(names) == made


def test_folder_show_is_not_limited_to_the_first_500_files(client, vault):
    folder = vault / "library" / "Heel groot"
    made = {p.name for p in _fill(folder / "a", 400)} | {p.name for p in _fill(folder / "z", 300, prefix="z")}
    names = [f["name"] for f in _content(client, folder)]
    assert len(names) == len(set(names)) == 700
    assert set(names) == made


def test_folder_show_has_no_audio_and_types_come_from_the_server(client, vault):
    folder = vault / "library" / "Gemengd"
    _fill(folder, 3)
    _fill(folder, 2, ext=".mp3", prefix="lied")
    _fill(folder, 1, ext=".mkv", prefix="film")
    files = _content(client, folder)
    assert sorted(f["type"] for f in files) == ["photo"] * 3 + ["video"]
    assert not any(f["name"].endswith(".mp3") for f in files)


def test_folder_show_skips_disabled_subfolders(client, vault):
    folder = vault / "library" / "Ouder"
    _fill(folder / "aan", 2)
    _fill(folder / "uit", 2, prefix="u")
    client.post("/api/shows/settings", json={"path": str(folder / "uit"), "enabled": False})
    assert {f["name"] for f in _content(client, folder)} == {"f0000.jpg", "f0001.jpg"}


def test_weighted_order_has_no_repeats_and_favours_high_ratings():
    random.seed(1234)
    paths = [f"/v/{i}.jpg" for i in range(10)]
    ratings = {"/v/7.jpg": 3000}
    first = Counter()
    for _ in range(3000):
        order = shows.weighted_order(paths, ratings)
        assert sorted(order) == sorted(paths)
        first[order[0]] += 1
    # weight 3000^1.5 vs 1000^1.5: ~5.2x, so ~37% first instead of 10%
    assert first["/v/7.jpg"] / 3000 > 0.25
    assert shows.weighted_order(paths, ratings, k=3)[0] in paths
    assert len(shows.weighted_order(paths, ratings, k=3)) == 3
    assert shows.weighted_order([], ratings) == []


def test_featured_respects_ratings_and_sees_the_whole_library(client, vault, app):
    lib = vault / "library"
    _fill(lib / "aaa veel", 600)
    liked = _fill(lib / "zzz laatst", 20, prefix="top")
    assert client.post("/api/shows/vote", json={"path": str(liked[0]), "vote": "up"}).status_code == 200
    for p in liked:
        shows.db.update_rating(str(p.resolve()), 5000)   # as after 80 up-votes
    random.seed(42)
    featured = client.get("/api/shows/stories").json()["featured"]
    names = [f["name"] for f in featured["files"]]
    assert len(names) == len(set(names)) == 50
    # 20 files with weight (5000/1000)^1.5 = 11x among ~610 with weight 1: about 13 of
    # the 50 (a uniform pick, or only the first 500 files in walk order, gives ~1.6 or 0)
    assert sum(n.startswith("top") for n in names) >= 6
    assert all(f["type"] in ("photo", "video") for f in featured["files"])
    top = next(f for f in featured["files"] if f["name"].startswith("top"))
    assert top["rating"] == 5000


def test_show_list_is_consistent(client, vault):
    lib = vault / "library"
    # Only subfolders (and Finder litter): still a show, with a preview from below
    (lib / "Alleen mappen").mkdir()
    (lib / "Alleen mappen" / ".DS_Store").write_bytes(b"\0" * 10)
    _fill(lib / "Alleen mappen" / "diep" / "dieper", 2)
    # AppleDouble files are never a preview
    _fill(lib / "Met spook", 1)
    (lib / "Met spook" / "._f0000.jpg").write_bytes(b"junk")
    # Only audio: no show
    _fill(lib / "Muziek", 2, ext=".mp3")
    stories = client.get("/api/shows/stories").json()
    by_title = {s["title"]: s for s in stories["shows"]}

    root = str((vault / "library").resolve())
    assert all(s["path"] != root for s in stories["shows"])   # library itself is no show
    assert "library" not in by_title
    assert by_title["Alleen mappen"]["preview"]["path"].endswith("/diep/dieper/" + by_title["Alleen mappen"]["preview"]["name"])
    assert "dieper" in by_title and "diep" in by_title
    assert by_title["Met spook"]["preview"]["name"] == "f0000.jpg"
    assert "Muziek" not in by_title
    for show in stories["shows"]:
        assert not show["preview"]["name"].startswith(".")
        assert show["preview"]["path"].startswith(show["path"] + "/")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a chmod-000 folder")
def test_one_unreadable_folder_does_not_end_the_show_list(client, vault):
    lib = vault / "library"
    _fill(lib / "AAA dicht" / "binnen", 1)
    _fill(lib / "ZZZ open", 1)
    os.chmod(lib / "AAA dicht", 0)
    try:
        titles = {s["title"] for s in client.get("/api/shows/stories").json()["shows"]}
    finally:
        os.chmod(lib / "AAA dicht", 0o755)
    assert {"ZZZ open", "Familie", "Reis Italië"} <= titles

