"""Duplicate scan: deterministic groups, the rule for which copy stays, honest errors, a safe hash cache."""
import errno
import os
import random
import sqlite3
import stat
import time
from datetime import datetime

import pytest

from backend import scanner
from fixtures.vault_builder import STRAND, STRAND_COPIES


def _scan(client, folder, **params):
    return client.post("/api/duplicates/scan_folder", params={"folder_path": str(folder), **params})


def _write(path, data, ts=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if ts is not None:
        # On macOS an mtime before the birthtime also sets the birthtime back
        os.utime(path, (ts, ts))
    return path


def _keeper(group):
    return group["copies"][group["keep"]]["path"]


def _ts(year):
    return time.mktime((year, 6, 1, 12, 0, 0, 0, 0, -1))


def test_scan_is_deterministic_across_scans_and_cache_states(client, vault, config):
    """SCAN-DUP-DB-2: the copy that stays never depends on disk or thread order, or on what was cached."""
    folder = vault / "Big"
    names = [f"d{i:02d}/f{j}.jpg" for i in range(12) for j in range(3)]
    random.Random(7).shuffle(names)  # created in random order: directory order on disk is not sorted
    data = b"same bytes" * 1000
    for name in names:
        _write(folder / name, data, _ts(2020))

    first = _scan(client, folder).json()
    second = _scan(client, folder).json()  # now fully cached
    assert first["groups"] == second["groups"]
    assert len(first["groups"]) == 1 and len(first["groups"][0]["copies"]) == 36
    # Same dates everywhere: the shortest path, then alphabetical, decides
    assert _keeper(first["groups"][0]) == str(folder / "d00/f0.jpg")

    # Half the cache gone: still the same answer
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("DELETE FROM file_hashes WHERE path LIKE ?", (f"{folder}/d0%",))
    assert _scan(client, folder).json()["groups"] == first["groups"]
    copies = [c["path"] for c in first["groups"][0]["copies"]]
    assert copies == sorted(copies)


def test_scan_skips_empty_hidden_and_excluded_files(client, vault, config):
    """SCAN-DUP-DB-15: empty files are not duplicates of each other; internal folders are never scanned."""
    folder = vault / "Leeg"
    _write(folder / "z1.jpg", b"")
    _write(folder / "z2.mp4", b"")
    _write(folder / "a.jpg", b"x" * 100)
    _write(folder / "._a.jpg", b"x" * 100)
    _write(folder / ".a.jpg", b"x" * 100)
    os.symlink(folder / "a.jpg", folder / "link.jpg")
    _write(config.DUPLICATES_FOLDER / "Leeg" / "a.jpg", b"x" * 100)

    r = _scan(client, folder)
    assert r.status_code == 200
    body = r.json()
    assert body["groups"] == [] and body["empty_skipped"] == 2 and body["total_files"] == 1
    assert body["complete"] is True

    r = _scan(client, vault)
    involved = {c["path"] for g in r.json()["groups"] for c in g["copies"]}
    assert not any("RAW_DUPLICATES" in p or "/." in p or p.endswith("link.jpg") for p in involved)


def test_scan_without_candidates_or_cancelled_answers_200(client, vault, monkeypatch):
    """SCAN-DUP-DB-5: the scanner returned a list where the route expected a dict (500)."""
    folder = vault / "Uniek"
    _write(folder / "a.jpg", b"a" * 100)
    _write(folder / "b.jpg", b"b" * 200)
    r = _scan(client, folder)
    assert r.status_code == 200, r.text
    assert r.json()["groups"] == [] and r.json()["complete"] is True and r.json()["cancelled"] is False

    monkeypatch.setattr(scanner, "is_scan_cancelled", lambda: True)
    r = _scan(client, vault / "RAW")
    assert r.status_code == 200, r.text
    assert r.json()["cancelled"] is True and r.json()["groups"] == [] and r.json()["complete"] is False
    assert client.get("/api/raw_duplicates/progress").json()["status"] == "cancelled"


def test_scan_refuses_folders_that_hold_moved_files(client, vault, config):
    """DUP-SCAN-PROTECTED-ROOT: groups found in RAW_DUPLICATES could never be cleaned."""
    for folder in (config.DUPLICATES_FOLDER / "zz", config.TRASH_FOLDER):
        _write(folder / "a.jpg", b"al verplaatst" * 20)
        _write(folder / "b.jpg", b"al verplaatst" * 20)
    for folder in (config.DUPLICATES_FOLDER, config.DUPLICATES_FOLDER / "zz", config.TRASH_FOLDER):
        r = _scan(client, folder)
        assert r.status_code == 400 and r.json()["detail"] == "Deze map wordt niet op duplicaten gescand"
    assert _scan(client, config.CACHE_FOLDER).status_code == 403


def test_scan_counts_and_reports_unreadable_folders(client, vault):
    """SCAN-DUP-DB-10: a folder it could not read made the scan say 'complete' with files missing."""
    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    folder = vault / "Lees"
    _write(folder / "a.jpg", b"q" * 300)
    _write(folder / "b.jpg", b"q" * 300)
    locked = folder / "op slot"
    _write(locked / "c.jpg", b"q" * 300)
    os.chmod(locked, 0)
    try:
        body = _scan(client, folder).json()
    finally:
        os.chmod(locked, stat.S_IRWXU)
    assert body["complete"] is False and body["errors"] == 1
    assert body["error_samples"][0]["path"] == str(locked)
    assert len(body["groups"]) == 1  # what it could read is still reported
    progress = client.get("/api/raw_duplicates/progress").json()
    assert progress["status"] == "incomplete" and progress["error_message"]


def test_scan_of_a_folder_that_disappears_answers_503(client, vault, monkeypatch):
    folder = vault / "Weg"
    _write(folder / "a.jpg", b"w" * 300)
    _write(folder / "b.jpg", b"w" * 300)

    def unplugged(path):
        if folder.exists():
            folder.rename(vault / "Weg (elders)")
        raise OSError(errno.ENOENT, "No such file or directory")

    monkeypatch.setattr(scanner, "hash_file_xxh3", unplugged)
    r = _scan(client, folder)
    assert r.status_code == 503
    assert client.get("/api/raw_duplicates/progress").json()["status"] == "error"


def test_second_scan_while_one_runs_gets_409(client, vault):
    from backend.progress import SCAN_LOCK
    with SCAN_LOCK:
        assert _scan(client, vault / "RAW").status_code == 409
    assert _scan(client, vault / "RAW").status_code == 200


@pytest.fixture
def dated(vault):
    """One group of three identical copies with different dates and path lengths."""
    data = b"dated" * 500
    return {
        "old_long": _write(vault / "Regels/x/diep/er/a.jpg", data, _ts(2010)),
        "mid_short": _write(vault / "Regels/b.jpg", data, _ts(2015)),
        "new": _write(vault / "Regels/zz/c.jpg", data, _ts(2020)),
    }


@pytest.mark.parametrize("rule, params, expected", [
    ("oldest", {}, "old_long"),
    ("newest", {}, "new"),
    ("shortest", {}, "mid_short"),
    ("folder", {"prefer_folder": "Regels/zz"}, "new"),
])
def test_each_rule_picks_its_copy(client, vault, dated, rule, params, expected):
    body = _scan(client, vault / "Regels", rule=rule, **params).json()
    assert body["rule"] == rule
    [group] = body["groups"]
    assert _keeper(group) == str(dated[expected])


def test_default_rule_is_oldest_and_folder_rule_falls_back_to_oldest(client, vault, dated):
    [group] = _scan(client, vault / "Regels").json()["groups"]
    assert _keeper(group) == str(dated["old_long"])
    _write(vault / "Regels/leeg/x.jpg", b"ander")
    [group] = _scan(client, vault / "Regels", rule="folder", prefer_folder="Regels/leeg").json()["groups"]
    assert _keeper(group) == str(dated["old_long"])  # no copy in the preferred folder


def test_rule_parameters_are_checked(client, vault):
    assert _scan(client, vault / "RAW", rule="random").status_code == 400
    assert _scan(client, vault / "RAW", rule="folder").status_code == 400
    assert _scan(client, vault / "RAW", rule="folder", prefer_folder="/etc").status_code == 403


def _copy(path, capture, file_ts=None, hidden=False, rank=0):
    return {"path": path, "capture_ts": capture, "file_ts": capture if file_ts is None else file_ts,
            "hidden": hidden, "path_len": len(path), "name_rank": rank}


def test_rule_order_hidden_last_then_date_then_path_length_then_alphabet():
    copies = [
        _copy("/v/.verborgen/a.jpg", 1, rank=0),
        _copy("/v/lang/pad/b.jpg", 5, rank=1),
        _copy("/v/kort/c.jpg", 5, rank=2),
        _copy("/v/kort/d.jpg", 5, rank=3),
    ]
    copies[0]["hidden"] = True
    assert scanner.choose_keeper(copies, "oldest") == 2  # hidden loses although oldest; then shortest, alphabetical
    assert scanner.choose_keeper(copies, "newest") == 2
    assert scanner.choose_keeper(copies, "shortest") == 2
    assert scanner.choose_keeper(copies, "folder", "/v/lang") == 1
    assert scanner.choose_keeper(copies, "folder", "/v/lan") == 2  # a name prefix is not the folder
    # EXIF ties (identical bytes): the file date decides
    tie = [_copy("/v/a.jpg", 100, 50, rank=0), _copy("/v/b.jpg", 100, 10, rank=1)]
    assert scanner.choose_keeper(tie, "oldest") == 1 and scanner.choose_keeper(tie, "newest") == 0
    with pytest.raises(ValueError):
        scanner.choose_keeper(copies, "bogus")


def test_hidden_flag_counts_as_hidden(client, vault):
    if not hasattr(stat, "UF_HIDDEN") or not hasattr(os, "chflags"):
        pytest.skip("no file flags on this platform")
    data = b"verstopt" * 100
    a = _write(vault / "Vlag/a.jpg", data, _ts(2010))  # oldest, but hidden in Finder
    b = _write(vault / "Vlag/b.jpg", data, _ts(2020))
    os.chflags(a, stat.UF_HIDDEN)
    [group] = _scan(client, vault / "Vlag").json()["groups"]
    assert group["copies"][0]["hidden"] is True
    assert _keeper(group) == str(b)


def test_capture_date_comes_from_exif(client, vault):
    [group] = [g for g in _scan(client, vault / "RAW").json()["groups"]
               if str(vault / STRAND) in {c["path"] for c in g["copies"]}]
    expected = datetime(2019, 7, 14, 19, 30).timestamp()
    assert {c["capture_source"] for c in group["copies"]} == {"exif"}
    assert {c["capture_ts"] for c in group["copies"]} == {expected}
    assert group["type"] == "photo"
    # The copy dated 2017 on disk is the oldest copy: it stays under the default rule
    assert _keeper(group) == str(vault / STRAND_COPIES[0])


def test_old_untagged_cache_rows_are_rehashed(client, vault, config):
    """SCAN-DUP-DB-7 / REPO-HYGIENE-9: a cached hash of another algorithm hid a duplicate."""
    folder = vault / "Cache"
    a = _write(folder / "a.jpg", b"c" * 400)
    _write(folder / "b.jpg", b"c" * 400)
    st = os.stat(a)
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("INSERT INTO file_hashes (path, size, mtime, hash) VALUES (?, ?, ?, ?)",
                     (str(a), st.st_size, st.st_mtime, "e3b0c44298fc1c149afbf4c8996fb924" * 2))
    [group] = _scan(client, folder).json()["groups"]
    assert len(group["copies"]) == 2
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        algo, ino = conn.execute("SELECT algo, ino FROM file_hashes WHERE path = ?", (str(a),)).fetchone()
    assert algo == scanner.HASH_ALGO and ino == st.st_ino


def test_cache_notices_an_in_place_edit_that_restores_the_mtime(client, vault):
    """SCAN-DUP-DB-8: size+mtime alone let a changed file pass as a duplicate."""
    folder = vault / "Edit"
    _write(folder / "a.jpg", b"e" * 400)
    b = _write(folder / "b.jpg", b"e" * 400)
    assert len(_scan(client, folder).json()["groups"]) == 1
    before = os.stat(b)
    with open(b, "r+b") as f:  # same size, same inode, mtime put back
        f.write(b"EDITED")
    os.utime(b, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert os.stat(b).st_mtime_ns == before.st_mtime_ns
    assert _scan(client, folder).json()["groups"] == []


def test_cache_is_one_query_and_one_write_per_scan(client, vault, config, monkeypatch):
    from backend.routes import duplicates
    calls = {"get": 0, "store": 0}
    real_get, real_store = duplicates.db.get_cached_hashes, duplicates.db.store_hashes

    def get(*a, **k):
        calls["get"] += 1
        return real_get(*a, **k)

    def store(*a, **k):
        calls["store"] += 1
        return real_store(*a, **k)

    monkeypatch.setattr(duplicates.db, "get_cached_hashes", get)
    monkeypatch.setattr(duplicates.db, "store_hashes", store)
    _scan(client, vault / "RAW")
    assert calls == {"get": 1, "store": 1}


def test_scan_prunes_cache_rows_of_files_that_are_gone(client, vault, config):
    folder = vault / "Prune"
    _write(folder / "a.jpg", b"p" * 400)
    b = _write(folder / "b.jpg", b"p" * 400)
    _scan(client, folder)
    os.rename(b, vault / "elders.jpg")
    _scan(client, folder)
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        rows = [r[0] for r in conn.execute("SELECT path FROM file_hashes WHERE path LIKE ?", (f"{folder}/%",))]
    assert rows == [str(folder / "a.jpg")]
