"""
Builds a 'showcase' vault: a fuller, realistic-looking archive for screenshots, design
reviews and demos. Where vault_builder.py collects the awkward edge cases, this one looks
like a tidy personal archive: about 190 generated landscape, city, forest and snow photos
(gradients, sun, mountains, water, skylines) spread over 2015-2025 and over a dozen
countries with GPS, a few bursts, exact copies in a phone backup, some videos and audio,
a 'library' for Shows and a Spotify export with two years of plays. Next to DIR it writes
the DataGatherer sources: a Chrome profile (DIR/../Chrome) and a Messages database
(DIR/../home); the Takeout with a location history on three trips sits in the vault.

    python tests/fixtures/showcase_vault.py DIR                # files + DIR_config.json
    python tests/fixtures/showcase_vault.py DIR --populate URL # then fill it through the API

Start the server for it with DATAVAULT_TESTING=1, DATAVAULT_CONFIG=DIR_config.json,
DATAVAULT_GATHER_HOME=DIR/../home and DATAVAULT_CHROME_DIR=DIR/../Chrome (DIR in the temp folder).

--populate talks to a running test server (DATAVAULT_TESTING=1 on that vault): it indexes
the library, finds series, makes albums, ratings, Elo duels, Keurer decisions, an export
and a few undoable runs, so every screen has something to show. It only uses the public
API, so the app's own rules (move_no_overwrite, undo records) apply as usual.
"""
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_builder import _exif, write_config  # noqa: E402

SEED = 20261029
W, H = 1200, 900

# Colour sets per scene: (sky top, sky bottom, ground/near, far mountains, sun/light)
PALETTES = {
    "strand": [((70, 140, 220), (190, 225, 245), (232, 210, 160), (40, 150, 175), (255, 245, 210)),
               ((40, 110, 200), (160, 210, 240), (225, 200, 150), (20, 130, 160), (255, 250, 230))],
    "zonsondergang": [((40, 40, 110), (250, 150, 80), (30, 30, 60), (120, 60, 90), (255, 220, 140)),
                      ((70, 30, 90), (255, 120, 70), (40, 20, 40), (150, 70, 80), (255, 200, 120))],
    "bergen": [((60, 120, 200), (200, 220, 240), (60, 110, 70), (110, 130, 160), (255, 255, 240)),
               ((90, 140, 210), (220, 230, 245), (80, 120, 80), (130, 140, 170), (255, 255, 235))],
    "sneeuw": [((120, 160, 210), (235, 240, 250), (245, 248, 252), (170, 185, 210), (255, 255, 255)),
               ((150, 170, 200), (230, 235, 245), (240, 244, 250), (160, 170, 195), (255, 250, 240))],
    "bos": [((120, 170, 200), (220, 230, 210), (40, 80, 40), (70, 110, 70), (255, 240, 200)),
            ((200, 160, 110), (240, 210, 160), (110, 60, 30), (150, 90, 40), (255, 220, 160))],
    "stad": [((10, 15, 40), (60, 50, 110), (20, 20, 30), (35, 35, 55), (255, 210, 120)),
             ((110, 160, 220), (220, 230, 240), (70, 75, 85), (120, 125, 140), (255, 250, 230))],
    "woestijn": [((90, 150, 220), (240, 215, 170), (215, 150, 90), (190, 120, 80), (255, 240, 200))],
    "noorderlicht": [((5, 10, 30), (20, 40, 60), (230, 235, 245), (30, 40, 60), (80, 255, 170))],
    "heuvels": [((100, 160, 220), (235, 225, 200), (150, 160, 70), (110, 140, 70), (255, 240, 200))],
}

# (folder, start date, days, scene mix, (lat, lon), camera, count)
TRIPS = [
    ("2015/Zomer Bretagne", "2015-07-18", 5, ["strand", "zonsondergang"], (47.87, -3.92), ("Canon", "EOS 600D"), 10),
    ("2016/Herfst op de Veluwe", "2016-10-27", 4, ["bos"], (52.10, 5.82), ("Canon", "EOS 600D"), 10),
    ("2017/Lente in Amsterdam", "2017-04-21", 3, ["stad", "heuvels"], (52.37, 4.89), ("Apple", "iPhone 7"), 10),
    ("2018/Wintersport Oostenrijk", "2018-02-10", 6, ["sneeuw", "bergen"], (47.13, 10.27), ("Apple", "iPhone 8"), 12),
    ("2018/Weekend Rome", "2018-10-27", 4, ["stad", "zonsondergang"], (41.90, 12.49), ("Apple", "iPhone 8"), 10),
    ("2019/Zomer Portugal", "2019-07-08", 8, ["strand", "zonsondergang"], (37.10, -8.67), ("Fujifilm", "X-T30"), 14),
    ("2019/Herfst Zwarte Woud", "2019-10-28", 3, ["bos", "bergen"], (48.00, 8.20), ("Fujifilm", "X-T30"), 10),
    ("2020/Fjorden Noorwegen", "2020-08-02", 7, ["bergen", "zonsondergang"], (61.10, 7.10), ("Fujifilm", "X-T30"), 14),
    ("2021/Griekse eilanden", "2021-06-12", 9, ["strand", "zonsondergang"], (36.39, 25.46), ("Apple", "iPhone 12"), 14),
    ("2021/Amsterdam bij avond", "2021-10-29", 1, ["stad"], (52.36, 4.90), ("Apple", "iPhone 12"), 8),
    ("2022/Marokko", "2022-03-14", 6, ["woestijn"], (31.63, -7.99), ("Fujifilm", "X-T4"), 12),
    ("2022/New York", "2022-09-20", 5, ["stad"], (40.71, -74.00), ("Fujifilm", "X-T4"), 12),
    ("2023/Japan", "2023-04-02", 10, ["stad", "bergen"], (35.68, 139.69), ("Sony", "A7 IV"), 14),
    ("2023/Herfst in Zeeland", "2023-10-29", 2, ["zonsondergang", "strand"], (51.55, 3.45), ("Apple", "iPhone 14"), 10),
    ("2024/Lapland", "2024-12-27", 5, ["sneeuw", "noorderlicht"], (68.42, 27.41), ("Sony", "A7 IV"), 12),
    ("2025/Toscane", "2025-08-11", 7, ["heuvels", "zonsondergang"], (43.32, 11.33), ("Apple", "iPhone 15 Pro"), 14),
]

# Openly licensed real photos from the semantic fixtures (CC0 / public domain only)
REAL_PHOTOS = [
    ("hond_strand.jpg", "2021/Griekse eilanden/hond op het strand.jpg", "2021-06-15 11:20:00", (36.40, 25.45)),
    ("hond_gras.jpg", "2017/Lente in Amsterdam/Bram in het park.jpg", "2017-04-22 15:00:00", (52.36, 4.87)),
    ("kat.jpg", "2020/Thuis/Poes.jpg", "2020-10-29 09:10:00", None),
    ("stad_nacht.jpg", "2022/New York/hotel bij nacht.jpg", "2022-09-22 22:40:00", (40.76, -73.98)),
]

ARTISTS = ["De Kustlijn", "Nachtvlucht", "Lena Morel", "Polderpop", "The Quiet Harbours", "Saffier",
           "Echo Park Kids", "Marieke Vos", "Orbit Lane", "Zuiderwind"]
TRACKS = ["Zomeravond", "Ver weg", "Golden Hour", "Stadslicht", "Slow Tide", "Noorderzon", "Paper Planes",
          "Thuiskomen", "Wildflower", "Kilometers", "Late Trains", "Blauwe uur", "Overal", "Afterglow"]


def _lerp(a, b, t):
    return a + (b - a) * t


def _ridge(rng, width, base, amp, rough):
    """A smooth random mountain ridge: y per x column."""
    x = np.arange(width, dtype=float)
    y = np.full(width, float(base))
    for octave in range(4):
        freq = (octave + 1) * rough / width
        phase = rng.uniform(0, 2 * math.pi)
        y -= amp / (octave + 1.4) * np.sin(x * freq * 2 * math.pi + phase)
    return y


def render_scene(scene: str, seed: int, size=(W, H)) -> Image.Image:
    """One generated photo: sky gradient, light, ridges, water or skyline, a little grain."""
    rng = np.random.default_rng(seed)
    w, h = size
    pal = PALETTES[scene][seed % len(PALETTES[scene])]
    top, bottom, near, far, light = [np.array(c, dtype=float) for c in pal]
    jitter = rng.uniform(-18, 18, 3)
    top, bottom = np.clip(top + jitter, 0, 255), np.clip(bottom + jitter / 2, 0, 255)

    yy = np.linspace(0, 1, h)[:, None, None]
    img = top * (1 - yy) + bottom * yy
    img = np.broadcast_to(img, (h, w, 3)).copy()
    X, Y = np.meshgrid(np.arange(w), np.arange(h))

    horizon = int(h * rng.uniform(0.55, 0.7))
    # Sun or glow
    sx, sy = rng.uniform(0.2, 0.8) * w, horizon - rng.uniform(0.05, 0.3) * h
    dist = np.sqrt((X - sx) ** 2 + (Y - sy) ** 2)
    if scene != "noorderlicht":
        glow = np.clip(1 - dist / (w * 0.45), 0, 1) ** 2.2 * 0.55
        img = img * (1 - glow[..., None]) + light * glow[..., None]
        if scene in ("zonsondergang", "strand", "woestijn", "heuvels"):
            disc = np.clip((h * 0.055 - dist) / 2.5, 0, 1)
            img = img * (1 - disc[..., None]) + light * disc[..., None]
    else:
        # Aurora: soft green waves high in the sky, plus stars
        for band in range(3):
            cy = h * (0.18 + 0.1 * band) + 40 * np.sin(X / w * 6 + band * 1.7 + seed)
            a = np.exp(-((Y - cy) / (h * 0.05)) ** 2) * (0.55 - band * 0.12)
            img = img * (1 - a[..., None]) + light * a[..., None]
    if scene in ("stad", "noorderlicht") and pal[0][0] < 60:
        stars = rng.random((h, w)) > 0.9993
        img[stars & (Y < horizon)] = 235

    def fill(mask, colour, shade=0.0):
        nonlocal img
        col = np.array(colour, dtype=float)
        if shade:
            col = col * (1 - shade)
        img[mask] = img[mask] * 0.0 + col

    if scene == "stad":
        # Far haze ridge, then a skyline with lit windows
        x = 0
        dark = pal[0][0] < 60
        while x < w:
            bw = int(rng.uniform(40, 120))
            bh = int(rng.uniform(0.15, 0.5) * h)
            top_y = horizon - bh
            m = (X >= x) & (X < x + bw) & (Y >= top_y)
            tone = far * rng.uniform(0.7, 1.15)
            img[m] = np.clip(tone, 0, 255)
            if dark:
                lit = ((X - x) % 14 < 6) & ((Y - top_y) % 18 < 8) & m & (rng.random((h, w)) > 0.55)
                img[lit] = light * rng.uniform(0.7, 1.0)
            x += bw + int(rng.uniform(0, 12))
        ground = Y >= horizon
        img[ground] = near * (0.8 + 0.4 * ((Y[ground] - horizon) / (h - horizon)))[:, None] * 0.6 + near * 0.4
    else:
        for i, (base, amp, colour) in enumerate([
            (horizon - h * 0.12, h * 0.12, far),
            (horizon - h * 0.03, h * 0.07, (far + near) / 2),
        ]):
            if scene in ("strand",) and i == 1:
                break
            ridge = _ridge(rng, w, base, amp if scene not in ("woestijn", "heuvels") else amp * 0.5,
                           rng.uniform(1.2, 3.5) if scene in ("bergen", "sneeuw") else rng.uniform(0.6, 1.4))
            m = Y >= ridge[None, :]
            shade = (Y - ridge[None, :]) / h
            tint = colour[None, None, :] * (1 - np.clip(shade, 0, 0.3)[..., None])
            img = np.where(m[..., None], tint, img)
            if scene in ("bergen", "sneeuw") and i == 0:
                snow = m & (Y < ridge[None, :] + h * 0.035)
                img[snow] = img[snow] * 0.25 + 245 * 0.75
        ground = Y >= horizon
        if scene in ("strand", "zonsondergang"):
            # Water with a light path, then a beach
            water = ground & (Y < horizon + h * 0.18)
            wt = ((Y - horizon) / (h * 0.18))
            sea = far * (1 - wt[..., None] * 0.4) if scene == "strand" else (top * 0.5 + bottom * 0.5)
            img = np.where(water[..., None], sea, img)
            path = water & (np.abs(X - sx) < (8 + (Y - horizon) * 0.8)) & ((Y // 3) % 2 == 0)
            img[path] = img[path] * 0.4 + light * 0.6
            beach = Y >= horizon + h * 0.18
            img = np.where(beach[..., None], near[None, None, :] * (0.9 + 0.1 * (Y / h))[..., None], img)
        else:
            img = np.where(ground[..., None], near[None, None, :] * (0.75 + 0.35 * (Y / h))[..., None], img)
        if scene == "bos":
            for _ in range(int(rng.uniform(14, 26))):
                tx, base_y = rng.uniform(0, w), rng.uniform(horizon, h * 1.05)
                th, tw = rng.uniform(0.18, 0.45) * h, rng.uniform(30, 70)
                m = (Y < base_y) & (Y > base_y - th) & (np.abs(X - tx) < tw * (Y - (base_y - th)) / th)
                img[m] = near * rng.uniform(0.45, 0.8)

    # Vignette and grain, then a soft blur so it reads as a photo
    vig = 1 - 0.28 * (((X - w / 2) / (w / 2)) ** 2 + ((Y - h / 2) / (h / 2)) ** 2) / 2
    img = img * vig[..., None] + rng.normal(0, 3.2, (h, w, 1))
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    return out.filter(ImageFilter.GaussianBlur(0.8))


def _save(img: Image.Image, path: Path, when: datetime, camera, gps):
    path.parent.mkdir(parents=True, exist_ok=True)
    make, model = camera
    img.save(path, "JPEG", quality=84, exif=_exif(when.strftime("%Y:%m:%d %H:%M:%S"), make=make,
                                                   model=model, gps=gps))
    stamp = when.timestamp()
    import os
    os.utime(path, (stamp, stamp))


def _ffmpeg(*args) -> bool:
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    subprocess.run([ff, "-nostdin", "-loglevel", "error", "-y", *args], check=True)
    return True


def build_showcase_vault(root: Path) -> Path:
    """Write the showcase archive under root. Returns root."""
    root = Path(root)
    raw = root / "RAW"
    raw.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(SEED)
    made = []
    number = 1000

    for t, (folder, start, days, scenes, (lat, lon), camera, count) in enumerate(TRIPS):
        day0 = datetime.strptime(start, "%Y-%m-%d")
        for i in range(count):
            number += rnd.randint(1, 7)
            when = day0 + timedelta(days=min(days - 1, i * days // count), hours=rnd.uniform(8, 21))
            if "Amsterdam bij avond" in folder:
                when = day0 + timedelta(hours=18 + i * 0.4)
            scene = scenes[i % len(scenes)] if rnd.random() > 0.25 else rnd.choice(scenes)
            portrait = rnd.random() < 0.18
            size = (H, W) if portrait else (W, H)
            img = render_scene(scene, SEED + t * 100 + i, size)
            gps = None if rnd.random() < 0.08 else (lat + rnd.uniform(-0.25, 0.25), lon + rnd.uniform(-0.35, 0.35))
            path = raw / folder / f"IMG_{number:04d}.JPG"
            _save(img, path, when, camera, gps)
            made.append((path, scene, when, camera, gps))

            # Bursts: three moments shot five times in a row, each frame slightly shifted
            if i == 3 and t in (5, 8, 13):
                for k in range(1, 5):
                    number += 1
                    frame = img.transform(img.size, Image.AFFINE, (1, 0, k * 3, 0, 1, k * 2), resample=Image.BILINEAR)
                    frame = frame.point(lambda v, k=k: min(255, int(v * (1 + k * 0.01))))
                    _save(frame, raw / folder / f"IMG_{number:04d}.JPG", when + timedelta(seconds=k), camera, gps)

    # Real photos (CC0 / public domain, see semantic/SOURCES.txt), upscaled a little
    for name, rel, when, gps in REAL_PHOTOS:
        src = Path(__file__).resolve().parent / "semantic" / name
        img = Image.open(src).convert("RGB")
        img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
        _save(img, raw / rel, datetime.strptime(when, "%Y-%m-%d %H:%M:%S"), ("Apple", "iPhone 12"), gps)

    # A phone backup with exact copies of a few trip photos (for Duplicaten)
    backup = raw / "Telefoon backup 2023"
    backup.mkdir(parents=True, exist_ok=True)
    for path, *_ in rnd.sample(made, 12):
        shutil.copy2(path, backup / path.name)

    # A few photos without any date, like scans and screenshots
    for i, scene in enumerate(["heuvels", "bergen", "strand"]):
        img = render_scene(scene, SEED + 9000 + i)
        p = raw / "Scans" / f"scan_{i + 1:03d}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        img.save(p, "JPEG", quality=82)

    # Videos (a slow pan over a generated photo) and audio
    for i, (path, scene, when, camera, gps) in enumerate(rnd.sample(made, 4)):
        out = path.parent / f"VID_{path.stem[4:]}.mp4"
        if _ffmpeg("-loop", "1", "-i", str(path), "-t", "3", "-r", "15",
                   "-vf", "scale=960:-2,zoompan=z='1+on/300':d=45:s=960x720", "-pix_fmt", "yuv420p", str(out)):
            import os
            os.utime(out, (when.timestamp(), when.timestamp()))
    (raw / "Audio").mkdir(exist_ok=True)
    for i, freq in enumerate((330, 440, 523)):
        _ffmpeg("-f", "lavfi", "-i", f"sine=frequency={freq}:duration=3", str(raw / f"Audio/memo {i + 1}.m4a"))

    # The library Shows uses: favourite trips, copied from RAW
    for folder in ("2019/Zomer Portugal", "2021/Griekse eilanden", "2024/Lapland", "2025/Toscane"):
        dst = root / "library" / folder.split("/", 1)[1]
        dst.mkdir(parents=True, exist_ok=True)
        for p in sorted((raw / folder).glob("*.JPG"))[:8]:
            shutil.copy2(p, dst / p.name)

    _spotify(root, rnd)
    _gather(root, rnd)
    return root


# Trips with a Tijdlijn (location history) in the Takeout: the route per day on the Kaart and
# estimated places for the photos without GPS
LOCATION_TRIPS = ("2021/Griekse eilanden", "2023/Herfst in Zeeland", "2025/Toscane")

CHATS = [
    ("Lotte", "+32470000001", [
        (0, "Zijn de foto's van Toscane al gesorteerd?"), (1, "Bijna! De mooiste staan al in een album"),
        (0, "Stuur je die van de zonsondergang in Siena?"), (1, "Komt eraan, die is echt gelukt"),
        (0, "Wauw. Die moet in een lijst aan de muur")]),
    ("Weekend Zeeland", None, [
        (2, "Wie rijdt zaterdag?"), (1, "Ik kan rijden, vertrek om 9 uur"), (0, "Top, ik neem koffie mee"),
        (2, "Vergeet de lader van de camera niet"), (1, "Staat al in de tas")]),
    ("Thomas", "thomas@example.com", [
        (0, "Heb je nog die playlist van de roadtrip?"), (1, "Ja, 'Kilometers' staat bovenaan"),
        (0, "Perfect voor vanavond")]),
]

SITES = [("https://www.nos.nl/", "NOS Nieuws"), ("https://www.buienradar.nl/", "Buienradar"),
         ("https://www.ns.nl/reisplanner", "NS Reisplanner"), ("https://nl.wikipedia.org/wiki/Siena", "Siena - Wikipedia"),
         ("https://www.booking.com/", "Hotels in Toscane"), ("https://www.ah.nl/", "Albert Heijn"),
         ("https://github.com/", "GitHub"), ("https://www.youtube.com/", "YouTube"),
         ("https://www.funda.nl/", "Funda"), ("https://www.bol.com/", "bol.com")]


def _gather(root: Path, rnd: random.Random):
    """
    DataGatherer sources next to the vault (never the real ones): an unpacked Dutch Takeout in
    data/Matthew/RAW_google with a location history on three trips and some activity, a
    Chrome profile in ../Chrome and a Messages database in ../home (the server reads them with
    DATAVAULT_CHROME_DIR and DATAVAULT_GATHER_HOME, see the module docstring).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import fake_exports
    from fake_messages import make_chat_db

    records, activity = [], []
    for folder, start, days, _scenes, (lat, lon), _cam, _n in TRIPS:
        if folder not in LOCATION_TRIPS:
            continue
        day0 = datetime.strptime(start, "%Y-%m-%d")
        for d in range(days):
            # A wandering route through the day, every ten minutes from 08:30 to 21:00 (UTC is close enough)
            y, x = lat + rnd.uniform(-0.15, 0.15), lon + rnd.uniform(-0.2, 0.2)
            heading = rnd.uniform(0, 2 * math.pi)
            for step in range(75):
                heading += rnd.uniform(-0.5, 0.5)
                y += math.sin(heading) * 0.004
                x += math.cos(heading) * 0.005
                when = day0 + timedelta(days=d, hours=8.5, minutes=step * 10)
                records.append({"latitudeE7": int(y * 1e7), "longitudeE7": int(x * 1e7), "accuracy": 15,
                                "timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ")})
            activity.append({"header": "Maps", "title": f"Gezocht naar {folder.split('/', 1)[1]}",
                             "titleUrl": "https://www.google.com/maps/search/x",
                             "time": (day0 + timedelta(days=d, hours=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                             "products": ["Maps"]})
    videos = ["Timelapse noorderlicht", "Hoe fotografeer je een zonsondergang", "Wandelroutes Toscane",
              "Fujifilm X-T4 review", "Lightroom in tien minuten"]
    base = datetime(2025, 9, 1)
    for i in range(40):
        when = base + timedelta(days=rnd.randint(0, 55), hours=rnd.uniform(7, 23))
        if rnd.random() < 0.5:
            activity.append({"header": "YouTube", "title": f"{rnd.choice(videos)} bekeken",
                             "titleUrl": "https://www.youtube.com/watch?v=x", "products": ["YouTube"],
                             "time": when.strftime("%Y-%m-%dT%H:%M:%S.000Z")})
        else:
            activity.append({"header": "Zoeken", "title": f"Gezocht naar {rnd.choice(['weer morgen', 'treintijden', 'recept pasta', 'Siena kathedraal'])}",
                             "titleUrl": "https://www.google.com/search?q=x", "products": ["Zoeken"],
                             "time": when.strftime("%Y-%m-%dT%H:%M:%S.000Z")})
    takeout = root / "data" / "Matthew" / "RAW_google" / "takeout-20251030T000000Z-001" / "Takeout"
    for rel, data in {"Locatiegeschiedenis/Records.json": {"locations": records},
                      "Mijn activiteit/Zoeken/MijnActiviteit.json": activity}.items():
        path = takeout / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    visits = []
    for i in range(160):
        when = base + timedelta(days=rnd.randint(0, 59), hours=rnd.uniform(7, 23.5))
        url, title = rnd.choice(SITES)
        visits.append((int(when.timestamp()), url, title))
    fake_exports.chrome_history_db(root.parent / "Chrome" / "Default" / "History", sorted(visits))

    handles, chats, messages = {}, [], []
    when = datetime(2025, 8, 20, 19, 0).timestamp()
    for c, (name, handle, lines) in enumerate(CHATS, 1):
        ids = [handle] if handle else ["+32470000002", "+32470000003"]
        hids = []
        for h in ids:
            hid = len(handles) + 1
            handles[hid] = h
            hids.append(hid)
        guid = f"iMessage;-;{ids[0]}" if handle else f"iMessage;+;chat{c}"
        chats.append({"rowid": c, "guid": guid, "style": 45 if handle else 43, "name": None if handle else name,
                      "handles": hids})
        for who, text in lines:
            when += rnd.uniform(60, 3600)
            messages.append({"rowid": len(messages) + 1, "guid": f"S-{len(messages) + 1}", "chat": c,
                             "handle": 0 if who == 1 else hids[min(who, len(hids)) - 1 if who else 0],
                             "me": who == 1, "text": text, "ts": when})
        when += 86400 * 3
    folder = root.parent / "home" / "Library" / "Messages"
    folder.mkdir(parents=True, exist_ok=True)
    make_chat_db(folder / "chat.db", handles, chats, messages)


def _spotify(root: Path, rnd: random.Random):
    folder = root / "data" / "Matthew" / "Spotify Extended Streaming History"
    folder.mkdir(parents=True, exist_ok=True)
    plays = []
    start = datetime(2023, 1, 1)
    for i in range(4200):
        when = start + timedelta(minutes=rnd.randint(0, 60 * 24 * 730))
        # Evenings and weekends are busier
        if rnd.random() < 0.4:
            when = when.replace(hour=rnd.choice([7, 8, 18, 19, 20, 21, 22]))
        artist = rnd.choices(ARTISTS, weights=[14, 11, 9, 8, 7, 6, 5, 4, 3, 2])[0]
        skipped = rnd.random() < 0.18
        plays.append({
            "ts": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ms_played": rnd.randint(5000, 30000) if skipped else rnd.randint(120000, 260000),
            "master_metadata_track_name": rnd.choice(TRACKS),
            "master_metadata_album_artist_name": artist,
            "master_metadata_album_album_name": f"{artist} – {rnd.choice(['Live', 'Album', 'EP'])}",
            "skipped": skipped,
            "reason_end": "fwdbtn" if skipped else "trackdone",
        })
    plays.sort(key=lambda p: p["ts"])
    (folder / "Streaming_History_Audio_2023-2024.json").write_text(json.dumps(plays))


# ---------------------------------------------------------------------------------------
# Populate through the API of a running test server
# ---------------------------------------------------------------------------------------

class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token = self.get("/api/session")["token"]

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if method != "GET":
            req.add_header("X-DataVault-Token", self.token if hasattr(self, "token") else "")
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path}: {e.code} {e.read()[:300]!r}") from None

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body=None):
        return self._req("POST", path, body if body is not None else {})

    def wait(self, job_id, timeout=3600):
        until = time.time() + timeout
        while time.time() < until:
            j = self.get(f"/api/jobs/{job_id}")
            if j["status"] in ("done", "failed", "cancelled"):
                return j
            time.sleep(0.5)
        raise RuntimeError(f"job {job_id} did not finish")

    def job(self, type_, params=None, timeout=3600, once=False):
        if once:  # a rerun of populate skips what an earlier run already finished
            done = [j for j in self.get(f"/api/jobs?type={type_}")["jobs"] if j["status"] == "done"]
            if done:
                return done[0]
        job = self.post("/api/jobs", {"type": type_, "params": params or {}})["job"]
        return self.wait(job["id"], timeout)


def populate(base: str, vault: Path) -> dict:
    api = Api(base)
    rnd = random.Random(SEED)
    log = {}
    log["index"] = api.job("index", once=True)["status"]
    log["phash"] = api.job("phash", once=True)["status"]
    try:
        log["semantic"] = api.job("semantic", once=True)["status"]
    except RuntimeError as e:  # no search model on this machine: Slim zoeken stays empty
        log["semantic"] = str(e)[:120]

    page = api.get("/api/library/browse?limit=400&sort=-taken")
    items = page.get("items") or page.get("files") or []
    photos = [i for i in items if i.get("type") == "photo"]
    log["photos"] = len(photos)

    # Ratings (Shows) and Elo duels
    for p in rnd.sample(photos, min(60, len(photos))):
        api.post("/api/shows/vote", {"path": p["path"], "vote": "up" if rnd.random() < 0.7 else "down"})
    for _ in range(40):
        a, b = rnd.sample(photos, 2)
        try:
            api.post("/api/shows/compare", {"a": a["path"], "b": b["path"], "winner": rnd.choice("ab"),
                                            "scope": "all"})
        except RuntimeError:
            break

    # Smart albums besides the defaults
    for name, filters, icon in [
        ("Strand en zee", {"folders": ["RAW/2019/Zomer Portugal", "RAW/2021/Griekse eilanden"]}, "fa-umbrella-beach"),
        ("Bergen", {"folders": ["RAW/2020/Fjorden Noorwegen", "RAW/2018/Wintersport Oostenrijk"]}, "fa-mountain-sun"),
        ("Reizen 2022", {"year": 2022}, "fa-plane"),
    ]:
        try:
            api.post("/api/library/albums", {"name": name, "filters": filters, "icon": icon})
        except RuntimeError as e:
            log.setdefault("album_errors", []).append(str(e)[:160])

    # Keurer: keep most of one trip, move two photos to the trash (undoable from the Herstelcentrum)
    folder = vault / "RAW/2016/Herfst op de Veluwe"
    files = sorted(folder.glob("*.JPG"))
    for i, f in enumerate(files[:7]):
        api.post("/api/keurer/decide", {"path": str(f), "folder": str(folder),
                                        "decision": "delete" if i in (2, 5) else "keep"})

    # Duplicates: scan the phone backup's parent and move the copies (one undo run)
    scan = api.job("dup_scan", {"folder": str(vault / "RAW")})
    log["dup_scan"] = scan["status"]
    groups = []
    backup = vault / "RAW/Telefoon backup 2023"
    for copy in sorted(backup.glob("*.JPG"))[:6]:
        originals = [p for p in (vault / "RAW").rglob(copy.name) if p.parent != backup]
        if originals:
            groups.append({"keep": str(originals[0]), "move": [str(copy)]})
    if groups:
        log["dup_move"] = len(api.post("/api/duplicates/move", {"groups": groups, "folder": str(backup)})["moved"])

    # Series: keep the best of one burst (moves the rest, undoable)
    try:
        series = api.get("/api/series?limit=10")
        groups = series.get("series") or series.get("items") or []
        if groups:
            members = [m["id"] for m in groups[0]["members"]]
            api.post("/api/series/keep", {"members": members, "keep": members[:1]})
            log["series_keep"] = len(members)
    except (RuntimeError, KeyError, TypeError) as e:
        log["series_error"] = str(e)[:160]

    # A finished export next to the vault
    dest = Path(tempfile.mkdtemp(prefix="export-", dir=vault.parent))
    chosen = [p["path"] for p in photos[:12]]
    log["export"] = api.job("export", {"paths": chosen, "destination": str(dest), "mode": "originals"})["status"]

    # DataGatherer: read the Takeout (Tijdlijn and activity), the Chrome history and Messages,
    # when the server can see them (DATAVAULT_CHROME_DIR and DATAVAULT_GATHER_HOME)
    for source, action in (("google_takeout", "import"), ("chrome_history", "sync"), ("imessage", "sync")):
        try:
            job = api.post(f"/api/datagatherer/sources/{source}/{action}")["job"]
            log[source] = api.wait(job["id"])["status"]
        except RuntimeError as e:
            log[source] = str(e)[:160]
    return log


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve()
    if "--populate" in sys.argv:
        url = sys.argv[sys.argv.index("--populate") + 1]
        print(json.dumps(populate(url, target), indent=2, ensure_ascii=False))
    else:
        build_showcase_vault(target)
        write_config(target, target.parent / f"{target.name}_config.json")
        print(f"Showcase vault: {target}")
        print(f"Config:         {target.parent / (target.name + '_config.json')}")
