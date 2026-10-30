"""
Export (step 3.7): copy a selection, or the best photos of a period, out of the archive
(backend/export.py, routes/export.py). The archive itself is never changed.
"""
import csv
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from backend import export
from fixtures.vault_builder import BURST, HEIC_PHOTO, PORTRAIT, STRAND
from test_media_index import _index, _manager


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _snapshot(vault: Path):
    out = {}
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d != ".datavault_cache"]
        for name in files:
            p = Path(root) / name
            out[str(p)] = (p.stat().st_size, p.stat().st_mtime_ns)
    return out


def _run(client, vault, rels, dest, expect="done", **params):
    body = {"paths": [str(vault / r) for r in rels], "destination": str(dest), **params}
    r = client.post("/api/jobs", json={"type": "export", "params": body})
    if expect == "refused":
        return r
    assert r.status_code in (200, 201), r.text
    job = _manager().wait(r.json()["job"]["id"], timeout=60)
    assert job["status"] == expect, job
    return client.get(f"/api/jobs/{job['id']}/result").json()["result"] if expect == "done" else job


@pytest.fixture
def indexed(client):
    _index(client, full=True)
    return client


def test_originals_are_byte_identical_and_the_archive_is_untouched(indexed, vault, tmp_path):
    rels = [STRAND, "RAW/2020/Café Ümlaut/koffie é à ü.jpg", "RAW/video/clip één.mp4"]
    before = _snapshot(vault)
    os.utime(vault / rels[0], (1_400_000_000, 1_400_000_000))
    before = _snapshot(vault)
    dest = tmp_path / "out" / "Export"
    result = _run(indexed, vault, rels, dest)
    assert result["copied"] == 3 and result["errors"] == 0 and result["complete"]
    for rel in rels:
        copy = dest / Path(rel).name
        assert _sha(copy) == _sha(vault / rel)
    assert int((dest / Path(STRAND).name).stat().st_mtime) == 1_400_000_000
    assert _snapshot(vault) == before
    assert not [p for p in dest.iterdir() if p.name.startswith(".")]      # log and temporary files are gone


def test_manifest_csv_and_json(indexed, vault, tmp_path):
    result = _run(indexed, vault, [STRAND, PORTRAIT], tmp_path / "a", manifest="csv")
    assert result["manifest"] == "manifest.csv"
    rows = list(csv.DictReader(open(tmp_path / "a" / "manifest.csv", encoding="utf-8")))
    assert {r["source_path"] for r in rows} == {str(vault / STRAND), str(vault / PORTRAIT)}
    assert all((tmp_path / "a" / r["exported_as"]).is_file() for r in rows)
    _run(indexed, vault, [STRAND], tmp_path / "b", manifest="json")
    data = json.load(open(tmp_path / "b" / "manifest.json", encoding="utf-8"))
    assert data[0]["source_path"] == str(vault / STRAND)
    _run(indexed, vault, [STRAND], tmp_path / "c", manifest="none")
    assert not (tmp_path / "c" / "manifest.csv").exists() and not (tmp_path / "c" / "manifest.json").exists()


def test_name_collisions_get_a_number_and_nothing_is_overwritten(indexed, vault, tmp_path):
    dest = tmp_path / "Export"
    dest.mkdir()
    (dest / "strand 001.jpg").write_bytes(b"mine, do not touch")
    other = vault / "RAW/misc/map/strand 001.jpg"
    other.parent.mkdir(parents=True)
    shutil.copy2(vault / "RAW/2020/Café Ümlaut/koffie é à ü.jpg", other)
    _index(indexed)
    result = _run(indexed, vault, [STRAND, "RAW/misc/map/strand 001.jpg"], dest)
    assert result["copied"] == 2
    assert (dest / "strand 001.jpg").read_bytes() == b"mine, do not touch"
    assert sorted(p.name for p in dest.glob("strand*")) == ["strand 001 (2).jpg", "strand 001 (3).jpg", "strand 001.jpg"]
    assert {_sha(dest / "strand 001 (2).jpg"), _sha(dest / "strand 001 (3).jpg")} == {_sha(vault / STRAND), _sha(other)}
    # a second export into the same folder adds to it
    _run(indexed, vault, [STRAND], dest)
    assert (dest / "strand 001 (4).jpg").is_file()


def test_resized_keeps_orientation_and_exif(indexed, vault, tmp_path):
    dest = tmp_path / "Export"
    result = _run(indexed, vault, [PORTRAIT, STRAND], dest, mode="resized", max_edge=256)
    assert result["converted"] == 2
    with Image.open(dest / "portret.jpg") as im:
        assert im.size == (128, 256)                      # 400x200 stored sideways, shown 200x400, scaled to 256
        assert im.getexif().get(0x0112, 1) == 1
        assert im.getexif().get(0x010F) == "Apple"
        assert im.getpixel((64, 20))[0] > 150 and im.getpixel((64, 230))[2] > 150   # red on top, blue below
    with Image.open(dest / "strand 001.jpg") as im:
        assert max(im.size) == 256 and im.format == "JPEG"
    assert int((dest / "portret.jpg").stat().st_mtime) == int((vault / PORTRAIT).stat().st_mtime)


def test_a_small_photo_is_not_enlarged(indexed, vault, tmp_path):
    _run(indexed, vault, [STRAND], tmp_path / "e", mode="resized", max_edge=8192)
    with Image.open(tmp_path / "e" / "strand 001.jpg") as im:
        assert im.size == (800, 600)


def test_heic_becomes_jpeg(indexed, vault, tmp_path):
    if not (vault / HEIC_PHOTO).exists():
        pytest.skip("no HEIC encoder")
    result = _run(indexed, vault, [HEIC_PHOTO], tmp_path / "e", mode="resized", max_edge=320)
    assert result["converted"] == 1
    with Image.open(tmp_path / "e" / "IMG_4001.jpg") as im:
        assert im.format == "JPEG" and max(im.size) == 320


def test_undecodable_photo_is_copied_as_is_in_resized_mode(indexed, vault, tmp_path):
    result = _run(indexed, vault, ["RAW/misc/corrupt.jpg", "RAW/video/clip één.mp4"], tmp_path / "e", mode="resized")
    assert result["original_instead"] == 1 and result["copied"] == 2
    assert _sha(tmp_path / "e" / "corrupt.jpg") == _sha(vault / "RAW/misc/corrupt.jpg")
    assert _sha(tmp_path / "e" / "clip één.mp4") == _sha(vault / "RAW/video/clip één.mp4")


def test_structures(indexed, vault, tmp_path):
    rels = [STRAND, "RAW/2020/Café Ümlaut/koffie é à ü.jpg", "RAW/2021/🎉 feest/no exif.jpg"]
    _run(indexed, vault, rels, tmp_path / "m", structure="month")
    assert (tmp_path / "m/2019-07/strand 001.jpg").is_file()
    assert (tmp_path / "m/2020-10/koffie é à ü.jpg").is_file()
    _run(indexed, vault, rels, tmp_path / "f", structure="folders")
    assert (tmp_path / "f/RAW/2019/zomer vakantie/strand 001.jpg").is_file()
    assert (tmp_path / "f/RAW/2020/Café Ümlaut/koffie é à ü.jpg").is_file()


def test_refuses_paths_outside_the_archive_and_missing_files(indexed, vault, tmp_path):
    outside = vault.parent / f"{vault.name}_outside" / "secret.jpg"
    r = _run(indexed, vault, [], tmp_path / "e", expect="refused", )
    assert r.status_code == 400
    body = {"paths": [str(outside), str(vault / "RAW/nietbestaand.jpg"), str(vault / STRAND)],
            "destination": str(tmp_path / "e2")}
    job = indexed.post("/api/jobs", json={"type": "export", "params": body}).json()["job"]
    assert job["params"]["paths"] == [str(vault / STRAND)]
    _manager().wait(job["id"], timeout=60)
    assert not (tmp_path / "e2" / "secret.jpg").exists()


@pytest.mark.parametrize("rel", ["", "RAW", "RAW/2019", "TRASH", "RAW_DUPLICATES/x", ".datavault_cache", "library/new"])
def test_destination_inside_the_vault_is_refused(config, vault, rel):
    with pytest.raises(export.ExportError):
        export.check_destination(str(vault / rel), config)


@pytest.mark.parametrize("path", ["/", "/System", "/System/Library/x", "/usr/local/x", "/Library", "/Applications/Foo",
                                  "/Users", "/Volumes", "~", "~/Library/Mobile Documents", "relatief/pad", "", "   "])
def test_system_and_invalid_destinations_are_refused(config, path):
    with pytest.raises(export.ExportError):
        export.check_destination(path, config)


def test_destination_ok_and_a_file_is_refused(config, tmp_path):
    assert export.check_destination(str(tmp_path / "nieuw" / "map"), config) == Path(os.path.realpath(tmp_path / "nieuw" / "map"))
    (tmp_path / "bestand").write_text("x")
    with pytest.raises(export.ExportError):
        export.check_destination(str(tmp_path / "bestand"), config)
    # a symlink into the vault is followed: still refused
    os.symlink(config.VAULT_ROOT / "RAW", tmp_path / "snelkoppeling")
    with pytest.raises(export.ExportError):
        export.check_destination(str(tmp_path / "snelkoppeling" / "x"), config)


def test_job_refuses_a_destination_in_the_vault(indexed, vault):
    r = _run(indexed, vault, [STRAND], vault / "RAW" / "export", expect="refused")
    assert r.status_code == 400 and "buiten het archief" in r.json()["error"]["message"]
    assert not (vault / "RAW" / "export").exists()


def test_refuses_when_there_is_not_enough_room(indexed, vault, tmp_path, monkeypatch):
    usage = shutil.disk_usage
    monkeypatch.setattr(export.shutil, "disk_usage", lambda p: usage(p)._replace(free=10 * 1024 ** 2))
    r = _run(indexed, vault, [STRAND], tmp_path / "e", expect="refused")
    assert r.status_code == 400 and "Te weinig ruimte" in r.json()["error"]["message"]
    assert not (tmp_path / "e").exists()
    plan = indexed.post("/api/export/plan", json={"paths": [str(vault / STRAND)], "destination": str(tmp_path / "e")}).json()
    assert plan["fits"] is False and "Te weinig ruimte" in plan["destination_error"]
    # exactly enough with the margin is accepted
    monkeypatch.setattr(export.shutil, "disk_usage",
                        lambda p: usage(p)._replace(free=export.needed_bytes(sum(
                            (vault / STRAND).stat().st_size for _ in [0]))))
    assert _run(indexed, vault, [STRAND], tmp_path / "e")["copied"] == 1


def test_plan_reports_sizes_and_resized_estimate(indexed, vault, tmp_path):
    paths = [str(vault / STRAND), str(vault / "RAW/misc/corrupt.jpg"), str(vault / "nergens.jpg")]
    plan = indexed.post("/api/export/plan", json={"paths": paths, "destination": str(tmp_path / "e")}).json()
    assert plan["count"] == 2 and plan["skipped"] == 1 and plan["fits"] is True
    assert plan["total_bytes"] == (vault / STRAND).stat().st_size + (vault / "RAW/misc/corrupt.jpg").stat().st_size
    small = indexed.post("/api/export/plan", json={"paths": paths[:1], "mode": "resized", "max_edge": 256}).json()
    assert small["estimated_bytes"] < plan["estimated_bytes"]
    bad = indexed.post("/api/export/plan", json={"paths": paths[:1], "destination": "/System"}).json()
    assert bad["destination_error"] and bad["fits"] is None
    assert indexed.post("/api/export/plan", json={"paths": paths[:1], "max_edge": 5}).status_code == 400


def test_cancel_leaves_no_partial_files_and_pause_resume_finishes(indexed, vault, tmp_path, monkeypatch):
    rels = [STRAND, "RAW/2020/Café Ümlaut/koffie é à ü.jpg", "RAW/2021/🎉 feest/no exif.jpg",
            "RAW/misc/UPPER.JPEG", "library/Familie/opa.jpg"]
    real_copy = export._copy
    calls = {"n": 0, "action": "pause"}

    def stopping_copy(ctx, src, tmp):
        calls["n"] += 1
        if calls["n"] == 3 and calls["action"]:
            getattr(_manager(), calls["action"])(ctx.job_id)
        real_copy(ctx, src, tmp)

    monkeypatch.setattr(export, "_copy", stopping_copy)
    dest = tmp_path / "Export"
    job = _run(indexed, vault, rels, dest, expect="paused")
    done_names = sorted(p.name for p in dest.iterdir() if not p.name.startswith("."))
    assert len(done_names) == 2
    assert not list(dest.glob(".datavault-export-*.part"))
    calls["action"] = None
    _manager().resume(job["id"])
    final = _manager().wait(job["id"], timeout=60)
    assert final["status"] == "done"
    result = indexed.get(f"/api/jobs/{job['id']}/result").json()["result"]
    assert result["copied"] == 5
    assert sorted(p.name for p in dest.iterdir()) == sorted(
        [Path(r).name for r in rels] + ["manifest.csv"])
    assert len(list(csv.DictReader(open(dest / "manifest.csv", encoding="utf-8")))) == 5
    for rel in rels:
        assert _sha(dest / Path(rel).name) == _sha(vault / rel)


def test_cancel(indexed, vault, tmp_path, monkeypatch):
    rels = [STRAND, "RAW/2020/Café Ümlaut/koffie é à ü.jpg", "RAW/2021/🎉 feest/no exif.jpg"]
    real_copy = export._copy

    def cancelling_copy(ctx, src, tmp):
        if src.name == "no exif.jpg":
            _manager().cancel(ctx.job_id)
        real_copy(ctx, src, tmp)

    monkeypatch.setattr(export, "_copy", cancelling_copy)
    dest = tmp_path / "Export"
    _run(indexed, vault, rels, dest, expect="cancelled")
    names = sorted(p.name for p in dest.iterdir() if not p.name.endswith(".jsonl"))
    assert "no exif.jpg" not in names and len(names) == 2
    assert not list(dest.glob("*.part"))


def test_source_files_are_never_written_by_the_job(indexed, vault, tmp_path, monkeypatch):
    """Only reads: opening a source for writing, or removing/renaming one, would fail the test."""
    src_root = str(vault)
    real_open = open

    def guarded_open(file, mode="r", *a, **k):
        if str(file).startswith(src_root) and any(c in mode for c in "wax+"):
            raise AssertionError(f"write to the archive: {file}")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr("builtins.open", guarded_open)
    for name in ("remove", "unlink", "rename", "replace"):
        real = getattr(os, name)

        def guard(path, *a, _real=real, _name=name, **k):
            if str(path).startswith(src_root):
                raise AssertionError(f"os.{_name} in the archive: {path}")
            return _real(path, *a, **k)
        monkeypatch.setattr(os, name, guard)
    assert _run(indexed, vault, [STRAND, PORTRAIT], tmp_path / "e", mode="resized")["copied"] == 2


# ---------- the best of a period ----------

def _row(i, taken, rating=None, votes=0, folder="/v/a", cam=("C", "1"), size=(800, 600)):
    return {"id": i, "path": f"{folder}/{i}.jpg", "folder": folder, "name": f"{i}.jpg", "taken": taken,
            "rating": rating, "votes": votes, "camera_make": cam[0], "camera_model": cam[1],
            "width": size[0], "height": size[1], "size": 1000 + i}


def test_select_best_is_deterministic_and_ranks_by_rating_and_keep():
    rows = [_row(i, 1_000_000 + i * 10_000) for i in range(10)]
    rows[3]["rating"], rows[3]["votes"] = 1200, 4
    rows[7]["rating"], rows[7]["votes"] = 1100, 2
    first = export.select_best(rows, keep_ids={5}, count=3, percent=None)
    again = export.select_best(list(reversed(rows)), keep_ids={5}, count=3, percent=None)
    assert [r["id"] for r in first] == [r["id"] for r in again] == [3, 5, 7]       # oldest first
    assert first[0]["reasons"] == ["hoog gewaardeerd"] and first[1]["reasons"] == ["gehouden in de Keurer"]


def test_select_best_one_per_burst_and_series():
    burst = [_row(i, 1_000_000 + i, rating=1000 + 100 * i, votes=1) for i in range(5)]     # 1 s apart
    others = [_row(10 + i, 2_000_000 + i * 50_000, folder="/v/b") for i in range(3)]
    chosen = export.select_best(burst + others, set(), count=10, percent=None)
    assert [r["id"] for r in chosen if r["id"] < 5] == [4]
    assert len(chosen) == 4
    # a series of step 3.4 beats the time heuristic: two far-apart photos of one series
    far = [_row(20, 5_000_000), _row(21, 6_000_000, rating=1100, votes=2)]
    chosen = export.select_best(far, set(), count=5, percent=None, series={20: 7, 21: 7})
    assert [r["id"] for r in chosen] == [21]
    # the quality score within the series decides between equals
    chosen = export.select_best(far[:1] + [_row(22, 6_000_000)], set(), count=1, percent=None,
                                series={20: 7, 22: 7}, quality={20: 30.0, 22: 90.0})
    assert [r["id"] for r in chosen] == [22]


def test_select_best_spreads_over_time_and_takes_a_percentage():
    # twelve photos on day one, two on day thirty: equal scores, but the pick is spread out
    rows = [_row(i, 1_000_000 + i * 1000, folder=f"/v/{i}") for i in range(12)]
    rows += [_row(100 + i, 1_000_000 + 30 * 86400 + i * 1000, folder=f"/w/{i}") for i in range(2)]
    chosen = export.select_best(rows, set(), count=2, percent=None)
    assert chosen[0]["taken"] < 1_100_000 and chosen[1]["taken"] > 3_000_000
    pct = export.select_best(rows, set(), count=None, percent=50)
    assert len(pct) == 7
    assert export.select_best([], set(), count=3, percent=None) == []


def test_best_of_api_period_folder_and_validation(indexed, vault, config):
    r = indexed.post("/api/export/best", json={"date_from": "2022-05-01", "date_to": "2022-05-01", "count": 5})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["pool"] == 5 and data["count"] == 1                       # the burst is one series
    assert Path(data["items"][0]["path"]).name.startswith("burst_")
    # a rating on one shot makes it the pick
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("INSERT INTO file_ratings (path, rating, votes) VALUES (?, 1200, 4)", (str(vault / BURST[2]),))
    data = indexed.post("/api/export/best", json={"date_from": "2022-05-01", "date_to": "2022-05-01", "count": 5}).json()
    assert data["items"][0]["path"] == str(vault / BURST[2]) and data["items"][0]["reasons"] == ["hoog gewaardeerd"]
    # a year, in a folder, as a percentage
    data = indexed.post("/api/export/best", json={"date_from": "2019-01-01", "date_to": "2019-12-31",
                                                  "percent": 100, "folder": "RAW/2019"}).json()
    assert {Path(i["path"]).name for i in data["items"]} == {"strand 001.jpg", "IMG_0002.JPG"}
    for body in ({"count": 3, "percent": 10}, {}, {"count": 0}, {"count": 3, "date_from": "nonsens"},
                 {"percent": 500}):
        assert indexed.post("/api/export/best", json=body).status_code == 400
    assert indexed.post("/api/export/best", json={"count": 3, "folder": "/etc"}).status_code == 403


def test_best_of_uses_keurer_keep(indexed, vault, config):
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision) VALUES (?, 'x', 'keep')",
                     (str(vault / "RAW/2019/zomer vakantie/IMG_0002.JPG"),))
    data = indexed.post("/api/export/best", json={"date_from": "2019-01-01", "date_to": "2019-12-31", "count": 1}).json()
    assert Path(data["items"][0]["path"]).name == "IMG_0002.JPG"
    assert data["items"][0]["reasons"] == ["gehouden in de Keurer"]


def test_defaults_choose_folder_and_reveal(indexed, vault, tmp_path, monkeypatch):
    d = indexed.get("/api/export/defaults").json()
    assert d["base"].endswith("Pictures/DataVault Export") and d["free_bytes"] > 0
    calls = []
    monkeypatch.setattr("backend.routes.export.subprocess.run", lambda cmd, **k: calls.append(cmd) or type(
        "R", (), {"returncode": 0, "stdout": "/tmp/gekozen/\n"})())
    assert indexed.post("/api/export/choose-folder").json() == {"path": "/tmp/gekozen"}
    assert indexed.post("/api/export/reveal", json={"path": str(tmp_path)}).json() == {"success": True}
    assert calls[-1] == ["open", str(tmp_path)]
    assert indexed.post("/api/export/reveal", json={"path": str(vault / "RAW")}).status_code == 403
    assert indexed.post("/api/export/reveal", json={"path": str(tmp_path / "nergens")}).status_code == 404


def _dirs(vault: Path):
    return sorted(str(Path(root)) for root, dirs, _ in os.walk(vault) if ".datavault_cache" not in root)


def test_original_folders_never_land_inside_the_archive(client, vault):
    """The vault's parent is an allowed destination; an archive folder named like the vault
    would put its copy (and with it a new folder) inside the archive itself."""
    nested = vault / vault.name / "dieper" / "nested.jpg"
    nested.parent.mkdir(parents=True)
    shutil.copy2(vault / STRAND, nested)
    _index(client, full=True)
    before, dirs = _snapshot(vault), _dirs(vault)
    result = _run(client, vault, [f"{vault.name}/dieper/nested.jpg", STRAND], vault.parent, structure="folders")
    assert result["copied"] == 1 and result["errors"] == 1 and not result["complete"]
    assert "buiten het archief" in result["error_samples"][0]
    assert (vault.parent / "RAW/2019/zomer vakantie/strand 001.jpg").is_file()    # the other one is fine
    assert _snapshot(vault) == before and _dirs(vault) == dirs                    # nothing new in the archive


def test_a_month_folder_that_is_a_symlink_into_the_archive_is_refused(indexed, vault, tmp_path):
    dest = tmp_path / "export"
    dest.mkdir()
    os.symlink(vault / "RAW", dest / "2019-07")
    before, dirs = _snapshot(vault), _dirs(vault)
    result = _run(indexed, vault, [STRAND, "RAW/2020/Café Ümlaut/koffie é à ü.jpg"], dest, structure="month")
    assert result["copied"] == 1 and result["errors"] == 1
    assert (dest / "2020-10/koffie é à ü.jpg").is_file()
    assert _snapshot(vault) == before and _dirs(vault) == dirs
