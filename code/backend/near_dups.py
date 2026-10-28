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
