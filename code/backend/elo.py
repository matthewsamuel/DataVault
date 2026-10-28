"""
DataVault Dashboard - 'Welke is mooier?': Elo ratings from side-by-side comparisons

Shows ranks photos by a rating on one scale: 1000 for a file nobody judged yet, +50 or
-50 per up/down vote in the slideshow (routes/shows.py), and since this module also
comparisons. Two photos are shown side by side; the one the user picks wins, and both
ratings move by the standard Elo rule:

    expected score   E_a = 1 / (1 + 10 ** ((R_b - R_a) / 400))       (E_b = 1 - E_a)
    new rating       R_a' = max(FLOOR, R_a + round(K_a * (S_a - E_a)))  S = 1 win, 0 loss

K is 32 for a photo's first 30 comparisons (its rating still has to find its level) and
16 after that (it is settled; one surprising pick should not undo thirty). Each photo uses
its own K, so a new photo moves faster than a settled one it is compared with. With equal
K the change is symmetric: what one photo wins, the other loses (Python's round() rounds
halves to even, so round(-x) == -round(x)). A rating never drops below FLOOR (100, the
same floor as the votes) and one comparison never moves a rating by more than K.
Ratings stay whole numbers, like the votes made them.

Both ratings, both game counts and the history row are written in ONE transaction that
takes the write lock before it reads (BEGIN IMMEDIATE), so two comparisons at the same
moment (two tabs) are applied one after the other, never on the same old ratings. The
history row (elo_comparisons, migration 50) keeps both paths, the winner and both ratings
and game counts before and after, so undo_comparison puts back exactly what was there, as
long as nothing changed those two photos since (a later vote or comparison); otherwise it
refuses instead of guessing. How many comparisons a photo had ('games', for its K) is
counted from that history, so an undo lowers it again by itself. Paths in the history
follow moves like the ratings do (media_index._carry_user_data).

Pairs (choose_pair): the first photo is drawn with a preference for photos with few
comparisons (weight 1 / (1 + games)^2); its opponent comes from the photos nearest to it
in rating (the closest NEIGHBOURS, the least compared of those most likely). Photos with
the same rating (in a fresh library: nearly all of them, at 1000) are equally near, so the
walk starts at a random place among them and picks a random side at every tie; otherwise
the opponents would always be the alphabetically first photos. Never the pair that was
just shown or compared, and never two photos that are (nearly) the same: the same file size
(an exact copy), or one series (mark_series: in one folder, sorted by the capture time from
EXIF or the video itself, a series goes on as long as each photo follows the previous one
within BURST_SECONDS, so the first and the fifth frame of a burst one second apart are one
series too). A later perceptual-hash feature adds its own test with
register_near_duplicate_check(fn).
"""
import bisect
import os
import random
import stat
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.config import PHOTO_EXTENSIONS, RAW_PHOTO_EXTENSIONS

RATING_START = 1000        # the rating of a file nobody judged yet (as for the votes)
RATING_FLOOR = 100         # a rating never drops below this (as for the votes)
K_NEW = 32                 # K for a photo's first K_SETTLE_GAMES comparisons
K_SETTLED = 16             # K after that
K_SETTLE_GAMES = 30

NEIGHBOURS = 8             # the opponent comes from this many photos nearest in rating
PAIR_ATTEMPTS = 24         # first photos tried before giving up on a pair
BURST_SECONDS = 2.0        # a series (same folder) goes on while photos follow within this

# Photos a browser can show at full size; RAW files have no picture of their own there
# (and usually sit next to a JPEG of the same moment, which would be a near-duplicate)
ELO_EXTENSIONS = PHOTO_EXTENSIONS - RAW_PHOTO_EXTENSIONS


class ComparisonConflict(Exception):
    """An undo that cannot put back the exact ratings any more (Dutch message)."""


# ==================== THE MATH ====================

def expected_score(rating: float, other: float) -> float:
    """The chance (0..1) that a photo with this rating wins against the other."""
    return 1.0 / (1.0 + 10.0 ** ((other - rating) / 400.0))


def k_factor(games: int) -> int:
    """32 while a photo has had fewer than 30 comparisons, 16 after that."""
    return K_NEW if games < K_SETTLE_GAMES else K_SETTLED


def rating_change(rating: int, other: int, score: float, k: int) -> int:
    """The whole-number change for one side: round(K * (S - E)), never more than K."""
    change = round(k * (score - expected_score(rating, other)))
    return max(-k, min(k, change))


def elo_update(rating_a: int, rating_b: int, winner: str, games_a: int = 0, games_b: int = 0
               ) -> Tuple[int, int, int, int]:
    """Both new ratings and the K each used: (new_a, new_b, k_a, k_b). winner is 'a' or 'b'."""
    if winner not in ("a", "b"):
        raise ValueError("winner must be 'a' or 'b'")
    score_a = 1.0 if winner == "a" else 0.0
    k_a, k_b = k_factor(games_a), k_factor(games_b)
    new_a = max(RATING_FLOOR, rating_a + rating_change(rating_a, rating_b, score_a, k_a))
    new_b = max(RATING_FLOOR, rating_b + rating_change(rating_b, rating_a, 1.0 - score_a, k_b))
    return new_a, new_b, k_a, k_b


# ==================== RECORDING AND UNDOING ====================

def _games(conn, path: str) -> int:
    """How many comparisons this photo took part in (counted from the history)."""
    return conn.execute(
        "SELECT (SELECT COUNT(*) FROM elo_comparisons WHERE path_a = ?) "
        "+ (SELECT COUNT(*) FROM elo_comparisons WHERE path_b = ?)", (path, path)).fetchone()[0]


def _rating_row(conn, path: str) -> Optional[Tuple[int, int, int]]:
    """(rating, games, votes), or None when the file has no rating row."""
    row = conn.execute("SELECT rating, votes FROM file_ratings WHERE path = ?", (path,)).fetchone()
    return (int(row[0]), _games(conn, path), int(row[1] or 0)) if row else None


def record_comparison(db, path_a: str, path_b: str, winner: str, scope: Optional[str] = None) -> Dict[str, Any]:
    """
    Apply one comparison: both ratings, both game counts and the history row in one
    transaction (the write lock is taken before the ratings are read). Returns the row.
    """
    if path_a == path_b:
        raise ValueError("a photo cannot be compared with itself")
    if winner not in ("a", "b"):
        raise ValueError("winner must be 'a' or 'b'")
    now = time.time()
    with db._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        before_a, before_b = _rating_row(conn, path_a), _rating_row(conn, path_b)
        rating_a, games_a, _ = before_a or (RATING_START, _games(conn, path_a), 0)
        rating_b, games_b, _ = before_b or (RATING_START, _games(conn, path_b), 0)
        new_a, new_b, k_a, k_b = elo_update(rating_a, rating_b, winner, games_a, games_b)
        for path, rating in ((path_a, new_a), (path_b, new_b)):
            conn.execute("""
                INSERT INTO file_ratings (path, rating, votes, updated_at)
                VALUES (?, ?, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET rating = excluded.rating, updated_at = CURRENT_TIMESTAMP
            """, (path, rating))
        cur = conn.execute("""
            INSERT INTO elo_comparisons (path_a, path_b, winner, rating_a_before, rating_a_after,
                rating_b_before, rating_b_after, games_a_before, games_b_before, existed_a, existed_b,
                k_a, k_b, scope, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (path_a, path_b, winner, rating_a, new_a, rating_b, new_b, games_a, games_b,
              int(before_a is not None), int(before_b is not None), k_a, k_b, scope, now))
        comparison_id = cur.lastrowid
    return {
        "id": comparison_id, "winner": winner, "scope": scope, "created_at": now,
        "a": {"path": path_a, "before": rating_a, "rating": new_a, "games": games_a + 1, "k": k_a},
        "b": {"path": path_b, "before": rating_b, "rating": new_b, "games": games_b + 1, "k": k_b},
    }


_COMPARISON_COLUMNS = ("id", "path_a", "path_b", "winner", "rating_a_before", "rating_a_after",
                       "rating_b_before", "rating_b_after", "games_a_before", "games_b_before",
                       "existed_a", "existed_b", "k_a", "k_b", "scope", "created_at")


def _comparison(conn, comparison_id: Optional[int], scope: Optional[str]) -> Optional[Dict[str, Any]]:
    columns = ", ".join(_COMPARISON_COLUMNS)
    if comparison_id is not None:
        row = conn.execute(f"SELECT {columns} FROM elo_comparisons WHERE id = ?", (comparison_id,)).fetchone()
    elif scope is not None:
        row = conn.execute(f"SELECT {columns} FROM elo_comparisons WHERE scope = ? ORDER BY id DESC LIMIT 1",
                           (scope,)).fetchone()
    else:
        row = conn.execute(f"SELECT {columns} FROM elo_comparisons ORDER BY id DESC LIMIT 1").fetchone()
    return dict(zip(_COMPARISON_COLUMNS, row)) if row else None


def last_comparison(db, scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        return _comparison(conn, None, scope)


def undo_comparison(db, comparison_id: Optional[int] = None, scope: Optional[str] = None) -> Dict[str, Any]:
    """
    Take back one comparison (by id, else the newest one in scope, else the newest one):
    both ratings and game counts go back to exactly what they were before it, and its
    history row goes. A file_ratings row the comparison made is removed again. Refused
    (ComparisonConflict) when either photo's rating or game count changed since; raises
    LookupError when there is nothing to undo.
    """
    with db._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        comp = _comparison(conn, comparison_id, scope)
        if comp is None:
            raise LookupError("Er is geen vergelijking om terug te draaien")
        sides = []
        for side in ("a", "b"):
            path = comp[f"path_{side}"]
            now = _rating_row(conn, path)
            expected = (comp[f"rating_{side}_after"], comp[f"games_{side}_before"] + 1)
            if now is None or (now[0], now[1]) != expected:
                raise ComparisonConflict(
                    f"Deze vergelijking kan niet meer precies terug: de score van {Path(path).name} "
                    "is daarna nog veranderd (door een stem of een latere vergelijking).")
            sides.append((side, path, now))
        for side, path, (_, _, votes) in sides:
            if not comp[f"existed_{side}"] and votes == 0:
                conn.execute("DELETE FROM file_ratings WHERE path = ?", (path,))
            else:
                conn.execute("UPDATE file_ratings SET rating = ?, updated_at = CURRENT_TIMESTAMP WHERE path = ?",
                             (comp[f"rating_{side}_before"], path))
        conn.execute("DELETE FROM elo_comparisons WHERE id = ?", (comp["id"],))
    return {
        "id": comp["id"], "scope": comp["scope"],
        "a": {"path": comp["path_a"], "rating": comp["rating_a_before"], "games": comp["games_a_before"]},
        "b": {"path": comp["path_b"], "rating": comp["rating_b_before"], "games": comp["games_b_before"]},
    }


# ==================== WHICH PHOTOS, WHICH PAIR ====================

def _prefix_range(folder: str) -> Tuple[str, str]:
    prefix = folder.rstrip("/") + "/"
    return prefix, prefix[:-1] + "0"   # '0' is the character right after '/'


def ratings_below(db, folder: str) -> Dict[str, Tuple[int, int, int]]:
    """{path: (rating, games, votes)} for every judged file anywhere below folder."""
    low, high = _prefix_range(folder)
    with db._connect() as conn:
        rows = conn.execute("SELECT path, rating, votes FROM file_ratings WHERE path >= ? AND path < ?",
                            (low, high)).fetchall()
        games: Dict[str, int] = {}
        for column in ("path_a", "path_b"):
            for path, count in conn.execute(
                    f"SELECT {column}, COUNT(*) FROM elo_comparisons WHERE {column} >= ? AND {column} < ? "
                    f"GROUP BY {column}", (low, high)):
                games[path] = games.get(path, 0) + count
    return {path: (int(rating), games.get(path, 0), int(votes or 0)) for path, rating, votes in rows}


def comparisons_in_scope(db, scope: str) -> int:
    with db._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM elo_comparisons WHERE scope = ?", (scope,)).fetchone()[0]


def wins_of(db, paths: Sequence[str]) -> Dict[str, int]:
    """{path: comparisons won} for these paths."""
    wins: Dict[str, int] = {}
    with db._connect() as conn:
        for start in range(0, len(paths), 400):
            chunk = list(paths[start:start + 400])
            marks = ",".join("?" * len(chunk))
            for path, count in conn.execute(
                    f"SELECT path_a, COUNT(*) FROM elo_comparisons WHERE winner = 'a' AND path_a IN ({marks}) "
                    f"GROUP BY path_a UNION ALL "
                    f"SELECT path_b, COUNT(*) FROM elo_comparisons WHERE winner = 'b' AND path_b IN ({marks}) "
                    f"GROUP BY path_b", chunk + chunk):
                wins[path] = wins.get(path, 0) + count
    return wins


def is_elo_photo(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in ELO_EXTENSIONS


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
    registered check finds."""
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


def choose_pair(candidates: Sequence[Dict[str, Any]], avoid: Iterable[Iterable[str]] = (),
                near_dup: Callable[[Dict[str, Any], Dict[str, Any]], bool] = near_duplicate,
                rng: Optional[random.Random] = None) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    Two candidates to compare (each a dict with at least path, rating and games), in random
    left/right order, or None when no pair is allowed. avoid: pairs (of paths) that must not
    come now (the pair just shown or compared). Candidates without a 'series' key get one
    (mark_series); pass them marked to save that work.
    """
    rng = rng or random
    if len(candidates) < 2:
        return None
    if any("series" not in c for c in candidates):
        mark_series(candidates)
    avoid_set = {frozenset(pair) for pair in avoid}
    by_rating = sorted(candidates, key=lambda c: (c["rating"], c["path"]))
    ratings = [c["rating"] for c in by_rating]
    weights = [1.0 / (1 + c["games"]) ** 2 for c in candidates]
    tried = set()
    for _ in range(min(PAIR_ATTEMPTS, len(candidates)) * 2):
        if len(tried) >= len(candidates):
            break
        first = rng.choices(candidates, weights)[0]
        if first["path"] in tried:
            continue
        tried.add(first["path"])
        options = _neighbours(first, by_rating, ratings, avoid_set, near_dup, rng)
        if options:
            second = rng.choices(options, [1.0 / (1 + c["games"]) for c in options])[0]
            return (first, second) if rng.random() < 0.5 else (second, first)
    return None


def _neighbours(first, by_rating, ratings, avoid_set, near_dup, rng=random) -> List[Dict[str, Any]]:
    """The NEIGHBOURS allowed candidates nearest to first in rating, walking out both ways
    from a random place among the photos with first's rating (by_rating is sorted by rating
    and path, so starting at the edge of that block would always give the same, alphabetically
    first, photos). An equal distance on both sides is a coin toss."""
    low = bisect.bisect_left(ratings, first["rating"])
    high = bisect.bisect_right(ratings, first["rating"])
    at = rng.randint(low, high)
    left, right = at - 1, at
    options: List[Dict[str, Any]] = []
    while len(options) < NEIGHBOURS and (left >= 0 or right < len(by_rating)):
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
        if near_dup(first, cand):
            continue
        options.append(cand)
    return options


def still_there(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False
