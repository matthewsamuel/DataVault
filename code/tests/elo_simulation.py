"""
Simulation harness for 'Welke is mooier?' (backend/elo.py): how fast does a rating system
find the true order of a set of photos from noisy pairwise choices?

Every simulated photo has a hidden true quality (on the Elo scale, normal with SPREAD
points of spread). A simulated user is shown the pairs the system under test chooses and
picks the better photo with the Bradley-Terry probability of the TRUE qualities, so close
photos are near coin flips. On top of that a share of LAPSE picks is plain random (a
mis-click, a mood): the inconsistent picks real people make. Optionally the user calls
near-equal pairs a draw.

After every CHECKPOINTS comparisons the system's ranking is scored against the truth:
Spearman's rho, Kendall's tau and top-10 precision (how many of the true top 10 are in the
system's top 10). Everything is seeded, so a run is exactly repeatable.

Two systems:
- LegacySystem: the system as it was before this step (classic Elo, K 32 -> 16 after 30
  games, first photo weighted 1/(1+games)^2, opponent among the 8 nearest in rating). It is
  copied here verbatim so the baseline stays fixed while backend/elo.py moves on.
- CurrentSystem: backend/elo.py as it is now (Glicko-2 and information-driven pairs), using
  the same functions the server uses.

Run as a script for the table in docs/ELO.md:
    venv/bin/python tests/elo_simulation.py [--items 100] [--seeds 20]
"""
import argparse
import bisect
import math
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SPREAD = 200.0          # standard deviation of the true quality, Elo points
LAPSE = 0.05            # share of picks that are random
TOP = 10


# ==================== THE SIMULATED USER ====================

class User:
    def __init__(self, n: int, rng: random.Random, spread: float = SPREAD, lapse: float = LAPSE,
                 draw_margin: float = 0.0):
        self.rng = rng
        self.truth = [rng.gauss(0.0, spread) for _ in range(n)]
        self.lapse = lapse
        self.draw_margin = draw_margin

    def add(self, m: int, spread: float = SPREAD) -> None:
        """m new photos (with their own hidden quality) join the set."""
        self.truth.extend(self.rng.gauss(0.0, spread) for _ in range(m))

    def choose(self, a: int, b: int) -> str:
        """'a', 'b' or 'draw' for the pair (a, b) of item numbers."""
        if self.rng.random() < self.lapse:
            return "a" if self.rng.random() < 0.5 else "b"
        diff = self.truth[a] - self.truth[b]
        if self.draw_margin and abs(diff) < self.draw_margin and self.rng.random() < 0.5:
            return "draw"
        p = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
        return "a" if self.rng.random() < p else "b"


# ==================== METRICS ====================

def _ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else 0.0


def kendall(x: Sequence[float], y: Sequence[float]) -> float:
    n, s = len(x), 0
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = x[i] - x[j], y[i] - y[j]
            s += (dx * dy > 0) - (dx * dy < 0)
    return 2.0 * s / (n * (n - 1))


def top_precision(truth: Sequence[float], estimate: Sequence[float], top: int = TOP) -> float:
    true_top = set(sorted(range(len(truth)), key=lambda i: -truth[i])[:top])
    est_top = sorted(range(len(estimate)), key=lambda i: (-estimate[i], i))[:top]
    return len(true_top.intersection(est_top)) / float(top)


# ==================== THE BASELINE (verbatim copy of the old backend/elo.py) ====================

class LegacySystem:
    name = "Elo (oud)"
    RATING_START, RATING_FLOOR, K_NEW, K_SETTLED, K_SETTLE_GAMES, NEIGHBOURS, PAIR_ATTEMPTS = 1000, 100, 32, 16, 30, 8, 24

    def __init__(self, n: int, rng: random.Random):
        self.rng = rng
        self.items = [{"path": f"/sim/{i:05d}.jpg", "i": i, "rating": self.RATING_START, "games": 0} for i in range(n)]
        self.last: Optional[Tuple[str, str]] = None

    def _k(self, games):
        return self.K_NEW if games < self.K_SETTLE_GAMES else self.K_SETTLED

    def _change(self, rating, other, score, k):
        e = 1.0 / (1.0 + 10.0 ** ((other - rating) / 400.0))
        return max(-k, min(k, round(k * (score - e))))

    def pair(self) -> Optional[Tuple[int, int]]:
        avoid = [self.last] if self.last else []
        found = self._choose_pair(self.items, avoid)
        return (found[0]["i"], found[1]["i"]) if found else None

    def record(self, a: int, b: int, outcome: str) -> None:
        if outcome == "draw":          # the old system had no draw: the user had to pick one
            outcome = "a" if self.rng.random() < 0.5 else "b"
        ia, ib = self.items[a], self.items[b]
        score_a = 1.0 if outcome == "a" else 0.0
        ka, kb = self._k(ia["games"]), self._k(ib["games"])
        ra, rb = ia["rating"], ib["rating"]
        ia["rating"] = max(self.RATING_FLOOR, ra + self._change(ra, rb, score_a, ka))
        ib["rating"] = max(self.RATING_FLOOR, rb + self._change(rb, ra, 1.0 - score_a, kb))
        ia["games"] += 1
        ib["games"] += 1
        self.last = (ia["path"], ib["path"])

    def scores(self) -> List[float]:
        return [float(it["rating"]) for it in self.items]

    def add_items(self, m: int) -> None:
        start = len(self.items)
        self.items.extend({"path": f"/sim/{i:05d}.jpg", "i": i, "rating": self.RATING_START, "games": 0}
                          for i in range(start, start + m))

    def _choose_pair(self, candidates, avoid):
        rng = self.rng
        avoid_set = {frozenset(pair) for pair in avoid}
        by_rating = sorted(candidates, key=lambda c: (c["rating"], c["path"]))
        ratings = [c["rating"] for c in by_rating]
        weights = [1.0 / (1 + c["games"]) ** 2 for c in candidates]
        tried = set()
        for _ in range(min(self.PAIR_ATTEMPTS, len(candidates)) * 2):
            if len(tried) >= len(candidates):
                break
            first = rng.choices(candidates, weights)[0]
            if first["path"] in tried:
                continue
            tried.add(first["path"])
            options = self._neighbours(first, by_rating, ratings, avoid_set)
            if options:
                second = rng.choices(options, [1.0 / (1 + c["games"]) for c in options])[0]
                return (first, second) if rng.random() < 0.5 else (second, first)
        return None

    def _neighbours(self, first, by_rating, ratings, avoid_set):
        rng = self.rng
        low = bisect.bisect_left(ratings, first["rating"])
        high = bisect.bisect_right(ratings, first["rating"])
        at = rng.randint(low, high)
        left, right = at - 1, at
        options = []
        while len(options) < self.NEIGHBOURS and (left >= 0 or right < len(by_rating)):
            if right >= len(by_rating):
                take_left = True
            elif left < 0:
                take_left = False
            else:
                below, above = first["rating"] - ratings[left], ratings[right] - first["rating"]
                take_left = below < above or (below == above and rng.random() < 0.5)
            if take_left:
                cand, left = by_rating[left], left - 1
            else:
                cand, right = by_rating[right], right + 1
            if cand["path"] == first["path"] or frozenset((first["path"], cand["path"])) in avoid_set:
                continue
            options.append(cand)
        return options


# ==================== THE CURRENT SYSTEM (backend/elo.py) ====================

class CurrentSystem:
    name = "Glicko-2 + informatie (nieuw)"

    def __init__(self, n: int, rng: random.Random):
        from backend import elo
        self.elo = elo
        self.rng = rng
        self.items = [elo.new_candidate(f"/sim/{i:05d}.jpg") for i in range(n)]
        for i, item in enumerate(self.items):
            item["i"] = i
        self.history = elo.PairMemory()

    def pair(self) -> Optional[Tuple[int, int]]:
        found = self.elo.select_pair(self.items, memory=self.history, near_dup=lambda a, b: False, rng=self.rng)
        return (found[0]["i"], found[1]["i"]) if found else None

    def record(self, a: int, b: int, outcome: str) -> None:
        ia, ib = self.items[a], self.items[b]
        new_a, new_b = self.elo.glicko_pair(self.elo.state_of(ia), self.elo.state_of(ib), outcome)
        for item, new in ((ia, new_a), (ib, new_b)):
            item["mu"], item["rd"], item["vol"] = new
            item["rating"] = round(new[0])
            item["games"] += 1
        self.history.add(ia["path"], ib["path"])

    def scores(self) -> List[float]:
        return [it["mu"] for it in self.items]

    def settled(self) -> float:
        return self.elo.settled_share(self.items)

    def add_items(self, m: int) -> None:
        start = len(self.items)
        for i in range(start, start + m):
            item = self.elo.new_candidate(f"/sim/{i:05d}.jpg")
            item["i"] = i
            self.items.append(item)


SYSTEMS = {"legacy": LegacySystem, "current": CurrentSystem}


# ==================== RUNNING ====================

SCENARIOS = {
    # name: (description, spread, lapse)
    "standaard": ("Twijfelachtige kiezer: foto's liggen dicht bij elkaar, 5% willekeurige keuzes", 200.0, 0.05),
    "beslist": ("Besliste kiezer: duidelijkere verschillen, 2% willekeurige keuzes", 300.0, 0.02),
}


def run(system_cls, n: int, seed: int, checkpoints: Sequence[int], spread: float = SPREAD, lapse: float = LAPSE,
        draw_margin: float = 0.0) -> List[Dict[str, float]]:
    """One seeded run: metrics at each checkpoint (number of comparisons)."""
    user = User(n, random.Random(seed), spread=spread, lapse=lapse, draw_margin=draw_margin)
    system = system_cls(n, random.Random(seed * 7919 + 1))
    out, done = [], 0
    for target in sorted(set(checkpoints)):
        while done < target:
            pair = system.pair()
            if pair is None:
                break
            system.record(pair[0], pair[1], user.choose(*pair))
            done += 1
        est = system.scores()
        row = {"comparisons": done, "spearman": spearman(user.truth, est), "kendall": kendall(user.truth, est),
               "top10": top_precision(user.truth, est)}
        if hasattr(system, "settled"):
            row["settled"] = system.settled()
        out.append(row)
    return out


def newcomer_run(system_cls, n: int, newcomers: int, seed: int, warmup: int, checkpoints: Sequence[int],
                 spread: float = SPREAD, lapse: float = LAPSE) -> List[Dict[str, float]]:
    """
    New photos join a ranked set: n photos get `warmup` comparisons, then `newcomers`
    new ones are added. At each checkpoint (comparisons after the addition): how far, on
    average, a newcomer's rank is from its true rank, as a share of the list (0 = exact,
    about 0.33 = random), and Spearman's rho of the whole set.
    """
    user = User(n, random.Random(seed), spread=spread, lapse=lapse)
    system = system_cls(n, random.Random(seed * 7919 + 1))
    for _ in range(warmup):
        a, b = system.pair()
        system.record(a, b, user.choose(a, b))
    user.add(newcomers, spread)
    system.add_items(newcomers)
    total = n + newcomers
    out, done = [], 0
    for target in sorted(set(checkpoints)):
        while done < target:
            a, b = system.pair()
            system.record(a, b, user.choose(a, b))
            done += 1
        est = system.scores()
        true_rank = {i: r for r, i in enumerate(sorted(range(total), key=lambda i: -user.truth[i]))}
        est_rank = {i: r for r, i in enumerate(sorted(range(total), key=lambda i: (-est[i], i)))}
        error = sum(abs(true_rank[i] - est_rank[i]) for i in range(n, total)) / newcomers / total
        out.append({"comparisons": done, "newcomer_error": error, "spearman": spearman(user.truth, est)})
    return out


def _mean(runs: List[List[Dict[str, float]]]) -> List[Dict[str, float]]:
    rows = []
    for k in range(len(runs[0])):
        row = {"comparisons": runs[0][k]["comparisons"]}
        for key in runs[0][k]:
            if key != "comparisons":
                row[key] = sum(r[k][key] for r in runs) / len(runs)
        rows.append(row)
    return rows


def average(system_cls, n: int, seeds: int, checkpoints: Sequence[int], **kwargs) -> List[Dict[str, float]]:
    return _mean([run(system_cls, n, seed, checkpoints, **kwargs) for seed in range(1, seeds + 1)])


def average_newcomers(system_cls, n: int, newcomers: int, seeds: int, warmup: int, checkpoints: Sequence[int],
                      **kwargs) -> List[Dict[str, float]]:
    return _mean([newcomer_run(system_cls, n, newcomers, seed, warmup, checkpoints, **kwargs)
                  for seed in range(1, seeds + 1)])


def comparisons_to_reach(rows: List[Dict[str, float]], metric: str, target: float, lower: bool = False) -> Optional[int]:
    """The first checkpoint at which the averaged metric reaches target (linear in between);
    lower=True for a metric where lower is better."""
    prev = None
    for row in rows:
        value, goal = (-row[metric], -target) if lower else (row[metric], target)
        if value >= goal - 1e-12:
            if prev is None:
                return row["comparisons"]
            before = -prev[metric] if lower else prev[metric]
            span = value - before
            frac = (goal - before) / span if span > 0 else 1.0
            return round(prev["comparisons"] + frac * (row["comparisons"] - prev["comparisons"]))
        prev = row
    return None


def checkpoints_for(n: int, per_item: float = 15.0, steps: int = 60) -> List[int]:
    budget = int(n * per_item)
    return sorted({max(1, round(budget * (i + 1) / steps)) for i in range(steps)})


def savings(n: int, seeds: int, per_item: float, spread: float, lapse: float,
            levels: Sequence[Tuple[str, float]] = ()) -> Dict[str, Any]:
    """
    The comparison of both systems in one scenario: the curves, and for each metric the
    comparisons each system needs to reach the accuracy the OLD system has at the end of its
    budget (and any extra levels): {'curves': {...}, 'rows': [(metric, level, old, new)]}.
    """
    cps = checkpoints_for(n, per_item)
    curves = {key: average(cls, n, seeds, cps, spread=spread, lapse=lapse) for key, cls in SYSTEMS.items()}
    rows = []
    final = curves["legacy"][-1]
    wanted = [(m, round(final[m] - 0.005, 2)) for m in ("spearman", "kendall", "top10")] + list(levels)
    for metric, level in wanted:
        rows.append((metric, level, comparisons_to_reach(curves["legacy"], metric, level),
                     comparisons_to_reach(curves["current"], metric, level)))
    return {"curves": curves, "rows": rows, "budget": cps[-1]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--items", type=int, default=100)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--per-item", type=float, default=15.0, help="comparison budget per photo")
    ap.add_argument("--curves", action="store_true", help="also print the full curves")
    args = ap.parse_args(argv)
    names = {"spearman": "Spearman rho", "kendall": "Kendall tau", "top10": "top-10-precisie"}
    for key, (text, spread, lapse) in SCENARIOS.items():
        result = savings(args.items, args.seeds, args.per_item, spread, lapse,
                         levels=[("spearman", 0.8), ("top10", 0.6)])
        print(f"\n### Scenario '{key}': {text}")
        print(f"{args.items} foto's, {args.seeds} seeds, budget {result['budget']} vergelijkingen\n")
        print("| Doel | Oud (Elo) | Nieuw (Glicko-2 + informatie) | Besparing |")
        print("|---|---:|---:|---:|")
        for metric, level, old, new in result["rows"]:
            old_s = str(old) if old is not None else f"> {result['budget']}"
            new_s = str(new) if new is not None else f"> {result['budget']}"
            saving = f"{1 - new / old:.0%}" if old and new else ("-" if not new else f"> {1 - new / result['budget']:.0%}")
            print(f"| {names[metric]} {level:.2f} | {old_s} | {new_s} | {saving} |")
        if args.curves:
            for i, row in enumerate(result["curves"]["legacy"]):
                cur = result["curves"]["current"][i]
                print(f"{row['comparisons']:6d}  old {row['spearman']:.3f}/{row['kendall']:.3f}/{row['top10']:.2f}"
                      f"  new {cur['spearman']:.3f}/{cur['kendall']:.3f}/{cur['top10']:.2f} ({cur['settled']:.0%})")
    # New photos join a ranked set
    n, m, warm = args.items, max(5, args.items // 5), args.items * 10
    cps = [m, 2 * m, 4 * m, 8 * m]
    print(f"\n### Scenario 'nieuwkomers': {m} nieuwe foto's bij {n} gerangschikte (na {warm} vergelijkingen)")
    print("Gemiddelde rangfout van de nieuwkomers (aandeel van de lijst; 0 = precies goed, ~0,33 = gegokt)\n")
    print("| Vergelijkingen na toevoegen | Oud (Elo) | Nieuw |")
    print("|---:|---:|---:|")
    curves = {key: average_newcomers(cls, n, m, args.seeds, warm, cps) for key, cls in SYSTEMS.items()}
    for i, cp in enumerate(cps):
        print(f"| {cp} | {curves['legacy'][i]['newcomer_error']:.3f} | {curves['current'][i]['newcomer_error']:.3f} |")


if __name__ == "__main__":
    main()
