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
    assert golden["sharpness"] == pytest.approx(49.449, abs=0.01)    # measured at 512 px (version 2)
    assert near_dups.ALGO_VERSION == 2


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


def _brute_pairs(hashes, radius):
    d = near_dups.popcount(hashes[:, None] ^ hashes[None, :]).astype(np.int64)
    i, j = np.nonzero(np.triu(d <= radius, k=1))
    return set(zip(i.tolist(), j.tolist(), d[i, j].tolist()))


def test_hash_index_slices_crowded_buckets_and_still_finds_every_pair(monkeypatch):
    # Real photos are not random: documents and screenshots fill a few buckets with
    # thousands of hashes. The candidates are then checked a slice at a time (tiny here)
    monkeypatch.setattr(near_dups, "CANDIDATE_BUDGET", 97)
    rng = np.random.default_rng(11)
    n = 1500
    crowded = np.uint64(0xBEEF)                                  # one 16-bit part shared by most
    sparse = np.zeros(n, dtype=np.uint64)
    for bit in range(16, 64):                                    # few set bits in the rest: many close pairs
        sparse |= (rng.random(n) < 0.08).astype(np.uint64) << np.uint64(bit)
    hashes = np.where(rng.random(n) < 0.8, sparse | crowded, rng.integers(0, 2 ** 63, n, dtype=np.uint64))
    brute = _brute_pairs(hashes, near_dups.PAIR_RADIUS)
    i, j, d = near_dups.HashIndex(hashes).pairs(near_dups.PAIR_RADIUS)
    assert set(zip(i.tolist(), j.tolist(), d.tolist())) == brute
    assert len(brute) > 10_000
    queries = np.arange(0, n, 5)
    i, j, d = near_dups.HashIndex(hashes).pairs(near_dups.PAIR_RADIUS, queries)
    assert set(zip(i.tolist(), j.tolist(), d.tolist())) == {p for p in brute if p[0] % 5 == 0 or p[1] % 5 == 0}


def test_bit_counting_works_without_numpy_2():
    rng = np.random.default_rng(3)
    values = np.concatenate((rng.integers(0, 2 ** 63, 500, dtype=np.uint64) * np.uint64(2),
                             np.array([0, 2 ** 64 - 1], dtype=np.uint64)))
    expected = [bin(int(v)).count("1") for v in values]
    assert near_dups._popcount_fallback(values).tolist() == expected
    assert int(near_dups._popcount_fallback(np.uint64(2 ** 64 - 1))) == 64


# ---------- the job and the thresholds on the test vault ----------

def test_the_test_vault_gives_the_burst_and_the_resized_copy_and_nothing_else(client, vault):
    _index(client)
    result = _run(client)
    assert result["complete"] and result["errors"] == 0
    assert result["analysed"] >= 18 and result["undecodable"] >= 2      # corrupt.jpg, empty.jpg, fake.heic
    status = client.get("/api/series/status").json()
    assert status["pending"] == 0 and status["analysed"] == result["analysed"]

    for strictness in ("streng", "normaal", "ruim"):
        listed = _series(client, strictness)
        names = [_names(s) for s in listed["items"]]
        # The resized, recompressed copy matches its original; the byte-identical copies of the
        # original count as that one photo (they are the duplicates screen's work)
        strand = _with(listed, "strand klein.jpg")
        assert len(strand) == 1 and len(strand[0]["members"]) == 2, names
        original = next(m for m in strand[0]["members"] if m["name"] != "strand klein.jpg")
        assert original["name"] in {Path(p).name for p in [STRAND] + STRAND_COPIES}
        assert len(original["copies"]) >= 2
        assert strand[0]["kind"] == "near"
        # The five shots of the burst are one series, a burst
        burst = _with(listed, "burst_00.jpg")
        assert len(burst) == 1 and _names(burst[0]) == [Path(p).name for p in BURST], names
        assert burst[0]["kind"] == "burst"
        # Nothing else: the other test photos are clearly different (other colours, other content)
        assert len(listed["items"]) == 2, names
        assert listed["total"] == 2


def test_exact_copies_alone_are_no_series(client, vault):
    """Two byte-identical files are the duplicates screen's work: no series here."""
    (vault / KLEIN).rename(vault / "RAW/2022/klein.bak")    # outside the index: no media extension
    _index(client)
    _run(client)
    listed = _series(client, "ruim")
    assert not _with(listed, Path(STRAND).name) and not any(
        n in {Path(p).name for p in STRAND_COPIES} for s in listed["items"] for n in _names(s))


def test_bursts_need_the_same_camera_a_short_gap_and_the_same_folder(client, vault):
    folder = vault / "RAW/2024/serie"
    folder.mkdir(parents=True)
    base = _scene(11)

    def shot(name, shift, second, model="EOS R6"):
        # The camera moves 30 px between shots: too different for a near-duplicate at
        # 'streng' (phash ~10), alike enough for a burst
        base.crop((shift, int(shift * .75), 800, 600)).resize((800, 600)).save(
            folder / name, quality=90, exif=_exif(f"2024:03:01 10:00:{second:02d}", model=model))

    for i, second in enumerate([0, 1, 2, 4, 12]):          # 12 s: too long after the shot before
        shot(f"IMG_{i}.jpg", i * 30, second)
    # Two shots a second apart that look as alike as IMG_0 and IMG_1, by two cameras
    shot("A eigen camera.jpg", 300, 30)
    shot("B andere camera.jpg", 330, 31, model="X-T4")
    _index(client)
    _run(client)
    listed = [s for s in _series(client, "streng")["items"] if s["folder"] == str(folder)]
    assert len(listed) == 1, [_names(s) for s in listed]
    burst = listed[0]
    assert burst["kind"] == "burst"
    assert _names(burst) == ["IMG_0.jpg", "IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg"]
    with sqlite3.connect(str(client.app.state.config.DB_PATH)) as conn:
        gaps = [r[0] for r in conn.execute("SELECT gap FROM near_dup_pairs WHERE kind = 'burst'")]
    assert gaps and max(gaps) <= near_dups.BURST_LOOKAHEAD * near_dups.BURST_GAP

    # Spread over two folders, the shots are no burst any more (pairs are kept, the folder
    # is checked when the series are made)
    other = vault / "RAW/2024/elders"
    other.mkdir()
    for name in ("IMG_1.jpg", "IMG_3.jpg"):
        os.rename(folder / name, other / name)   # test setup in the temp vault, like a move in Finder
    _index(client)
    _run(client)
    assert not [s for s in _series(client, "streng")["items"] if s["folder"] in (str(folder), str(other), None)
                and "IMG_0.jpg" in _names(s) + ["IMG_1.jpg"]]
    assert not [s for s in _series(client, "streng")["items"] if {"IMG_1.jpg", "IMG_3.jpg"} & set(_names(s))]


def test_strictness_changes_what_counts_as_alike(client, vault):
    from PIL import ImageEnhance
    folder = vault / "RAW/2024/varianten"
    folder.mkdir(parents=True)
    base = _scene(21)
    w, h = base.size
    base.save(folder / "origineel.jpg", quality=92)
    base.resize((400, 300)).save(folder / "klein.jpg", quality=60)                  # phash 0
    ImageEnhance.Brightness(base).enhance(1.08).save(folder / "lichter.jpg", quality=90)  # colour 11
    base.crop((int(w * .04), int(h * .032), w, h)).resize((w, h)).save(folder / "bijgesneden.jpg", quality=90)  # phash 10
    _scene(22).save(folder / "iets anders.jpg", quality=92)
    _index(client)
    _run(client)
    found = {}
    for strictness in ("streng", "normaal", "ruim"):
        series = _with(_series(client, strictness), "origineel.jpg")
        found[strictness] = _names(series[0]) if series else []
    assert found == {
        "streng": ["klein.jpg", "origineel.jpg"],
        "normaal": ["klein.jpg", "lichter.jpg", "origineel.jpg"],
        "ruim": ["bijgesneden.jpg", "klein.jpg", "lichter.jpg", "origineel.jpg"],
    }
    assert client.get("/api/series", params={"strictness": "heel"}).status_code == 400
    assert client.get("/api/series", params={"sort": "kleur"}).status_code == 400


# ---------- quality ----------

def test_the_sharp_photo_beats_its_blurred_copy_and_the_order_is_fixed(client, vault):
    folder = vault / "RAW/2024/scherp"
    folder.mkdir(parents=True)
    base = _scene(31, (1200, 900))
    base.save(folder / "a scherp.jpg", quality=90)
    base.filter(ImageFilter.GaussianBlur(2.5)).save(folder / "b wazig.jpg", quality=90)
    base.resize((600, 450)).save(folder / "c klein.jpg", quality=90)
    _index(client)
    _run(client)
    series = _with(_series(client, "normaal"), "a scherp.jpg")[0]
    members = {m["name"]: m for m in series["members"]}
    assert members["a scherp.jpg"]["best"] and series["best"] == members["a scherp.jpg"]["id"]
    assert members["a scherp.jpg"]["score"] > members["b wazig.jpg"]["score"]
    assert members["a scherp.jpg"]["breakdown"]["sharpness"] > members["b wazig.jpg"]["breakdown"]["sharpness"]
    assert members["a scherp.jpg"]["breakdown"]["resolution"] > members["c klein.jpg"]["breakdown"]["resolution"]
    assert set(members["a scherp.jpg"]["breakdown"]) == {"sharpness", "resolution", "exposure", "choice"}
    again = _with(_series(client, "normaal"), "a scherp.jpg")[0]
    assert again == series


def test_a_scaled_down_copy_never_scores_sharper_than_its_original(client, vault):
    folder = vault / "RAW/2024/kust"
    folder.mkdir(parents=True)
    base = _scene(57, (1600, 1200))
    base.save(folder / "kust.jpg", quality=92)
    base.resize((800, 600), Image.Resampling.LANCZOS).save(folder / "kust half.jpg", quality=70)
    base.resize((400, 300), Image.Resampling.LANCZOS).save(folder / "kust klein.jpg", quality=60)
    base.filter(ImageFilter.GaussianBlur(1.5)).save(folder / "kust wazig.jpg", quality=92)
    # Measured at one size, the small copy is as soft as it is (at its own 400 px it looked sharper)
    small = near_dups.analyse(str(folder / "kust klein.jpg"))["sharpness"]
    assert small < near_dups.analyse(str(folder / "kust.jpg"))["sharpness"]
    _index(client)
    _run(client)
    series = _with(_series(client, "ruim"), "kust.jpg")[0]
    members = {m["name"]: m for m in series["members"]}
    assert set(members) == {"kust.jpg", "kust half.jpg", "kust klein.jpg", "kust wazig.jpg"}
    sharp = {name: m["breakdown"]["sharpness"] for name, m in members.items()}
    assert sharp["kust.jpg"] == near_dups.POINTS["sharpness"]
    assert sharp["kust half.jpg"] <= sharp["kust.jpg"] and sharp["kust klein.jpg"] < sharp["kust.jpg"]
    assert sharp["kust wazig.jpg"] < sharp["kust.jpg"]
    assert members["kust.jpg"]["best"]

    # Directly: a smaller member is capped at the sharpest bigger one, a same-size one is not
    def member(i, w, sharpness):
        return {"id": i, "path": f"/v/{i}.jpg", "size": 100, "width": w, "height": w * 3 // 4,
                "sharpness": sharpness, "clip_low": 0.0, "clip_high": 0.0, "keurer_keep": False,
                "rating": None, "votes": 0}
    group = [member(1, 1600, 100.0), member(2, 800, 130.0), member(3, 1600, 60.0)]
    near_dups.score_members(group)
    assert group[1]["breakdown"]["sharpness"] == group[0]["breakdown"]["sharpness"] == 40
    assert group[2]["breakdown"]["sharpness"] < 40 and group[0]["best"]


def test_quality_score_weighs_exposure_and_the_users_own_choice():
    def member(i, **kw):
        base = {"id": i, "path": f"/v/{i}.jpg", "size": 100, "width": 1000, "height": 750, "sharpness": 100.0,
                "clip_low": 0.0, "clip_high": 0.0, "keurer_keep": False, "rating": None, "votes": 0}
        return {**base, **kw}

    members = [member(1, clip_high=0.2), member(2)]
    near_dups.score_members(members)
    assert members[1]["best"] and members[0]["breakdown"]["exposure"] < members[1]["breakdown"]["exposure"]

    members = [member(1, sharpness=90.0), member(2, keurer_keep=True)]
    near_dups.score_members(members)
    assert members[1]["best"] and members[1]["breakdown"]["choice"] == 12

    members = [member(1, rating=1200, votes=4), member(2)]
    near_dups.score_members(members)
    assert members[0]["best"] and members[0]["breakdown"]["choice"] == 8

    # A tie: the bigger file, then the first path
    members = [member(2), member(1)]
    near_dups.score_members(members)
    assert members[1]["best"]
    members = [member(2), member(1, size=101)]
    near_dups.score_members(members)
    assert members[1]["best"]


# ---------- the job: incremental, resumable, cancellable ----------

def _phash_rows(config):
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        return conn.execute("SELECT COUNT(*) FROM media_phash").fetchone()[0]


def test_the_job_only_analyses_what_is_new_or_changed(client, vault, config, monkeypatch):
    _index(client)
    first = _run(client)
    assert first["analysed"] > 0
    calls = []
    original = near_dups.analyse
    monkeypatch.setattr(near_dups, "analyse", lambda path: calls.append(path) or original(path))
    again = _run(client)
    assert again["analysed"] == 0 and again["undecodable"] == 0 and calls == []
    # Undecodable files are not tried again either (they give the same answer every time)
    assert client.get("/api/series/status").json()["pending"] == 0

    # One new photo and one edited in place: only those two are read
    _scene(41).save(vault / "RAW/2024 nieuw.jpg", quality=90)
    edited = vault / "RAW/2019/zomer vakantie/IMG_0002.JPG"
    _scene(42).save(edited, quality=90)
    _index(client, full=True)
    assert client.get("/api/series/status").json()["pending"] == 2
    third = _run(client)
    assert third["analysed"] == 2 and sorted(Path(p).name for p in calls) == ["2024 nieuw.jpg", "IMG_0002.JPG"]
    # The vault's series did not change through this
    assert len(_series(client)["items"]) == 2


@pytest.mark.parametrize("how", ["pause", "cancel"])
def test_a_stopped_run_keeps_what_it_did_and_the_next_one_finishes(client, config, monkeypatch, how):
    monkeypatch.setattr(near_dups, "BATCH", 3)
    monkeypatch.setattr(near_dups, "ANALYSE_WORKERS", 1)
    _index(client)
    original = near_dups.analyse
    calls = []
    job_id = {}

    def counted(path):
        calls.append(path)
        if len(calls) == 7 and job_id:
            (_manager().pause if how == "pause" else _manager().cancel)(job_id["id"])
        return original(path)

    monkeypatch.setattr(near_dups, "analyse", counted)
    r = client.post("/api/jobs", json={"type": "phash"})
    job_id["id"] = r.json()["job"]["id"]
    job = _manager().wait(job_id["id"], timeout=60)
    assert job["status"] == ("paused" if how == "pause" else "cancelled"), job
    stored = _phash_rows(config)
    assert 0 < stored < 20
    before = len(calls)
    if how == "pause":
        _manager().resume(job_id["id"])
        job = _manager().wait(job_id["id"], statuses=("queued", "running"), timeout=60)
        assert job["status"] == "done", job
        result = client.get(f"/api/jobs/{job_id['id']}/result").json()["result"]
    else:
        result = _run(client)
    # What the stopped run wrote is not read again (at most the one photo that was underway)
    assert len(calls) - before <= result["analysed"] + result["undecodable"] + 1
    assert len(set(calls)) >= len(calls) - 2
    status = client.get("/api/series/status").json()
    assert status["pending"] == 0
    assert len(_series(client)["items"]) == 2


def test_the_job_waits_for_the_index_and_waits_for_an_unplugged_vault(client, vault, config, monkeypatch):
    r = client.post("/api/jobs", json={"type": "phash"})
    assert r.status_code == 409 and "bibliotheek" in r.text.lower()
    _index(client)

    moved_away = vault.parent / "vault_unplugged"
    original = near_dups.analyse
    state = {"n": 0}

    def unplug_once(path):
        state["n"] += 1
        if state["n"] == 3:
            os.rename(vault, moved_away)      # the temp vault 'unplugged'
        return original(path)

    monkeypatch.setattr(near_dups, "analyse", unplug_once)
    monkeypatch.setattr(near_dups, "ANALYSE_WORKERS", 1)
    job = client.post("/api/jobs", json={"type": "phash"}).json()["job"]
    waiting = _manager().wait(job["id"], statuses=("queued", "running"), timeout=60)
    assert waiting["status"] == "waiting_for_vault", waiting
    os.rename(moved_away, vault)
    done = _manager().wait(job["id"], statuses=("queued", "running", "waiting_for_vault"), timeout=60)
    assert done["status"] == "done", done
    assert client.get("/api/series/status").json()["pending"] == 0
    assert len(_series(client)["items"]) == 2


def _byte_copies_in_one_series(client, vault):
    """Series in which two photos shown side by side are byte for byte the same file."""
    found = []
    for series in _series(client, "ruim")["items"]:
        shas = [_sha(Path(m["path"])) for m in series["members"]]
        if len(set(shas)) < len(shas):
            found.append(_names(series))
    return found


@pytest.mark.parametrize("how", ["pause", "fail"])
def test_a_run_that_stops_before_the_copies_are_hashed_never_shows_copies_as_a_series(
        client, vault, config, monkeypatch, how):
    from backend import scanner
    monkeypatch.setattr(near_dups, "CONTENT_BATCH", 1)
    _index(client)
    original = scanner.hash_file_xxh3
    calls = []
    job_id = {}

    def hashing(path):
        calls.append(str(path))
        if len(calls) == 2:
            if how == "fail":
                raise RuntimeError("stopped while hashing")
            _manager().pause(job_id["id"])
        return original(path)

    monkeypatch.setattr(scanner, "hash_file_xxh3", hashing)
    job_id["id"] = client.post("/api/jobs", json={"type": "phash"}).json()["job"]["id"]
    job = _manager().wait(job_id["id"], timeout=60)
    assert job["status"] == ("paused" if how == "pause" else "failed"), job
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        hashed = conn.execute("SELECT COUNT(*) FROM media_phash WHERE content IS NOT NULL").fetchone()[0]
    assert hashed >= 1            # written as it went, not only at the end
    # The analysis is stored, the copies are not all known: none of them is shown as a different photo
    assert _byte_copies_in_one_series(client, vault) == []
    assert not any(len(s["members"]) == len(s.get("copies", [])) for s in _series(client, "ruim")["items"])

    if how == "pause":
        _manager().resume(job_id["id"])
        job = _manager().wait(job_id["id"], statuses=("queued", "running"), timeout=60)
        assert job["status"] == "done", job
    else:
        monkeypatch.setattr(scanner, "hash_file_xxh3", original)
        _run(client)
    assert _byte_copies_in_one_series(client, vault) == []
    listed = _series(client)
    assert len(listed["items"]) == 2
    strand = _with(listed, "strand klein.jpg")[0]
    assert len(next(m for m in strand["members"] if m["name"] != "strand klein.jpg")["copies"]) >= 2
    if how == "pause":
        # What the paused run hashed was not read again
        assert len(calls) == len(set(calls))


def test_copies_the_duplicates_screen_already_hashed_are_not_read_again(client, vault, config, monkeypatch):
    from backend import scanner
    job = client.post("/api/jobs", json={"type": "dup_scan", "params": {"folder": str(vault)}}).json()["job"]
    assert _manager().wait(job["id"], timeout=60)["status"] == "done"
    _index(client)
    original = scanner.hash_file_xxh3
    calls = []
    monkeypatch.setattr(scanner, "hash_file_xxh3", lambda path: (calls.append(path), original(path))[1])
    result = _run(client)
    assert result["copies_cached"] >= 4 and result["copies_hashed"] == 0 and calls == []
    strand = _with(_series(client), "strand klein.jpg")[0]
    assert len(next(m for m in strand["members"] if m["name"] != "strand klein.jpg")["copies"]) >= 2


def test_rows_of_removed_files_are_cleaned_up_with_the_index(client, vault, config):
    _index(client)
    _run(client)
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        klein_id = conn.execute("SELECT id FROM media WHERE name = 'strand klein.jpg'").fetchone()[0]
    (vault / KLEIN).rename(vault / "RAW/2022/bursts/klein.weg")   # no longer media: gone for the index
    _index(client)
    _index(client)          # the second pass removes rows that stayed missing
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM media_phash WHERE media_id = ?", (klein_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM near_dup_pairs WHERE a = ?1 OR b = ?1", (klein_id,)).fetchone()[0] == 0
    assert not _with(_series(client), Path(STRAND).name)


# ---------- keep the best: move and undo ----------

def _fingerprint(path: Path):
    st = os.stat(path)
    return _sha(path), st.st_mtime_ns, st.st_birthtime, st.st_size


def test_keeping_the_best_moves_the_rest_as_one_undo_and_undo_restores_exactly(client, vault, config):
    _index(client)
    _run(client)
    burst = _with(_series(client), "burst_00.jpg")[0]
    by_name = {m["name"]: m for m in burst["members"]}
    keep = [by_name["burst_02.jpg"]["id"], by_name["burst_04.jpg"]["id"]]
    members = [m["id"] for m in burst["members"]]
    before = {rel: _fingerprint(vault / rel) for rel in BURST}

    r = client.post("/api/series/keep", json={"members": members, "keep": keep})
    assert r.status_code == 200, r.text
    result = r.json()
    assert len(result["moved"]) == 3 and result["failed"] == []
    holding = config.DUPLICATES_FOLDER / "near-duplicates"
    assert result["destination"] == str(holding)
    for item in result["moved"]:
        src, dst = Path(item["path"]), Path(item["dst"])
        assert not src.exists()
        rel = src.relative_to(config.RAW_FOLDER)
        assert dst.resolve() == (holding / rel).resolve()
        assert _fingerprint(dst) == before[str(Path("RAW") / rel)]
    for name in ("burst_02.jpg", "burst_04.jpg"):
        assert (vault / "RAW/2022/bursts" / name).exists()

    # The two that stay are not offered as a series again; the strand series is still there
    listed = _series(client)
    assert not _with(listed, "burst_00.jpg") and not _with(listed, "burst_02.jpg")
    assert _with(listed, "strand klein.jpg")

    # One entry in the recovery center, with every file
    entries = client.get("/api/recovery").json()["entries"]
    entry = next(e for e in entries if e["source"] == "near_dup")
    assert entry["label"] == "Beste uit reeks" and entry["file_count"] == 3 and entry["restorable"] == 3
    assert entry["undo"]["url"] == "/api/recovery/undo"
    r = client.post(entry["undo"]["url"], json=entry["undo"]["body"])
    assert r.status_code == 200 and r.json()["restored"] == 3, r.text
    for rel, fingerprint in before.items():
        assert _fingerprint(vault / rel) == fingerprint, rel
    assert not any(holding.rglob("*.jpg"))
    # Back in the index with the same ids, so the whole series is offered again
    again = _with(_series(client), "burst_00.jpg")
    assert len(again) == 1 and sorted(m["id"] for m in again[0]["members"]) == sorted(members)


def test_keeping_the_small_copy_takes_the_exact_copies_of_the_original_along(client, vault, config):
    _index(client)
    _run(client)
    series = _with(_series(client), "strand klein.jpg")[0]
    klein = next(m for m in series["members"] if m["name"] == "strand klein.jpg")
    original = next(m for m in series["members"] if m is not klein)
    assert original["best"] and series["best"] == original["id"]     # bigger: the suggestion
    r = client.post("/api/series/keep", json={"members": [m["id"] for m in series["members"]], "keep": [klein["id"]]})
    assert r.status_code == 200, r.text
    moved = r.json()["moved"]
    assert len(moved) == 1 + len(original["copies"]) and sum(1 for m in moved if m["copy"]) == len(original["copies"])
    assert (vault / KLEIN).exists()
    assert not _with(_series(client, "ruim"), "strand klein.jpg")
    entry = next(e for e in client.get("/api/recovery").json()["entries"] if e["source"] == "near_dup")
    assert client.post("/api/recovery/undo", json=entry["undo"]["body"]).json()["restored"] == len(moved)
    for item in moved:
        assert Path(item["path"]).exists()


def test_keep_requests_are_checked_before_anything_moves(client, vault, config):
    _index(client)
    _run(client)
    burst = _with(_series(client), "burst_00.jpg")[0]
    strand = _with(_series(client), "strand klein.jpg")[0]
    ids = [m["id"] for m in burst["members"]]

    def keep(members, kept):
        return client.post("/api/series/keep", json={"members": members, "keep": kept})

    assert keep(ids, []).status_code == 400
    assert keep(ids, [strand["members"][0]["id"]]).status_code == 400              # not in the series
    assert keep(ids[:1], ids[:1]).status_code == 400                               # one photo is no series
    assert keep(ids + [strand["members"][0]["id"]], ids[:1]).status_code == 409    # two series in one
    assert keep(["1", 2], [2]).status_code == 400
    assert keep([True, 2], [2]).status_code == 400
    assert client.post("/api/series/keep", json=[1, 2]).status_code == 400

    # The photo that would stay was changed since the comparison: nothing moves
    stays = vault / BURST[0]
    stays.write_bytes(stays.read_bytes() + b"\0")
    r = keep(ids, [burst["members"][0]["id"]])
    assert r.status_code == 409, r.text
    assert all((vault / rel).exists() for rel in BURST)
    assert not any(e["source"] == "near_dup" for e in client.get("/api/recovery").json()["entries"])

    # A photo that would move was changed: it stays and is reported; the rest moves
    changed = vault / BURST[4]
    changed.write_bytes(changed.read_bytes() + b"\0")
    r = keep(ids, [burst["members"][1]["id"]])
    assert r.status_code == 200, r.text
    failed = r.json()["failed"]
    # (burst_00, changed above, would move now: it stays too)
    assert sorted(Path(f["path"]).name for f in failed) == ["burst_00.jpg", "burst_04.jpg"]
    assert all("Veranderd" in f["reason"] for f in failed)
    assert changed.exists() and stays.exists()
    assert len(r.json()["moved"]) == 2


def test_keep_all_hides_a_series_until_it_changes_and_reset_shows_it_again(client, vault):
    _index(client)
    _run(client)
    burst = _with(_series(client), "burst_00.jpg")[0]
    ids = [m["id"] for m in burst["members"]]
    assert client.post("/api/series/keep-all", json={"members": ids}).status_code == 200
    assert not _with(_series(client), "burst_00.jpg")
    assert all((client.app.state.config.VAULT_ROOT / rel).exists() for rel in BURST)   # nothing moved
    assert client.get("/api/series/status").json()["reviewed"] == 1
    # A sixth shot joins the burst: it is a different series now and shows up again
    Image.open(client.app.state.config.VAULT_ROOT / BURST[4]).save(
        client.app.state.config.VAULT_ROOT / "RAW/2022/bursts/burst_05.jpg", quality=85,
        exif=Image.open(client.app.state.config.VAULT_ROOT / BURST[4]).getexif())
    _index(client)
    _run(client)
    assert len(_with(_series(client), "burst_00.jpg")[0]["members"]) == 6
    assert client.post("/api/series/keep-all", json={"members": [ids[0], 99999]}).status_code == 409
    r = client.post("/api/series/reviews/reset")
    assert r.status_code == 200 and r.json()["shown_again"] == 1
    assert client.get("/api/series/status").json()["reviewed"] == 0


def test_series_are_paged_and_sorted(client, vault):
    folder = vault / "RAW/2024/veel"
    folder.mkdir(parents=True)
    for n in range(4):
        base = _scene(100 + n)
        for k in range(n + 2):          # series of 2, 3, 4 and 5 photos
            base.resize((800 - 40 * k, 600 - 30 * k)).save(folder / f"s{n}_{k}.jpg", quality=90 - k)
    _index(client)
    _run(client)
    first = client.get("/api/series", params={"limit": 2}).json()
    assert first["total"] == 6 and first["next_offset"] == 2 and len(first["items"]) == 2
    sizes = [s["count"] for s in first["items"]]
    rest = client.get("/api/series", params={"limit": 50, "offset": 2}).json()
    assert rest["next_offset"] is None
    sizes += [s["count"] for s in rest["items"]]
    assert sizes == sorted(sizes, reverse=True) and sizes[:2] == [5, 5]
    recent = client.get("/api/series", params={"sort": "recent", "limit": 50}).json()["items"]
    taken = [s["taken"] for s in recent]
    assert taken == sorted(taken, reverse=True)
    assert client.get("/api/series", params={"limit": 999}).json()["limit"] == 50
    assert client.get("/api/series", params={"offset": -1}).status_code == 422


def test_hundreds_of_photos_that_look_the_same_are_one_group_not_a_million_pairs(client, vault, config):
    folder = vault / "RAW/2024/scans"
    folder.mkdir(parents=True)
    sample = (vault / BURST[0]).read_bytes()
    for i in range(300):
        (folder / f"scan_{i:03d}.jpg").write_bytes(sample + str(i).encode())   # same picture, other bytes
    (folder / "scan_kopie.jpg").write_bytes(sample + b"7")                     # byte for byte scan_007
    _index(client)
    started = time.monotonic()
    _run(client)
    assert time.monotonic() - started < 30
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM near_dup_pairs").fetchone()[0] < 2000
    listed = _series(client)

    def in_scans(items):
        return [m for s in items for m in s["members"] if m["folder"] == str(folder)]

    # 301 scans, of which two byte for byte the same: 300 photos, in parts of at most 48
    total = len(in_scans(listed["items"]))
    assert total == 300
    assert all(s["count"] <= near_dups.MAX_SERIES_SIZE for s in listed["items"])
    assert sum(1 for s in listed["items"] if s["part"]) >= 300 // near_dups.MAX_SERIES_SIZE
    copies = [m for s in listed["items"] for m in s["members"] if m["copies"]]
    assert any(m["name"] in ("scan_007.jpg", "scan_kopie.jpg") and [Path(c["path"]).name for c in m["copies"]]
               in (["scan_kopie.jpg"], ["scan_007.jpg"]) for m in copies)



def test_when_the_first_photo_of_a_group_leaves_the_rest_is_paired_again(client, vault):
    """Pairs between groups of lookalikes are stored between their first photos."""
    folder = vault / "RAW/2024/groep"
    folder.mkdir(parents=True)
    base = _scene(61)
    base.save(folder / "a.jpg", quality=92)
    (folder / "b.jpg").write_bytes((folder / "a.jpg").read_bytes() + b"x")   # looks the same, other bytes
    base.resize((400, 300)).save(folder / "c.jpg", quality=60)
    _index(client)
    _run(client)
    assert _names(_with(_series(client), "a.jpg")[0]) == ["a.jpg", "b.jpg", "c.jpg"]
    (folder / "a.jpg").rename(folder / "a.weg")
    _index(client)
    _index(client)                       # a.jpg (and its pairs) leave the index
    assert client.get("/api/series/status").json()["pending"] >= 1
    _run(client)
    assert _names(_with(_series(client), "b.jpg")[0]) == ["b.jpg", "c.jpg"]


def test_a_very_big_group_comes_in_parts_that_each_can_be_decided(client, vault, monkeypatch):
    monkeypatch.setattr(near_dups, "MAX_SERIES_SIZE", 3)
    _index(client)
    _run(client)
    parts = [s for s in _series(client)["items"] if s["kind"] == "burst"]
    assert sorted(len(s["members"]) for s in parts) == [2, 3]
    assert sorted(tuple(s["part"]) for s in parts) == [(1, 2), (2, 2)]
    assert sorted(n for s in parts for n in _names(s)) == [Path(p).name for p in BURST]
    first = next(s for s in parts if s["part"] == [1, 2])
    ids = [m["id"] for m in first["members"]]
    r = client.post("/api/series/keep", json={"members": ids, "keep": ids[:1]})
    assert r.status_code == 200 and len(r.json()["moved"]) == 2, r.text
    assert client.post("/api/series/keep-all", json={
        "members": [m["id"] for m in next(s for s in parts if s["part"] == [2, 2])["members"]]}).status_code == 200


# ---------- migration ----------

def test_migration_30_adds_the_tables_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from backend.database import Database
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 12, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{\"full\": true}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                     "VALUES ('/v/RAW/a.jpg', '/v/RAW', 'a.jpg', 'photo', '.jpg', 10, 1, 1, 1, 1)")
        conn.execute("INSERT INTO media_folders VALUES ('/v/RAW', '/v', 1, 1.0)")
        conn.execute("INSERT INTO recovery_actions (kind, title, folder, details, created_at) "
                     "VALUES ('viewer_move', 'x', '/v/RAW', NULL, 1.0)")
        conn.execute("INSERT INTO recovery_moves (action_id, src_path, dst_path, dst_ino, dst_size, moved_at) "
                     "VALUES (1, '/v/RAW/b.jpg', '/v/RAW/c/b.jpg', 5, 6, 1.0)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    assert before["sqlite_sequence"] and before["recovery_moves"] and before["media"]

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 31
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        assert {r[1] for r in conn.execute("PRAGMA table_info(media_phash)")} >= {
            "media_id", "algo", "version", "size", "mtime_ns", "status", "dhash", "phash", "colors",
            "sharpness", "clip_low", "clip_high", "contrast", "paired", "content"}
        assert {r[1] for r in conn.execute("PRAGMA table_info(near_dup_pairs)")} >= {
            "a", "b", "kind", "phash_d", "dhash_d", "color_d", "gap", "flat", "same_bytes"}
        assert {r[1] for r in conn.execute("PRAGMA table_info(near_dup_reviews)")} == {
            "key", "media_ids", "decision", "decided_at"}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v12-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_recent_keurer_decisions(1)[0]["dest_ino"] == 77
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    assert db.get_recovery_moves(1)[0]["dst_path"] == "/v/RAW/c/b.jpg"
    # Hashes as big as 64 bits fit (stored signed)
    with db._connect() as conn:
        conn.execute("INSERT INTO media_phash (media_id, algo, version, size, mtime_ns, status, dhash, phash, "
                     "computed_at) VALUES (1, 'x', 1, 10, 1, 'ok', ?, ?, 1.0)",
                     (near_dups.to_signed(2 ** 64 - 1), near_dups.to_signed(2 ** 63)))
        row = conn.execute("SELECT dhash, phash FROM media_phash").fetchone()
    assert [near_dups.to_unsigned(v) for v in row] == [2 ** 64 - 1, 2 ** 63]
    db.close_all()
