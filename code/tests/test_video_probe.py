"""Videos the QuickTime parser does not read (avi, mkv, mts): date, duration and size by ffprobe."""
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import media_meta
from backend.database import Database

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe not installed")


def _make(path: Path, *args, size="160x120", seconds=1.5):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=10", "-t", str(seconds), *args, "-y", str(path)],
                   check=True, stdin=subprocess.DEVNULL, timeout=60)
    return path


@pytest.fixture(autouse=True)
def _fresh_lookup():
    media_meta._ffprobe_seen[:] = [None, -1e9]
    yield
    media_meta._ffprobe_seen[:] = [None, -1e9]


@needs_ffmpeg
def test_avi_mkv_and_mts_get_their_date_duration_and_size(tmp_path):
    mkv = _make(tmp_path / "a.mkv", "-metadata", "creation_time=2021-06-05T10:20:30Z", "-c:v", "mpeg4",
                size="320x240", seconds=2)
    avi = _make(tmp_path / "b.avi", "-metadata", "date=2019-03-04 05:06:07", "-c:v", "mjpeg")
    mts = _make(tmp_path / "c.mts", "-c:v", "mpeg2video", "-f", "mpegts", size="640x360", seconds=1)
    rows = {p.suffix: media_meta.read_metadata(p, os.stat(p), probe=True) for p in (mkv, avi, mts)}

    assert rows[".mkv"]["taken"] == datetime(2021, 6, 5, 10, 20, 30, tzinfo=timezone.utc).timestamp()
    assert rows[".mkv"]["taken_source"] == "video"
    assert (rows[".mkv"]["width"], rows[".mkv"]["height"], rows[".mkv"]["duration"]) == (320, 240, 2.0)
    # An AVI date has no zone: local time, like EXIF
    assert rows[".avi"]["taken"] == datetime(2019, 3, 4, 5, 6, 7).timestamp()
    assert (rows[".avi"]["width"], rows[".avi"]["height"], rows[".avi"]["duration"]) == (160, 120, 1.5)
    # This transport stream has no date of its own: the file date, but the rest is read
    assert rows[".mts"]["taken_source"] in ("birthtime", "mtime")
    assert (rows[".mts"]["width"], rows[".mts"]["height"], rows[".mts"]["duration"]) == (640, 360, 1.0)
    assert {r["meta_status"] for r in rows.values()} == {media_meta.META_OK}
    assert {r["kind"] for r in rows.values()} == {"video"}


@needs_ffmpeg
def test_a_rotated_video_is_as_shown(tmp_path):
    clip = _make(tmp_path / "r.mkv", "-c:v", "mpeg4", "-metadata:s:v", "rotate=90", size="320x240", seconds=1)
    row = media_meta.read_metadata(clip, os.stat(clip), probe=True)
    assert (row["width"], row["height"], row["orientation"]) == (240, 320, 6)


def test_without_ffprobe_or_outside_the_job_the_video_waits_for_a_later_pass(tmp_path, monkeypatch):
    clip = tmp_path / "oud.avi"
    clip.write_bytes(b"RIFF" + b"\x00" * 200)
    called = []
    monkeypatch.setattr(media_meta, "ffprobe_info", lambda *a, **k: called.append(a) or {})
    row = media_meta.read_metadata(clip, os.stat(clip))                 # a request: never a process
    assert row["meta_status"] == media_meta.META_PROBE and row["taken_source"] in ("birthtime", "mtime")
    monkeypatch.setattr(media_meta.shutil, "which", lambda name: None)
    row = media_meta.read_metadata(clip, os.stat(clip), probe=True)      # the job, without ffprobe
    assert row["meta_status"] == media_meta.META_PROBE and not called


@needs_ffmpeg
def test_a_broken_file_gets_its_file_date_and_is_not_read_again(tmp_path):
    clip = tmp_path / "kapot.mkv"
    clip.write_bytes(b"\x1aE\xdf\xa3" + os.urandom(3000))
    row = media_meta.read_metadata(clip, os.stat(clip), probe=True)
    assert row["meta_status"] == media_meta.META_NONE
    assert row["taken_source"] in ("birthtime", "mtime") and row["duration"] is None


def test_a_hanging_ffprobe_is_stopped_by_the_timeout(tmp_path):
    slow = tmp_path / "ffprobe"
    slow.write_text("#!/bin/sh\nsleep 20\n")
    slow.chmod(0o755)
    clip = tmp_path / "x.mts"
    clip.write_bytes(b"\x47" * 1880)
    started = time.monotonic()
    assert media_meta.ffprobe_info(clip, str(slow), timeout=0.5) == {}
    assert time.monotonic() - started < 5


def test_an_unreadable_file_raises_so_the_pass_reads_it_again(tmp_path):
    clip = tmp_path / "geheim.avi"
    clip.write_bytes(b"RIFF" + b"\x00" * 100)
    os.chmod(clip, 0)
    try:
        with pytest.raises(PermissionError):
            media_meta.ffprobe_info(clip, "/bin/echo")
    finally:
        os.chmod(clip, 0o644)


@pytest.mark.parametrize("text, expected", [
    ("2021-06-05T10:20:30.000000Z", datetime(2021, 6, 5, 10, 20, 30, tzinfo=timezone.utc).timestamp()),
    ("2021-06-05T12:20:30+02:00", datetime(2021, 6, 5, 10, 20, 30, tzinfo=timezone.utc).timestamp()),
    ("2019-03-04 05:06:07", datetime(2019, 3, 4, 5, 6, 7).timestamp()),
    ("2019:03:04 05:06:07", datetime(2019, 3, 4, 5, 6, 7).timestamp()),
    ("Mon Mar 04 05:06:07 2019", datetime(2019, 3, 4, 5, 6, 7).timestamp()),
    ("2019-03-04", datetime(2019, 3, 4).timestamp()),
    ("1904-01-01T00:00:00Z", None),           # a camera without a clock
    ("2999-01-01", None),
    ("gisteren", None),
    ("", None),
])
def test_container_dates(text, expected):
    assert media_meta._video_date(text) == expected


# ---------- the index pass ----------

@needs_ffmpeg
def test_the_index_reads_a_video_again_once_ffprobe_is_there(client, config, monkeypatch):
    from test_media_index import _index, _row
    rel = "RAW/2019/zomer vakantie/camcorder.mts"
    _make(config.VAULT_ROOT / rel, "-c:v", "mpeg2video", "-f", "mpegts", size="640x360", seconds=1)
    real_which = shutil.which
    monkeypatch.setattr(media_meta.shutil, "which", lambda name: None if name == "ffprobe" else real_which(name))
    _index(client)
    row = _row(config, rel)
    assert row["meta_status"] == media_meta.META_PROBE and row["duration"] is None
    # Unchanged folder, nothing else new: still listed again once ffprobe can run
    monkeypatch.setattr(media_meta.shutil, "which", real_which)
    media_meta._ffprobe_seen[:] = [None, -1e9]
    result = _index(client)
    row = _row(config, rel)
    assert result["changed"] >= 1
    assert row["meta_status"] == media_meta.META_OK
    assert (row["width"], row["height"], row["duration"]) == (640, 360, 1.0)
    # ... and then left alone
    assert _index(client)["changed"] == 0


# ---------- migration 180 ----------

def test_migration_180_marks_only_the_videos_ffprobe_reads_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    from backend import elo
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 170, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == 170
        for i, (name, kind, status) in enumerate([("a.avi", "video", "none"), ("b.MKV", "video", "none"),
                                                   ("c.mts", "video", "error"), ("d.mp4", "video", "none"),
                                                   ("e.jpg", "photo", "none"), ("f.m2ts", "video", "ok")]):
            ext = os.path.splitext(name)[1].lower()
            conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, "
                         "seen_at, meta_status) VALUES (?, '/v/RAW', ?, ?, ?, 10, 1, ?, ?, ?, ?)",
                         (f"/v/RAW/{name}", name, kind, ext, now, now, now, status))
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('elo_session_goal', '50', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO recovery_actions (kind, title, created_at) VALUES ('viewer_move', 'x', 1)")
    db = Database(db_path)
    elo.record_comparison(db, "/v/library/a.jpg", "/v/library/b.jpg", "a", "/v/library")
    elo.record_skip(db, "/v/library/a.jpg", "/v/library/c.jpg", "/v/library")
    db.apply_vote("/v/library/d.jpg", 50)
    db.close_all()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM schema_version WHERE version >= 180")
        conn.execute("UPDATE media SET meta_status = 'none' WHERE name IN ('a.avi', 'b.MKV')")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name NOT IN ('schema_version', 'media')")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
        media_before = conn.execute("SELECT * FROM media ORDER BY id").fetchall()
    assert before["elo_comparisons"] and before["file_ratings"] and before["keurer_decisions"] and before["jobs"]
    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 180
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        status = dict(conn.execute("SELECT name, meta_status FROM media"))
        assert status == {"a.avi": "probe", "b.MKV": "probe", "c.mts": "error", "d.mp4": "none",
                          "e.jpg": "none", "f.m2ts": "ok"}
        columns = [r[1] for r in conn.execute("PRAGMA table_info(media)")]
        status_at = columns.index("meta_status")
        after = conn.execute("SELECT * FROM media ORDER BY id").fetchall()
        assert [r[:status_at] + r[status_at + 1:] for r in after] == \
               [r[:status_at] + r[status_at + 1:] for r in media_before]
    db.close_all()


def test_a_move_in_the_app_never_starts_ffprobe(client, config, monkeypatch):
    from backend import media_index
    from test_media_index import _row
    rel = "RAW/2019/zomer vakantie/verhuisd.mkv"
    (config.VAULT_ROOT / rel).write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 500)
    monkeypatch.setattr(media_meta, "ffprobe_info", lambda *a, **k: pytest.fail("ffprobe in a request"))
    db = Database(config.DB_PATH)
    media_index.record_move(db, config, config.VAULT_ROOT / "elders.mkv", config.VAULT_ROOT / rel)
    assert _row(config, rel)["meta_status"] == media_meta.META_PROBE


def test_migration_181_only_adds_the_live_photo_index(tmp_path, monkeypatch):
    from backend import migrations, near_dups
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 180, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        for name, kind, ext in (("IMG_1.HEIC", "photo", ".heic"), ("IMG_1.MOV", "video", ".mov"),
                                ("clip.mov", "video", ".mov")):
            conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, "
                         "seen_at) VALUES (?, '/v/RAW', ?, ?, ?, 10, 1, ?, ?, ?)", (f"/v/RAW/{name}", name, kind, ext,
                                                                                    now, now, now))
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('elo_session_goal', '50', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('phash', '{}', 'paused', 3, ?, ?, '{}')", (now, now))
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 181
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        plan = " ".join(r[3] for r in conn.execute(
            f"EXPLAIN QUERY PLAN SELECT COUNT(*) FROM media m WHERE m.kind = 'video' AND {near_dups._LIVE_VIDEO}"))
        assert "idx_media_photo_stem" in plan
        live = conn.execute(f"SELECT name FROM media m WHERE m.kind = 'video' AND {near_dups._LIVE_VIDEO}").fetchall()
        assert live == [("IMG_1.MOV",)]
    db.close_all()
