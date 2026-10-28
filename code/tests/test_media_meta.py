"""What the media index reads from one file (backend/media_meta.py): cheap, never the pixels."""
import os
import shutil
import subprocess

import pytest
from PIL import Image

from backend import media_meta
from fixtures.vault_builder import _exif, _photo


def _meta(path):
    return media_meta.read_metadata(path, os.stat(path))


def test_photo_metadata_without_decoding_pixels(tmp_path, monkeypatch):
    photo = _photo(tmp_path / "zuid.jpg", (1, 2, 3), "x", "2018:02:03 04:05:06", make="NIKON CORPORATION ",
                   model="NIKON D750\x00", gps=(-33.8568, -151.2153), size=(640, 480))
    monkeypatch.setattr(Image.Image, "load", lambda self: pytest.fail("pixels decoded"))
    row = _meta(photo)
    assert row["kind"] == "photo" and row["meta_status"] == "ok" and row["taken_source"] == "exif"
    assert (row["width"], row["height"], row["camera_make"], row["camera_model"]) == (
        640, 480, "NIKON CORPORATION", "NIKON D750")
    assert row["lat"] == pytest.approx(-33.8568, abs=1e-3) and row["lon"] == pytest.approx(-151.2153, abs=1e-3)


def test_no_gps_fix_and_a_png_without_exif(tmp_path):
    zero = _photo(tmp_path / "nul.jpg", (1, 2, 3), "x", "2018:02:03 04:05:06", gps=(0.0, 0.0))
    assert _meta(zero)["lat"] is None
    png = tmp_path / "scherm.png"
    Image.new("RGB", (30, 20)).save(png)
    row = _meta(png)
    assert (row["width"], row["height"], row["taken_source"]) == (30, 20, "birthtime")


def test_a_raw_file_reports_its_real_size_not_the_preview(tmp_path):
    exif = _exif("2020:01:01 10:00:00", make="Canon", model="EOS R5")
    exif.get_ifd(0x8769)[0xA002] = 8192
    exif.get_ifd(0x8769)[0xA003] = 5464
    raw = tmp_path / "IMG_1.dng"
    Image.new("RGB", (160, 120)).save(raw, "JPEG", exif=exif)   # opens like a RAW with a small preview
    row = _meta(raw)
    assert (row["width"], row["height"], row["camera_model"], row["taken_source"]) == (8192, 5464, "EOS R5", "exif")
    unknown = tmp_path / "IMG_2.cr3"
    unknown.write_bytes(b"\x00\x00\x00\x18ftypcrx " + b"\x00" * 64)
    row = _meta(unknown)
    assert row["meta_status"] == "none" and row["width"] is None and row["taken_source"] == "birthtime"


def test_iso6709_positions():
    assert media_meta._iso6709("+52.3702+004.8952+001.500/") == {"lat": 52.3702, "lon": 4.8952, "altitude": 1.5}
    assert media_meta._iso6709("-33.8568+151.2153/") == {"lat": -33.8568, "lon": 151.2153}
    assert media_meta._iso6709("+91.0+004.0/") == {} and media_meta._iso6709("rommel") == {}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_quicktime_video_date_duration_rotation_gps_and_camera(tmp_path):
    src = tmp_path / "src.mov"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=1.5:size=320x240:rate=10", "-pix_fmt", "yuv420p", str(src)], check=True)
    video = tmp_path / "IMG_0001.MOV"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-display_rotation", "90", "-i", str(src),
                    "-c", "copy", "-metadata", "location=+52.3702+004.8952/", "-metadata", "make=Apple",
                    "-metadata", "model=iPhone 13", "-metadata", "creation_time=2021-06-01T10:00:00Z",
                    str(video)], check=True)
    row = _meta(video)
    assert row["kind"] == "video" and row["taken_source"] == "video" and row["taken"] == 1622541600.0
    assert row["duration"] == pytest.approx(1.5, abs=0.11)
    assert (row["width"], row["height"]) == (240, 320) and row["orientation"] in (6, 8)
    assert (row["lat"], row["lon"], row["camera_make"], row["camera_model"]) == (52.3702, 4.8952, "Apple", "iPhone 13")


def test_a_damaged_video_gives_fewer_fields_not_an_error(tmp_path):
    broken = tmp_path / "kapot.mp4"
    broken.write_bytes(b"\x00\x00\x00\x10moov\x00\x00\x00\x09mvhd" + b"\xff" * 40)
    row = _meta(broken)
    assert row["kind"] == "video" and row["duration"] is None and row["taken_source"] == "birthtime"


def test_an_unreadable_file_raises_so_it_is_read_again(tmp_path):
    photo = _photo(tmp_path / "dicht.jpg", (1, 2, 3), "x", "2018:02:03 04:05:06")
    os.chmod(photo, 0)
    try:
        with pytest.raises(PermissionError):
            _meta(photo)
    finally:
        os.chmod(photo, 0o644)
