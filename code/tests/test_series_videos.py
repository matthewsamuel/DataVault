"""Videos in 'Beste uit reeks': one frame per video (ffmpeg, in the phash job), pairs only between videos."""
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image

from backend import near_dups, thumbnail
from test_media_index import _index
from test_near_dups import _run, _scene, _series

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

FOLDER = "RAW/2021/video's"


def _clip(path: Path, picture, *args, seconds=2):
    """A short video that shows one picture (a still scene), encoded with the given options."""
    path.parent.mkdir(parents=True, exist_ok=True)
    still = path.with_suffix(".still.png")
    picture.save(still)
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-loop", "1", "-i", str(still), "-t", str(seconds),
                        "-r", "10", *args, "-y", str(path)], check=True, stdin=subprocess.DEVNULL, timeout=60)
    finally:
        still.unlink()
    return path


@pytest.fixture
def videos(config):
    root = config.VAULT_ROOT
    made = {
        "origineel": _clip(root / FOLDER / "zee origineel.mp4", _scene(51), "-c:v", "mpeg4", "-q:v", "2"),
        "kopie": _clip(root / FOLDER / "zee kopie klein.mkv", _scene(51), "-c:v", "mpeg4", "-q:v", "9",
                       "-vf", "scale=400:300"),
        "anders": _clip(root / FOLDER / "bos.mp4", _scene(52), "-c:v", "mpeg4"),
    }
    # A Live Photo: the photo and its video side by side, with the same name
    _scene(53).save(root / FOLDER / "IMG_7001.JPG", quality=90)
    made["live"] = _clip(root / FOLDER / "IMG_7001.MOV", _scene(53), "-c:v", "mpeg4", "-f", "mov")
    # The same picture as a photo elsewhere: a photo and a video never form a series
    (root / "RAW/2021/los").mkdir(parents=True, exist_ok=True)
    _scene(51).save(root / "RAW/2021/los/zee foto.jpg", quality=90)
    return made


def _rows(config, sql, args=()):
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        return conn.execute(sql, args).fetchall()


def test_two_copies_of_a_video_form_a_series_of_videos_only(client, config, videos):
    _index(client)
    result = _run(client)
    assert result["videos"] >= 3
    items = _series(client)["items"]
    video_series = [s for s in items if s["media"] == "video"]
    assert [sorted(m["name"] for m in s["members"]) for s in video_series] == \
        [["zee kopie klein.mkv", "zee origineel.mp4"]]
    series = video_series[0]
    assert {m["kind"] for m in series["members"]} == {"video"}
    assert all(m["duration"] for m in series["members"])
    # The larger, sharper original is the suggested best
    assert next(m for m in series["members"] if m["best"])["name"] == "zee origineel.mp4"
    # No series mixes a photo and a video; the Live Photo video is not compared at all
    for s in items:
        assert len({m["kind"] for m in s["members"]}) == 1, s
    live = _rows(config, "SELECT COUNT(*) FROM media_phash h JOIN media m ON m.id = h.media_id WHERE m.name = ?",
                 ("IMG_7001.MOV",))[0][0]
    assert live == 0
    status = client.get("/api/series/status").json()
    assert status["pending"] == 0 and status["video_frames"] is True and status["videos"] >= 3


def test_a_video_is_decoded_once_until_it_changes(client, config, videos, monkeypatch):
    _index(client)
    _run(client)
    calls = []
    original = near_dups.analyse_video
    monkeypatch.setattr(near_dups, "analyse_video", lambda path, *a: calls.append(path) or original(path, *a))
    assert _run(client)["analysed"] == 0 and calls == []
    # Edited in place (other size and mtime): read again, and only that one
    time.sleep(0.01)
    _clip(videos["anders"], _scene(54), "-c:v", "mpeg4", seconds=3)
    _index(client, full=True)
    assert _run(client)["analysed"] == 1 and [Path(p).name for p in calls] == ["bos.mp4"]


def test_without_ffmpeg_videos_are_left_out_and_nothing_waits(client, config, videos, monkeypatch):
    monkeypatch.setattr(thumbnail, "have_ffmpeg", lambda: False)
    _index(client)
    result = _run(client)
    assert result["videos"] == 0
    assert _rows(config, "SELECT COUNT(*) FROM media_phash h JOIN media m ON m.id = h.media_id "
                         "WHERE m.kind = 'video'")[0][0] == 0
    status = client.get("/api/series/status").json()
    assert status["pending"] == 0 and status["video_frames"] is False
    assert all(s["media"] == "photo" for s in _series(client)["items"])


def test_ffmpeg_that_disappears_mid_run_stores_nothing_for_the_video(tmp_path, monkeypatch):
    clip = _clip(tmp_path / "a.mp4", _scene(55), "-c:v", "mpeg4")
    st = clip.stat()
    monkeypatch.setattr(near_dups.shutil if hasattr(near_dups, "shutil") else shutil, "which", lambda name: None)
    status, figures, _ = near_dups._analyse_row((1, str(clip), st.st_size, st.st_mtime_ns, "video"))
    assert status == near_dups.SKIPPED and figures is None


def test_a_hanging_ffmpeg_is_stopped_and_the_video_counts_as_unreadable(tmp_path, monkeypatch):
    slow = tmp_path / "ffmpeg"
    slow.write_text("#!/bin/sh\nsleep 20\n")
    slow.chmod(0o755)
    clip = tmp_path / "b.mp4"
    clip.write_bytes(b"\x00" * 1000)
    monkeypatch.setattr(shutil, "which", lambda name: str(slow) if name == "ffmpeg" else None)
    monkeypatch.setattr(near_dups, "VIDEO_TIMEOUT", 0.5)
    started = time.monotonic()
    with pytest.raises(near_dups.Undecodable):
        near_dups.analyse_video(str(clip))
    assert time.monotonic() - started < 5


def test_a_video_frame_is_fingerprinted_like_the_photo_it_shows(tmp_path):
    clip = _clip(tmp_path / "c.mp4", _scene(56), "-c:v", "mpeg4", "-q:v", "2")
    photo = tmp_path / "c.jpg"
    _scene(56).save(photo, quality=92)
    frame, still = near_dups.analyse_video(str(clip)), near_dups.analyse(str(photo))
    assert near_dups.hamming(frame["phash"], still["phash"]) <= 4
    assert near_dups.color_distance(frame["colors"], still["colors"]) <= 6


def test_the_done_message_names_photos_and_videos():
    base = {"series": 2, "undecodable": 0, "errors": 0}
    assert near_dups.run_message({**base, "analysed": 5}) == "Klaar: 5 foto's vergeleken, 2 reeksen"
    assert near_dups.run_message({**base, "analysed": 2, "videos_analysed": 2}) == "Klaar: 2 video's vergeleken, 2 reeksen"
    assert near_dups.run_message({**base, "analysed": 4, "videos_analysed": 1}) == \
        "Klaar: 3 foto's en 1 video vergeleken, 2 reeksen"


def _opens_on(path: Path, picture, lead: str = "black", lead_seconds=2, seconds=2):
    """A video that opens on lead_seconds of one flat colour, then shows the picture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    still = path.with_suffix(".still.png")
    picture.resize((320, 240)).save(still)
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error",
                        "-f", "lavfi", "-i", f"color=c={lead}:s=320x240:d={lead_seconds}:r=10",
                        "-loop", "1", "-t", str(seconds), "-r", "10", "-i", str(still),
                        "-filter_complex", "[1]setsar=1[b];[0]setsar=1[a];[a][b]concat=n=2:v=1",
                        "-c:v", "mpeg4", "-q:v", "3", "-y", str(path)],
                       check=True, stdin=subprocess.DEVNULL, timeout=60)
    finally:
        still.unlink()
    return path


def test_two_different_videos_that_open_on_black_are_no_series(client, config):
    folder = config.VAULT_ROOT / FOLDER
    _opens_on(folder / "feest.mp4", _scene(61))
    _opens_on(folder / "strand.mp4", _scene(62))
    # Fingerprinted on a later frame that shows something, not on the black opening
    a = near_dups.analyse_video(str(folder / "feest.mp4"), 4.0)
    b = near_dups.analyse_video(str(folder / "strand.mp4"), 4.0)
    assert a["contrast"] >= near_dups.FLAT_CONTRAST and b["contrast"] >= near_dups.FLAT_CONTRAST
    assert near_dups.hamming(a["phash"], b["phash"]) > 11
    _index(client)
    _run(client)
    for strictness in ("streng", "normaal", "ruim"):
        assert [s for s in _series(client, strictness)["items"] if s["media"] == "video"] == []


def test_a_copy_of_a_video_that_opens_on_black_is_still_found(client, config):
    folder = config.VAULT_ROOT / FOLDER
    _opens_on(folder / "feest.mp4", _scene(63))
    shutil.copy(folder / "feest.mp4", folder / "feest kopie.mp4")
    (folder / "feest kopie.mp4").write_bytes((folder / "feest kopie.mp4").read_bytes())
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(folder / "feest.mp4"), "-c:v", "mpeg4",
                    "-q:v", "8", "-y", str(folder / "feest klein.mp4")], check=True, stdin=subprocess.DEVNULL)
    (folder / "feest kopie.mp4").unlink()
    _index(client)
    _run(client)
    video = [s for s in _series(client, "streng")["items"] if s["media"] == "video"]
    assert [sorted(m["name"] for m in s["members"]) for s in video] == [["feest klein.mp4", "feest.mp4"]]


def test_videos_that_are_black_throughout_stay_unpaired_and_nothing_waits(client, config):
    folder = config.VAULT_ROOT / FOLDER
    _opens_on(folder / "zwart 1.mp4", Image.new("RGB", (320, 240)), seconds=2)
    _opens_on(folder / "zwart 2.mp4", Image.new("RGB", (320, 240)), seconds=2)
    _index(client)
    result = _run(client)
    assert result["videos_analysed"] >= 2
    assert _rows(config, "SELECT h.status FROM media_phash h JOIN media m ON m.id = h.media_id "
                         "WHERE m.name LIKE 'zwart %'") == [("ok",), ("ok",)]
    assert [s for s in _series(client, "ruim")["items"] if s["media"] == "video"] == []
    assert client.get("/api/series/status").json()["pending"] == 0


def test_the_same_picture_in_videos_of_another_length_is_no_series(client, config):
    folder = config.VAULT_ROOT / FOLDER
    _clip(folder / "titel kort.mp4", _scene(64), "-c:v", "mpeg4", "-q:v", "2", seconds=3)
    _clip(folder / "titel lang.mp4", _scene(64), "-c:v", "mpeg4", "-q:v", "2", seconds=12)
    _clip(folder / "titel kopie.mp4", _scene(64), "-c:v", "mpeg4", "-q:v", "6", seconds=12)
    _index(client)
    _run(client)
    video = [s for s in _series(client, "ruim")["items"] if s["media"] == "video"]
    assert [sorted(m["name"] for m in s["members"]) for s in video] == [["titel kopie.mp4", "titel lang.mp4"]]


def test_same_length_allows_a_second_or_two_percent():
    assert near_dups.same_length(10.0, 10.9) and not near_dups.same_length(10.0, 11.2)
    assert near_dups.same_length(100.0, 101.9) and not near_dups.same_length(100.0, 102.5)
    assert near_dups.same_length(None, 5.0) and near_dups.same_length(0, 5.0)
    runs = near_dups._split_by_length([1, 2, 3, 4], {1: 10.0, 2: 30.0, 3: 10.4, 4: None}.get)
    assert runs == [[1, 3], [2], [4]]


def test_a_video_frame_that_fails_because_the_vault_went_away_is_a_read_error(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    clip = _clip(vault / "a.mp4", _scene(65), "-c:v", "mpeg4")
    st = clip.stat()
    real_run = subprocess.run
    unplug = [True]

    def unplugged(cmd, *args, **kwargs):
        if cmd and str(cmd[0]).endswith("ffmpeg") and unplug:
            unplug.clear()
            vault.rename(tmp_path / "weg")       # the disk goes away while ffmpeg reads
            return subprocess.CompletedProcess(cmd, 1, b"", b"")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", unplugged)
    with pytest.raises(OSError) as caught:
        near_dups._analyse_row((1, str(clip), st.st_size, st.st_mtime_ns, "video", 2.0))
    assert caught.value.errno is not None
    # Back again: the frame reads fine
    (tmp_path / "weg").rename(vault)
    status, figures, _ = near_dups._analyse_row((1, str(clip), st.st_size, st.st_mtime_ns, "video", 2.0))
    assert status == near_dups.OK and figures
