"""
Near-duplicates and bursts ('Beste uit reeks', backend/near_dups.py): perceptual hashes,
the multi-index pair lookup, thresholds on the test vault, bursts, the quality score,
the 'phash' job (incremental, resumable, cancellable), and keeping the best of a series
(moved to RAW_DUPLICATES/near-duplicates as one recovery action that undoes exactly).
"""
import hashlib
import os
import random
import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from backend import near_dups
from fixtures.vault_builder import BURST, STRAND, STRAND_COPIES
from test_media_index import _index, _manager

KLEIN = "RAW/2022/bursts/strand klein.jpg"


# ---------- helpers ----------

def _scene(seed: int, size=(800, 600)) -> Image.Image:
    """A photo-like picture: a gradient sky, shapes and some texture, different per seed."""
    rng = random.Random(seed)
    w, h = size
    img = Image.new("RGB", size)
    top = tuple(rng.randrange(256) for _ in range(3))
    bottom = tuple(rng.randrange(256) for _ in range(3))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    for _ in range(14):
        x0, y0 = rng.randrange(w), rng.randrange(h)
        x1, y1 = x0 + rng.randrange(40, w // 2), y0 + rng.randrange(40, h // 2)
        color = tuple(rng.randrange(256) for _ in range(3))
        (draw.ellipse if rng.random() < 0.5 else draw.rectangle)([x0, y0, x1, y1], fill=color)
    noise = np.random.default_rng(seed).integers(-12, 13, (h, w, 3))
    return Image.fromarray(np.clip(np.asarray(img, dtype=np.int16) + noise, 0, 255).astype(np.uint8))


def _exif(dt: str, model="EOS R6"):
    ex = Image.new("RGB", (1, 1)).getexif()
    ex[0x010F] = "Canon"
    ex[0x0110] = model
    ex.get_ifd(0x8769)[0x9003] = dt
    return ex


def _run(client, expect="done"):
    r = client.post("/api/jobs", json={"type": "phash"})
    assert r.status_code in (200, 201), r.text
    job = _manager().wait(r.json()["job"]["id"], timeout=60)
    assert job["status"] == expect, job
    if expect != "done":
        return job
    return client.get(f"/api/jobs/{job['id']}/result").json()["result"]


def _series(client, strictness="normaal", **params):
    r = client.get("/api/series", params={"strictness": strictness, "limit": 50, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _names(series):
    return sorted(m["name"] for m in series["members"])


def _with(series_list, name):
    return [s for s in series_list["items"] if name in _names(s)]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- hashes ----------

def test_hashes_are_stable_and_survive_resizing_and_recompression(tmp_path):
    img = _scene(1)
    img.save(tmp_path / "a.jpg", quality=92)
    img.resize((400, 300)).save(tmp_path / "half.jpg", quality=60)
    img.save(tmp_path / "a.png")
    _scene(2).save(tmp_path / "other.jpg", quality=92)

    first = near_dups.analyse(str(tmp_path / "a.jpg"))
    assert near_dups.analyse(str(tmp_path / "a.jpg")) == first          # same file, same answer
    assert 0 <= first["dhash"] < 2 ** 64 and 0 <= first["phash"] < 2 ** 64
    assert len(first["colors"]) == 12
    for variant in ("half.jpg", "a.png"):
        other = near_dups.analyse(str(tmp_path / variant))
        assert near_dups.hamming(first["phash"], other["phash"]) <= 4, variant
        assert near_dups.hamming(first["dhash"], other["dhash"]) <= 6, variant
        assert near_dups.color_distance(first["colors"], other["colors"]) <= 4, variant
    different = near_dups.analyse(str(tmp_path / "other.jpg"))
    assert near_dups.hamming(first["phash"], different["phash"]) > 11

    # Fixed values: a change in the algorithm must come with a new ALGO_VERSION
    Image.new("RGB", (64, 64), (10, 20, 30)).save(tmp_path / "flat.png")
    assert near_dups.analyse(str(tmp_path / "flat.png"))["colors"] == bytes([10, 20, 30] * 4)
    _scene(7, (256, 192)).save(tmp_path / "golden.png")
    golden = near_dups.analyse(str(tmp_path / "golden.png"))
    assert (golden["dhash"], golden["phash"]) == (0xC1CCCCCCCBCBCFF3, 0xA2D64D6988B59D33)
    assert near_dups.ALGO_VERSION == 1


def test_signed_storage_round_trips_every_64_bit_value():
    for value in (0, 1, 2 ** 63 - 1, 2 ** 63, 2 ** 64 - 1, 0xDEADBEEFCAFEBABE):
        stored = near_dups.to_signed(value)
        assert -2 ** 63 <= stored < 2 ** 63
        assert near_dups.to_unsigned(stored) == value


def test_undecodable_files_are_told_apart_from_read_errors(tmp_path):
    (tmp_path / "corrupt.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8)
    (tmp_path / "empty.jpg").write_bytes(b"")
    for name in ("corrupt.jpg", "empty.jpg"):
        with pytest.raises(near_dups.Undecodable):
            near_dups.analyse(str(tmp_path / name))
    with pytest.raises(OSError):
        near_dups.analyse(str(tmp_path / "gone.jpg"))


def test_exif_orientation_is_applied_before_hashing(tmp_path):
    img = _scene(3, (600, 400))
    img.save(tmp_path / "upright.jpg", quality=92)
    exif = Image.new("RGB", (1, 1)).getexif()
    exif[0x0112] = 6   # stored sideways: turn 90 degrees clockwise to stand upright
    img.transpose(Image.Transpose.ROTATE_90).save(tmp_path / "sideways.jpg", quality=92, exif=exif)
    a = near_dups.analyse(str(tmp_path / "upright.jpg"))
    b = near_dups.analyse(str(tmp_path / "sideways.jpg"))
    assert near_dups.hamming(a["phash"], b["phash"]) <= 4


# ---------- multi-index hashing ----------

@pytest.mark.parametrize("radius", [0, 3, 7, 11])
def test_hash_index_finds_exactly_what_brute_force_finds(radius):
    rng = np.random.default_rng(radius)
    hashes = rng.integers(0, 2 ** 63, 3000, dtype=np.uint64) * np.uint64(2) + rng.integers(0, 2, 3000, dtype=np.uint64)
    # Plant close pairs at every distance up to radius + 2, so both sides of the edge are there
    for k in range(400):
        a, b = rng.integers(0, len(hashes), 2)
        value = hashes[a]
        for bit in rng.choice(64, int(rng.integers(0, radius + 3)), replace=False):
            value ^= np.uint64(1) << np.uint64(bit)
        hashes[b] = value
    hashes[7] = hashes[8]                                   # identical hashes too
    brute = set()
    as_int = [int(h) for h in hashes]
    for i in range(len(as_int)):
        for j in range(i + 1, len(as_int)):
            d = bin(as_int[i] ^ as_int[j]).count("1")
            if d <= radius:
                brute.add((i, j, d))
    index = near_dups.HashIndex(hashes)
    i, j, d = index.pairs(radius)
    assert set(zip(i.tolist(), j.tolist(), d.tolist())) == brute
    assert len(brute) > 50

    # Only the pairs of some photos (the incremental lookup of new photos)
    queries = np.arange(0, len(hashes), 7)
    i, j, d = index.pairs(radius, queries)
    wanted = {p for p in brute if p[0] % 7 == 0 or p[1] % 7 == 0}
    assert set(zip(i.tolist(), j.tolist(), d.tolist())) == wanted


def test_hash_index_scales_without_comparing_every_pair():
    rng = np.random.default_rng(5)
    hashes = rng.integers(0, 2 ** 63, 100_000, dtype=np.uint64)
    started = time.monotonic()
    i, _j, _d = near_dups.HashIndex(hashes).pairs(near_dups.PAIR_RADIUS)
    assert time.monotonic() - started < 20     # ~2 s on an M3; all pairs would be 5e9 comparisons
    assert len(i) < 2000      # chance pairs of random hashes (C(63,11) / 2**63 * 5e9: a few hundred)
