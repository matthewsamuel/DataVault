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

Photos and, when ffmpeg is installed, videos: one representative frame per video (the
frame at VIDEO_FRAME_AT seconds, else the first; ffmpeg pipes it out at analysis size,
inside the job, VIDEO_TIMEOUT at most). The fingerprint is stored with the file's size
and mtime like a photo's, so a video is decoded once. Videos only pair with videos (a
frame next to a photo says little), and the video of a Live Photo (a video with a photo
of the same name in its folder) is left out: it belongs to that photo. Without ffmpeg
videos are simply not counted. RAW files Pillow cannot decode are 'undecodable'. JPEGs
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
from itertools import repeat
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
from PIL import Image, ImageOps

from backend import thumbnail  # noqa: F401  (registers the HEIC opener)

PHASH_JOB = "phash"
ALGO = "dhash+phash/dct32"
ALGO_VERSION = 2              # bump when analyse() changes: every photo is analysed again
                              # (2: sharpness of small photos measured at ANALYSE_SIZE too)
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


def _at_analyse_size(grey: Image.Image) -> Image.Image:
    """
    Sharpness is measured with the long side at ANALYSE_SIZE for every photo: a small photo
    is scaled up first. Measured at its own size, a 400 px copy has steeper edges per pixel
    than its 1600 px original and would look sharper; scaled up it looks as soft as it is.
    """
    w, h = grey.size
    if max(w, h) >= ANALYSE_SIZE:
        return grey
    scale = ANALYSE_SIZE / max(w, h)
    return grey.resize((max(3, round(w * scale)), max(3, round(h * scale))), Image.Resampling.BILINEAR)


def _laplacian(g: np.ndarray) -> np.ndarray:
    return 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]


VIDEO_FRAME_AT = 1.0          # seconds into a video for its representative frame (as its thumbnail)
VIDEO_TIMEOUT = 30            # seconds ffmpeg may take for one frame
VIDEO_LATER_SHARES = (0.1, 0.5)   # of the duration: where to look when the frame at VIDEO_FRAME_AT is flat
VIDEO_LATER_UNKNOWN = (5.0, 15.0)  # seconds, the same for a video of unknown duration
VIDEO_LENGTH_SLACK = (1.0, 0.02)   # two videos are copies only within 1 s or 2 % of each other's duration


class NoFfmpeg(Exception):
    """ffmpeg is not (or no longer) installed: the video is left for a later run."""


def video_frames_available() -> bool:
    return thumbnail.have_ffmpeg()


def _video_times(duration: Optional[float]) -> List[Optional[float]]:
    """
    The moments (seconds, None = the very first frame) tried for a video's frame, in order:
    VIDEO_FRAME_AT (as its thumbnail), then later moments for a video that opens on a flat
    frame (black, a fade-in, a plain title card), then the first frame for a very short one.
    """
    if duration and duration > 0:
        later = [t for t in (duration * share for share in VIDEO_LATER_SHARES)
                 if VIDEO_FRAME_AT + 0.5 <= t < duration - 0.1]
    else:
        later = list(VIDEO_LATER_UNKNOWN)
    return [VIDEO_FRAME_AT, *later, None]


def _check_readable(path: str) -> None:
    """Raises OSError (with an errno) when the file cannot be read now, to its last byte."""
    st = os.stat(path)
    with open(path, "rb") as f:
        if st.st_size:
            f.seek(st.st_size - 1)
        f.read(1)


def _video_frame(binary: str, path: str, at: Optional[float]) -> Optional[Image.Image]:
    """The frame at 'at' seconds as RGB, or None when ffmpeg gives none there."""
    import io
    import subprocess
    scale = f"scale={ANALYSE_SIZE}:{ANALYSE_SIZE}:force_original_aspect_ratio=decrease"
    seek = ["-ss", f"{at:.3f}"] if at is not None else []
    cmd = [binary, "-nostdin", "-v", "error", "-threads", "1", *seek, "-i", path, "-frames:v", "1",
           "-vf", scale, "-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=VIDEO_TIMEOUT)
    except subprocess.TimeoutExpired:
        _check_readable(path)       # a disk that went away is a read error, tried again next run
        raise Undecodable("ffmpeg deed er te lang over")
    if result.returncode != 0 or not result.stdout:
        return None
    try:
        img = Image.open(io.BytesIO(result.stdout))
        img = img.convert("RGB")
    except (OSError, ValueError, SyntaxError) as e:
        raise Undecodable(str(e)) from e
    if img.width < 8 or img.height < 8:
        raise Undecodable("te klein")
    return img


def _video_small(path: str, duration: Optional[float] = None) -> Image.Image:
    """
    One frame of a video as RGB, upright (ffmpeg applies the rotation), at most
    ANALYSE_SIZE on a side, piped as PNG (no temporary file). The frame at VIDEO_FRAME_AT,
    unless that one is flat (nearly one colour): a black opening or a fade-in carries
    nothing, and two unrelated videos that open alike must not look like copies, so a
    later moment (VIDEO_LATER_SHARES of the duration) is taken instead. When every frame
    tried is flat, the first one is returned (and update_pairs leaves the video unpaired).

    Raises NoFfmpeg, OSError (with an errno) when the file cannot be read (also when it
    went away while ffmpeg read it), Undecodable when ffmpeg gets no frame.
    """
    import shutil
    binary = shutil.which("ffmpeg")
    if not binary:
        raise NoFfmpeg()
    with open(path, "rb") as f:      # a clear read error first (permission, disk)
        f.read(1)
    first = None
    for at in _video_times(duration):
        if first is not None and at is None:
            break                     # the first frame is only for a video too short for a seek
        img = _video_frame(binary, path, at)
        if img is None:
            continue
        if _grey_spread(img) >= FLAT_CONTRAST:
            return img
        first = first or img
    if first is not None:
        return first
    _check_readable(path)            # no frame because the disk went away: a read error, not undecodable
    raise Undecodable("ffmpeg kon geen beeld uit de video halen")


def _grey_spread(img: Image.Image) -> float:
    """The contrast figure of _figures (spread of the grey values)."""
    return float(np.asarray(img.convert("L"), dtype=np.float32).std())


def analyse(path: str) -> Dict[str, Any]:
    """
    The fingerprint and quality figures of one photo. Raises Undecodable for a file that
    is no decodable picture, OSError (with an errno) when it cannot be read.
    """
    return _figures(_open_small(path))


def analyse_video(path: str, duration: Optional[float] = None) -> Dict[str, Any]:
    """The same for a video's representative frame (duration in seconds, when known); also raises NoFfmpeg."""
    return _figures(_video_small(path, duration))


def _figures(img: Image.Image) -> Dict[str, Any]:
    grey = img.convert("L")
    g = np.asarray(grey, dtype=np.float32)
    lap = _laplacian(np.asarray(_at_analyse_size(grey), dtype=np.float32))
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


def _popcount_fallback(values: np.ndarray) -> np.ndarray:
    """Set bits per uint64 (numpy before 2.0 has no bitwise_count)."""
    as_bytes = np.ascontiguousarray(values, dtype=np.uint64).view(np.uint8).reshape(-1, 8)
    return np.unpackbits(as_bytes, axis=1).sum(axis=1).astype(np.uint8).reshape(np.shape(values))


popcount = getattr(np, "bitwise_count", None) or _popcount_fallback

CANDIDATE_BUDGET = 1 << 17    # candidates checked per step: bounds memory whatever the hashes look like


class HashIndex:
    """
    Finds every pair of 64-bit hashes within `radius` bits, without comparing all pairs.
    Multi-index hashing: split each hash into MIH_PARTS parts; two hashes within radius
    bits agree up to radius // MIH_PARTS bits in at least one part (pigeonhole). Per part
    the hashes are bucketed by that part's value (a counting sort: one array of offsets
    per 65536 values); a query looks up every value within that many bits of its own part
    and checks the full distance only for what it finds there.

    A pair counts only in the first part where it is that close (later parts skip it), so
    every pair is reported exactly once and nothing has to be kept to drop duplicates:
    scan() hands each slice of pairs to a sink as it goes, so memory stays bounded by
    CANDIDATE_BUDGET whatever the hashes look like (documents and screenshots fill a few
    buckets with thousands of hashes).
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
        self._sorted = [self.hashes[order] for _, order, _, _ in self.parts]   # hashes in bucket order, per part

    def pairs(self, radius: int, queries: Optional[np.ndarray] = None,
              check: Optional[Callable[[], None]] = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        (i, j, distance) arrays of every pair within radius bits, i < j, each pair once.
        queries: only pairs with at least one of these positions (all when None).
        check() is called between steps (a job's cancel/pause check).
        """
        out: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        self.scan(radius, lambda i, j, d: out.append((i, j, d)), queries, check)
        if not out:
            empty = np.zeros(0, dtype=np.int64)
            return empty, empty, empty
        i, j, d = (np.concatenate(parts) for parts in zip(*out))
        order = np.lexsort((j, i))
        return i[order], j[order], d[order]

    def scan(self, radius: int, sink: Callable[[np.ndarray, np.ndarray, np.ndarray], None],
             queries: Optional[np.ndarray] = None, check: Optional[Callable[[], None]] = None) -> None:
        """Like pairs(), but hands the pairs to sink(i, j, distance) a slice at a time (not sorted)."""
        per_part = radius // MIH_PARTS
        masks = _flip_masks(_PART_BITS, per_part)
        q = np.arange(self.n, dtype=np.int64) if queries is None else np.unique(np.asarray(queries, dtype=np.int64))
        is_query = None
        if queries is not None:
            is_query = np.zeros(self.n, dtype=bool)
            is_query[q] = True
        for p, (keys, order, counts, starts) in enumerate(self.parts):
            qkeys = keys[q]
            for mask in masks:
                if check is not None:
                    check()
                look = qkeys ^ mask
                cnt = counts[look]
                hit = cnt > 0
                # A full scan meets two buckets that differ by `mask` from both sides: look
                # only from the bucket without the mask's top bit (each pair comes up once)
                lower = mask and is_query is None
                if lower:
                    hit &= (qkeys & (1 << (int(mask).bit_length() - 1))) == 0
                if not hit.any():
                    continue
                hq, hlook, hcnt = q[hit], look[hit], cnt[hit]
                # In slices of at most CANDIDATE_BUDGET candidates (one query's bucket at least)
                ends = np.cumsum(hcnt)
                begin = 0
                while begin < len(hq):
                    base = int(ends[begin - 1]) if begin else 0
                    end = max(begin + 1, int(np.searchsorted(ends, base + CANDIDATE_BUDGET, side="right")))
                    self._check_slice(p, per_part, hq[begin:end], starts[hlook[begin:end]], hcnt[begin:end],
                                      order, is_query, radius, sink, lower)
                    begin = end
                    if check is not None and begin < len(hq):
                        check()

    def _check_slice(self, part, per_part, qs, firsts, cnt, order, is_query, radius, sink, lower=False) -> None:
        # Candidate k is query qs[i] against bucket position pos[k] (i from the cumulative
        # counts); only the hashes are gathered for every candidate, positions only for the
        # few that are close
        ends = np.cumsum(cnt)
        pos = np.repeat(firsts - (ends - cnt), cnt) + np.arange(int(ends[-1]), dtype=np.int64)
        x = np.repeat(self.hashes[qs], cnt) ^ self._sorted[part][pos]
        dist = popcount(x)
        close = np.flatnonzero(dist <= radius)
        if not len(close):
            return
        if len(close) * 8 < len(pos):            # few close: find their query by bisection
            qi = qs[np.searchsorted(ends, close, side="right")]
        else:
            qi = np.repeat(qs, cnt)[close]
        cj = order[pos[close]]
        x, dist = x[close], dist[close]
        # Each unordered pair once: from its lower position, or from the query side (two
        # different buckets in a full scan already come up once: see `lower` in scan)
        if is_query is None:
            once = None if lower else qi < cj
        else:
            once = (qi != cj) & ((qi < cj) | ~is_query[cj])
        # ... and only in the first part where it is within per_part bits
        for earlier in range(part):
            shift = np.uint64(earlier * _PART_BITS)
            later = popcount((x >> shift) & np.uint64((1 << _PART_BITS) - 1)) > per_part
            once = later if once is None else once & later
        if once is not None:
            if not once.any():
                return
            qi, cj, dist = qi[once], cj[once], dist[once]
        sink(np.minimum(qi, cj), np.maximum(qi, cj), dist.astype(np.int64))


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
# A video whose every frame tried is flat (black, one colour) carries nothing to compare: it stays unpaired
_NOT_FLAT_VIDEO = f"NOT (m.kind = 'video' AND COALESCE(h.contrast, 0) < {FLAT_CONTRAST!r})"
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


# The video of a Live Photo: a photo with the same name (without extension) in its folder
_LIVE_VIDEO = ("EXISTS (SELECT 1 FROM media p WHERE p.folder = m.folder AND p.kind = 'photo' AND p.missing = 0 "
               "AND lower(substr(p.name, 1, length(p.name) - length(p.ext))) = "
               "lower(substr(m.name, 1, length(m.name) - length(m.ext))))")


def _compared() -> str:
    """SQL condition on media m: the files this module fingerprints (videos only with ffmpeg)."""
    if not video_frames_available():
        return "m.kind = 'photo'"
    return f"(m.kind = 'photo' OR (m.kind = 'video' AND NOT {_LIVE_VIDEO}))"


def counts(db) -> Dict[str, int]:
    """
    Photos (and videos, with ffmpeg) in the index, and how many are analysed, still to do,
    undecodable or failed. 'photos' counts both; 'videos' how many of them are videos.
    """
    compared = _compared()
    with db._connect() as conn:
        photos, videos = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(m.kind = 'video'), 0) FROM media m WHERE m.missing = 0 AND {compared}"
        ).fetchone()
        rows = conn.execute(
            f"SELECT h.status, COUNT(*) FROM media m JOIN media_phash h ON h.media_id = m.id "
            f"WHERE m.missing = 0 AND {compared} AND {_CURRENT} GROUP BY h.status",
            (ALGO_VERSION,)).fetchall()
        unpaired = conn.execute(
            f"SELECT COUNT(*) FROM media m JOIN media_phash h ON h.media_id = m.id "
            f"WHERE m.missing = 0 AND h.paired = 0 AND h.status = 'ok' AND {_CURRENT} AND {_NOT_FLAT_VIDEO}",
            (ALGO_VERSION,)).fetchone()[0]
        pairs = conn.execute("SELECT COUNT(*) FROM near_dup_pairs").fetchone()[0]
        signature = list(conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(m.id), 0) FROM media m JOIN media_phash h ON h.media_id = m.id "
            f"WHERE h.status = 'ok' AND {_CURRENT} AND {_NOT_FLAT_VIDEO}", (ALGO_VERSION,)).fetchone())
        reviewed = conn.execute("SELECT COUNT(*) FROM near_dup_reviews").fetchone()[0]
    by_status = dict(rows)
    done = by_status.get(OK, 0) + by_status.get(UNDECODABLE, 0)
    to_analyse = max(0, photos - done)
    pending = to_analyse + unpaired
    if not pending and signature[0] and state(db).get("pairs_signature") not in (None, signature):
        pending = 1   # a photo left the index since the last lookup: the next run looks again
    return {
        "photos": photos,
        "videos": videos,
        "video_frames": video_frames_available(),
        "analysed": by_status.get(OK, 0),
        "undecodable": by_status.get(UNDECODABLE, 0),
        "errors": by_status.get(ERROR, 0),
        "pending": pending,            # work for the next run: photos to analyse plus analysed-but-unpaired
        "to_analyse": to_analyse,      # only the photos still to analyse (analysed + undecodable + this = photos)
        "pairs": pairs,
        "reviewed": reviewed,
    }


def _todo(db) -> List[Tuple[int, str, int, int, str, Optional[float]]]:
    """Photos and videos without a current fingerprint (new, changed, older version, or failed last time)."""
    with db._connect() as conn:
        return conn.execute(
            f"SELECT m.id, m.path, m.size, m.mtime_ns, m.kind, m.duration FROM media m "
            f"LEFT JOIN media_phash h ON h.media_id = m.id "
            f"WHERE m.missing = 0 AND {_compared()} "
            f"AND (h.media_id IS NULL OR NOT ({_CURRENT}) OR h.status = 'error') ORDER BY m.id",
            (ALGO_VERSION,)).fetchall()


SKIPPED = "skipped"           # _analyse_row: a video while ffmpeg is gone; nothing is stored


def _analyse_row(row: Tuple) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """(status, figures, error text) for one photo or video; 'behind' when the file changed since it was indexed."""
    _id, path, size, mtime_ns = row[:4]
    video = len(row) > 4 and row[4] == "video"
    duration = row[5] if len(row) > 5 else None
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "behind", None, None
    if not stat_module.S_ISREG(st.st_mode) or (st.st_size, st.st_mtime_ns) != (size, mtime_ns):
        return "behind", None, None   # the index is behind the disk: its next pass reads it again
    try:
        return OK, (analyse_video(path, duration) if video else analyse(path)), None
    except Undecodable as e:
        return UNDECODABLE, None, str(e)[:200]
    except NoFfmpeg:
        return SKIPPED, None, None


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
    vault_present = lambda: config.VAULT_ROOT.is_dir()  # noqa: E731
    forget_orphans(db)
    todo = _todo(db)
    total = len(todo)
    done = {"analysed": 0, "undecodable": 0, "behind": 0, "skipped": 0, "videos": 0}
    noun = "Foto's en video's" if any(row[4] == "video" for row in todo) else "Foto's"
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

    ctx.progress(0, total, f"{noun} vergelijken... 0/{total}")
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
                if status == UNDECODABLE and row[4] == "video" and (errors.vault_lost or not vault_present()):
                    # ffmpeg may have failed because the disk went away: tried again next run
                    pending.append(_values(row, ERROR, None, text, now))
                elif status in ("behind", SKIPPED):
                    done[status] += 1
                else:
                    done["analysed" if status == OK else "undecodable"] += 1
                    done["videos"] += status == OK and row[4] == "video"
                    pending.append(_values(row, status, figures, text, now))
            tick = time.monotonic()
            if len(pending) >= BATCH or tick - last_flush >= FLUSH_SECONDS:
                flush()
            if tick - last_progress >= PROGRESS_SECONDS or processed == total:
                last_progress = tick
                ctx.progress(processed, total, f"{noun} vergelijken... {processed}/{total}")
    finally:
        flush()
    ctx.check()
    vault_check()
    if done["behind"]:
        from backend import media_index
        media_index.request_update()

    def check() -> None:
        ctx.check()
        if errors.vault_lost:
            vault_check()

    twins = hash_twins(db, check, lambda n, of: ctx.progress(n, of, f"Exacte kopieën herkennen... {n}/{of}"),
                       errors)
    vault_check()
    ctx.progress(total, total, "Reeksen zoeken...")
    paired = update_pairs(db, ctx.check)
    seconds = round(time.time() - started, 2)
    figures = counts(db)
    series = len(series_structure(db, DEFAULT_STRICTNESS)["series"])
    result = {
        "photos": figures["photos"],
        "videos": figures["videos"],
        "analysed": done["analysed"],
        "videos_analysed": done["videos"],
        "undecodable": done["undecodable"],
        "behind": done["behind"] + twins["behind"],
        "copies_hashed": twins["hashed"],
        "copies_cached": twins["cached"],
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
    videos = result.get("videos_analysed") or 0
    photos = result["analysed"] - videos
    photo_word = "foto" if photos == 1 else "foto's"
    video_word = "video" if videos == 1 else "video's"
    if not videos:
        what = f"{photos} foto's"
    elif not photos:
        what = f"{videos} {video_word}"
    else:
        what = f"{photos} {photo_word} en {videos} {video_word}"
    text = f"Klaar: {what} vergeleken, {n} {'reeks' if n == 1 else 'reeksen'}"
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


def same_length(a: Optional[float], b: Optional[float]) -> bool:
    """Whether two videos last about as long (VIDEO_LENGTH_SLACK); an unknown duration passes."""
    if not a or not b:
        return True
    seconds, share = VIDEO_LENGTH_SLACK
    return abs(a - b) <= max(seconds, share * max(a, b))


def _split_by_length(members: List[int], length: Callable[[int], Optional[float]]) -> List[List[int]]:
    """
    Videos that look alike but differ in length are no copies: the members in runs of
    about the same duration (in their own order within a run), the unknown ones together.
    """
    order = {i: n for n, i in enumerate(members)}
    known = sorted((i for i in members if length(i)), key=lambda i: (length(i), order[i]))
    runs: List[List[int]] = []
    for i in known:
        if runs and same_length(length(runs[-1][-1]), length(i)):
            runs[-1].append(i)
        else:
            runs.append([i])
    unknown = [i for i in members if not length(i)]
    if unknown:
        runs.append(unknown)
    return sorted((sorted(run, key=order.__getitem__) for run in runs), key=lambda run: order[run[0]])


def _twins(rows) -> Dict[Tuple, List[int]]:
    """
    Positions of rows that look exactly alike to the analysis (same hashes and colours,
    and the same kind when the rows have one: a photo and a video are never twins).
    Videos are twins only when they also last about as long (same_length; the duration
    is r[13] when the rows have it).
    """
    twins: Dict[Tuple, List[int]] = {}
    for i, r in enumerate(rows):
        twins.setdefault((r[1], r[2], r[3], r[12] if len(r) > 12 else "photo"), []).append(i)
    split: Dict[Tuple, List[int]] = {}
    for key, members in twins.items():
        if key[3] != "video" or len(members) < 2 or len(rows[members[0]]) <= 13:
            split[key] = members
            continue
        for n, run in enumerate(_split_by_length(members, lambda i: rows[i][13])):
            split[(*key, n)] = run
    return split


CONTENT_BATCH = 100           # content hashes written per transaction


def hash_twins(db, check: Callable[[], None] = lambda: None,
               progress: Optional[Callable[[int, int], None]] = None,
               on_error: Optional[Callable[[str, BaseException], None]] = None) -> Dict[str, int]:
    """
    Content hashes (xxh3, media_phash.content) of the twins of the same size that have
    none yet: photos with the same hashes, colours and size may be byte-for-byte copies,
    and those are the duplicates screen's work. Until a twin has its hash, all_series
    leaves it out, so a stopped run never shows copies as different photos.

    A hash the duplicates screen already made of the unchanged file (file_hashes, same
    inode, size, mtime and ctime) is reused; new ones go into that cache too. Written in
    batches, so a stop or an unplugged disk keeps what was hashed. check() between files.
    """
    from backend.scanner import HASH_ALGO, _fingerprint, hash_file_xxh3
    with db._connect() as conn:
        rows = conn.execute(
            f"SELECT m.id, h.phash, h.dhash, h.colors, m.size, m.path, m.mtime_ns, h.content FROM media m "
            f"JOIN media_phash h ON h.media_id = m.id "
            f"WHERE m.missing = 0 AND h.status = 'ok' AND {_CURRENT} ORDER BY m.id", (ALGO_VERSION,)).fetchall()
    buckets: Dict[Tuple, List[Tuple]] = {}
    for r in rows:
        buckets.setdefault((r[1], r[2], r[3], r[4]), []).append(r)
    todo = [r for bucket in buckets.values() if len(bucket) > 1 for r in bucket if r[7] is None]
    done = {"hashed": 0, "cached": 0, "failed": 0, "behind": 0}
    if not todo:
        return done
    cache: Dict[str, Tuple[Tuple, str]] = {}
    with db._connect() as conn:
        for chunk in _chunks([r[5] for r in todo]):
            for path, ino, size, mtime_ns, ctime_ns, digest in conn.execute(
                    f"SELECT path, ino, size, mtime_ns, ctime_ns, hash FROM file_hashes "
                    f"WHERE algo = ? AND path IN ({','.join('?' * len(chunk))})", (HASH_ALGO, *chunk)):
                cache[path] = ((ino, size, mtime_ns, ctime_ns), digest)

    contents: List[Tuple[str, int]] = []
    fresh: List[Tuple[str, Tuple, str]] = []
    last_flush = last_progress = time.monotonic()

    def flush() -> None:
        nonlocal last_flush
        last_flush = time.monotonic()
        if contents:
            with db._connect() as conn:
                conn.executemany("UPDATE media_phash SET content = ? WHERE media_id = ?", contents)
            contents.clear()
            _bump()
        if fresh:
            try:
                db.store_hashes(list(fresh), HASH_ALGO)
            except Exception:
                pass          # the cache is an optimisation
            fresh.clear()

    total = len(todo)
    try:
        for n, r in enumerate(todo):
            check()
            media_id, path, size, mtime_ns = r[0], r[5], r[4], r[6]
            try:
                st = os.lstat(path)
                if not stat_module.S_ISREG(st.st_mode) or (st.st_size, st.st_mtime_ns) != (size, mtime_ns):
                    done["behind"] += 1      # changed since it was analysed: analysed again next time
                    continue
                fingerprint = _fingerprint(st)
                cached = cache.get(path)
                if cached and cached[0] == fingerprint:
                    digest = cached[1]
                    done["cached"] += 1
                else:
                    digest = hash_file_xxh3(Path(path))
                    if _fingerprint(os.lstat(path)) != fingerprint:
                        done["behind"] += 1  # changed while it was read
                        continue
                    fresh.append((path, fingerprint, digest))
                    done["hashed"] += 1
            except OSError as e:
                done["failed"] += 1
                if on_error is not None:
                    on_error(path, e)
                continue
            contents.append((digest, media_id))
            tick = time.monotonic()
            if len(contents) >= CONTENT_BATCH or tick - last_flush >= FLUSH_SECONDS:
                flush()
            if progress is not None and (tick - last_progress >= PROGRESS_SECONDS or n + 1 == total):
                last_progress = tick
                progress(n + 1, total)
    finally:
        flush()
    if done["behind"]:
        from backend import media_index
        media_index.request_update()
    return done


NEAR_PAIR_LIMIT = 4_000_000   # memory guard: near pairs kept from one lookup (~35 bytes each)


def _passes(phash_d: np.ndarray, dhash_d: np.ndarray, color_d: np.ndarray, flat: np.ndarray, strictness: str) -> np.ndarray:
    p, d, c = STRICTNESS[strictness]["near"]
    color_d = np.round(color_d, 2)          # as stored (all_series compares the stored value)
    return (phash_d <= p) & (dhash_d <= d) & (color_d <= np.where(flat, min(c, FLAT_COLOR), c))


def _roots(label: np.ndarray) -> np.ndarray:
    """Pointer jumping until every entry points at its root."""
    while True:
        up = label[label]
        if np.array_equal(up, label):
            return label
        label = up


def spanning_forest(a: np.ndarray, b: np.ndarray, n: int) -> np.ndarray:
    """
    Mask of the edges (a[k], b[k]) that Kruskal would keep when it takes them in index
    order (the first edge that joins two trees wins), computed with Borůvka rounds in
    numpy: every tree picks its lowest-index edge to another tree. With the index as a
    unique weight that is the same minimum spanning forest, in O(log n) vector steps
    instead of a Python loop over every edge.
    """
    keep = np.zeros(len(a), dtype=bool)
    label = np.arange(n, dtype=np.int64)
    alive = np.arange(len(a), dtype=np.int64)
    none = np.int64(len(a))
    while len(alive):
        la, lb = label[a[alive]], label[b[alive]]
        apart = la != lb
        alive, la, lb = alive[apart], la[apart], lb[apart]
        if not len(alive):
            break
        best = np.full(n, none, dtype=np.int64)
        np.minimum.at(best, la, alive)
        np.minimum.at(best, lb, alive)
        chosen = np.unique(best[best < none])
        keep[chosen] = True
        # Hook the trees along the chosen edges (a forest, so no cycles), larger root under smaller
        x, y = label[a[chosen]], label[b[chosen]]
        while len(x):
            lo, hi = np.minimum(x, y), np.maximum(x, y)
            np.minimum.at(label, hi, lo)
            label = _roots(label)
            x, y = label[x], label[y]
            joined = x != y
            x, y = x[joined], y[joined]
        label = _roots(label)
    return keep


def _find(parent: np.ndarray, x: np.ndarray) -> np.ndarray:
    """The roots of x in a union-find parent array (parent[i] <= i), compressing their paths."""
    root = parent[x]
    while True:
        up = parent[root]
        if np.array_equal(up, root):
            break
        root = up
    parent[x] = root
    return root


def _join(parent: np.ndarray, x: np.ndarray, y: np.ndarray) -> None:
    """Union the trees of x[k] and y[k] for every k, in place (the smaller root wins)."""
    rx, ry = _find(parent, x), _find(parent, y)
    while True:
        apart = rx != ry
        if not apart.any():
            return
        rx, ry = rx[apart], ry[apart]
        np.minimum.at(parent, np.maximum(rx, ry), np.minimum(rx, ry))
        rx, ry = _find(parent, rx), _find(parent, ry)


def _forest(found: Dict[str, np.ndarray], n: int) -> Dict[str, np.ndarray]:
    """
    Only the pairs that join photos into series: per strictness, a pair whose two photos
    are already connected by others is left out (a spanning forest; the settings are
    nested, so every series of every setting stays the same). The memory guard of
    near_pairs: a few huge clusters of lookalikes (thousands of scans of one form) would
    otherwise give tens of millions of pairs, while at most 3 per photo are needed.
    """
    keep = np.zeros(len(found["a"]), dtype=bool)
    for strictness in STRICTNESS:
        passing = np.flatnonzero(_passes(found["phash_d"], found["dhash_d"], found["color_d"], found["flat"], strictness))
        keep[passing[spanning_forest(found["a"][passing], found["b"][passing], n)]] = True
    return {key: value[keep] for key, value in found.items()}


_ALL, _OTHERS, _SAME = 0, 1, 2   # which photos of the other value a query photo is paired with


def near_pairs(phashes: np.ndarray, dhashes: np.ndarray, colors: np.ndarray, flat: np.ndarray,
               queries: Optional[np.ndarray] = None, check: Optional[Callable[[], None]] = None,
               limit: Optional[int] = None) -> Dict[str, np.ndarray]:
    """
    Every pair of positions (a < b) that passes the loosest 'near' thresholds, as arrays
    {a, b, phash_d, dhash_d, color_d, flat}. queries: only pairs with at least one of
    these positions (all pairs when None).

    Photos with the same phash are handled as one value: HashIndex finds the close pairs of
    distinct values, and each is expanded to the photos behind them right away, keeping
    only those within the dhash and colour limits. Uniform pictures (screenshots,
    documents, two-tone graphics) share a few phash values between thousands of photos:
    of 100k such photos 89 million pairs are within the phash radius, but only about a
    million also look alike by dhash and colour, and only those are ever kept. Work goes
    in slices of CANDIDATE_BUDGET pairs, so memory stays bounded, and with many short
    numpy steps the server's other threads keep getting their turn. Past `limit` pairs
    (NEAR_PAIR_LIMIT) they are thinned to the ones that make the series (_forest).
    """
    _, loose_d, loose_c = STRICTNESS[LOOSEST]["near"]
    flat_c = min(loose_c, FLAT_COLOR)
    limit = NEAR_PAIR_LIMIT if limit is None else limit
    phashes = np.ascontiguousarray(phashes, dtype=np.uint64)
    dhashes = np.ascontiguousarray(dhashes, dtype=np.uint64)
    n = len(phashes)
    found: Dict[str, List[np.ndarray]] = {k: [] for k in ("a", "b", "phash_d", "dhash_d", "color_d", "flat")}
    kept = 0
    compact_at = limit
    # Once thinned: per strictness (strictest first) a union-find of the series so far. A
    # new pair that joins nothing new at the strictest setting it passes is dropped right
    # away, before its dhash and colours are even compared when its phash says enough
    levels = list(STRICTNESS)
    level_phash = np.array([STRICTNESS[s]["near"][0] for s in levels])
    parents: Optional[List[np.ndarray]] = None

    def compact() -> None:
        nonlocal kept, compact_at, parents
        forest = _forest({k: np.concatenate(v) for k, v in found.items()}, n)
        for k, v in forest.items():
            found[k] = [v]
        kept = len(forest["a"])
        compact_at = max(limit, 2 * kept)
        parents = []
        for strictness in levels:
            ok = _passes(forest["phash_d"], forest["dhash_d"], forest["color_d"], forest["flat"], strictness)
            parent = np.arange(n, dtype=np.int64)
            _join(parent, forest["a"][ok], forest["b"][ok])
            parents.append(parent)

    def joins_something(r: np.ndarray, c: np.ndarray, level: np.ndarray) -> np.ndarray:
        """Whether r[k] and c[k] are still apart at strictness levels[level[k]]."""
        apart = np.ones(len(r), dtype=bool)
        for k, parent in enumerate(parents):
            at = np.flatnonzero(level == k)
            if len(at):
                apart[at] = _find(parent, r[at]) != _find(parent, c[at])
        return apart
    if n >= 2:
        values, value_of = np.unique(phashes, return_inverse=True)
        value_of = value_of.reshape(-1)
        members = np.argsort(value_of, kind="stable")          # the photos of each value, by position
        counts = np.bincount(value_of, minlength=len(values))
        starts = np.concatenate(([0], np.cumsum(counts)[:-1]))
        is_query = np.ones(n, dtype=bool)
        if queries is not None:
            is_query[:] = False
            is_query[np.asarray(queries, dtype=np.int64)] = True
        q_members = members[is_query[members]]                 # the query photos of each value
        q_counts = np.bincount(value_of[q_members], minlength=len(values))
        q_starts = np.concatenate(([0], np.cumsum(q_counts)[:-1]))

        def slices(sizes: np.ndarray) -> Iterable[Tuple[int, int]]:
            """[begin, end) ranges whose sizes add up to at most CANDIDATE_BUDGET (one item at least)."""
            ends = np.cumsum(sizes)
            begin = 0
            while begin < len(sizes):
                base = int(ends[begin - 1]) if begin else 0
                end = max(begin + 1, int(np.searchsorted(ends, base + CANDIDATE_BUDGET, side="right")))
                yield begin, end
                begin = end

        def spread(firsts: np.ndarray, sizes: np.ndarray) -> np.ndarray:
            """firsts[k], firsts[k] + 1, ..., firsts[k] + sizes[k] - 1 for every k."""
            total = int(sizes.sum())
            if total == len(sizes):                     # one each (most phash values have one photo)
                return firsts
            return np.repeat(firsts, sizes) + (np.arange(total, dtype=np.int64)
                                               - np.repeat(np.cumsum(sizes) - sizes, sizes))

        def pair_photos(rows: np.ndarray, with_value: np.ndarray, mode: int, value_d: np.ndarray) -> None:
            nonlocal kept
            sizes = counts[with_value]
            single = int(sizes.sum()) == len(sizes)
            for begin, end in slices(sizes):
                if check is not None:
                    check()
                if single:
                    r, c, pd = rows[begin:end], members[starts[with_value[begin:end]]], value_d[begin:end]
                else:
                    r = np.repeat(rows[begin:end], sizes[begin:end])
                    c = members[spread(starts[with_value[begin:end]], sizes[begin:end])]
                    pd = np.repeat(value_d[begin:end], sizes[begin:end])
                if mode == _OTHERS:
                    keep = ~is_query[c]                     # query pairs come from the other side
                elif mode == _SAME:
                    keep = (r != c) & (~is_query[c] | (r < c))
                else:
                    keep = None
                if keep is not None:
                    r, c, pd = r[keep], c[keep], pd[keep]
                if parents is not None:      # already together at the strictest setting this phash allows
                    level = np.zeros(len(pd), dtype=np.int64)
                    for limit_p in level_phash[:-1]:
                        level += pd > limit_p
                    new = joins_something(r, c, level)
                    r, c, pd = r[new], c[new], pd[new]
                dd = popcount(dhashes[r] ^ dhashes[c])
                ok = dd <= loose_d
                r, c, dd, pd = r[ok], c[ok], dd[ok], pd[ok]
                if not len(r):
                    continue
                # The mean colour distance, compared as a sum of 12 integers (exact, no floats)
                cs = np.abs(colors[r] - colors[c]).sum(axis=1, dtype=np.int32)
                fl = flat[r] | flat[c]
                ok = cs <= np.where(fl, 12 * flat_c, 12 * loose_c)
                if not ok.any():
                    continue
                r, c, pd, dd, cd, fl = r[ok], c[ok], pd[ok], dd[ok], cs[ok] / 12.0, fl[ok]
                if parents is not None:
                    strictest = np.full(len(r), len(levels) - 1, dtype=np.int64)
                    for k in range(len(levels) - 2, -1, -1):
                        strictest[_passes(pd, dd, cd, fl, levels[k])] = k
                    new = joins_something(r, c, strictest)
                    if not new.any():
                        continue
                    r, c, pd, dd, cd, fl, strictest = (v[new] for v in (r, c, pd, dd, cd, fl, strictest))
                    for k, parent in enumerate(parents):
                        at = strictest <= k
                        _join(parent, r[at], c[at])
                kept += len(r)
                found["a"].append(np.minimum(r, c))
                found["b"].append(np.maximum(r, c))
                found["phash_d"].append(pd.astype(np.int8))
                found["dhash_d"].append(dd.astype(np.int8))
                found["color_d"].append(cd)
                found["flat"].append(fl)
                if kept > compact_at:
                    compact()

        def pair_values(x: np.ndarray, y: np.ndarray, mode: int, d: np.ndarray) -> None:
            """The query photos of value x[k] with the photos of value y[k] (phash distance d[k])."""
            sizes = q_counts[x]
            use = (sizes > 0) & (counts[y] > 0)
            x, y, sizes, d = x[use], y[use], sizes[use], d[use]
            single = int(sizes.sum()) == len(sizes)
            for begin, end in slices(sizes):
                rows = q_members[spread(q_starts[x[begin:end]], sizes[begin:end])]
                if single:
                    pair_photos(rows, y[begin:end], mode, d[begin:end])
                else:
                    pair_photos(rows, np.repeat(y[begin:end], sizes[begin:end]), mode,
                                np.repeat(d[begin:end], sizes[begin:end]))

        def close_values(u: np.ndarray, v: np.ndarray, d: np.ndarray) -> None:
            pair_values(u, v, _ALL, d)
            if queries is not None:
                pair_values(v, u, _OTHERS, d)

        query_values = None if queries is None else np.flatnonzero(q_counts)
        HashIndex(values).scan(PAIR_RADIUS, close_values, query_values, check)
        same = np.flatnonzero((q_counts > 0) & (counts > 1))   # photos with the same phash as each other
        pair_values(same, same, _SAME, np.zeros(len(same), dtype=np.int64))
        if parents is not None:
            compact()          # pairs of one slice can still join the same two series
    if not kept:
        return {"a": np.zeros(0, dtype=np.int64), "b": np.zeros(0, dtype=np.int64),
                "phash_d": np.zeros(0, dtype=np.int8), "dhash_d": np.zeros(0, dtype=np.int8),
                "color_d": np.zeros(0, dtype=np.float64), "flat": np.zeros(0, dtype=bool)}
    out = {k: np.concatenate(v) for k, v in found.items()}
    order = np.lexsort((out["b"], out["a"]))                  # written in key order: faster inserts
    return {k: v[order] for k, v in out.items()}


ANCHOR = 2                    # media_phash.paired of the photo a group's near pairs were looked up from
FULL_LOOKUP_SHARE = 0.5       # more groups than this share to look up: one full lookup is cheaper


def update_pairs(db, check: Callable[[], None] = lambda: None) -> Dict[str, int]:
    """
    When any analysed photo is new or changed (paired = 0), or a photo left the index,
    look up the pairs from the stored fingerprints (no photo is decoded):

    - Photos that look exactly alike (same hashes and colours: 'twins') are one group.
      Pairs are looked up once per group, from one photo of it (its anchor, paired =
      ANCHOR), so a folder of 1,500 copies of one shot is one group, not a million pairs.
      The series are made from the groups when they are shown (all_series), so the anchor
      may be gone (moved, or back after an undo) without breaking anything.
    - Only the groups without an anchor are looked up: new photos, a photo that changed,
      and a group whose anchor left the index or changed. The pairs of the other groups
      stay as they are, so a few new photos cost a lookup of a few photos, not of all.
      With more than FULL_LOOKUP_SHARE of the groups to look up (the first run), every
      pair is looked up again at once.
    - Bursts: shots by the same camera within BURST_GAP seconds, compared one by one
      (cheap: they are looked up again every time).

    Which twins are byte-for-byte copies is hash_twins' work (run before this).
    Photos hidden as missing (moved to RAW_DUPLICATES by the app) stay in: when an undo
    brings them back, their pairs are still there. Returns the counts written.
    """
    with db._connect() as conn:
        rows = conn.execute(
            f"SELECT m.id, h.phash, h.dhash, h.colors, h.paired, m.size, m.path, m.taken, m.taken_source, "
            f"m.camera_make, m.camera_model, h.contrast, m.kind, m.duration FROM media m "
            f"JOIN media_phash h ON h.media_id = m.id "
            f"WHERE h.status = 'ok' AND {_CURRENT} AND {_NOT_FLAT_VIDEO} ORDER BY m.id",
            (ALGO_VERSION,)).fetchall()
    dirty = [r[0] for r in rows if not r[4]]
    # (also when a photo left the index: it may have been the anchor of its group)
    signature = [len(rows), sum(r[0] for r in rows)]
    if not dirty and state(db).get("pairs_signature") == signature:
        return {"near": 0, "burst": 0}
    twins = list(_twins(rows).values())
    firsts = [members[0] for members in twins]
    lookup = [k for k, members in enumerate(twins) if not any(rows[i][4] == ANCHOR for i in members)]
    full = len(lookup) > FULL_LOOKUP_SHARE * len(twins)
    phashes = np.array([to_unsigned(rows[i][1]) for i in firsts], dtype=np.uint64)
    dhashes = np.array([to_unsigned(rows[i][2]) for i in firsts], dtype=np.uint64)
    colors = _color_matrix([rows[i][3] for i in firsts])
    flat = np.array([(rows[i][11] or 0.0) < FLAT_CONTRAST for i in firsts], dtype=bool)
    # Photos pair with photos and videos with videos: one lookup per kind
    kinds = np.array([rows[i][12] == "video" for i in firsts], dtype=bool)
    looked = np.zeros(len(firsts), dtype=bool)
    looked[lookup] = True
    parts = []
    for is_video in (False, True):
        pos = np.flatnonzero(kinds == is_video)
        queries = None if full else np.flatnonzero(looked[pos])
        if len(pos) < 2 or (queries is not None and not len(queries)):
            continue
        part = near_pairs(phashes[pos], dhashes[pos], colors[pos], flat[pos], queries, check)
        part["a"], part["b"] = pos[part["a"]], pos[part["b"]]
        if is_video and len(part["a"]):
            # Two videos that look alike but differ in length are no copies (an edit, another clip)
            lengths = [rows[i][13] for i in firsts]
            keep = np.array([same_length(lengths[a], lengths[b])
                             for a, b in zip(part["a"].tolist(), part["b"].tolist())], dtype=bool)
            part = {k: v[keep] for k, v in part.items()}
        parts.append(part)
    if len(parts) == 1:
        found = parts[0]
    else:
        found = {k: np.concatenate([p[k] for p in parts]) if parts else np.zeros(0, dtype=dtype)
                 for k, dtype in (("a", np.int64), ("b", np.int64), ("phash_d", np.int8), ("dhash_d", np.int8),
                                  ("color_d", np.float64), ("flat", bool))}
        order = np.lexsort((found["b"], found["a"]))
        found = {k: v[order] for k, v in found.items()}
    ids = np.array([rows[i][0] for i in firsts], dtype=np.int64)
    near = len(found["a"])

    def near_rows() -> Iterable[Tuple]:
        for start in range(0, near, 50_000):
            part = slice(start, start + 50_000)
            yield from zip(ids[found["a"][part]].tolist(), ids[found["b"][part]].tolist(), repeat("near"),
                           found["phash_d"][part].tolist(), found["dhash_d"][part].tolist(),
                           np.round(found["color_d"][part], 2).tolist(), repeat(None),
                           found["flat"][part].astype(np.int64).tolist(), repeat(0))

    bursts = _burst_pairs(rows, np.array([to_unsigned(r[1]) for r in rows], dtype=np.uint64),
                          np.array([to_unsigned(r[2]) for r in rows], dtype=np.uint64),
                          _color_matrix([r[3] for r in rows]),
                          np.array([(r[11] or 0.0) < FLAT_CONTRAST for r in rows], dtype=bool))
    anchors = [rows[firsts[k]][0] for k in (range(len(twins)) if full else lookup)]
    check()
    with db._connect() as conn:
        if full:
            conn.execute("DELETE FROM near_dup_pairs WHERE kind = 'near'")
            conn.execute(f"UPDATE media_phash SET paired = 1 WHERE paired = {ANCHOR}")
        else:
            # The pairs of a changed photo were looked up from its old fingerprint
            for chunk in _chunks(dirty):
                marks = ','.join('?' * len(chunk))
                conn.execute(f"DELETE FROM near_dup_pairs WHERE kind = 'near' AND (a IN ({marks}) OR b IN ({marks}))",
                             (*chunk, *chunk))
        # Bursts: only what changed (most of them stay the same from run to run)
        before = set(conn.execute("SELECT a, b, kind, phash_d, dhash_d, color_d, gap, flat, same_bytes "
                                  "FROM near_dup_pairs WHERE kind = 'burst'"))
        now = set(bursts)
        conn.executemany("DELETE FROM near_dup_pairs WHERE a = ? AND b = ? AND kind = 'burst'",
                         [(r[0], r[1]) for r in before - now])
        conn.executemany(_INSERT_PAIR, sorted(now - before))
        conn.executemany(_INSERT_PAIR, near_rows())
        for chunk in _chunks(dirty):
            conn.execute(f"UPDATE media_phash SET paired = 1 WHERE media_id IN ({','.join('?' * len(chunk))})", chunk)
        for chunk in _chunks(anchors):
            conn.execute(f"UPDATE media_phash SET paired = {ANCHOR} WHERE media_id IN ({','.join('?' * len(chunk))})",
                         chunk)
    db.set_setting(STATE_KEY, {**state(db), "pairs_signature": signature})
    _bump()
    return {"near": near, "burst": len(bursts), "looked_up": len(twins) if full else len(lookup)}


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
            p = int(popcount(phashes[a] ^ phashes[b]))
            d = int(popcount(dhashes[a] ^ dhashes[b]))
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
            f"m.size, m.mtime_ns, m.kind, m.duration FROM media m JOIN media_phash h ON h.media_id = m.id "
            f"WHERE h.status = 'ok' AND {_CURRENT} AND {_NOT_FLAT_VIDEO}", (ALGO_VERSION,)).fetchall()
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
    videos: Set[int] = {r[0] for r in analysed if r[11] == "video"}
    looks: Dict[Tuple, List[int]] = {}
    for n, r in enumerate(analysed):
        looks.setdefault((r[1], r[2], r[3], r[11]), []).append(n)
    look_of: Dict[int, Tuple] = {}
    for key, positions in looks.items():
        runs = (_split_by_length(positions, lambda n: analysed[n][12])
                if key[3] == "video" and len(positions) > 1 else [positions])
        for k, run in enumerate(runs):
            for n in run:
                look_of[n] = (*key, k)
    for n, r in enumerate(analysed):
        g = group_ids.setdefault(look_of[n], len(group_ids))
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

    # 1. Byte-identical copies count as one photo: the oldest (then shortest path) stands for them.
    #    A twin of the same size without a content hash yet (a run stopped before hash_twins
    #    got to it) may be such a copy: it is left out until the next run knows.
    copies_of: Dict[int, List[int]] = {}
    reps_of_group: Dict[int, List[int]] = {}
    for g, members in members_of.items():
        sizes: Dict[int, int] = {}
        for i in members:
            sizes[rows[i][3]] = sizes.get(rows[i][3], 0) + 1
        clusters: Dict[Tuple, List[int]] = {}
        for i in members:
            if content[i] is None and sizes[rows[i][3]] > 1:
                continue
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
                "videos": sum(1 for i in part if i in videos),
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


def _fair_sharpness(members: List[Dict[str, Any]]) -> List[float]:
    """
    The sharpness of each member, never above that of a member with more pixels: the
    photos of a series show the same thing, so a smaller one is a scaled-down copy and
    cannot hold more detail than its source. (Measured at one size, recompression
    noise can make such a copy look a little sharper; it then ties with its source.)
    """
    pixels = [(m["width"] or 0) * (m["height"] or 0) for m in members]
    out = []
    for m, own in zip(members, pixels):
        sharp = m["sharpness"] or 0.0
        bigger = [b["sharpness"] or 0.0 for b, p in zip(members, pixels) if p > own]
        out.append(min(sharp, max(bigger)) if bigger else sharp)
    return out


def score_members(members: List[Dict[str, Any]]) -> None:
    """Adds score, breakdown and best to every member (dicts with the fields of _member_rows)."""
    sharpness = _fair_sharpness(members)
    max_sharp = max(sharpness) or 0.0
    max_pixels = max((m["width"] or 0) * (m["height"] or 0) for m in members) or 0
    for m, fair in zip(members, sharpness):
        sharp = POINTS["sharpness"] * (fair / max_sharp) ** 0.5 if max_sharp > 0 else POINTS["sharpness"]
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
                f"EXISTS (SELECT 1 FROM keurer_decisions k WHERE k.path = m.path AND k.decision = 'keep'), "
                f"m.kind, m.duration "
                f"FROM media m LEFT JOIN media_phash h ON h.media_id = m.id "
                f"LEFT JOIN file_ratings r ON r.path = m.path WHERE m.id IN ({marks})", chunk):
                out[r[0]] = {
                    "id": r[0], "path": r[1], "folder": r[2], "name": r[3], "size": r[4], "width": r[5],
                    "height": r[6], "taken": r[7],
                    "camera": " ".join(x for x in (r[8], r[9]) if x) or None,
                    "sharpness": r[10], "clip_low": r[11], "clip_high": r[12],
                    "rating": r[13], "votes": r[14] or 0, "keurer_keep": bool(r[15]),
                    "kind": r[16], "duration": r[17],
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
        "media": "video" if members and all(m["kind"] == "video" for m in members) else "photo",
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
        "photos": sum(len(s["ids"]) for s in ordered),       # photos and videos
        "videos": sum(s.get("videos", 0) for s in ordered),
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


def undo_review(db, key: str) -> int:
    """Forget one decision ('Alles behouden' right after it was made): the series is shown again."""
    with db._connect() as conn:
        n = conn.execute("DELETE FROM near_dup_reviews WHERE key = ?", (key,)).rowcount
    _bump()
    return n


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
