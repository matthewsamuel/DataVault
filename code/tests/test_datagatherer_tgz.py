"""
DataGatherer: Google Takeouts as .tgz/.tar.gz are read like a .zip, as a stream (member by
member, never extracted to disk), in the Dutch and the English layout. The raw file is
copied and never changed, reading twice adds nothing, and a .tgz that was only copied by an
earlier version is read on the next import. Everything is synthetic (fake_exports.py).
"""
import gzip
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import time
from pathlib import Path

import pytest

import fake_exports as fx
from backend.datagatherer import daemon, export_kit, location_store, raw_store, registry, state
from fixtures.vault_builder import DATA_OWNER

READ = lambda base: base.lower().endswith((".json", ".html", ".ics"))


def _wait(app, job_id, timeout=60):
    return app.state.jobs.wait(job_id, timeout=timeout)


def _import(client, app):
    r = client.post("/api/datagatherer/sources/google_takeout/import")
    assert r.status_code in (200, 201), r.text
    job = r.json()["job"]
    assert _wait(app, job["id"])["status"] == "done"
    return app.state.jobs.result(job["id"])


def _rows(app, sql, args=()):
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        return conn.execute(sql, args).fetchall()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(folder: Path):
    return sorted((str(p.relative_to(folder)), p.stat().st_size, p.stat().st_mtime_ns, _sha(p))
                  for p in folder.rglob("*") if p.is_file())


def _kinds(app):
    return dict(_rows(app, "SELECT kind, COUNT(*) FROM activity_events WHERE source = 'google_takeout' GROUP BY kind"))


# ---------- reading ----------

def test_a_tgz_lists_the_same_files_as_the_zip_in_dutch_and_english(tmp_path):
    zip_names = sorted(n for n, _ in export_kit.files(fx.english_takeout_zip(tmp_path / "e.zip"), READ))
    tgz = fx.english_takeout_tgz(tmp_path / "takeout-e.tar.gz")
    got = {n: r() for n, r in export_kit.files(tgz, READ)}
    assert sorted(got) == zip_names                         # './' prefix of GNU tar dropped
    with __import__("zipfile").ZipFile(tmp_path / "e.zip") as zf:
        assert all(got[n] == zf.read(n) for n in zip_names)
    dutch = dict(export_kit.files(fx.dutch_takeout_tgz(tmp_path / "takeout-nl.tgz"), READ))
    assert "Takeout/Mijn activiteit/YouTube/MijnActiviteit.json" in dutch
    assert "Takeout/YouTube en YouTube Music/geschiedenis/kijkgeschiedenis.json" in dutch
    assert not any(n.endswith(".jpg") for n in dutch)       # a photo is skipped, never read
    # detect() sees the first members only, and recognises a Takeout in both languages
    takeout = registry.get("google_takeout")
    for path in (tmp_path / "takeout-nl.tgz", tgz):
        names = raw_store.archive_names(path, tar=True)
        assert names and takeout.detect(path, names) == "takeout"
        assert raw_store.archive_names(path) is None        # a connector that reads no tar sees nothing
    assert raw_store.tar_head_names(tmp_path / "takeout-nl.tgz", members=2) == [
        "Takeout/Google Foto's/Foto's uit 2019/IMG_0.jpg", "Takeout/Agenda/Mijn agenda.ics"]
    # Not a tar: no names, nothing yielded, nothing raised
    junk = tmp_path / "kapot.tgz"
    junk.write_bytes(b"geen tar" * 100)
    assert raw_store.tar_head_names(junk) is None and list(raw_store.iter_export_files(junk, READ)) == []


def test_a_tgz_is_read_as_a_stream_and_never_extracted(tmp_path, monkeypatch):
    tgz = fx.dutch_takeout_tgz(tmp_path / "takeout.tgz")
    before = _snapshot(tmp_path)
    opened = []
    real_open = tarfile.open
    monkeypatch.setattr(tarfile, "open", lambda *a, **k: opened.append(k.get("mode")) or real_open(*a, **k))
    monkeypatch.setattr(tarfile.TarFile, "extract", lambda *a, **k: pytest.fail("extract"))
    monkeypatch.setattr(tarfile.TarFile, "extractall", lambda *a, **k: pytest.fail("extractall"))
    items = list(raw_store.iter_export_files(tgz, READ))
    assert opened == ["r|*"]                                # stream mode: it can never seek back
    assert len(items) == 10
    assert _snapshot(tmp_path) == before                    # nothing written next to it, the file unchanged
    # A member can only be read while it is the current one: later, it says so instead of guessing
    with pytest.raises(raw_store.RawStoreError, match="al verder gelezen"):
        items[0][1]()


def test_a_large_records_json_in_a_tgz_is_streamed_with_a_peek(tmp_path, monkeypatch):
    monkeypatch.setattr(location_store, "STREAM_FROM", 10_000)
    monkeypatch.setattr(raw_store, "MAX_MEMBER_BYTES", 20_000)        # stand-in for 1 GB: read() would refuse
    big = json.dumps({"locations": [{"latitudeE7": 510500000 + i, "longitudeE7": 37200000 + i,
                                     "timestamp": f"2019-07-14T10:{i // 60:02d}:{i % 60:02d}Z"} for i in range(400)]},
                     indent=2)
    tgz = fx.write_tgz(tmp_path / "takeout.tgz", {"Takeout/Location History/Records.json": big,
                                                  "Takeout/Keep/Na.json": fx.keep_note()})
    seen = []
    for name, read in raw_store.iter_export_files(tgz, READ):
        if name.endswith("Records.json"):
            fmt, points, _ = location_store.parse_export_file(read)    # peeks 4 KB, then opens again
            seen.append((fmt, len(list(points))))
        else:
            seen.append((name, len(read())))
    assert seen[0] == ("records", 400) and seen[1][0] == "Takeout/Keep/Na.json"


def test_a_gzip_bomb_in_a_tgz_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(raw_store, "BOMB_FLOOR", 1024 * 1024)       # stand-in for 50 MB
    tgz = fx.write_tgz(tmp_path / "takeout.tgz", {"Takeout/Keep/Groot.json": b" " * (8 * 1024 * 1024),
                                                  "Takeout/Keep/Klein.json": fx.keep_note()})
    assert tgz.stat().st_size < 64 * 1024
    with pytest.raises(raw_store.RawStoreError, match="verdacht"):
        for _, read in raw_store.iter_export_files(tgz, READ):
            read()
    # A truncated download: a clear Dutch error, not a crash
    cut = tmp_path / "takeout-cut.tgz"
    whole = fx.dutch_takeout_tgz(tmp_path / "heel.tgz").read_bytes()
    cut.write_bytes(whole[: len(whole) // 2])
    with pytest.raises(raw_store.RawStoreError, match="beschadigd of onvolledig"):
        for _, read in raw_store.iter_export_files(cut, READ):
            read()


# ---------- importing ----------

def test_tgz_takeouts_in_the_archive_are_read_in_place_like_zips(client, app, vault):
    folder = vault / "data" / DATA_OWNER / "RAW_google"
    fx.dutch_takeout_tgz(folder / "takeout-20190801T000000Z-001.tgz")
    fx.english_takeout_tgz(folder / "takeout-20190901T000000Z-001.tar.gz")
    before = _snapshot(folder)
    result = _import(client, app)
    assert result["errors"] == 0 and result["imports"] == 1
    kinds = _kinds(app)
    # The same counts as the unpacked Dutch folder plus the English zip (test_datagatherer_google_location)
    assert kinds["youtube_watch"] == 3 and kinds["youtube_search"] == 2
    assert kinds["search"] == 1 and kinds["maps"] == 1 and kinds["web_visit"] == 2
    assert kinds["calendar"] == 3 and kinds["note"] == 1
    assert _rows(app, "SELECT COUNT(*) FROM location_points WHERE origin = 'google_takeout'")[0][0] == 7
    stats = _rows(app, "SELECT stats FROM dg_raw_imports")[0][0]
    assert {"Mijn activiteit", "My Activity", "Agenda", "Calendar"} <= set(json.loads(stats)["products"])
    assert _snapshot(folder) == before                       # the raw files are never changed
    assert sorted(p.name for p in folder.iterdir()) == ["takeout-20190801T000000Z-001.tgz",
                                                        "takeout-20190901T000000Z-001.tar.gz"]
    # Idempotent: read again, nothing new
    totals = _rows(app, "SELECT COUNT(*) FROM activity_events")[0][0], _rows(app, "SELECT COUNT(*) FROM location_points")[0][0]
    state.update_raw(app.state.db, _rows(app, "SELECT id FROM dg_raw_imports")[0][0], status="pending")
    _import(client, app)
    assert (_rows(app, "SELECT COUNT(*) FROM activity_events")[0][0],
            _rows(app, "SELECT COUNT(*) FROM location_points")[0][0]) == totals


def test_a_tgz_upload_is_copied_unchanged_and_read_once(client, app, tmp_path):
    tgz = fx.dutch_takeout_tgz(tmp_path / "takeout-20240101T000000Z-001.tgz")
    sha = _sha(tgz)
    for attempt in range(2):
        r = client.post(f"/api/datagatherer/sources/google_takeout/upload?name={tgz.name}", content=tgz.read_bytes())
        assert r.status_code in (200, 201), r.text
        result = app.state.jobs.result(_wait(app, r.json()["job"]["id"])["id"])
        if attempt == 0:
            assert result["created"] and _wait(app, result["import_job"])["status"] == "done"
            copy = Path(result["path"])
        else:
            assert not result["created"] and result["import_job"] is None     # the same bytes: already there
    assert copy.name == tgz.name and "Google" in str(copy) and _sha(copy) == sha
    assert _kinds(app)["calendar"] == 3 and _sha(tgz) == sha
    assert _rows(app, "SELECT COUNT(*) FROM dg_raw_imports")[0][0] == 1


def test_a_tgz_in_downloads_is_recognised_by_the_watcher(app, tmp_path):
    downloads = tmp_path / "Downloads"
    fx.english_takeout_tgz(downloads / "takeout-20240101T000000Z-001.tgz")
    fx.write_tgz(downloads / "takeout-20240101T000000Z-002.tgz", {"backup/notities.txt": "geen takeout"})
    for p in downloads.iterdir():
        os.utime(p, (time.time() - 3600, time.time() - 3600))
    daemon.forget_seen()
    found = daemon.candidates(app.state.config, app.state.db, registry.get("google_takeout"), folders=[downloads])
    assert [(c["name"], c["kind"]) for c in found] == [("takeout-20240101T000000Z-001.tgz", "takeout")]


def test_a_tgz_that_an_earlier_version_only_copied_is_read_now(client, app, config, tmp_path):
    tgz = fx.dutch_takeout_tgz(tmp_path / "takeout-20240101T000000Z-001.tgz")
    raw, created = raw_store.archive_file(config, app.state.db, registry.get("google_takeout"), "takeout", tgz, "manual")
    # As version 2 left it: copied, 'imported' with nothing read
    state.update_raw(app.state.db, raw["id"], status="imported", parser_version=2, records_added=0)
    assert _import(client, app)["imports"] == 1
    assert _kinds(app)["note"] == 1
    assert state.get_raw(app.state.db, raw["id"])["parser_version"] == 3
    assert _import(client, app)["imports"] == 0              # once
