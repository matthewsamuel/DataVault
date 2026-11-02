"""
Synthetic benchmark for near_dups.near_pairs: 100.000 photos with skewed hashes.

    cd code
    venv/bin/python scripts/bench_near_pairs.py . doc            # documents: 5,000 layouts, zipf 2.2
    venv/bin/python scripts/bench_near_pairs.py . photo          # balanced bits, ~3M near pairs (bursts)
    venv/bin/python scripts/bench_near_pairs.py . biased         # strongly biased bits (worst for the index)
    venv/bin/python scripts/bench_near_pairs.py . uniform
    venv/bin/python scripts/bench_near_pairs.py . doc 100000 old_near_dups.py   # another version, by path

Prints the time, the pairs kept and the peak RSS. The sets are seeded, so runs compare.
"""
import resource, sys, time
import numpy as np
if len(sys.argv) > 1: sys.path.insert(0, sys.argv[1])
from backend import near_dups

def flips(rng, base, mean_bits, n):
    out = base.copy()
    k = rng.poisson(mean_bits, n)
    for b in range(64):
        hit = rng.random(n) < (k / 64.0)
        out ^= np.where(hit, np.uint64(1) << np.uint64(b), np.uint64(0))
    return out

def make(kind, n, seed=3):
    rng = np.random.default_rng(seed)
    if kind == "doc":       # documents, screenshots: a few hundred layouts, many near-identical, many exact twins
        L = 5000            # zipf 2.2: the largest layout has ~9k photos; ~60M pairs within the phash radius
        centres_p = rng.integers(0, 2**63, L, dtype=np.uint64) * np.uint64(2)
        centres_d = rng.integers(0, 2**63, L, dtype=np.uint64)
        sizes = rng.zipf(2.2, L).astype(float); c = rng.choice(L, n, p=sizes / sizes.sum())
        p = flips(rng, centres_p[c], 3.0, n); d = flips(rng, centres_d[c], 10.0, n)
        colors = (rng.normal(230, 3, (L, 12))[c] + rng.normal(0, 2, (n, 12))).astype(np.float32)
        flat = rng.random(n) < 0.3
    elif kind == "photo":   # photos: balanced bits (as measured on real photos) but many bursts and look-alike series
        sizes = np.minimum(rng.geometric(1 / 40, 3000), 300)          # series of ~40 shots, the rest single
        sizes = sizes[np.cumsum(sizes) <= int(n * 0.85)]
        g = np.concatenate([np.repeat(np.arange(len(sizes)), sizes), len(sizes) + np.arange(n - sizes.sum())])
        groups = g.max() + 1
        base = rng.integers(0, 2**63, groups, dtype=np.uint64) * np.uint64(2)
        dbase = rng.integers(0, 2**63, groups, dtype=np.uint64)
        p = flips(rng, base[g], 3.0, n); d = flips(rng, dbase[g], 4.0, n)
        colors = (rng.uniform(40, 200, (groups, 12))[g] + rng.normal(0, 4, (n, 12))).astype(np.float32)
        flat = np.zeros(n, dtype=bool)
    elif kind == "biased":  # extreme: strongly biased bits (sky, dark corners) and many bursts of look-alikes (sky, dark corners) and many bursts of look-alikes
        bias = rng.beta(0.6, 0.6, 64)
        groups = n // 4
        bits = rng.random((groups, 64)) < bias
        base = (bits.astype(np.uint64) << np.arange(64, dtype=np.uint64)).sum(axis=1).astype(np.uint64)
        g = rng.integers(0, groups, n)
        p = flips(rng, base[g], 5.0, n)
        dbits = rng.random((groups, 64)) < bias[::-1]
        dbase = (dbits.astype(np.uint64) << np.arange(64, dtype=np.uint64)).sum(axis=1).astype(np.uint64)
        d = flips(rng, dbase[g], 6.0, n)
        colors = (rng.uniform(40, 200, (groups, 12))[g] + rng.normal(0, 4, (n, 12))).astype(np.float32)
        flat = np.zeros(n, dtype=bool)
    else:                   # uniform random
        p = rng.integers(0, 2**63, n, dtype=np.uint64) * np.uint64(2); d = rng.integers(0, 2**63, n, dtype=np.uint64)
        colors = rng.uniform(0, 255, (n, 12)).astype(np.float32); flat = np.zeros(n, dtype=bool)
    return p, d, colors, flat

if __name__ == "__main__":
    kind = sys.argv[2]; n = int(sys.argv[3]) if len(sys.argv) > 3 else 100_000
    if len(sys.argv) > 4:      # another near_dups.py (the version before), loaded by path
        import importlib.util
        spec = importlib.util.spec_from_file_location("near_dups_other", sys.argv[4])
        near_dups = importlib.util.module_from_spec(spec); spec.loader.exec_module(near_dups)
    p, d, colors, flat = make(kind, n)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t = time.perf_counter(); out = near_dups.near_pairs(p, d, np.clip(np.round(colors), 0, 255).astype(np.int16), flat); s = time.perf_counter() - t
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"{'old' if len(sys.argv) > 4 else 'new'} {kind} n={n}: {s:.2f} s, {len(out['a'])} pairs, peak RSS {peak/2**20:.0f} MB (+{(peak-before)/2**20:.0f} MB), distinct phash {len(np.unique(p))}")
