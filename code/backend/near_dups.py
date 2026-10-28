"""
DataVault Dashboard - Near-duplicates and bursts ('Beste uit reeks')

Exact byte-for-byte copies are the duplicates screen's work (scanner.py). This module
finds photos that LOOK the same: a resized or recompressed copy, an edit, and burst
series (several shots within a few seconds). For every photo of the media index it
keeps a small fingerprint (table media_phash, migration 30), computed once by the
'phash' job from a small decoded version of the photo:

- dhash and phash: 64-bit perceptual hashes (difference hash on 9x8 grey, DCT hash on
  32x32 grey). Two photos that look alike differ in few bits (Hamming distance).
- colors: the mean colour of the four quarters of the photo (12 bytes). Perceptual
  hashes are colour-blind and say little about a nearly flat photo (a blue sky, a
  black frame); the colours keep a red and a blue photo of the same shape apart.
- contrast (spread of the grey values): a nearly flat photo (FLAT_CONTRAST) has hashes
  that say little, so two photos of which one is flat must match closely in colour.
- sharpness (variance of the Laplacian), the share of clipped shadows and highlights,
  and the size in pixels, for the quality score that suggests the best of a series.

Only photos: videos are skipped (a frame would need ffmpeg per video, which is not
cheap), and so are RAW files Pillow cannot decode (counted as 'undecodable'). JPEGs
are decoded at 1/2 to 1/8 of their size (draft mode), HEIC through pillow_heif.

Pairs (table near_dup_pairs) are found with multi-index hashing: the 64 phash bits are
split into 4 parts of 16; two hashes within PAIR_RADIUS bits agree in at least one part
up to PAIR_RADIUS // 4 bits, so every candidate is found by looking up a few hundred
variations of each part in a sorted array (numpy). Never all pairs of 100k photos.
Bursts are photos from the same folder and camera, taken (EXIF) within a few seconds
of each other, that also look alike; they get a looser visual threshold.

Series are the connected groups of pairs that pass the chosen strictness. The user
picks which to keep; the others are moved (file_ops.move_no_overwrite) into
RAW_DUPLICATES/near-duplicates/<path below RAW>, one recovery action per series, and
can be put back from the recovery center. Nothing is ever deleted.
"""
import hashlib
import os
import stat as stat_module
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
from PIL import Image, ImageOps

from backend import thumbnail  # noqa: F401  (registers the HEIC opener)

PHASH_JOB = "phash"
ALGO = "dhash+phash/dct32"
ALGO_VERSION = 1              # bump when analyse() changes: every photo is analysed again
ANALYSE_SIZE = 512            # the photo is analysed at most this many pixels wide or high
HASH_BITS = 64

UNDECODABLE = "undecodable"   # media_phash.status of a photo Pillow cannot read (same on every run)
ERROR = "error"               # read error: tried again on the next run
OK = "ok"


class Undecodable(Exception):
    """The file reads fine, but it is no picture Pillow can decode (corrupt, RAW, empty)."""


# ---------- one photo ----------

def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n))
    m[0] *= 1 / np.sqrt(2)
    return m * np.sqrt(2 / n)


_DCT32 = _dct_matrix(32)
_BIT_WEIGHTS = (np.uint64(1) << np.arange(63, -1, -1, dtype=np.uint64))


def _bits_to_int(bits: np.ndarray) -> int:
    """64 booleans (row-major, first = most significant) as an unsigned 64-bit int."""
    return int(np.bitwise_or.reduce(np.where(bits.reshape(-1), _BIT_WEIGHTS, np.uint64(0))))


def dhash(grey: Image.Image) -> int:
    pixels = np.asarray(grey.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.float32)
    return _bits_to_int(pixels[:, 1:] > pixels[:, :-1])


def phash(grey: Image.Image) -> int:
    pixels = np.asarray(grey.resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_DCT32 @ pixels @ _DCT32.T)[:8, :8]
    return _bits_to_int(low > np.median(low))


def _open_small(path: str) -> Image.Image:
    """The photo as RGB, upright (EXIF orientation), at most ANALYSE_SIZE pixels on a side."""
    try:
        img = Image.open(path)
        img.draft("RGB", (ANALYSE_SIZE, ANALYSE_SIZE))   # JPEG: decode at 1/2, 1/4 or 1/8 size
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img.thumbnail((ANALYSE_SIZE, ANALYSE_SIZE), Image.Resampling.BILINEAR, reducing_gap=2.0)
    except OSError as e:
        if getattr(e, "errno", None) is not None:
            raise          # a real read error (permission, disk): tried again next time
        raise Undecodable(str(e)) from e
    except (ValueError, SyntaxError, Image.DecompressionBombError, MemoryError, EOFError) as e:
        raise Undecodable(str(e)) from e
    if img.width < 8 or img.height < 8:
        raise Undecodable("te klein")
    return img


def analyse(path: str) -> Dict[str, Any]:
    """
    The fingerprint and quality figures of one photo. Raises Undecodable for a file that
    is no decodable picture, OSError (with an errno) when it cannot be read.
    """
    img = _open_small(path)
    grey = img.convert("L")
    g = np.asarray(grey, dtype=np.float32)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    rgb = np.asarray(img, dtype=np.float32)
    h2, w2 = rgb.shape[0] // 2, rgb.shape[1] // 2
    quarters = [rgb[:h2, :w2], rgb[:h2, w2:], rgb[h2:, :w2], rgb[h2:, w2:]]
    colors = bytes(int(round(v)) for q in quarters for v in q.reshape(-1, 3).mean(axis=0))
    return {
        "dhash": dhash(grey),
        "phash": phash(grey),
        "colors": colors,
        "sharpness": float(lap.var()),
        "clip_low": float((g <= 3).mean()),
        "clip_high": float((g >= 252).mean()),
        "brightness": float(g.mean()),
        "contrast": float(g.std()),
    }


# ---------- distances ----------

def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & 0xFFFFFFFFFFFFFFFF).count("1")


def color_distance(a: bytes, b: bytes) -> float:
    """Mean absolute difference of the 12 quarter colour values (0-255)."""
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def to_signed(value: int) -> int:
    """SQLite integers are signed 64-bit: store the hash as its two's complement."""
    return value - (1 << 64) if value >= (1 << 63) else value


def to_unsigned(value: int) -> int:
    return value + (1 << 64) if value < 0 else value


# ---------- multi-index hashing ----------

MIH_PARTS = 4                 # 64 bits in 4 parts of 16
_PART_BITS = HASH_BITS // MIH_PARTS


def _flip_masks(bits: int, radius: int) -> np.ndarray:
    """Every value with at most `radius` of the low `bits` bits set (the XOR variations)."""
    from itertools import combinations
    masks = [0]
    for r in range(1, radius + 1):
        for combo in combinations(range(bits), r):
            m = 0
            for b in combo:
                m |= 1 << b
            masks.append(m)
    return np.array(masks, dtype=np.int64)


class HashIndex:
    """
    Finds every pair of 64-bit hashes within `radius` bits, without comparing all pairs.
    Multi-index hashing: split each hash into MIH_PARTS parts; two hashes within radius
    bits agree up to radius // MIH_PARTS bits in at least one part (pigeonhole). Per part
    the hashes are bucketed by that part's value (a counting sort: one array of offsets
    per 65536 values); a query looks up every value within that many bits of its own part
    and checks the full distance only for what it finds there.
    """

    def __init__(self, hashes: np.ndarray):
        self.hashes = np.ascontiguousarray(hashes, dtype=np.uint64)
        self.n = len(self.hashes)
        size = 1 << _PART_BITS
        self.parts = []
        for p in range(MIH_PARTS):
            shift = np.uint64(p * _PART_BITS)
            keys = ((self.hashes >> shift) & np.uint64(size - 1)).astype(np.int64)
            order = np.argsort(keys, kind="stable")
            counts = np.bincount(keys, minlength=size)
            starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
            self.parts.append((keys, order, counts, starts))

    def pairs(self, radius: int, queries: Optional[np.ndarray] = None,
              check: Optional[Callable[[], None]] = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        (i, j, distance) arrays of every pair within radius bits, i < j, each pair once.
        queries: only pairs with at least one of these positions (all when None).
        check() is called between steps (a job's cancel/pause check).
        """
        masks = _flip_masks(_PART_BITS, radius // MIH_PARTS)
        q = np.arange(self.n, dtype=np.int64) if queries is None else np.asarray(queries, dtype=np.int64)
        is_query = None
        if queries is not None:
            is_query = np.zeros(self.n, dtype=bool)
            is_query[q] = True
        found = []
        for keys, order, counts, starts in self.parts:
            qkeys = keys[q]
            for mask in masks:
                if check is not None:
                    check()
                look = qkeys ^ mask
                cnt = counts[look]
                hit = cnt > 0
                if not hit.any():
                    continue
                cnt = cnt[hit]
                total = int(cnt.sum())
                qi = np.repeat(q[hit], cnt)
                first = np.repeat(starts[look[hit]], cnt)
                offset = np.arange(total, dtype=np.int64) - np.repeat(np.cumsum(cnt) - cnt, cnt)
                cj = order[first + offset]
                # Each unordered pair once: from its lower position, or from the query side
                if is_query is None:
                    keep = qi < cj
                else:
                    keep = (qi != cj) & ((qi < cj) | ~is_query[cj])
                qi, cj = qi[keep], cj[keep]
                dist = np.bitwise_count(self.hashes[qi] ^ self.hashes[cj])
                close = dist <= radius
                if close.any():
                    a, b = qi[close], cj[close]
                    found.append(np.stack((np.minimum(a, b), np.maximum(a, b))))
        if not found:
            empty = np.zeros(0, dtype=np.int64)
            return empty, empty, empty
        both = np.unique(np.concatenate(found, axis=1), axis=1)
        i, j = both[0], both[1]
        return i, j, np.bitwise_count(self.hashes[i] ^ self.hashes[j]).astype(np.int64)


# ---------- strictness ----------
#
# (phash, dhash, colour) thresholds per setting. Tuned on the test vault (the resized
# 'strand klein.jpg' is at distance 0 of its original; the solid test photos of other
# colours are kept apart by colour) and on 132 real photos with made-up variants:
# half size + JPEG quality 60 and a Gaussian blur stay within phash 4 / dhash 6; a 3%
# shift or crop within phash 8 (90%); +15% brightness within phash 4 (90%) but moves the
# colours by up to 27. Different photos below phash 8 were almost only the same design
# in another colour, which the colour check keeps apart. Bursts (same camera, taken
# within BURST_GAP seconds of each other, same folder) get looser visual thresholds: the
# shots of a burst differ by movement (an 8% shift is phash 14 at the median).

STRICTNESS: Dict[str, Dict[str, Tuple[int, int, float]]] = {
    "streng": {"near": (4, 6, 10.0), "burst": (14, 16, 16.0)},
    "normaal": {"near": (8, 10, 16.0), "burst": (20, 22, 22.0)},
    "ruim": {"near": (11, 14, 24.0), "burst": (26, 28, 30.0)},
}
FLAT_CONTRAST = 10.0          # grey spread below which a photo counts as nearly flat
FLAT_COLOR = 6.0              # the colour limit (at most) when one of the two is flat
DEFAULT_STRICTNESS = "normaal"
LOOSEST = "ruim"
PAIR_RADIUS = STRICTNESS[LOOSEST]["near"][0]    # = 11: radius // 4 = 2 bits per part (fast)
BURST_GAP = 3.0               # seconds between two shots of one burst
BURST_LOOKAHEAD = 6           # a shot is compared with this many shots after it in its burst
MAX_SERIES_SIZE = 48          # photos per series on screen; a bigger group comes in parts, in time order


def strictness_or_default(value: Optional[str]) -> str:
    if value is None or value == "":
        return DEFAULT_STRICTNESS
    if value not in STRICTNESS:
        raise ValueError(value)
    return value


# ---------- the analysis job ----------

STATE_KEY = "near_dups"       # app_settings: {last_run_at, last_complete_at, seconds, ...}
ANALYSE_WORKERS = 4           # Pillow decodes outside the GIL
BATCH = 200                   # analysed photos written per transaction
FLUSH_SECONDS = 2.0
PROGRESS_SECONDS = 0.5

_generation = 0
_generation_lock = threading.Lock()


def _bump() -> None:
    global _generation
    with _generation_lock:
        _generation += 1


def generation() -> int:
    return _generation


def state(db) -> Dict[str, Any]:
    try:
        value = db.get_setting(STATE_KEY, {})
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


# A photo whose stored fingerprint was made from this very file (same size and mtime_ns)
# with this version of analyse()
_CURRENT = "h.size = m.size AND h.mtime_ns = m.mtime_ns AND h.version = ?"


def forget_orphans(db) -> int:
    """Rows of media rows that are gone (removed by the index): their pairs go with them."""
    with db._connect() as conn:
        gone = conn.execute("DELETE FROM media_phash WHERE media_id NOT IN (SELECT id FROM media)").rowcount
        conn.execute("DELETE FROM near_dup_pairs WHERE a NOT IN (SELECT id FROM media) "
                     "OR b NOT IN (SELECT id FROM media)")
    if gone:
        _bump()
    return gone


def counts(db) -> Dict[str, int]:
    """Photos in the index, and how many are analysed, still to do, undecodable or failed."""
    with db._connect() as conn:
        photos = conn.execute("SELECT COUNT(*) FROM media WHERE missing = 0 AND kind = 'photo'").fetchone()[0]
        rows = conn.execute(
            f"SELECT h.status, COUNT(*) FROM media m JOIN media_phash h ON h.media_id = m.id "
            f"WHERE m.missing = 0 AND m.kind = 'photo' AND {_CURRENT} GROUP BY h.status",
            (ALGO_VERSION,)).fetchall()
        unpaired = conn.execute(
            f"SELECT COUNT(*) FROM media m JOIN media_phash h ON h.media_id = m.id "
            f"WHERE m.missing = 0 AND h.paired = 0 AND h.status = 'ok' AND {_CURRENT}", (ALGO_VERSION,)).fetchone()[0]
        pairs = conn.execute("SELECT COUNT(*) FROM near_dup_pairs").fetchone()[0]
        signature = list(conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(m.id), 0) FROM media m JOIN media_phash h ON h.media_id = m.id "
            f"WHERE h.status = 'ok' AND {_CURRENT}", (ALGO_VERSION,)).fetchone())
        reviewed = conn.execute("SELECT COUNT(*) FROM near_dup_reviews").fetchone()[0]
    by_status = dict(rows)
    done = by_status.get(OK, 0) + by_status.get(UNDECODABLE, 0)
    pending = max(0, photos - done) + unpaired
    if not pending and signature[0] and state(db).get("pairs_signature") not in (None, signature):
        pending = 1   # a photo left the index since the last lookup: the next run looks again
    return {
        "photos": photos,
        "analysed": by_status.get(OK, 0),
        "undecodable": by_status.get(UNDECODABLE, 0),
        "errors": by_status.get(ERROR, 0),
        "pending": pending,
        "pairs": pairs,
        "reviewed": reviewed,
    }


def _todo(db) -> List[Tuple[int, str, int, int]]:
    """Photos without a current fingerprint (new, changed, older version, or failed last time)."""
    with db._connect() as conn:
        return conn.execute(
            f"SELECT m.id, m.path, m.size, m.mtime_ns FROM media m "
            f"LEFT JOIN media_phash h ON h.media_id = m.id "
            f"WHERE m.missing = 0 AND m.kind = 'photo' "
            f"AND (h.media_id IS NULL OR NOT ({_CURRENT}) OR h.status = 'error') ORDER BY m.id",
            (ALGO_VERSION,)).fetchall()


def _analyse_row(row: Tuple[int, str, int, int]) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """(status, figures, error text) for one photo; 'behind' when the file changed since it was indexed."""
    _id, path, size, mtime_ns = row
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "behind", None, None
    if not stat_module.S_ISREG(st.st_mode) or (st.st_size, st.st_mtime_ns) != (size, mtime_ns):
        return "behind", None, None   # the index is behind the disk: its next pass reads it again
    try:
        return OK, analyse(path), None
    except Undecodable as e:
        return UNDECODABLE, None, str(e)[:200]


_UPSERT = """
    INSERT OR REPLACE INTO media_phash (media_id, algo, version, size, mtime_ns, status, dhash, phash,
        colors, sharpness, clip_low, clip_high, brightness, contrast, error, paired, computed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
"""


def _values(row, status: str, figures: Optional[Dict[str, Any]], error: Optional[str], now: float) -> Tuple:
    f = figures or {}
    return (row[0], ALGO, ALGO_VERSION, row[2], row[3], status,
            to_signed(f["dhash"]) if figures else None, to_signed(f["phash"]) if figures else None,
            f.get("colors"), f.get("sharpness"), f.get("clip_low"), f.get("clip_high"), f.get("brightness"),
            f.get("contrast"), error, now)


def run_job(ctx, config, db, errors, vault_check) -> Dict[str, Any]:
    """
    The 'phash' job: analyse every photo without a current fingerprint, then look up the
    pairs of the new ones. Written in batches, so pause, cancel, a server stop or an
    unplugged disk lose at most one batch: a resumed run only does what is left.
    errors(path, exc) logs read errors; vault_check() raises VaultUnavailable.
    """
    from backend.jobs import bounded_map
    started = time.time()
    forget_orphans(db)
    todo = _todo(db)
    total = len(todo)
    done = {"analysed": 0, "undecodable": 0, "behind": 0}
    pending: List[Tuple] = []
    last_flush = last_progress = time.monotonic()

    def flush() -> None:
        nonlocal last_flush
        if pending:
            with db._connect() as conn:
                conn.executemany(_UPSERT, pending)
            pending.clear()
            _bump()
        last_flush = time.monotonic()

    ctx.progress(0, total, f"Foto's vergelijken... 0/{total}")
    processed = 0
    try:
        stop = lambda: ctx.stopping or errors.vault_lost  # noqa: E731
        for row, outcome, error in bounded_map(_analyse_row, todo, workers=ANALYSE_WORKERS, stop=stop):
            processed += 1
            now = time.time()
            if error is not None:
                errors(row[1], error)
                pending.append(_values(row, ERROR, None, str(error)[:200], now))
            else:
                status, figures, text = outcome
                if status == "behind":
                    done["behind"] += 1
                else:
                    done["analysed" if status == OK else "undecodable"] += 1
                    pending.append(_values(row, status, figures, text, now))
            tick = time.monotonic()
            if len(pending) >= BATCH or tick - last_flush >= FLUSH_SECONDS:
                flush()
            if tick - last_progress >= PROGRESS_SECONDS or processed == total:
                last_progress = tick
                ctx.progress(processed, total, f"Foto's vergelijken... {processed}/{total}")
    finally:
        flush()
    ctx.check()
    vault_check()
    if done["behind"]:
        from backend import media_index
        media_index.request_update()

    ctx.progress(total, total, "Reeksen zoeken...")
    paired = update_pairs(db, ctx.check)
    seconds = round(time.time() - started, 2)
    figures = counts(db)
    series = len(series_structure(db, DEFAULT_STRICTNESS)["series"])
    result = {
        "photos": figures["photos"],
        "analysed": done["analysed"],
        "undecodable": done["undecodable"],
        "behind": done["behind"],
        "errors": errors.count,
        "error_samples": errors.samples,
        "complete": errors.count == 0,
        "pairs": figures["pairs"],
        "new_pairs": paired["near"],
        "bursts": paired["burst"],
        "series": series,
        "seconds": seconds,
        "per_second": round(done["analysed"] / seconds, 1) if seconds else None,
    }
    previous = state(db)
    db.set_setting(STATE_KEY, {**previous, "last_run_at": time.time(), "seconds": seconds,
                               **({"last_complete_at": time.time()} if result["complete"] else {})})
    return result


def run_message(result: Dict[str, Any]) -> str:
    n = result["series"]
    text = f"Klaar: {result['analysed']} foto's vergeleken, {n} {'reeks' if n == 1 else 'reeksen'}"
    if result["undecodable"]:
        text += f", {result['undecodable']} niet te openen"
    if result["errors"]:
        text += f" ({result['errors']} leesfout{'' if result['errors'] == 1 else 'en'}: onvolledig)"
    return text


# ---------- pairs ----------

def _chunks(items: Sequence, size: int = 500) -> Iterable[Sequence]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _color_matrix(blobs: Sequence[Optional[bytes]]) -> np.ndarray:
    out = np.full((len(blobs), 12), 255, dtype=np.int16)
    for i, blob in enumerate(blobs):
        if blob and len(blob) == 12:
            out[i] = np.frombuffer(blob, dtype=np.uint8)
    return out


def _twins(rows) -> Dict[Tuple, List[int]]:
    """Positions of rows that look exactly alike to the analysis (same hashes and colours)."""
    twins: Dict[Tuple, List[int]] = {}
    for i, r in enumerate(rows):
        twins.setdefault((r[1], r[2], r[3]), []).append(i)
    return twins


def update_pairs(db, check: Callable[[], None] = lambda: None) -> Dict[str, int]:
    """
    When any analysed photo is new or changed (paired = 0), find the pairs again from the
    stored fingerprints (no photo is decoded; about 2 s for 100k photos):

    - Photos that look exactly alike (same hashes and colours: 'twins') are one group.
      Pairs are looked up once per group, between the groups' first photos, so a folder
      of 1,500 copies of one shot is one group, not a million pairs. The series are made
      from the groups when they are shown (all_series), so a group's first photo may be
      gone (moved, or back after an undo) without breaking anything.
    - Twins of the same size get a content hash (xxh3, media_phash.content): the ones
      with the same hash are byte-for-byte copies, the duplicates screen's work.
    - Bursts: shots by the same camera within BURST_GAP seconds, compared one by one.

    Photos hidden as missing (moved to RAW_DUPLICATES by the app) stay in: when an undo
    brings them back, their pairs are still there. Returns the counts written.
    """
    from backend.scanner import hash_file_xxh3
    with db._connect() as conn:
        rows = conn.execute(
            f"SELECT m.id, h.phash, h.dhash, h.colors, h.paired, m.size, m.path, m.taken, m.taken_source, "
            f"m.camera_make, m.camera_model, h.contrast, h.content FROM media m "
            f"JOIN media_phash h ON h.media_id = m.id "
            f"WHERE h.status = 'ok' AND {_CURRENT} ORDER BY m.id", (ALGO_VERSION,)).fetchall()
    dirty = [r[0] for r in rows if not r[4]]
    # (also when a photo left the index: it may have been the first of its group)
    signature = [len(rows), sum(r[0] for r in rows)]
    if not dirty and state(db).get("pairs_signature") == signature:
        return {"near": 0, "burst": 0}

    # Content hashes of twins of the same size (only the ones not known yet)
    twins = _twins(rows)
    contents = []
    for members in twins.values():
        by_size: Dict[int, List[int]] = {}
        for i in members:
            by_size.setdefault(rows[i][5], []).append(i)
        for same in by_size.values():
            for i in (same if len(same) > 1 else ()):
                if rows[i][12] is None:
                    check()
                    try:
                        contents.append((hash_file_xxh3(Path(rows[i][6])), rows[i][0]))
                    except OSError:
                        pass

    # Near pairs between the groups, by their first photo
    firsts = [members[0] for members in twins.values()]
    phashes = np.array([to_unsigned(rows[i][1]) for i in firsts], dtype=np.uint64)
    dhashes = np.array([to_unsigned(rows[i][2]) for i in firsts], dtype=np.uint64)
    colors = _color_matrix([rows[i][3] for i in firsts])
    flat = np.array([(rows[i][11] or 0.0) < FLAT_CONTRAST for i in firsts], dtype=bool)
    loose_p, loose_d, loose_c = STRICTNESS[LOOSEST]["near"]
    i, j, pd = HashIndex(phashes).pairs(PAIR_RADIUS, None, check)
    dd = np.bitwise_count(dhashes[i] ^ dhashes[j]).astype(np.int64)
    cd = np.abs(colors[i] - colors[j]).mean(axis=1)
    fl = flat[i] | flat[j]
    keep = (dd <= loose_d) & (cd <= np.where(fl, min(loose_c, FLAT_COLOR), loose_c))
    near = [(rows[firsts[a]][0], rows[firsts[b]][0], "near", p, d, round(c, 2), None, int(f), 0)
            for a, b, p, d, c, f in zip(i[keep].tolist(), j[keep].tolist(), pd[keep].tolist(),
                                        dd[keep].tolist(), cd[keep].tolist(), fl[keep].tolist())]

    bursts = _burst_pairs(rows, np.array([to_unsigned(r[1]) for r in rows], dtype=np.uint64),
                          np.array([to_unsigned(r[2]) for r in rows], dtype=np.uint64),
                          _color_matrix([r[3] for r in rows]),
                          np.array([(r[11] or 0.0) < FLAT_CONTRAST for r in rows], dtype=bool))
    check()
    with db._connect() as conn:
        conn.execute("DELETE FROM near_dup_pairs")
        conn.executemany(_INSERT_PAIR, near)
        conn.executemany(_INSERT_PAIR, bursts)
        conn.executemany("UPDATE media_phash SET content = ? WHERE media_id = ?", contents)
        for chunk in _chunks(dirty):
            conn.execute(f"UPDATE media_phash SET paired = 1 WHERE media_id IN ({','.join('?' * len(chunk))})", chunk)
    db.set_setting(STATE_KEY, {**state(db), "pairs_signature": signature})
    _bump()
    return {"near": len(near), "burst": len(bursts)}


_INSERT_PAIR = ("INSERT OR REPLACE INTO near_dup_pairs (a, b, kind, phash_d, dhash_d, color_d, gap, flat, same_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")


def _identical(a: str, b: str) -> bool:
    """Byte for byte the same (False when either cannot be read)."""
    from backend.file_ops import files_identical
    try:
        return files_identical(Path(a), Path(b))
    except OSError:
        return False


def _burst_pairs(rows, phashes, dhashes, colors, flat) -> List[Tuple]:
    """
    Shots by the same camera (EXIF make and model) with an EXIF capture time, at most
    BURST_GAP seconds after the shot before, that look alike within the loosest burst
    thresholds. Whether both are in the same folder is checked when the series are
    built, so a burst that was moved (and put back) needs no new lookup.
    """
    loose_p, loose_d, loose_c = STRICTNESS[LOOSEST]["burst"]
    shots = sorted((i for i, r in enumerate(rows) if r[8] == "exif" and r[7] is not None),
                   key=lambda i: (rows[i][9] or "", rows[i][10] or "", rows[i][7], rows[i][0]))
    pairs = []
    for n, a in enumerate(shots):
        prev = a
        for b in shots[n + 1:n + 1 + BURST_LOOKAHEAD]:
            ra, rb, rp = rows[a], rows[b], rows[prev]
            if (rb[9], rb[10]) != (ra[9], ra[10]) or rb[7] - rp[7] > BURST_GAP:
                break
            prev = b
            p = int(np.bitwise_count(phashes[a] ^ phashes[b]))
            d = int(np.bitwise_count(dhashes[a] ^ dhashes[b]))
            c = float(np.abs(colors[a] - colors[b]).mean())
            f = bool(flat[a] or flat[b])
            if p <= loose_p and d <= loose_d and c <= (min(loose_c, FLAT_COLOR) if f else loose_c):
                lo, hi = sorted((ra[0], rb[0]))
                pairs.append((lo, hi, "burst", p, d, round(c, 2), round(rb[7] - ra[7], 3), int(f), 0))
    return pairs


# ---------- series ----------

class _UnionFind:
    def __init__(self):
        self.parent: Dict[int, int] = {}

    def find(self, x: int) -> int:
        parent = self.parent
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                ra, rb = rb, ra
            self.parent[ra] = rb

    def groups(self) -> Dict[int, List[int]]:
        out: Dict[int, List[int]] = {}
        for x in list(self.parent):
            out.setdefault(self.find(x), []).append(x)
        return out


def series_key(ids: Iterable[int]) -> str:
    """The same set of photos always gives the same key (for 'alles behouden')."""
    text = ",".join(str(i) for i in sorted(set(ids)))
    return hashlib.sha1(text.encode()).hexdigest()[:20]


_structure_cache: Dict[str, Tuple[Tuple, Dict[str, Any]]] = {}
_structure_lock = threading.Lock()


def series_structure(db, strictness: str) -> Dict[str, Any]:
    """all_series without the series the user already decided on."""
    structure = all_series(db, strictness)
    with db._connect() as conn:
        reviewed = {r[0] for r in conn.execute("SELECT key FROM near_dup_reviews")}
    if not reviewed:
        return structure
    return {**structure, "series": [s for s in structure["series"] if s["key"] not in reviewed]}


def all_series(db, strictness: str) -> Dict[str, Any]:
    """
    Every series at this strictness: {series: [{key, kind, ids, copies: {rep: [ids]},
    taken, size, part}], components, copies, rows: {id: (path, folder, taken, size,
    mtime_ns)}, version}. Cached until the index or the fingerprints change.

    Built from groups of twins (photos that look exactly alike, see update_pairs): the
    pairs join groups, and a group counts with the photos of it that are in the archive
    now. Byte-identical copies are the duplicates screen's work: they count as one photo
    here, shown once (the oldest copy) with the others listed with it. A group of more
    than MAX_SERIES_SIZE photos is cut into parts (part: [n, of]); components keeps the
    whole groups, for checking what a request names.
    """
    from backend import media_index
    version = (media_index.generation(), generation())
    with _structure_lock:
        cached = _structure_cache.get(strictness)
        if cached and cached[0] == version:
            return cached[1]
    near_p, near_d, near_c = STRICTNESS[strictness]["near"]
    burst_p, burst_d, burst_c = STRICTNESS[strictness]["burst"]
    with db._connect() as conn:
        analysed = conn.execute(
            f"SELECT m.id, h.phash, h.dhash, h.colors, h.content, m.missing, m.path, m.folder, m.taken, "
            f"m.size, m.mtime_ns FROM media m JOIN media_phash h ON h.media_id = m.id "
            f"WHERE h.status = 'ok' AND {_CURRENT}", (ALGO_VERSION,)).fetchall()
        pairs = conn.execute(
            "SELECT a, b, kind FROM near_dup_pairs WHERE "
            "(kind = 'near' AND phash_d <= ? AND dhash_d <= ? AND color_d <= CASE WHEN flat THEN ? ELSE ? END) "
            "OR (kind = 'burst' AND phash_d <= ? AND dhash_d <= ? AND color_d <= CASE WHEN flat THEN ? ELSE ? END)",
            (near_p, near_d, min(near_c, FLAT_COLOR), near_c,
             burst_p, burst_d, min(burst_c, FLAT_COLOR), burst_c)).fetchall()

    # Twins: one group per look; only the photos that are in the archive now take part
    group_of: Dict[int, int] = {}
    group_ids: Dict[Tuple, int] = {}
    members_of: Dict[int, List[int]] = {}
    rows: Dict[int, Tuple] = {}
    content: Dict[int, Optional[str]] = {}
    for r in analysed:
        g = group_ids.setdefault((r[1], r[2], r[3]), len(group_ids))
        group_of[r[0]] = g
        if r[5] == 0:
            members_of.setdefault(g, []).append(r[0])
            rows[r[0]] = (r[6], r[7], r[8], r[9], r[10])
            content[r[0]] = r[4]

    groups = _UnionFind()
    for g, members in members_of.items():
        groups.find(g)
    burst_photos: Set[int] = set()
    for a, b, kind in pairs:
        ga, gb = group_of.get(a), group_of.get(b)
        if ga is None or gb is None or ga not in members_of or gb not in members_of:
            continue
        if kind == "burst":
            # A burst only counts within one folder (as the photos are now)
            if a not in rows or b not in rows or rows[a][1] != rows[b][1]:
                continue
            burst_photos.update((a, b))
        groups.union(ga, gb)

    # 1. Byte-identical copies count as one photo: the oldest (then shortest path) stands for them
    copies_of: Dict[int, List[int]] = {}
    reps_of_group: Dict[int, List[int]] = {}
    for g, members in members_of.items():
        clusters: Dict[Tuple, List[int]] = {}
        for i in members:
            clusters.setdefault((rows[i][3], content[i]) if content[i] else ("photo", i), []).append(i)
        reps = []
        for cluster in clusters.values():
            rep = min(cluster, key=lambda i: (rows[i][4], len(rows[i][0]), rows[i][0]))
            reps.append(rep)
            if len(cluster) > 1:
                copies_of[rep] = sorted(i for i in cluster if i != rep)
        reps_of_group[g] = reps

    # 2. Series: the photos (one per set of copies) of connected groups
    series = []
    components = []
    for gs in groups.groups().values():
        members = [i for g in gs for i in reps_of_group[g]]
        if len(members) < 2:
            continue
        members.sort(key=lambda i: (rows[i][2] if rows[i][2] is not None else float("inf"), rows[i][0]))
        components.append(members)
        # A very big group (hundreds of the same shot) is shown in parts, in time order
        parts = -(-len(members) // MAX_SERIES_SIZE)
        size = -(-len(members) // parts)
        for start in range(0, len(members), size):
            part = members[start:start + size]
            taken = [rows[i][2] for i in part if rows[i][2] is not None]
            series.append({
                "key": series_key(part),
                "kind": "burst" if all(i in burst_photos for i in part) else "near",
                "ids": part,
                "copies": {i: copies_of[i] for i in part if copies_of.get(i)},
                "taken": max(taken) if taken else None,
                "size": sum(rows[i][3] for i in part),
                "part": [start // size + 1, parts] if parts > 1 else None,
            })
    result = {"series": series, "components": components, "copies": copies_of, "rows": rows,
              "version": f"{version[0]:x}.{version[1]:x}"}
    with _structure_lock:
        _structure_cache[strictness] = (version, result)
    return result


SORTS = ("size", "recent")


def sorted_series(structure: Dict[str, Any], sort: str) -> List[Dict[str, Any]]:
    """size: most photos first (then newest); recent: newest first."""
    if sort not in SORTS:
        raise ValueError(sort)
    newest = lambda s: -(s["taken"] or 0)  # noqa: E731
    if sort == "recent":
        return sorted(structure["series"], key=lambda s: (newest(s), -len(s["ids"]), s["key"]))
    return sorted(structure["series"], key=lambda s: (-len(s["ids"]), newest(s), s["key"]))


# ---------- quality ----------
#
# Points out of 100, relative to the other photos of the same series, so the best one
# stands out whatever camera it came from. Deterministic: the same photos always give
# the same order (ties go to the bigger file, then the path).

POINTS = {"sharpness": 40, "resolution": 25, "exposure": 15, "choice": 20}


def score_members(members: List[Dict[str, Any]]) -> None:
    """Adds score, breakdown and best to every member (dicts with the fields of _member_rows)."""
    max_sharp = max((m["sharpness"] or 0.0) for m in members) or 0.0
    max_pixels = max((m["width"] or 0) * (m["height"] or 0) for m in members) or 0
    for m in members:
        sharp = POINTS["sharpness"] * ((m["sharpness"] or 0.0) / max_sharp) ** 0.5 if max_sharp > 0 else POINTS["sharpness"]
        pixels = (m["width"] or 0) * (m["height"] or 0)
        resolution = POINTS["resolution"] * pixels / max_pixels if max_pixels else POINTS["resolution"]
        clipped = (m["clip_low"] or 0.0) + (m["clip_high"] or 0.0)
        exposure = POINTS["exposure"] * max(0.0, 1.0 - 4.0 * clipped)
        choice = 0.0
        if m["keurer_keep"]:
            choice += 12
        if m["votes"]:
            choice += 8 * min(1.0, max(0.0, ((m["rating"] or 1000) - 1000) / 200))
        breakdown = {"sharpness": round(sharp), "resolution": round(resolution),
                     "exposure": round(exposure), "choice": round(min(choice, POINTS["choice"]))}
        m["breakdown"] = breakdown
        m["score"] = sum(breakdown.values())
        m["_exact"] = sharp + resolution + exposure + min(choice, POINTS["choice"])
    best = max(members, key=lambda m: (m["_exact"], m["size"], _neg_path(m["path"])))
    for m in members:
        m["best"] = m is best
        del m["_exact"]


def _neg_path(path: str) -> Tuple:
    """Sort key that puts the alphabetically FIRST path on top under max()."""
    return tuple(-ord(ch) for ch in path)


def _member_rows(db, ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    with db._connect() as conn:
        for chunk in _chunks(list(ids)):
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT m.id, m.path, m.folder, m.name, m.size, m.width, m.height, m.taken, m.camera_make, "
                f"m.camera_model, h.sharpness, h.clip_low, h.clip_high, r.rating, r.votes, "
                f"EXISTS (SELECT 1 FROM keurer_decisions k WHERE k.path = m.path AND k.decision = 'keep') "
                f"FROM media m LEFT JOIN media_phash h ON h.media_id = m.id "
                f"LEFT JOIN file_ratings r ON r.path = m.path WHERE m.id IN ({marks})", chunk):
                out[r[0]] = {
                    "id": r[0], "path": r[1], "folder": r[2], "name": r[3], "size": r[4], "width": r[5],
                    "height": r[6], "taken": r[7],
                    "camera": " ".join(x for x in (r[8], r[9]) if x) or None,
                    "sharpness": r[10], "clip_low": r[11], "clip_high": r[12],
                    "rating": r[13], "votes": r[14] or 0, "keurer_keep": bool(r[15]),
                }
    return out


def present(db, series: Dict[str, Any]) -> Dict[str, Any]:
    """One series as the screen shows it: every photo with its score, the suggested best first marked."""
    copy_ids = [c for cs in series["copies"].values() for c in cs]
    rows = _member_rows(db, list(series["ids"]) + copy_ids)
    members = [rows[i] for i in series["ids"] if i in rows]
    score_members(members)
    for m in members:
        m["copies"] = [{"id": c, "path": rows[c]["path"]} for c in series["copies"].get(m["id"], []) if c in rows]
        for key in ("sharpness", "clip_low", "clip_high"):
            m[key] = None if m[key] is None else round(m[key], 4)
    folders = {m["folder"] for m in members}
    return {
        "key": series["key"],
        "kind": series["kind"],
        "count": len(members),
        "taken": series["taken"],
        "folder": folders.pop() if len(folders) == 1 else None,
        "part": series.get("part"),
        "best": next(m["id"] for m in members if m["best"]),
        "members": members,
    }


def page(db, strictness: str, sort: str, offset: int, limit: int) -> Dict[str, Any]:
    structure = series_structure(db, strictness)
    ordered = sorted_series(structure, sort)
    chosen = ordered[offset:offset + limit]
    total = len(ordered)
    return {
        "strictness": strictness,
        "sort": sort,
        "total": total,
        "photos": sum(len(s["ids"]) for s in ordered),
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < total else None,
        "version": structure["version"],
        "items": [present(db, s) for s in chosen],
    }


def record_review(db, ids: Iterable[int], decision: str) -> str:
    ids = sorted(set(ids))
    key = series_key(ids)
    with db._connect() as conn:
        conn.execute("INSERT OR REPLACE INTO near_dup_reviews (key, media_ids, decision, decided_at) "
                     "VALUES (?, ?, ?, ?)", (key, ",".join(map(str, ids)), decision, time.time()))
    _bump()
    return key


def reset_reviews(db) -> int:
    with db._connect() as conn:
        n = conn.execute("DELETE FROM near_dup_reviews").rowcount
    _bump()
    return n


def find_series(db, ids: Sequence[int]) -> Optional[Dict[str, Any]]:
    """
    The group of connected photos at the loosest setting (decisions ignored, never cut
    into parts) that holds every one of ids, as {key, ids, copies}; None when they are
    not one group.
    """
    structure = all_series(db, LOOSEST)
    wanted = set(ids)
    for members in structure["components"]:
        if wanted <= set(members):
            return {"key": series_key(members), "ids": members,
                    "copies": {i: structure["copies"][i] for i in members if structure["copies"].get(i)}}
    return None

# ---------- keeping the best ----------

RECOVERY_KIND = "near_dup"
HOLDING_FOLDER = "near-duplicates"   # below RAW_DUPLICATES
MAX_MEMBERS = 500                    # photos one request may name


class KeepError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def holding_folder(config) -> Path:
    return Path(config.DUPLICATES_FOLDER) / HOLDING_FOLDER


def _unchanged(path: str, size: int, mtime_ns: int) -> Optional[str]:
    """None when path is still the regular file the index knows, else the reason (Dutch)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "Staat niet meer op zijn plek"
    except OSError as e:
        return f"Niet te lezen: {e.strerror or e}"
    if not stat_module.S_ISREG(st.st_mode):
        return "Geen gewoon bestand meer"
    if (st.st_size, st.st_mtime_ns) != (size, mtime_ns):
        return "Veranderd sinds het vergelijken; vergelijk opnieuw"
    return None


def _destination(config, src: Path) -> Tuple[Path, Path]:
    """(source as its real path, root it keeps its place below): below RAW for files in RAW, else the vault."""
    real = src.parent.resolve() / src.name
    raw = Path(config.RAW_FOLDER).resolve()
    return real, raw if real.is_relative_to(raw) else Path(config.VAULT_ROOT).resolve()


def keep_best(db, config, member_ids: Sequence[int], keep_ids: Sequence[int]) -> Dict[str, Any]:
    """
    Keep keep_ids of one series and move the other photos of member_ids (and their
    byte-identical copies, which the screen shows with them) into
    RAW_DUPLICATES/near-duplicates/<their path below RAW>, as ONE recovery action.

    The server checks what the page asks: the photos must be one series (at the loosest
    setting), at least one photo that stays must still be there unchanged, and every
    photo is moved only when it is still the file that was analysed. A photo that cannot
    be moved stays where it is and is listed in failed with the reason. Never overwrites.
    """
    from backend import recovery
    from backend.file_ops import ARCHIVE_LOCK, FileOperationError, safe_move_with_structure
    from backend.paths import resolve_in_vault

    members, keep = list(dict.fromkeys(member_ids)), list(dict.fromkeys(keep_ids))
    if len(members) < 2 or len(members) > MAX_MEMBERS:
        raise KeepError(400, f"Een reeks heeft 2 tot {MAX_MEMBERS} foto's")
    if not keep or not set(keep) <= set(members):
        raise KeepError(400, "Kies minstens één foto uit de reeks om te houden")
    series = find_series(db, members)
    if series is None:
        raise KeepError(409, "Deze foto's vormen geen reeks (meer); vernieuw de lijst")
    losers = [i for i in members if i not in set(keep)]
    moving = [m for i in losers for m in [i] + series["copies"].get(i, [])]
    rows = _member_rows(db, keep + moving)
    with db._connect() as conn:
        fingerprints = {}
        for chunk in _chunks(keep + moving):
            for r in conn.execute(f"SELECT id, size, mtime_ns, missing FROM media WHERE id IN "
                                  f"({','.join('?' * len(chunk))})", chunk):
                fingerprints[r[0]] = r[1:]

    moved: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    action_id = None
    with ARCHIVE_LOCK:
        staying = [i for i in keep if i in rows and fingerprints.get(i, (0, 0, 1))[2] == 0
                   and _unchanged(rows[i]["path"], *fingerprints[i][:2]) is None]
        if not staying:
            raise KeepError(409, "De foto die blijft staat niet meer op zijn plek of is veranderd; "
                                 "er is niets verplaatst")
        kept_names = [rows[i]["name"] for i in keep if i in rows]
        folders = {rows[i]["folder"] for i in members if i in rows}
        recorder = recovery.ActionRecorder(
            db, RECOVERY_KIND,
            f"Reeks van {len(members)}: {', '.join(kept_names[:2])}{' …' if len(kept_names) > 2 else ''} bewaard",
            folder=folders.pop() if len(folders) == 1 else None,
            details={"series": series["key"], "keep": [rows[i]["path"] for i in keep if i in rows]},
        )
        # An exact copy goes along only while it is still byte for byte the photo that goes
        # (checked before anything moves)
        not_copies = {c for loser in losers for c in series["copies"].get(loser, [])
                      if c in rows and loser in rows and not _identical(rows[c]["path"], rows[loser]["path"])}
        config.ensure_duplicates_folder()
        target = holding_folder(config)
        for i in moving:
            row = rows.get(i)
            if row is None or fingerprints.get(i, (0, 0, 1))[2] != 0:
                failed.append({"id": i, "path": row["path"] if row else None, "reason": "Niet meer in de bibliotheek"})
                continue
            reason = _unchanged(row["path"], *fingerprints[i][:2])
            if reason is None and i in not_copies:
                reason = "Geen exacte kopie meer van de foto die weggaat"
            if reason is None:
                try:
                    src = resolve_in_vault(row["path"], config)   # never out of TRASH or RAW_DUPLICATES
                except Exception:
                    reason = "Dit pad mag niet verplaatst worden"
            if reason is not None:
                failed.append({"id": i, "path": row["path"], "reason": reason})
                continue
            try:
                real, base = _destination(config, src)
                target.mkdir(parents=True, exist_ok=True)
                dst, _ = safe_move_with_structure(real, base, target.resolve(), conflict_suffix="DUP")
            except (FileOperationError, OSError) as e:
                failed.append({"id": i, "path": row["path"], "reason": f"Verplaatsen mislukt: {e}"})
                continue
            action_id = recorder.add(src, dst)
            moved.append({"id": i, "path": row["path"], "dst": str(dst), "copy": i not in losers})
    if len(keep) > 1:
        record_review(db, keep, "kept")
    else:
        _bump()
    return {"action_id": action_id, "moved": moved, "failed": failed, "kept": keep,
            "destination": str(holding_folder(config))}
