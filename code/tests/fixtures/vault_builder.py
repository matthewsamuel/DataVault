"""
Builds a disposable test vault with the awkward cases a real archive contains:
photos with and without EXIF (incl. GPS), PNG, video, audio, exact duplicates,
a burst series, corrupt/empty/fake files, names with spaces, emoji, accents and
shell/URL special characters, HTML in file, folder and track names, a symlink
pointing outside the vault, a library folder for Shows and a Spotify export.

Usable from pytest (build_test_vault) and from the command line:
    python tests/fixtures/vault_builder.py /tmp/some/dir
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# Relative paths (from vault root) that tests refer to
STRAND = "RAW/2019/zomer vakantie/strand 001.jpg"
STRAND_COPIES = [
    "RAW/misc/strand 001 kopie.jpg",
    "RAW/2021/🎉 feest/dup van strand.jpg",
    "RAW/misc/.hidden.jpg",
]
BURST = [f"RAW/2022/bursts/burst_{i:02d}.jpg" for i in range(5)]

# Names that must show up as text in the UI and never run as code. A name cannot
# contain '/', so the folder has an unclosed <b> (rendered as HTML it would turn
# "Trip" bold and drop the tag from the text). The browser check makes sure
# window.__xss stays unset and these names appear literally.
XSS_FOLDER = "Matthew's <b>Trip"
XSS_FILE = "xss <img src=x onerror=window.__xss=1>.jpg"
XSS_TRACK = "<img src=x onerror=window.__xss=2>"
XSS_PHOTOS = [f"RAW/{XSS_FOLDER}/{XSS_FILE}", f"library/{XSS_FOLDER}/{XSS_FILE}"]


def _exif(dt, make="Canon", model="EOS 80D", gps=None):
    ex = Image.new("RGB", (1, 1)).getexif()
    ex[0x010F] = make
    ex[0x0110] = model
    ex[0x0132] = dt
    ex.get_ifd(0x8769)[0x9003] = dt  # DateTimeOriginal

    if gps:
        def dms(v):
            v = abs(v)
            d = int(v)
            m = int((v - d) * 60)
            return (d, m, round(((v - d) * 60 - m) * 60, 2))

        lat, lon = gps
        g = ex.get_ifd(0x8825)
        g[1] = "N" if lat >= 0 else "S"
        g[2] = dms(lat)
        g[3] = "E" if lon >= 0 else "W"
        g[4] = dms(lon)
    return ex


def _photo(path: Path, color, text, dt=None, size=(800, 600), **kw):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    ImageDraw.Draw(img).text((20, 20), text, fill=(255, 255, 255))
    if dt:
        img.save(path, "JPEG", quality=90, exif=_exif(dt, **kw))
    else:
        img.save(path, quality=90)
    return path


def _ffmpeg(*args):
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    subprocess.run([ff, "-nostdin", "-loglevel", "error", "-y", *args], check=True)
    return True


def build_test_vault(root: Path, with_media: bool = True) -> Path:
    """Create the vault under root (must not exist or be empty). Returns root."""
    root = Path(root)
    raw = root / "RAW"
    raw.mkdir(parents=True, exist_ok=True)

    strand = _photo(root / STRAND, (30, 120, 200), "strand", "2019:07:14 19:30:00", gps=(52.37, 4.89))
    _photo(raw / "2019/zomer vakantie/IMG_0002.JPG", (200, 120, 30), "zonsondergang",
           "2019:07:14 20:15:00", gps=(43.7, 7.26))
    _photo(raw / "2020/Café Ümlaut/koffie é à ü.jpg", (120, 80, 40), "koffie",
           "2020:10:27 10:00:00", make="Apple", model="iPhone 11")
    _photo(raw / "2021/🎉 feest/🎂 taart.png", (250, 200, 220), "taart")
    _photo(raw / "2021/🎉 feest/no exif.jpg", (10, 200, 10), "geen exif")
    _photo(raw / "misc/deep/er/nested/diep bestand.jpeg", (90, 90, 90), "diep", "2018:10:27 12:00:00")

    # Exact duplicates in other folders, one hidden
    for rel in STRAND_COPIES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(strand, root / rel)
    os.utime(root / STRAND_COPIES[0], (1500000000, 1500000000))

    # Burst series (near-duplicates) and a resized copy
    for i, rel in enumerate(BURST):
        _photo(root / rel, (100 + i * 3, 50, 150), f"burst {i}", f"2022:05:01 12:00:0{i}")
    Image.open(strand).resize((400, 300)).save(raw / "2022/bursts/strand klein.jpg", quality=70)

    # Broken and edge-case files
    misc = raw / "misc"
    (misc / "corrupt.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8)
    (misc / "empty.jpg").write_bytes(b"")
    (misc / "fake.heic").write_bytes(b"not a heic")
    (misc / "notes.txt").write_text("geen media")
    _photo(misc / "UPPER.JPEG", (1, 2, 3), "upper")
    _photo(misc / "name with  double  spaces & quote's #hash%.jpg", (5, 5, 50), "rare naam")

    # Symlink escaping the vault (path traversal probe)
    outside = root.parent / f"{root.name}_outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("SECRET-OUTSIDE-VAULT")
    shutil.copy2(misc / "UPPER.JPEG", outside / "secret.jpg")
    os.symlink(outside, misc / "link_to_outside")

    # Shows library
    _photo(root / "library/Reis Italië/rome.jpg", (180, 60, 60), "rome", "2023:06:01 10:00:00")
    _photo(root / "library/Reis Italië/venetië.jpg", (60, 60, 180), "venetie", "2023:06:03 10:00:00")
    _photo(root / "library/Familie/opa.jpg", (60, 180, 60), "opa", "2015:12:24 18:00:00")

    # HTML and an apostrophe in folder and file names (RAW for the viewer/Keurer, library for Shows)
    for rel in XSS_PHOTOS:
        _photo(root / rel, (200, 30, 120), "xss", "2017:03:03 15:00:00")

    if with_media:
        (raw / "video").mkdir(exist_ok=True)
        (raw / "audio").mkdir(exist_ok=True)
        _ffmpeg("-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
                "-pix_fmt", "yuv420p", str(raw / "video/clip één.mp4"))
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(raw / "audio/toon 🎵.mp3"))
        (raw / "video/kapot.mp4").write_bytes(b"\x00" * 4096)

    # Spotify extended streaming history sample
    spotify = root / "data/spotify"
    spotify.mkdir(parents=True, exist_ok=True)
    history = [
        {
            "ts": f"2023-0{1 + i % 9}-1{i % 9}T{(i * 7) % 24:02d}:15:00Z",
            "ms_played": 1000 + (i * 7919) % 239000,
            "master_metadata_track_name": (
                "Don't Stop Me Now" if i % 17 == 0 else XSS_TRACK if i % 17 == 1 else f"Track {i % 17}"
            ),
            "master_metadata_album_artist_name": "Guns N' Roses" if i % 6 == 5 else f"Artiest {i % 6}",
            "master_metadata_album_album_name": f"Album {i % 4}",
            "skipped": i % 5 == 0,
            "reason_end": "fwdbtn" if i % 5 == 0 else "trackdone",
        }
        for i in range(300)
    ]
    (spotify / "Streaming_History_Audio_2023.json").write_text(json.dumps(history))
    return root


def write_config(vault: Path, config_path: Path) -> Path:
    """Write a config.json for the given test vault."""
    config_path.write_text(json.dumps({
        "vault_root": str(vault),
        "raw_folder": str(vault / "RAW"),
        "duplicates_folder": str(vault / "RAW_DUPLICATES"),
        "trash_folder": str(vault / "TRASH"),
        "cache_folder": str(vault / ".datavault_cache"),
    }, indent=2))
    return config_path


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve()
    build_test_vault(target)
    write_config(target, target.parent / f"{target.name}_config.json")
    print(f"Test vault: {target}")
    print(f"Config:     {target.parent / (target.name + '_config.json')}")
