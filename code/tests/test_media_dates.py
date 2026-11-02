"""
Capture dates (LIVE-PROBE-15, MEDIA-THUMBS-TIMELINE-15) for the timeline, on this day and
photos-by-date; the timeline's folder list, nesting, cache and compact answer
(MEDIA-THUMBS-TIMELINE-12/-13/-14, PERF-6, LIVE-PROBE-22); a bounded on-this-day
(MEDIA-THUMBS-TIMELINE-10, PERF-14) that never shows audio (GAP-10); migration 9.
"""
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from backend import media_dates
from backend.database import Database
from fixtures.vault_builder import _exif, PORTRAIT
from helpers import timeline_files, kept_rows


def _photo(path, exif_dt=None, file_ts=None, color=(90, 90, 90)):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (64, 48), color)
    if exif_dt:
        img.save(path, "JPEG", exif=_exif(exif_dt))
    else:
        img.save(path, "JPEG")
    if file_ts is not None:
        os.utime(path, (file_ts, file_ts))  # on APFS this moves birthtime back too
    return path


def _ts(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp()


def _scan(client, *folders):
    r = client.get("/api/timeline/scan", params={"folders": [str(f) for f in folders]})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_timeline_orders_by_exif_then_file_date(client, vault):
    folder = vault / "RAW/volgorde"
    _photo(folder / "a.jpg", "2010:05:01 10:00:00", file_ts=_ts("2024-01-01 12:00"))
    _photo(folder / "b.jpg", None, file_ts=_ts("2012-03-03 08:00"))
    _photo(folder / "c.jpg", "2005:02:02 09:00:00", file_ts=_ts("2020-06-06 06:00"))
    data = _scan(client, folder)
    files = timeline_files(data)
    assert [Path(f["path"]).name for f in files] == ["c.jpg", "a.jpg", "b.jpg"]
    assert [f["date_source"] for f in files] == ["e", "e", "b"]
    assert files[0]["timestamp"] == _ts("2005-02-02 09:00")
    assert data["date_index"] == {"2005": {"2": 1}, "2010": {"5": 1}, "2012": {"3": 1}}
    assert data["year_boundaries"] == {"2005": 0, "2010": 1, "2012": 2}


def test_a_video_is_dated_by_its_own_creation_time(client, vault):
    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    clip = vault / "RAW/films/oud.mov"
    clip.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x48:rate=5",
                    "-metadata", "creation_time=2014-08-09T10:11:12Z", "-pix_fmt", "yuv420p", "-y", str(clip)],
                   check=True)
    assert media_dates.quicktime_date(clip) == datetime(2014, 8, 9, 10, 11, 12).replace(
        tzinfo=__import__("datetime").timezone.utc).timestamp()
    [row] = timeline_files(_scan(client, clip.parent))
    assert row["date_source"] == "q" and row["type"] == "v"
    # A file that is no QuickTime movie never breaks the parser
    junk = vault / "RAW/films/kapot.mp4"
    junk.write_bytes(b"\x00\x00\x00\x01moov" + b"\xff" * 40)
    assert media_dates.quicktime_date(junk) is None


def test_dates_are_read_once_and_again_after_a_change(client, vault, monkeypatch):
    folder = vault / "RAW/eenmaal"
    for i in range(4):
        _photo(folder / f"f{i}.jpg", f"2011:01:0{i + 1} 10:00:00")
    calls = []
    real = media_dates.embedded_date
    monkeypatch.setattr(media_dates, "embedded_date", lambda p: (calls.append(p), real(p))[1])
    _scan(client, folder)
    assert len(calls) == 4
    calls.clear()
    from backend.routes import timeline
    timeline._scan_cache.clear()        # not the answer cache: the date cache
    _scan(client, folder)
    assert calls == []
    _photo(folder / "f0.jpg", "2001:01:01 10:00:00")  # the photo itself changed
    timeline._scan_cache.clear()
    files = timeline_files(_scan(client, folder))
    assert [Path(c).name for c in calls] == ["f0.jpg"]
    assert Path(files[0]["path"]).name == "f0.jpg" and files[0]["timestamp"] == _ts("2001-01-01 10:00")


def test_the_timeline_answer_is_cached_until_a_folder_changes(client, vault, monkeypatch):
    from backend.routes import timeline
    folder = vault / "RAW/2019"
    walks = []
    real = timeline._walk_media
    monkeypatch.setattr(timeline, "_walk_media", lambda roots, *a: (walks.append(roots), real(roots, *a))[1])
    first = _scan(client, folder)
    second = _scan(client, folder)
    assert first == second and len(walks) == 1
    _photo(folder / "zomer vakantie" / "nieuw.jpg", "2019:07:15 10:00:00")
    third = _scan(client, folder)
    assert len(walks) == 2 and third["total"] == first["total"] + 1


def test_the_answer_is_compact_and_relative_to_the_vault(client, vault):
    data = _scan(client, vault / "RAW")
    assert data["root"] == str(vault)
    assert data["columns"] == ["timestamp", "path", "size", "type", "date_source"]
    assert all(not row[1].startswith("/") for row in data["rows"])
    assert "files" not in data
    files = timeline_files(data)
    assert all(Path(f["path"]).is_file() for f in files)
    assert all(f["type"] in ("p", "v") for f in files)
    # Audio and documents are never in the timeline
    assert not any(f["path"].endswith((".mp3", ".txt")) for f in files)


def test_folder_names_with_commas_work(client, vault):
    folder = vault / "RAW/Parijs, 2019"
    _photo(folder / "eiffel.jpg", "2019:04:04 12:00:00")
    data = _scan(client, folder)
    assert data["total"] == 1 and data["skipped"] == []
    # On this day takes one parameter per folder too
    r = client.get("/api/stats/photo-on-this-day", params={"allowed_folders": [str(folder), str(vault / "library")]})
    assert r.status_code == 200


def test_nested_selections_show_every_photo_once(client, vault):
    alone = _scan(client, vault / "RAW")
    nested = _scan(client, vault / "RAW", vault / "RAW/2019", vault / "RAW/2019/zomer vakantie")
    assert nested["total"] == alone["total"]
    paths = [f["path"] for f in timeline_files(nested)]
    assert len(paths) == len(set(paths))
    assert nested["folders"] == [str(vault / "RAW")]


def test_unknown_folders_are_reported_not_silently_dropped(client, vault):
    data = _scan(client, vault / "RAW/2019", vault / "RAW/bestaat niet")
    assert data["skipped"] == [str(vault / "RAW/bestaat niet")]
    r = client.get("/api/timeline/scan", params={"folders": [str(vault / "RAW/bestaat niet")]})
    assert r.status_code == 404


def _today_years_ago(years, hour=12):
    now = datetime.now()
    try:
        return now.replace(year=now.year - years, hour=hour, minute=0, second=0, microsecond=0)
    except ValueError:  # 29 February
        return now.replace(year=now.year - years, day=28, hour=hour, minute=0, second=0, microsecond=0)


def test_on_this_day_uses_the_capture_date_and_never_audio(client, vault):
    folder = vault / "library/Vandaag"
    exif_day = _today_years_ago(4)
    _photo(folder / "echt.jpg", exif_day.strftime("%Y:%m:%d %H:%M:%S"), file_ts=time.time() - 86400 * 40)
    # Copied today years ago by file date, but taken on another day: not 'on this day'
    other = _photo(folder / "kopie.jpg", "2003:01:01 10:00:00" if datetime.now().strftime("%m-%d") != "01-01"
                   else "2003:02:02 10:00:00", file_ts=_today_years_ago(2).timestamp())
    song = folder / "liedje.mp3"
    shutil.copy2(next((vault / "RAW/audio").iterdir()), song)
    os.utime(song, (_today_years_ago(3).timestamp(),) * 2)
    r = client.get("/api/stats/photo-on-this-day", params={"allowed_folders": [str(folder)]})
    data = r.json()
    names = {Path(p["path"]).name: p for p in data["photos"]}
    assert set(names) == {"echt.jpg"}, names
    assert names["echt.jpg"]["year"] == exif_day.year
    assert names["echt.jpg"]["type"] == "photo" and names["echt.jpg"]["date_source"] == "exif"
    assert data["complete"] is True
    assert other.exists()


def test_on_this_day_work_is_bounded_and_the_date_cache_fills_the_gap(client, vault, monkeypatch):
    from backend.routes import stats
    root = vault / "library/Veel"
    day = _today_years_ago(6)
    for i in range(12):
        _photo(root / f"map{i:02d}" / "foto.jpg", day.strftime("%Y:%m:%d %H:%M:%S"))
    monkeypatch.setattr(stats, "OTD_MAX_FOLDERS", 3)
    data = client.get("/api/stats/photo-on-this-day", params={"allowed_folders": [str(root)]}).json()
    assert data["complete"] is False and len(data["photos"]) <= 3
    # Once the dates are known (here: through the timeline), the cache answers at once
    _scan(client, root)
    monkeypatch.setattr(stats, "OTD_MAX_FOLDERS", 0)
    data = client.get("/api/stats/photo-on-this-day", params={"allowed_folders": [str(root)]}).json()
    assert len(data["photos"]) == 10 and data["complete"] is True
    # A cached row whose file is gone or changed is not shown
    (root / "map00" / "foto.jpg").unlink()
    _photo(root / "map01" / "foto.jpg", "1999:01:01 10:00:00")
    shown = {p["path"] for p in client.get("/api/stats/photo-on-this-day",
                                           params={"allowed_folders": [str(root)]}).json()["photos"]}
    assert str(root / "map00" / "foto.jpg") not in shown and str(root / "map01" / "foto.jpg") not in shown


def test_photos_by_date_uses_the_capture_date(client, vault):
    folder = vault / "RAW/datum"
    _photo(folder / "verjaardag.jpg", "2016:10:28 08:00:00", file_ts=_ts("2022-02-02 10:00"))
    data = client.get("/api/stats/photos-by-date", params={"date": "2016-10-28"}).json()
    names = {Path(p["path"]).name for p in data["photos"]}
    assert {"verjaardag.jpg", Path(PORTRAIT).name} <= names
    data = client.get("/api/stats/photos-by-date", params={"date": "2022-02-02"}).json()
    assert "verjaardag.jpg" not in {Path(p["path"]).name for p in data["photos"]}
    assert client.get("/api/stats/photos-by-date", params={"date": "28-10-2016"}).status_code == 400


def test_migration_9_adds_the_date_cache_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 8, monkeypatch)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO jobs (type, params, status, created_at, updated_at) "
                     "VALUES ('dup_scan', '{}', 'done', 1, 2)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 9
        for table, rows in before.items():
            assert kept_rows(conn, table, rows) == rows, table
        columns = {row[1] for row in conn.execute("PRAGMA table_info(media_dates)")}
        assert columns == {"path", "size", "mtime_ns", "taken", "source", "updated_at"}
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'idx_media_dates_taken'").fetchone()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v8-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_recent_keurer_decisions(1)[0]["dest_ino"] == 77
    db.store_media_dates([("/v/RAW/a.jpg", 10, 20, 1e9, "exif")])
    assert db.get_media_dates(["/v/RAW/a.jpg", "/v/x"]) == {"/v/RAW/a.jpg": (10, 20, 1e9, "exif")}
    db.close_all()


def test_an_unreadable_folder_never_leaves_a_partial_timeline_in_the_cache(client, vault):
    from backend.routes import timeline
    folder = vault / "RAW/2019"
    full = _scan(client, folder)
    assert full["complete"] is True and full["errors"] == 0
    inner = folder / "zomer vakantie"
    known = [str(p) for p in inner.iterdir() if p.suffix.lower() in (".jpg", ".jpeg")]
    assert known and set(timeline.db.get_media_dates(known)) == set(known)
    timeline._scan_cache.clear()
    os.chmod(inner, 0o000)
    try:
        partial = _scan(client, folder)
        assert partial["complete"] is False and partial["errors"] >= 1
        assert partial["total"] < full["total"]
        # The dates of the files that could not be reached are not pruned
        assert set(timeline.db.get_media_dates(known)) == set(known)
    finally:
        os.chmod(inner, 0o755)
    again = _scan(client, folder)
    assert again["complete"] is True
    assert again["total"] == full["total"] and again["rows"] == full["rows"]


def test_a_scan_while_the_vault_goes_away_is_not_cached(client, vault, monkeypatch):
    from backend.routes import timeline
    folder = vault / "RAW/2019"
    real = media_dates.capture_dates
    away = vault.parent / "weg"

    def unplug_during(*args, **kwargs):
        result = real(*args, **kwargs)
        vault.rename(away)
        return result

    monkeypatch.setattr(media_dates, "capture_dates", unplug_during)
    try:
        r = client.get("/api/timeline/scan", params={"folders": [str(folder)]})
    finally:
        away.rename(vault)
    assert r.status_code == 200 and r.json()["complete"] is False
    # Kept only for the rest of its own paging, never as a complete answer
    assert all(complete is False for _, _, complete, _ in timeline._scan_cache.values())
    monkeypatch.setattr(media_dates, "capture_dates", real)
    assert client.get("/api/timeline/scan", params={"folders": [str(folder)]}).json()["complete"] is True


def test_a_file_that_cannot_be_read_gets_no_stored_date(client, vault):
    from backend.routes import timeline
    folder = vault / "RAW/2018/x"
    photo = _photo(folder / "oud.jpg", "2005:05:05 10:00:00")
    os.chmod(photo, 0o000)
    try:
        with pytest.raises(PermissionError):
            media_dates.embedded_date(photo)
        data = _scan(client, folder)
        [row] = timeline_files(data)
        assert row["date_source"] in ("b", "m") and data["complete"] is False
        assert timeline.db.get_media_dates([str(photo)]) == {}
    finally:
        os.chmod(photo, 0o644)
    [row] = timeline_files(_scan(client, folder))
    assert row["date_source"] == "e" and row["timestamp"] == _ts("2005-05-05 10:00")
    names = {Path(p["path"]).name for p in
             client.get("/api/stats/photos-by-date", params={"date": "2005-05-05"}).json()["photos"]}
    assert "oud.jpg" in names


def test_a_file_without_a_date_of_its_own_is_not_a_read_error(vault):
    garbage = vault / "RAW/rommel.jpg"
    garbage.write_bytes(b"geen foto" * 50)
    assert media_dates.embedded_date(garbage) == (None, None)
    clip = vault / "RAW/rommel.mp4"
    clip.write_bytes(b"\x00\x00\x00\x01moov" + b"\xff" * 40)
    assert media_dates.embedded_date(clip) == (None, None)
    errors = []
    st = os.stat(garbage)
    assert media_dates.capture_dates([(str(garbage), st)], read_errors=errors) == {
        str(garbage): media_dates.file_date(st)}
    assert errors == []


def test_nothing_is_stored_when_the_vault_is_gone_by_the_end(tmp_path):
    class FakeDb:
        stored = None

        def get_media_dates(self, paths):
            return {}

        def store_media_dates(self, rows):
            self.stored = rows

    photo = _photo(tmp_path / "v" / "a.jpg", "2007:07:07 07:00:00")
    db = FakeDb()
    dates = media_dates.capture_dates([(str(photo), os.stat(photo))], db, vault_root=tmp_path / "weg")
    assert dates[str(photo)][1] == "exif" and db.stored is None
    media_dates.capture_dates([(str(photo), os.stat(photo))], db, vault_root=tmp_path / "v")
    assert db.stored and db.stored[0][0] == str(photo)


def _giant_jpeg(path, exif_dt, width=32000, height=20000):
    """A 64x64 JPEG with EXIF whose header states 640 MP, above Pillow's limit (2 x 300 MP)."""
    import io
    import struct
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 10)).save(buf, "JPEG", exif=_exif(exif_dt))
    data = bytearray(buf.getvalue())
    sof = data.find(b"\xff\xc0")
    struct.pack_into(">HH", data, sof + 5, height, width)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return path


def test_an_image_above_pillows_size_limit_still_gives_its_exif_date(client, vault):
    # R25B-2: such panoramas get a thumbnail, so they must land on their own date too
    folder = vault / "RAW/panorama"
    pano = _giant_jpeg(folder / "pano.jpg", "2015:06:01 12:00:00")
    with pytest.raises(Image.DecompressionBombError):
        Image.open(pano)
    assert media_dates.exif_date(pano) == _ts("2015-06-01 12:00")
    [row] = timeline_files(_scan(client, folder))
    assert row["date_source"] == "e" and row["timestamp"] == _ts("2015-06-01 12:00")
    # A TIFF above the limit (here a lowered one) too
    tif = folder / "scan.tif"
    Image.new("RGB", (300, 200)).save(tif, exif=_exif("2011:02:03 04:05:06"))
    import backend.thumbnail  # noqa: F401  (sets the limit this test lowers)
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 1000
    try:
        with pytest.raises(Image.DecompressionBombError):
            Image.open(tif)
        assert media_dates.exif_date(tif) == _ts("2011-02-03 04:05") + 6
    finally:
        Image.MAX_IMAGE_PIXELS = old


def test_a_huge_png_is_never_decoded_to_look_for_a_date(tmp_path, monkeypatch):
    from backend import thumbnail
    png = tmp_path / "groot.png"
    Image.new("L", (400, 300), 100).save(png)
    monkeypatch.setattr(thumbnail, "BIG_IMAGE_PIXELS", 1000)
    loads = []
    monkeypatch.setattr(Image.Image, "load", lambda self: loads.append(self.size))
    from PIL import ImageFile
    monkeypatch.setattr(ImageFile.ImageFile, "load", lambda self: loads.append(self.size))
    assert media_dates.exif_date(png) is None
    assert loads == []
