"""
DataVault Dashboard - 'Welke is mooier?': ratings from side-by-side comparisons

Two photos are shown side by side and the user picks the nicer one, calls it a draw
('gelijk') or skips the pair ('overslaan'). From those choices every photo gets a rating on
the one scale Shows has always used: 1000 for a file nobody judged yet, +50 or -50 per
up/down vote in the slideshow (routes/shows.py), and the comparisons on top of that.

THE MODEL (since migration 160): Glicko-2 (Glickman 2001/2012). Each photo has
    mu   its rating (the Elo scale: 400 points = 10 to 1 odds),
    rd   its rating deviation: how sure we are (350 for a photo nobody compared; it shrinks
         with every comparison, so the ranking can show a confidence band of mu +/- 2 rd),
    vol  its volatility: how erratic its results are (inconsistent picks raise it a little).
After a comparison both photos move by Glicko-2's rule for one game (score 1, 0 or 0.5 for
a draw), each against the other's state BEFORE the game. Uncertainty is what makes this
converge fast: a new photo moves by up to ~160 points on its first comparison, a settled
one by a few. The old system moved every photo by at most 32 (K) points, so a photo needed
dozens of comparisons to reach its level (measured in docs/ELO.md).

The public rating that Shows, albums and filters read stays file_ratings.rating: it is
round(mu), never below FLOOR. A vote changes file_ratings.rating by +/-50 as it always did;
the model sees it as a shift of mu (mu_effective = mu + (rating_now - public_written)), so a
vote is never lost and needs no change to the vote code. The model state lives in
elo_ratings (migration 160): mu, rd, vol, the public rating it wrote last, and games.

Both photos, both history fields and the history row are written in ONE transaction that
takes the write lock before it reads (BEGIN IMMEDIATE). The history row (elo_comparisons)
keeps the public rating and the full model state of both photos before and after, so
undo_comparison puts back exactly what was there, as long as nothing changed those two
photos since (a later vote or comparison); otherwise it refuses instead of guessing. Undo
works newest-first, step after step. A client_token makes a repeated POST (a double click,
a retry) count once.

When the model changes, the whole history is played again in order (replay_history), which
is what migration 160 did for the comparisons made with the old Elo rule: the comparisons
themselves (who, against whom, who won, when) never change, and votes cast between
comparisons stay exactly where they were.

PAIRS (choose_pairs): the next pair is the one that teaches the most about the ranking.
For a pair the expected information is the reduction of both photos' uncertainty that one
comparison brings (Glicko-2's variance update: biggest for two uncertain photos whose
outcome is a coin flip). A few first photos are drawn weighted by their uncertainty (rd^2),
each is matched with the photos nearest in rating plus a few random ones (exploration: new
and rarely compared photos get mixed in), and one pair is drawn with a probability that
grows steeply with its information, so the choice is near the best but never the same
pattern twice. Penalised: photos shown in the last few pairs (no repeats), pairs compared
before; never allowed: the pair just shown, pairs skipped recently, copies, bursts and
near-duplicates (see near_duplicate).
"""
import bisect
import datetime
import itertools
import math
import os
import random
import stat
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.config import PHOTO_EXTENSIONS, RAW_PHOTO_EXTENSIONS

MODEL = "glicko2"
RATING_START = 1000        # the rating of a file nobody judged yet (as for the votes)
RATING_FLOOR = 100         # a rating never drops below this (as for the votes)

GLICKO_SCALE = 173.7178    # Glicko-2 works on (rating - centre) / 173.7178
RD_START = 350.0           # the deviation of a photo nobody compared
RD_MIN = 30.0              # never surer than this (preferences drift a little)
VOL_START = 0.06
TAU = 0.5                  # how fast the volatility may change
RD_SETTLED = 75.0          # a photo with this deviation or less counts as settled
VOL_EPSILON = 1e-6

FIRST_DRAWS = 10           # first photos tried per pair
FIRST_POWER = 2.0          # a first photo is drawn with weight (rd / RD_START) ** FIRST_POWER
NEIGHBOURS = 14            # opponents nearest in rating per first photo
EXPLORE = 3                # plus this many random opponents
SHARPNESS = 3.0            # pair probability ~ information ** SHARPNESS
RECENT_ITEMS = 8           # photos shown in this many last pairs are held back
RECENT_PENALTY = 0.05
REPEAT_PENALTY = 0.15      # per time a pair was compared before
BURST_SECONDS = 2.0        # a series (same folder) goes on while photos follow within this
SKIP_DAYS = 30             # a skipped pair stays away this long
TOP_FOCUS = 2.0            # extra weight for photos that may belong to the top (see _relevance)
TOP_SHARE = 0.1            # the top is this share of the photos, at least TOP_MIN
TOP_MIN = 10

SESSION_GAP = 30 * 60      # comparisons further apart than this are separate sessions
GOAL_DEFAULT = 20
GOAL_CHOICES = (10, 20, 50, 100)

# Photos a browser can show at full size; RAW files have no picture of their own there
# (and usually sit next to a JPEG of the same moment, which would be a near-duplicate)
ELO_EXTENSIONS = PHOTO_EXTENSIONS - RAW_PHOTO_EXTENSIONS
OUTCOMES = ("a", "b", "draw")


class ComparisonConflict(Exception):
    """An undo that cannot put back the exact ratings any more (Dutch message)."""


# ==================== THE MATH (Glicko-2) ====================

def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


LOGISTIC_LIMIT = 30.0      # exp(30): an expectation never rounds to exactly 0 or 1


def _logistic(x: float) -> float:
    """1 / (1 + e^-x), with x clamped so that the result stays strictly between 0 and 1 (two
    photos thousands of points apart would otherwise divide by zero or overflow)."""
    return 1.0 / (1.0 + math.exp(-max(-LOGISTIC_LIMIT, min(LOGISTIC_LIMIT, x))))


def expected_score(rating: float, other: float, rd_other: float = 0.0) -> float:
    """The chance (0..1) that a photo with this rating wins against the other (with the
    other's deviation taken into account; rd_other 0 gives the classic Elo curve)."""
    g = _g(rd_other / GLICKO_SCALE)
    return _logistic(g * (rating - other) / GLICKO_SCALE)


def win_probability(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """P(a beats b) with both uncertainties."""
    phi = math.hypot(a["rd"], b["rd"]) / GLICKO_SCALE
    return _logistic(_g(phi) * (a["mu"] - b["mu"]) / GLICKO_SCALE)


def _new_volatility(phi: float, sigma: float, delta: float, v: float) -> float:
    """Glicko-2 step 5: the new volatility (Illinois iteration, Glickman 2012)."""
    a = math.log(sigma * sigma)
    tau2 = TAU * TAU

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2.0 * (phi * phi + v + ex) ** 2
        return num / den - (x - a) / tau2

    big_a = a
    if delta * delta > phi * phi + v:
        big_b = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        big_b = a - k * TAU
    fa, fb = f(big_a), f(big_b)
    for _ in range(100):
        if abs(big_b - big_a) <= VOL_EPSILON:
            break
        big_c = big_a + (big_a - big_b) * fa / (fb - fa)
        fc = f(big_c)
        if fc * fb <= 0:
            big_a, fa = big_b, fb
        else:
            fa /= 2.0
        big_b, fb = big_c, fc
    return math.exp(big_a / 2.0)


def glicko_update(state: Tuple[float, float, float], other: Tuple[float, float, float],
                  score: float) -> Tuple[float, float, float]:
    """One photo's new (mu, rd, vol) after one game against other (its state before the
    game), score 1 (won), 0 (lost) or 0.5 (draw)."""
    mu, rd, vol = state
    mu2, phi2 = (other[0] - RATING_START) / GLICKO_SCALE, other[1] / GLICKO_SCALE
    m, phi = (mu - RATING_START) / GLICKO_SCALE, rd / GLICKO_SCALE
    g = _g(phi2)
    e = _logistic(g * (m - mu2))
    v = 1.0 / (g * g * e * (1.0 - e))
    delta = v * g * (score - e)
    sigma = _new_volatility(phi, vol, delta, v)
    phi_star = math.sqrt(phi * phi + sigma * sigma)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    m_new = m + phi_new * phi_new * g * (score - e)
    rd_new = max(RD_MIN, min(RD_START, phi_new * GLICKO_SCALE))
    return RATING_START + m_new * GLICKO_SCALE, rd_new, sigma


def glicko_pair(a: Tuple[float, float, float], b: Tuple[float, float, float], outcome: str
                ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """Both new states for one comparison: outcome 'a', 'b' or 'draw'."""
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be 'a', 'b' or 'draw'")
    score_a = 1.0 if outcome == "a" else 0.0 if outcome == "b" else 0.5
    return glicko_update(a, b, score_a), glicko_update(b, a, 1.0 - score_a)


def public_rating(mu: float) -> int:
    """The whole-number rating Shows and the filters read."""
    return max(RATING_FLOOR, int(round(mu)))


def information(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """
    The expected information of comparing a and b: how much both photos' uncertainty
    shrinks (log of the variance ratio, Glicko's update 1/phi'^2 = 1/phi^2 + g^2 E(1-E)).
    Largest for two uncertain photos whose outcome is a coin flip.
    """
    pa, pb = a["rd"] / GLICKO_SCALE, b["rd"] / GLICKO_SCALE
    diff = (a["mu"] - b["mu"]) / GLICKO_SCALE
    ea = _logistic(_g(pb) * diff)
    eb = _logistic(-_g(pa) * diff)
    return (math.log1p(pa * pa * _g(pb) ** 2 * ea * (1.0 - ea))
            + math.log1p(pb * pb * _g(pa) ** 2 * eb * (1.0 - eb)))


def item_settled(rd: float) -> float:
    """0 for a photo nobody compared, 1 once its deviation is RD_SETTLED or less."""
    return max(0.0, min(1.0, (RD_START - rd) / (RD_START - RD_SETTLED)))


def settled_share(candidates: Sequence[Dict[str, Any]]) -> float:
    """How settled the ranking of these photos is (0..1): the mean of item_settled."""
    if not candidates:
        return 0.0
    return sum(item_settled(c["rd"]) for c in candidates) / len(candidates)


def new_candidate(path: str, rating: int = RATING_START) -> Dict[str, Any]:
    """A candidate nobody compared yet (for simulations and tests)."""
    return {"path": path, "rating": rating, "mu": float(rating), "rd": RD_START, "vol": VOL_START,
            "games": 0, "votes": 0, "size": None, "taken": None, "taken_source": None, "series": None}


def state_of(c: Dict[str, Any]) -> Tuple[float, float, float]:
    return c["mu"], c["rd"], c["vol"]


# ==================== STORED STATE ====================

def _combine(path: str, row, model) -> Dict[str, Any]:
    rating = int(row[0]) if row else None
    votes = int(row[1] or 0) if row else 0
    if model:
        mu, rd, vol, public, games = model
        current = rating if rating is not None else int(public)
        return {"path": path, "rating": current, "votes": votes, "existed": row is not None,
                "mu": float(mu) + (current - int(public)), "rd": float(rd), "vol": float(vol), "games": int(games)}
    current = rating if rating is not None else RATING_START
    return {"path": path, "rating": current, "votes": votes, "existed": row is not None,
            "mu": float(current), "rd": RD_START, "vol": VOL_START, "games": 0}


def _state(conn, path: str) -> Dict[str, Any]:
    """Everything known about one photo: public rating, votes, whether it has a rating row,
    and its model state (effective mu: a vote since the last comparison shifts it)."""
    row = conn.execute("SELECT rating, votes FROM file_ratings WHERE path = ?", (path,)).fetchone()
    model = conn.execute("SELECT mu, rd, vol, public, games FROM elo_ratings WHERE path = ?", (path,)).fetchone()
    return _combine(path, row, model)


def _rating_row(conn, path: str) -> Optional[Tuple[int, int, int]]:
    """(rating, games, votes), or None when the file has no rating row."""
    st = _state(conn, path)
    return (st["rating"], st["games"], st["votes"]) if st["existed"] else None


_COMPARISON_COLUMNS = (
    "id", "path_a", "path_b", "winner", "rating_a_before", "rating_a_after", "rating_b_before",
    "rating_b_after", "games_a_before", "games_b_before", "existed_a", "existed_b", "scope", "created_at",
    "mu_a_before", "rd_a_before", "vol_a_before", "mu_a_after", "rd_a_after", "vol_a_after",
    "mu_b_before", "rd_b_before", "vol_b_before", "mu_b_after", "rd_b_after", "vol_b_after",
    "model", "client_token")


def _answer(comp: Dict[str, Any], duplicate: bool = False) -> Dict[str, Any]:
    out = {"id": comp["id"], "winner": comp["winner"], "scope": comp["scope"], "created_at": comp["created_at"],
           "duplicate": duplicate}
    for side in ("a", "b"):
        out[side] = {"path": comp[f"path_{side}"], "before": comp[f"rating_{side}_before"],
                     "rating": comp[f"rating_{side}_after"], "games": comp[f"games_{side}_before"] + 1,
                     "rd": comp[f"rd_{side}_after"], "rd_before": comp[f"rd_{side}_before"]}
    return out


def record_comparison(db, path_a: str, path_b: str, winner: str, scope: Optional[str] = None,
                      client_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Apply one comparison (winner 'a', 'b' or 'draw'): both photos' model state, both public
    ratings and the history row in one transaction (the write lock is taken before anything
    is read). A client_token seen before answers that earlier comparison again (duplicate
    True) instead of counting twice.
    """
    if path_a == path_b:
        raise ValueError("a photo cannot be compared with itself")
    if winner not in OUTCOMES:
        raise ValueError("winner must be 'a', 'b' or 'draw'")
    now = time.time()
    with db._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if client_token:
            seen = _comparison_where(conn, "client_token = ?", (client_token,))
            if seen:
                return _answer(seen, duplicate=True)
        a, b = _state(conn, path_a), _state(conn, path_b)
        new_a, new_b = glicko_pair(state_of(a), state_of(b), winner)
        public_a, public_b = public_rating(new_a[0]), public_rating(new_b[0])
        for st, new, public in ((a, new_a, public_a), (b, new_b, public_b)):
            conn.execute("""
                INSERT INTO file_ratings (path, rating, votes, updated_at)
                VALUES (?, ?, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET rating = excluded.rating, updated_at = CURRENT_TIMESTAMP
            """, (st["path"], public))
            conn.execute("""
                INSERT INTO elo_ratings (path, mu, rd, vol, public, games, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET mu = excluded.mu, rd = excluded.rd, vol = excluded.vol,
                    public = excluded.public, games = excluded.games, updated_at = excluded.updated_at
            """, (st["path"], new[0], new[1], new[2], public, st["games"] + 1, now))
        cur = conn.execute(f"""
            INSERT INTO elo_comparisons ({", ".join(_COMPARISON_COLUMNS[1:])})
            VALUES ({", ".join("?" * (len(_COMPARISON_COLUMNS) - 1))})
        """, (path_a, path_b, winner, a["rating"], public_a, b["rating"], public_b, a["games"], b["games"],
              int(a["existed"]), int(b["existed"]), scope, now,
              a["mu"], a["rd"], a["vol"], new_a[0], new_a[1], new_a[2],
              b["mu"], b["rd"], b["vol"], new_b[0], new_b[1], new_b[2], MODEL, client_token))
        comp = _comparison_where(conn, "id = ?", (cur.lastrowid,))
    return _answer(comp)


def _comparison_where(conn, where: str, args: Tuple, order: str = "") -> Optional[Dict[str, Any]]:
    row = conn.execute(f"SELECT {', '.join(_COMPARISON_COLUMNS)} FROM elo_comparisons WHERE {where} {order} LIMIT 1",
                       args).fetchone()
    return dict(zip(_COMPARISON_COLUMNS, row)) if row else None


def _comparison(conn, comparison_id: Optional[int], scope: Optional[str]) -> Optional[Dict[str, Any]]:
    if comparison_id is not None:
        return _comparison_where(conn, "id = ?", (comparison_id,))
    if scope is not None:
        return _comparison_where(conn, "scope = ?", (scope,), "ORDER BY id DESC")
    return _comparison_where(conn, "1", (), "ORDER BY id DESC")


def last_comparison(db, scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        return _comparison(conn, None, scope)


def undo_comparison(db, comparison_id: Optional[int] = None, scope: Optional[str] = None) -> Dict[str, Any]:
    """
    Take back one comparison (by id, else the newest one in scope, else the newest one):
    both photos' public rating and model state go back to exactly what they were before
    it, and its history row goes. Rows the comparison made (a rating row, a model row) are
    removed again. Refused (ComparisonConflict) when either photo's rating or game count
    changed since; raises LookupError when there is nothing to undo.
    """
    with db._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        comp = _comparison(conn, comparison_id, scope)
        if comp is None:
            raise LookupError("Er is geen vergelijking om terug te draaien")
        sides = []
        for side in ("a", "b"):
            path = comp[f"path_{side}"]
            now = _state(conn, path)
            expected = (comp[f"rating_{side}_after"], comp[f"games_{side}_before"] + 1)
            if not now["existed"] or (now["rating"], now["games"]) != expected:
                raise ComparisonConflict(
                    f"Deze vergelijking kan niet meer precies terug: de score van {Path(path).name} "
                    "is daarna nog veranderd (door een stem of een latere vergelijking).")
            sides.append((side, path, now))
        for side, path, now in sides:
            if not comp[f"existed_{side}"] and now["votes"] == 0:
                conn.execute("DELETE FROM file_ratings WHERE path = ?", (path,))
            else:
                conn.execute("UPDATE file_ratings SET rating = ?, updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                             (comp[f"rating_{side}_before"], path))
            if comp[f"games_{side}_before"] == 0:
                conn.execute("DELETE FROM elo_ratings WHERE path = ?", (path,))
            else:
                conn.execute("UPDATE elo_ratings SET mu = ?, rd = ?, vol = ?, public = ?, games = ?, updated_at = ? "
                             "WHERE path = ?",
                             (comp[f"mu_{side}_before"], comp[f"rd_{side}_before"], comp[f"vol_{side}_before"],
                              comp[f"rating_{side}_before"], comp[f"games_{side}_before"], time.time(), path))
        conn.execute("DELETE FROM elo_comparisons WHERE id = ?", (comp["id"],))
    _history_removed(db, comp)
    return {
        "id": comp["id"], "scope": comp["scope"], "winner": comp["winner"],
        "a": {"path": comp["path_a"], "rating": comp["rating_a_before"], "games": comp["games_a_before"],
              "rd": comp["rd_a_before"]},
        "b": {"path": comp["path_b"], "rating": comp["rating_b_before"], "games": comp["games_b_before"],
              "rd": comp["rd_b_before"]},
    }


def record_skip(db, path_a: str, path_b: str, scope: Optional[str] = None) -> None:
    """'Overslaan': this pair stays away for SKIP_DAYS (it is not a comparison)."""
    a, b = sorted((path_a, path_b))
    with db._connect() as conn:
        conn.execute("INSERT INTO elo_skips (path_a, path_b, scope, created_at) VALUES (?, ?, ?, ?)",
                     (a, b, scope, time.time()))


# ==================== REPLAYING THE HISTORY ====================

def replay_history(conn) -> Dict[str, int]:
    """
    Play every comparison again, in order, with the current model, and rewrite what is
    derived from it: the before/after ratings and model states in elo_comparisons, the
    model rows (elo_ratings) and the public rating (file_ratings) of every compared photo.
    The comparisons themselves never change. Votes cast between two comparisons of a photo
    show as the gap between one row's 'after' and the next row's 'before'; they are carried
    over exactly, as is every vote after the last comparison. Runs inside the caller's
    transaction. Deterministic: the same history gives the same state.
    """
    rows = conn.execute("SELECT id, path_a, path_b, winner, rating_a_before, rating_a_after, "
                        "rating_b_before, rating_b_after FROM elo_comparisons ORDER BY id").fetchall()
    state: Dict[str, Dict[str, Any]] = {}
    for cid, path_a, path_b, winner, a_before, a_after, b_before, b_after in rows:
        sides = []
        for path, old_before in ((path_a, a_before), (path_b, b_before)):
            st = state.get(path)
            if st is None:
                st = {"mu": float(old_before), "rd": RD_START, "vol": VOL_START, "games": 0, "public": int(old_before)}
            else:
                gap = int(old_before) - st["old_after"]     # votes between the two comparisons
                st = {**st, "mu": st["mu"] + gap, "public": max(RATING_FLOOR, st["public"] + gap)}
            sides.append(st)
        new_a, new_b = glicko_pair(state_of(sides[0]), state_of(sides[1]), winner)
        values = []
        for st, new in zip(sides, (new_a, new_b)):
            values.extend([st["public"], public_rating(new[0]), st["games"], st["mu"], st["rd"], st["vol"], *new])
        conn.execute("""
            UPDATE elo_comparisons SET
                rating_a_before = ?, rating_a_after = ?, games_a_before = ?,
                mu_a_before = ?, rd_a_before = ?, vol_a_before = ?, mu_a_after = ?, rd_a_after = ?, vol_a_after = ?,
                rating_b_before = ?, rating_b_after = ?, games_b_before = ?,
                mu_b_before = ?, rd_b_before = ?, vol_b_before = ?, mu_b_after = ?, rd_b_after = ?, vol_b_after = ?,
                model = ?
            WHERE id = ?""", (*values, MODEL, cid))
        for path, st, new, old_after in ((path_a, sides[0], new_a, a_after), (path_b, sides[1], new_b, b_after)):
            state[path] = {"mu": new[0], "rd": new[1], "vol": new[2], "games": st["games"] + 1,
                           "public": public_rating(new[0]), "old_after": int(old_after)}
    now = time.time()
    for path, st in state.items():
        row = conn.execute("SELECT rating FROM file_ratings WHERE path = ?", (path,)).fetchone()
        gap = (int(row[0]) - st["old_after"]) if row else 0          # votes after the last comparison
        public = max(RATING_FLOOR, st["public"] + gap)
        if row:
            conn.execute("UPDATE file_ratings SET rating = ? WHERE path = ?", (public, path))
        conn.execute("INSERT OR REPLACE INTO elo_ratings (path, mu, rd, vol, public, games, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (path, st["mu"] + gap, st["rd"], st["vol"], public, st["games"], now))
    return {"comparisons": len(rows), "photos": len(state)}


# ==================== WHICH PHOTOS ====================

def _prefix_range(folder: str) -> Tuple[str, str]:
    prefix = folder.rstrip("/") + "/"
    return prefix, prefix[:-1] + "0"   # '0' is the character right after '/'


_STATE_SQL = ("SELECT f.path, f.rating, f.votes, e.mu, e.rd, e.vol, e.public, e.games "
              "FROM file_ratings f LEFT JOIN elo_ratings e ON e.path = f.path ")


def _states(rows) -> Dict[str, Dict[str, Any]]:
    out = {}
    for path, rating, votes, mu, rd, vol, public, games in rows:
        model = (mu, rd, vol, public, games) if mu is not None else None
        out[path] = _combine(path, (rating, votes), model)
    return out


def states_below(db, folder: str) -> Dict[str, Dict[str, Any]]:
    """{path: state} for every judged file (a rating row) anywhere below folder."""
    low, high = _prefix_range(folder)
    with db._connect() as conn:
        # Two range scans merged here: faster than the join (one index lookup per row)
        models = {row[0]: row[1:] for row in conn.execute(
            "SELECT path, mu, rd, vol, public, games FROM elo_ratings WHERE path >= ? AND path < ?", (low, high))}
        return {path: _combine(path, (rating, votes), models.get(path)) for path, rating, votes in conn.execute(
            "SELECT path, rating, votes FROM file_ratings WHERE path >= ? AND path < ?", (low, high))}


def states_of(db, paths: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """{path: state} for the judged files among paths (a collection that is not one folder)."""
    out: Dict[str, Dict[str, Any]] = {}
    paths = list(paths)
    with db._connect() as conn:
        for start in range(0, len(paths), 400):
            chunk = paths[start:start + 400]
            out.update(_states(conn.execute(
                _STATE_SQL + f"WHERE f.path IN ({','.join('?' * len(chunk))})", chunk).fetchall()))
    return out


def ratings_below(db, folder: str) -> Dict[str, Tuple[int, int, int]]:
    """{path: (rating, games, votes)} for every judged file anywhere below folder."""
    return {p: (s["rating"], s["games"], s["votes"]) for p, s in states_below(db, folder).items()}


def ratings_of(db, paths: Sequence[str]) -> Dict[str, Tuple[int, int, int]]:
    return {p: (s["rating"], s["games"], s["votes"]) for p, s in states_of(db, paths).items()}


def with_states(base: Sequence[Dict[str, Any]], states: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The candidates with their rating, model state, games and votes."""
    out = []
    for item in base:
        st = states.get(item["path"])
        if st is None:
            out.append({**item, "rating": RATING_START, "mu": float(RATING_START), "rd": RD_START,
                        "vol": VOL_START, "games": 0, "votes": 0})
        else:
            out.append({**item, "rating": st["rating"], "mu": st["mu"], "rd": st["rd"], "vol": st["vol"],
                        "games": st["games"], "votes": st["votes"]})
    return out


# ==================== COLLECTIONS OTHER THAN A FOLDER ====================
#
# A comparison round runs in a scope. A folder (a show, the whole library, any folder in
# the vault) is built in. Other kinds of collection (albums) plug in here without changing
# this module or the routes: register_scope('album', resolve), where resolve(value) answers
# {"title": str, "paths": [absolute file paths]} or None when that collection does not
# exist. The routes then accept ?scope=album:<value> (pair, leaderboard) and
# {"scope": "album:<value>"} (compare, undo), and the history row keeps 'album:<value>' as
# its scope. Folder scopes are stored as the folder path (always starts with '/').

_scopes: Dict[str, Callable[[str], Optional[Dict[str, Any]]]] = {}


def register_scope(kind: str, resolve: Callable[[str], Optional[Dict[str, Any]]]) -> None:
    if not kind or not kind.isidentifier():
        raise ValueError("a scope kind is a plain word, like 'album'")
    _scopes[kind] = resolve


def scope_kinds() -> List[str]:
    return sorted(_scopes)


def resolve_scope(key: str) -> Optional[Dict[str, Any]]:
    """{'key', 'kind', 'value', 'title', 'paths', 'filters', 'version'} for a registered 'kind:value',
    else None (an unknown kind, or a collection that does not exist). filters: the library
    filter of the collection when it has one (an album), for 'Top N als album'. version: when
    the resolver gives one, the same version means the same paths (the routes then reuse what
    they built for them)."""
    kind, sep, value = (key or "").partition(":")
    if not sep or kind not in _scopes:
        return None
    found = _scopes[kind](value)
    if not found:
        return None
    return {"key": f"{kind}:{value}", "kind": kind, "value": value,
            "title": str(found.get("title") or value), "paths": list(found.get("paths") or []),
            "filters": found.get("filters"), "version": found.get("version")}


def comparisons_in_scope(db, scope: str) -> int:
    with db._connect() as conn:
        h = _history(db, conn)
        with h.lock:
            return h.sync(conn).scopes.get(scope, 0)


def results_of(db, paths: Sequence[str]) -> Dict[str, Dict[str, int]]:
    """{path: {'wins', 'draws', 'losses'}} for these paths."""
    out: Dict[str, Dict[str, int]] = {}
    paths = list(paths)
    with db._connect() as conn:
        for start in range(0, len(paths), 400):
            chunk = paths[start:start + 400]
            marks = ",".join("?" * len(chunk))
            for column, side in (("path_a", "a"), ("path_b", "b")):
                for path, winner, count in conn.execute(
                        f"SELECT {column}, winner, COUNT(*) FROM elo_comparisons WHERE {column} IN ({marks}) "
                        f"GROUP BY {column}, winner", chunk):
                    rec = out.setdefault(path, {"wins": 0, "draws": 0, "losses": 0})
                    key = "draws" if winner == "draw" else "wins" if winner == side else "losses"
                    rec[key] += count
    return out


def wins_of(db, paths: Sequence[str]) -> Dict[str, int]:
    """{path: comparisons won} for these paths."""
    return {p: r["wins"] for p, r in results_of(db, paths).items()}


def is_elo_photo(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in ELO_EXTENSIONS


# ==================== THE HISTORY IN SHORT ====================
#
# Every pair request needs two things from the whole history: how often each pair was
# compared (the repeat penalty) and on which days there were comparisons (the streak).
# Reading all rows for that each time grows with the history (75,000 comparisons: about
# 90 ms per request), so each database keeps a summary in memory that follows the table:
# rows with a higher id than the summary has seen are added (ids only grow: AUTOINCREMENT),
# an undo takes its row out, and when the row count still does not match (rows changed
# another way) the summary is built again. A move that renames paths calls forget_history().

class _History:
    def __init__(self):
        self.lock = threading.Lock()
        self.count = 0
        self.max_id = 0
        self.seen: Counter = Counter()      # frozenset({path_a, path_b}) -> comparisons
        self.days: Counter = Counter()      # local day (ordinal) -> comparisons
        self.scopes: Counter = Counter()    # scope -> comparisons
        self.ready = False
        self.session = 0                    # comparisons in the newest session ...
        self.last_t: Optional[float] = None     # ... which ends with this one
        self.session_ok = False             # False: walk back once to find them again

    def _add(self, a: str, b: str, created_at: float, scope: Optional[str], n: int = 1) -> None:
        self.scopes[scope] += n
        if self.scopes[scope] <= 0:
            del self.scopes[scope]
        key = frozenset((a, b))
        self.seen[key] += n
        if self.seen[key] <= 0:
            del self.seen[key]
        day = _day(created_at)
        self.days[day] += n
        if self.days[day] <= 0:
            del self.days[day]

    def sync(self, conn) -> "_History":
        count, max_id = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM elo_comparisons").fetchone()
        if self.ready and count == self.count and max_id <= self.max_id:
            return self
        if self.ready and max_id > self.max_id:
            rows = conn.execute("SELECT id, path_a, path_b, created_at, scope FROM elo_comparisons WHERE id > ? "
                                "ORDER BY id", (self.max_id,)).fetchall()
            if self.count + len(rows) == count:
                for _, a, b, at, scope in rows:
                    self._add(a, b, at, scope)
                    self._extend_session(at)
                self.count, self.max_id = count, max(self.max_id, max_id)
                return self
        self.seen, self.days, self.scopes = Counter(), Counter(), Counter()
        for a, b, at, scope in conn.execute("SELECT path_a, path_b, created_at, scope FROM elo_comparisons"):
            self._add(a, b, at, scope)
        self.count, self.max_id, self.ready = count, max(self.max_id, max_id), True
        self.session_ok = False
        return self

    def _extend_session(self, at: float) -> None:
        if not self.session_ok:
            return
        if self.last_t is not None and at < self.last_t:     # the clock went back: walk again
            self.session_ok = False
        elif self.last_t is None or at - self.last_t > SESSION_GAP:
            self.session, self.last_t = 1, at
        else:
            self.session, self.last_t = self.session + 1, at

    def current_session(self, conn) -> Tuple[int, Optional[float]]:
        """(comparisons in the newest session, when its last one was), after sync."""
        if not self.session_ok:
            times = _session_times(conn)
            self.session, self.last_t = len(times), (times[0] if times else None)
            self.session_ok = True
        return self.session, self.last_t


_history_generation = [0]


def _history(db, conn) -> _History:
    """The in-memory summary of this database's comparisons, brought up to date (hold its lock)."""
    h = getattr(db, "_elo_history", None)
    if h is None or getattr(db, "_elo_history_generation", None) != _history_generation[0]:
        h = _History()
        db._elo_history = h
        db._elo_history_generation = _history_generation[0]
    return h


def _history_removed(db, comp: Dict[str, Any]) -> None:
    """An undo deleted this row: take it out of the summary (when the summary had it)."""
    h = getattr(db, "_elo_history", None)
    if h is None:
        return
    with h.lock:
        if h.ready and comp["id"] <= h.max_id and h.seen.get(frozenset((comp["path_a"], comp["path_b"])), 0) > 0:
            h._add(comp["path_a"], comp["path_b"], comp["created_at"], comp["scope"], -1)
            h.count -= 1
            h.session_ok = False
        else:
            h.ready = False


def forget_history() -> None:
    """Paths in the history changed (a move): every summary is built again on its next use."""
    _history_generation[0] += 1


def pair_counts(db) -> Counter:
    """{frozenset({path_a, path_b}): times compared} over the whole history (shared: read only)."""
    with db._connect() as conn:
        h = _history(db, conn)
        with h.lock:
            return h.sync(conn).seen


# ==================== SESSIONS, STREAKS, MOVEMENT ====================

def _session_times(conn, now: Optional[float] = None) -> List[float]:
    """The times of the newest session's comparisons, newest first (comparisons less than
    SESSION_GAP apart are one session). With now: [] when that session ended more than
    SESSION_GAP before now. Walks back along the created_at index only as far as needed."""
    times: List[float] = []
    for (t,) in conn.execute("SELECT created_at FROM elo_comparisons ORDER BY created_at DESC"):
        last = times[-1] if times else now
        if last is not None and last - t > SESSION_GAP:
            break
        times.append(t)
    return times


def session_start(db) -> Optional[float]:
    """When the newest session began, or None without any comparison."""
    with db._connect() as conn:
        times = _session_times(conn)
    return times[-1] if times else None


def _day(t: float) -> int:
    return datetime.date.fromtimestamp(t).toordinal()


def progress(db, now: Optional[float] = None, goal: int = GOAL_DEFAULT) -> Dict[str, Any]:
    """The motivation numbers: comparisons in the current session (a session that ended
    more than SESSION_GAP ago is over), today, the streak of days in a row with at least one
    comparison (it ends today or yesterday), the best streak ever, and the session goal."""
    now = time.time() if now is None else now
    with db._connect() as conn:
        h = _history(db, conn)
        with h.lock:
            h.sync(conn)
            session, last = h.current_session(conn)
            if last is None or now - last > SESSION_GAP:
                session = 0
            days = sorted(h.days)
            today_count = h.days.get(_day(now), 0)
            total = h.count
    present = set(days)
    today = _day(now)
    day = today if today in present else today - 1
    streak = 0
    while day in present:
        streak += 1
        day -= 1
    best, run, previous = 0, 0, None
    for d in days:
        run = run + 1 if previous is not None and d - previous == 1 else 1
        best = max(best, run)
        previous = d
    return {"session": session, "today": today_count, "streak": streak, "best_streak": best,
            "goal": goal, "total": total}


def ratings_at(db, paths: Sequence[str], when: float) -> Dict[str, int]:
    """Each photo's public rating just before `when`, from the history: the 'after' of its
    last comparison before then, or for a photo first compared later the 'before' of that
    first comparison. Photos without comparisons are not in the answer."""
    out: Dict[str, int] = {}
    paths = list(paths)
    with db._connect() as conn:
        for start in range(0, len(paths), 400):
            chunk = paths[start:start + 400]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT path_a, path_b, rating_a_before, rating_a_after, rating_b_before, rating_b_after, created_at "
                f"FROM elo_comparisons WHERE path_a IN ({marks}) OR path_b IN ({marks}) ORDER BY id", chunk + chunk)
            wanted = set(chunk)
            first_seen: Dict[str, bool] = {}
            for pa, pb, ab, aa, bb, ba, at in rows:
                for path, before, after in ((pa, ab, aa), (pb, bb, ba)):
                    if path not in wanted:
                        continue
                    if at < when:
                        out[path] = after
                    elif path not in out and path not in first_seen:
                        out[path] = before
                    first_seen[path] = True
    return out


def first_compared(db, paths: Sequence[str]) -> Dict[str, float]:
    """{path: time of its first comparison} for the compared photos among paths."""
    found: Dict[str, float] = {}
    paths = list(paths)
    with db._connect() as conn:
        for start in range(0, len(paths), 400):
            chunk = paths[start:start + 400]
            marks = ",".join("?" * len(chunk))
            for path, first in conn.execute(
                    f"SELECT p, MIN(t) FROM (SELECT path_a AS p, created_at AS t FROM elo_comparisons "
                    f"WHERE path_a IN ({marks}) UNION ALL SELECT path_b, created_at FROM elo_comparisons "
                    f"WHERE path_b IN ({marks})) GROUP BY p", chunk + chunk):
                found[path] = first
    return found


# ==================== WHICH PAIR ====================

class PairMemory:
    """What the pair choice remembers: how often each pair was compared, which pairs are
    blocked for now (the pair just shown, recently skipped ones), and the photos shown in
    the last RECENT_ITEMS pairs (held back, so the same photo does not keep coming)."""

    def __init__(self, recent: int = RECENT_ITEMS, seen: Optional[Counter] = None):
        self.seen: Counter = seen if seen is not None else Counter()
        self._shared = seen is not None
        self.blocked: set = set()
        self.recent: deque = deque(maxlen=recent * 2)

    def add(self, a: str, b: str) -> None:
        if self._shared:                    # the database's summary is never changed from here
            self.seen, self._shared = Counter(self.seen), False
        self.seen[frozenset((a, b))] += 1
        self.recent.extend((a, b))

    def block(self, a: str, b: str) -> None:
        self.blocked.add(frozenset((a, b)))

    def shown(self, pairs: Sequence[Sequence[str]]) -> None:
        """Pairs the screen showed lately (oldest first), some perhaps not saved yet: their
        photos are held back like those of the last saved pairs."""
        extra = [p for pair in pairs for p in pair]
        if extra:
            self.recent = deque(list(self.recent) + extra, maxlen=self.recent.maxlen + len(extra))


def pair_memory(db, scope: Optional[str] = None, now: Optional[float] = None) -> PairMemory:
    """The memory for the pair choice from the database: pairs compared before (in any
    scope, from the in-memory summary), the photos of the last RECENT_ITEMS pairs in this
    scope, and the skips of the last SKIP_DAYS days."""
    now = time.time() if now is None else now
    memory = PairMemory(seen=pair_counts(db))
    with db._connect() as conn:
        recent = conn.execute("SELECT path_a, path_b FROM elo_comparisons WHERE scope IS ? ORDER BY id DESC LIMIT ?",
                              (scope, RECENT_ITEMS)).fetchall()
        for a, b in conn.execute("SELECT path_a, path_b FROM elo_skips WHERE created_at >= ?",
                                 (now - SKIP_DAYS * 86400,)):
            memory.block(a, b)
    for a, b in reversed(recent):
        memory.recent.extend((a, b))
    return memory


_near_duplicate_checks: List[Callable[[Dict[str, Any], Dict[str, Any]], bool]] = []


def register_near_duplicate_check(check: Callable[[Dict[str, Any], Dict[str, Any]], bool]) -> None:
    """Add a test 'are these two candidates (nearly) the same photo?' (e.g. perceptual hashes).
    It gets two candidate dicts (path, id (media id or None), size, taken, taken_source, ...)."""
    if check not in _near_duplicate_checks:
        _near_duplicate_checks.append(check)


def _size(candidate: Dict[str, Any]) -> Optional[int]:
    if candidate.get("size") is None:
        try:
            candidate["size"] = os.lstat(candidate["path"]).st_size
        except OSError:
            candidate["size"] = -1
    size = candidate["size"]
    return size if size and size > 0 else None


def _own_time(candidate: Dict[str, Any]) -> Optional[float]:
    """The capture time the photo itself carries (EXIF or video), not a file date."""
    if candidate.get("taken") is None or candidate.get("taken_source") not in ("exif", "video"):
        return None
    return candidate["taken"]


def mark_series(candidates: Sequence[Dict[str, Any]]) -> None:
    """
    Give every candidate a 'series' key: photos in one folder whose own capture times follow
    each other within BURST_SECONDS share one (a burst of any length, frame after frame);
    any other photo gets None. Changes the dicts in place.
    """
    by_folder: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        c["series"] = None
        if _own_time(c) is not None:
            by_folder.setdefault(os.path.dirname(c["path"]), []).append(c)
    number = 0
    for folder, items in by_folder.items():
        items.sort(key=lambda c: c["taken"])
        for before, item in zip(items, items[1:]):
            if item["taken"] - before["taken"] <= BURST_SECONDS:
                if before["series"] is None:
                    number += 1
                    before["series"] = number
                item["series"] = before["series"]


def near_duplicate(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Exact copies (same size), one series (see mark_series; for two photos without a
    'series' key: same folder, own capture times within BURST_SECONDS) and whatever a
    registered check finds (perceptual hashes, see routes/shows)."""
    size_a, size_b = _size(a), _size(b)
    if size_a is not None and size_a == size_b:
        return True
    if a.get("series") is not None and a.get("series") == b.get("series"):
        return True
    time_a, time_b = _own_time(a), _own_time(b)
    if (time_a is not None and time_b is not None
            and os.path.dirname(a["path"]) == os.path.dirname(b["path"])
            and abs(time_a - time_b) <= BURST_SECONDS):
        return True
    for check in _near_duplicate_checks:
        try:
            if check(a, b):
                return True
        except Exception:
            continue
    return False


def _with_model(c: Dict[str, Any]) -> Dict[str, Any]:
    """Candidates that carry only rating and games get a model state."""
    if "mu" not in c:
        c["mu"] = float(c.get("rating", RATING_START))
        c["rd"] = RD_START if not c.get("games") else max(RD_MIN, RD_START / math.sqrt(1 + c["games"]))
        c["vol"] = VOL_START
    return c


def choose_pairs(candidates: Sequence[Dict[str, Any]], count: int = 1, avoid: Iterable[Iterable[str]] = (),
                 near_dup: Callable[[Dict[str, Any], Dict[str, Any]], bool] = near_duplicate,
                 rng: Optional[random.Random] = None, memory: Optional[PairMemory] = None,
                 exclude: Iterable[str] = ()) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    Up to `count` pairs to compare next, no photo in two of them and none of `exclude` (the
    photos on screen or already queued), each in random left/right order. avoid: pairs (of
    paths) that must not come now. See the module text for how a pair is chosen.
    """
    rng = rng or random
    memory = memory or PairMemory()
    if len(candidates) < 2:
        return []
    for c in candidates:
        _with_model(c)
    if any("series" not in c for c in candidates):
        mark_series(candidates)
    blocked = {frozenset(pair) for pair in avoid} | memory.blocked
    taken = set(exclude)
    pool = [c for c in candidates if c["path"] not in taken]
    if len(pool) < 2:
        pool = list(candidates)
        taken = set()
    by_mu = sorted(pool, key=lambda c: (c["mu"], c["path"]))
    mus = [c["mu"] for c in by_mu]
    recent = set(memory.recent)
    cutoff = _top_cutoff(mus)
    weights = [(c["rd"] / RD_START) ** FIRST_POWER * _relevance(c, cutoff)
               * (RECENT_PENALTY if c["path"] in recent else 1.0) for c in pool]
    out = []
    for _ in range(count):
        found = _one_pair(pool, weights, by_mu, mus, taken, recent, blocked, memory, near_dup, rng)
        if not found:
            break
        out.append(found)
        taken.update(c["path"] for c in found)
        for i, c in enumerate(pool):
            if c["path"] in taken:
                weights[i] = 0.0
    return out


def choose_pair(candidates: Sequence[Dict[str, Any]], avoid: Iterable[Iterable[str]] = (),
                near_dup: Callable[[Dict[str, Any], Dict[str, Any]], bool] = near_duplicate,
                rng: Optional[random.Random] = None, memory: Optional[PairMemory] = None,
                exclude: Iterable[str] = ()) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """The next pair (see choose_pairs), or None when no pair is allowed."""
    pairs = choose_pairs(candidates, 1, avoid, near_dup, rng, memory, exclude)
    return pairs[0] if pairs else None


select_pair = choose_pair


def _one_pair(pool, weights, by_mu, mus, taken, recent, blocked, memory, near_dup, rng):
    """One pair (see choose_pairs); weights: the first-photo weight of each pool photo (0 once taken)."""
    free = [c for c in pool if c["path"] not in taken] if taken else pool
    if len(free) < 2 or not any(weights):
        return None
    cum = list(itertools.accumulate(weights))
    options: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
    tried = set()
    for _ in range(FIRST_DRAWS * 3):
        if len(tried) >= min(FIRST_DRAWS, len(free)):
            break
        first = rng.choices(pool, cum_weights=cum)[0]
        if first["path"] in tried:
            continue
        tried.add(first["path"])
        _score_opponents(first, by_mu, mus, free, taken, recent, blocked, memory, near_dup, rng, options)
    if not options:
        # Few photos and every try was blocked: walk all of them once
        for first in free:
            if first["path"] not in tried:
                tried.add(first["path"])
                _score_opponents(first, by_mu, mus, free, taken, recent, blocked, memory, near_dup, rng, options)
                if options:
                    break
    if not options:
        return None
    top = max(g for g, _, _ in options) or 1.0
    picked = rng.choices(options, [max(1e-12, g / top) ** SHARPNESS for g, _, _ in options])[0]
    first, second = picked[1], picked[2]
    return (first, second) if rng.random() < 0.5 else (second, first)


def _top_cutoff(mus: Sequence[float]) -> float:
    """The rating of the photo at the edge of the top (mus sorted low to high)."""
    k = max(TOP_MIN, int(len(mus) * TOP_SHARE))
    return mus[max(0, len(mus) - k)]


def _relevance(c: Dict[str, Any], cutoff: float) -> float:
    """1 + TOP_FOCUS * the chance that this photo belongs above the top's edge: the top of
    the ranking is what Shows and the Ranglijst show most, so it is sorted out first."""
    z = (c["mu"] - cutoff) / max(1.0, c["rd"])
    return 1.0 + TOP_FOCUS * 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _score_opponents(first, by_mu, mus, free, taken, recent, blocked, memory, near_dup, rng, options) -> None:
    cutoff = _top_cutoff(mus)
    for other in _opponents(first, by_mu, mus, free, taken, blocked, near_dup, rng):
        gain = information(first, other) * math.sqrt(_relevance(first, cutoff) * _relevance(other, cutoff))
        seen = memory.seen.get(frozenset((first["path"], other["path"])), 0)
        if seen:
            gain *= REPEAT_PENALTY ** seen
        if other["path"] in recent:
            gain *= RECENT_PENALTY
        if first["path"] in recent:
            gain *= RECENT_PENALTY
        options.append((gain, first, other))


def _opponents(first, by_mu, mus, free, taken, blocked, near_dup, rng) -> List[Dict[str, Any]]:
    """The NEIGHBOURS allowed photos nearest to first in rating (walking out both ways from a
    random place among the photos with the same rating, so a fresh library does not always
    give the alphabetically first ones; an equal distance is a coin toss), plus EXPLORE
    random ones."""
    low = bisect.bisect_left(mus, first["mu"])
    high = bisect.bisect_right(mus, first["mu"])
    at = rng.randint(low, high)
    left, right = at - 1, at
    options: List[Dict[str, Any]] = []
    looked = 0
    while len(options) < NEIGHBOURS and (left >= 0 or right < len(by_mu)) and looked < NEIGHBOURS * 8:
        if right >= len(by_mu):
            take_left = True
        elif left < 0:
            take_left = False
        else:
            below, above = first["mu"] - mus[left], mus[right] - first["mu"]
            take_left = below < above or (below == above and rng.random() < 0.5)
        if take_left:
            cand, left = by_mu[left], left - 1
        else:
            cand, right = by_mu[right], right + 1
        looked += 1
        if _allowed(first, cand, taken, blocked, near_dup):
            options.append(cand)
    seen = {c["path"] for c in options}
    for _ in range(EXPLORE):
        cand = free[rng.randrange(len(free))]
        if cand["path"] not in seen and _allowed(first, cand, taken, blocked, near_dup):
            options.append(cand)
            seen.add(cand["path"])
    return options


def _allowed(first, cand, taken, blocked, near_dup) -> bool:
    if cand["path"] == first["path"] or cand["path"] in taken:
        return False
    if frozenset((first["path"], cand["path"])) in blocked:
        return False
    return not near_dup(first, cand)


def still_there(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False
