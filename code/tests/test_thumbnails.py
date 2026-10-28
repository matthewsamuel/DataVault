"""
Thumbnails (MEDIA-THUMBS-TIMELINE-1/-5/-6/-7/-19/-21, PERF-8, LIVE-PROBE-17/-21,
REPO-HYGIENE-20, GAP-1, GAP-7): small first then upright, a cache key that changes with
the file, placeholders that are never stored, HEIC, very large images, ffmpeg flags.
"""
import hashlib
import io
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from backend import thumbnail
from fixtures.vault_builder import HEIC_PHOTO, PORTRAIT, STRAND


def _thumb(client, path):
    r = client.get("/api/thumb", params={"path": str(path)})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    return r


def _image(r):
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _cache_files(config):
    if not config.THUMB_CACHE_PATH.is_dir():
        return []
    return sorted(p.name for p in config.THUMB_CACHE_PATH.iterdir())


def test_a_large_jpeg_is_decoded_small_and_never_at_full_size(tmp_path, monkeypatch):
    src = tmp_path / "groot.jpg"
    Image.new("RGB", (6000, 4000), (10, 120, 200)).save(src, quality=70)
    sizes = []
    real = Image.Image.thumbnail

    def spy(self, *args, **kwargs):
        sizes.append(self.size)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "thumbnail", spy)
    out = tmp_path / "t.jpg"
    thumbnail.render_photo_thumbnail(src, out)
    # draft() lets the JPEG decoder work at 1/8 scale: 750x500, not 6000x4000
    assert sizes and sizes[0][0] <= 1500 and sizes[0][1] <= 1000
    assert max(Image.open(out).size) == 300


def test_a_sideways_photo_gets_an_upright_thumbnail(client, vault):
    img = _image(_thumb(client, vault / PORTRAIT))
    w, h = img.size
    assert h > w  # stored 400x200 with orientation 6: shown 200x400
    top, bottom = img.getpixel((w // 2, 5)), img.getpixel((w // 2, h - 5))
    assert top[0] > 150 and top[2] < 100      # the red half ends up on top
    assert bottom[2] > 150 and bottom[0] < 100


def test_a_heic_photo_gets_a_real_thumbnail(client, vault):
    heic = vault / HEIC_PHOTO
    if not heic.exists():
        pytest.skip("pillow_heif cannot encode HEIC here")
    assert thumbnail.HEIF_SUPPORT
    r = _thumb(client, heic)
    assert "x-datavault-placeholder" not in r.headers
    r_, g, b = _image(r).getpixel((10, 10))
    assert g > 120 and r_ < 90  # the green test photo, not the grey placeholder


def test_a_photo_above_pillows_bomb_limit_gets_a_real_thumbnail(tmp_path):
    src = tmp_path / "panorama.jpg"
    Image.new("L", (18000, 10000), 200).save(src, quality=50)  # 180 MP > 2 x 89.5 MP
    out = tmp_path / "t.jpg"
    thumbnail.render_photo_thumbnail(src, out)
    assert Image.open(out).size == (300, 167)
    assert not thumbnail.is_placeholder(out)


def test_a_jpeg_above_twice_the_bomb_limit_is_drafted_instead_of_refused(tmp_path, monkeypatch):
    # The same situation as a 624 MP panorama with the real limit (2 x 300 MP), made small
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1_000_000)
    src = tmp_path / "gigapano.jpg"
    Image.new("L", (6000, 5000), 120).save(src, quality=60)   # 30 MP > 2 x 1 MP
    with pytest.raises(Image.DecompressionBombError):
        Image.open(src)
    out = tmp_path / "t.jpg"
    thumbnail.render_photo_thumbnail(src, out)
    assert Image.open(out).size == (300, 250) and not thumbnail.is_placeholder(out)
    # Not a JPEG: no draft, so the limit still holds
    png = tmp_path / "groot.png"
    Image.new("L", (6000, 5000), 120).save(png)
    with pytest.raises(thumbnail.Undecodable):
        thumbnail.render_photo_thumbnail(png, tmp_path / "p.jpg")
    # Still too large after draft (1/8 per side: 750x625 = 468750 px > 2 x 200000)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 200_000)
    with pytest.raises(thumbnail.Undecodable):
        thumbnail.render_photo_thumbnail(src, tmp_path / "x.jpg")
    assert not (tmp_path / "x.jpg").exists()


@pytest.mark.parametrize("name", ["corrupt.jpg", "empty.jpg", "fake.heic"])
def test_a_broken_photo_gets_a_placeholder_that_is_never_stored(client, vault, config, monkeypatch, name):
    calls = []
    real = thumbnail._decode_small
    monkeypatch.setattr(thumbnail, "_decode_small", lambda p: (calls.append(p), real(p))[1])
    monkeypatch.setattr(thumbnail, "_have_sips", lambda: False)
    thumbnail._FAILED.clear()
    path = vault / "RAW/misc" / name
    for _ in range(3):
        r = _thumb(client, path)
        assert r.content == thumbnail.placeholder_bytes("photo")
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-datavault-placeholder"] == "photo"
    assert not thumbnail.get_thumbnail_cache_path(path, config.THUMB_CACHE_PATH).exists()
    # Not tried again on every request, but again after RETRY_SECONDS
    assert len(calls) == 1
    monkeypatch.setattr(thumbnail, "RETRY_SECONDS", 0.0)
    thumbnail._FAILED.clear()
    _thumb(client, path)
    _thumb(client, path)
    assert len(calls) == 3


def test_audio_and_documents_get_a_placeholder_without_any_work(client, vault, config):
    before = _cache_files(config)
    for path, kind in ((next((vault / "RAW/audio").iterdir()), "audio"), (vault / "RAW/misc/notes.txt", "other")):
        r = _thumb(client, path)
        assert r.headers["x-datavault-placeholder"] == kind
    assert _cache_files(config) == before


def test_without_ffmpeg_a_video_placeholder_is_served_and_the_real_one_comes_later(client, vault, config, monkeypatch):
    clip = vault / "RAW/video/clip één.mp4"
    if not clip.exists() or not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg to build the test video")
    real_which = shutil.which
    monkeypatch.setattr(thumbnail.shutil, "which", lambda name: None if name == "ffmpeg" else real_which(name))
    r = _thumb(client, clip)
    assert r.content == thumbnail.placeholder_bytes("video")
    assert r.headers["cache-control"] == "no-store"
    assert not thumbnail.get_thumbnail_cache_path(clip, config.THUMB_CACHE_PATH).exists()
    monkeypatch.setattr(thumbnail.shutil, "which", real_which)
    r = _thumb(client, clip)
    assert "x-datavault-placeholder" not in r.headers
    assert thumbnail.get_thumbnail_cache_path(clip, config.THUMB_CACHE_PATH).exists()


def test_ffmpeg_seeks_in_the_input_and_never_reads_the_terminal(tmp_path, monkeypatch):
    import subprocess
    src = tmp_path / "v.mp4"
    src.write_bytes(b"\x00" * 100)
    commands = []

    class Done:
        returncode = 0

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        assert kwargs.get("stdin") == subprocess.DEVNULL
        Path(cmd[-1]).write_bytes(b"\xff\xd8 frame")
        return Done()

    monkeypatch.setattr(thumbnail.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(subprocess, "run", fake_run)
    thumbnail.render_video_thumbnail(src, tmp_path / "t.jpg")
    cmd = commands[0]
    assert "-nostdin" in cmd
    assert cmd.index("-ss") < cmd.index("-i")


def test_another_file_at_the_same_path_never_shows_the_old_thumbnail(client, vault):
    """LIVE-PROBE-17: red photo thumbnailed, moved away, a blue one with an OLDER mtime moved in."""
    folder = vault / "RAW/wissel"
    folder.mkdir()
    photo = folder / "foto.jpg"
    Image.new("RGB", (120, 90), (250, 0, 0)).save(photo)
    assert _image(_thumb(client, photo)).getpixel((5, 5))[0] > 200
    os.rename(photo, vault / "RAW/wissel_rood.jpg")  # test setup only (tmp vault)
    blue = vault / "RAW/blauw.jpg"
    Image.new("RGB", (120, 90), (0, 0, 250)).save(blue)
    os.utime(blue, (1500000000, 1500000000))
    os.rename(blue, photo)
    pixel = _image(_thumb(client, photo)).getpixel((5, 5))
    assert pixel[2] > 200 and pixel[0] < 50


def test_old_style_thumbnails_are_never_used_and_get_cleaned_up(client, vault, config):
    """REPO-HYGIENE-20: thumbnails cached before EXIF rotation are made again."""
    photo = vault / PORTRAIT
    config.THUMB_CACHE_PATH.mkdir(parents=True, exist_ok=True)
    old = config.THUMB_CACHE_PATH / f"{hashlib.md5(str(photo).encode()).hexdigest()}.jpg"
    Image.new("RGB", (300, 150), (0, 255, 0)).save(old)   # sideways and green: the old thumbnail
    (config.THUMB_CACHE_PATH / "_placeholder_photo.jpg").write_bytes(thumbnail.placeholder_bytes("photo"))
    stale_tmp = config.THUMB_CACHE_PATH / ".abc.1.2.tmp.jpg"
    stale_tmp.write_bytes(b"half")
    os.utime(stale_tmp, (time.time() - 7200,) * 2)
    img = _image(_thumb(client, photo))
    assert img.size[1] > img.size[0]
    current = thumbnail.get_thumbnail_cache_path(photo, config.THUMB_CACHE_PATH)
    assert current.name.startswith(f"v{thumbnail.THUMB_VERSION}_")
    assert thumbnail.prune_thumbnail_cache(config.THUMB_CACHE_PATH) == 3
    assert _cache_files(config) == [current.name]


def test_the_cache_is_trimmed_to_its_limit_least_recently_used_first(tmp_path):
    cache = tmp_path / "thumbs"
    cache.mkdir()
    now = time.time()
    names = []
    for i in range(10):
        f = cache / f"v{thumbnail.THUMB_VERSION}_{i:032x}.jpg"
        f.write_bytes(b"j" * 1000)
        os.utime(f, (now - 86400 * (10 - i), now - 86400 * 20))  # 0 used longest ago, 9 last
        names.append(f.name)
    other = cache / "notes.txt"
    other.write_bytes(b"x" * 5000)  # not a thumbnail: never touched, never counted
    assert thumbnail.prune_thumbnail_cache(cache, max_bytes=20_000) == 0
    assert len(list(cache.glob("v*.jpg"))) == 10
    # 10 kB over a 6 kB limit: trimmed to 80 % (4.8 kB), so the 4 most recently used stay
    assert thumbnail.prune_thumbnail_cache(cache, max_bytes=6_000) == 6
    assert sorted(p.name for p in cache.glob("v*.jpg")) == names[6:]
    assert other.exists()


def test_many_requests_for_one_new_thumbnail_make_it_once_and_all_get_a_whole_jpeg(client, vault, monkeypatch):
    photo = vault / STRAND
    calls = []
    real = thumbnail._decode_small

    def slow(p):
        calls.append(p)
        time.sleep(0.2)
        return real(p)

    monkeypatch.setattr(thumbnail, "_decode_small", slow)
    results = []

    def get():
        results.append(client.get("/api/thumb", params={"path": str(photo)}))

    threads = [threading.Thread(target=get) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert all(r.status_code == 200 and Image.open(io.BytesIO(r.content)).size[0] > 0 for r in results)
    assert len({r.content for r in results}) == 1


def test_a_moved_file_keeps_its_thumbnail(client, vault, config, monkeypatch):
    photo = vault / STRAND
    _thumb(client, photo)
    old_cache = thumbnail.get_thumbnail_cache_path(photo, config.THUMB_CACHE_PATH)
    assert old_cache.exists()
    dest = vault / "RAW/2020"
    r = client.post("/api/move-file", json={"source_path": str(photo), "destination_folder": str(dest)})
    assert r.status_code == 200, r.text
    moved = Path(r.json()["new_path"])
    assert not old_cache.exists()
    assert thumbnail.get_thumbnail_cache_path(moved, config.THUMB_CACHE_PATH).exists()
    monkeypatch.setattr(thumbnail, "_decode_small", lambda p: pytest.fail("made again"))
    _thumb(client, moved)


def test_a_raw_or_heic_photo_pillow_cannot_read_goes_through_sips(tmp_path, monkeypatch):
    if not shutil.which("sips"):
        pytest.skip("sips is macOS only")
    try:
        from pillow_heif import register_heif_opener  # noqa: F401
        src = tmp_path / "IMG.heic"
        Image.new("RGB", (500, 400), (200, 200, 20)).save(src, "HEIF")
    except Exception:
        pytest.skip("cannot write a HEIC here")

    def no_pillow(p):
        raise ValueError("cannot identify image file")

    monkeypatch.setattr(thumbnail, "_decode_small", no_pillow)
    out = tmp_path / "t.jpg"
    thumbnail.render_photo_thumbnail(src, out)
    img = Image.open(out)
    assert max(img.size) == 300
    r, g, b = img.convert("RGB").getpixel((10, 10))
    assert r > 150 and g > 150 and b < 100
