"""
'Welke is mooier?' in Shows: Elo ratings from side-by-side comparisons (backend/elo.py).

- The math: expected score, a symmetric update with equal K, K 32 -> 16 after 30 games,
  the floor of 100, and never more than K per comparison.
- Both ratings and the history row change in one transaction: comparisons that arrive at
  the same moment are applied one after the other.
- An undo puts both ratings and game counts back exactly, also after a move, and refuses
  when something changed the photos since.
- Pairs: few comparisons first, near in rating, never the same pair twice in a row, never
  copies or bursts of each other, only within the chosen folder.
- Migration 50 keeps every existing row.
"""
import random
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from backend import elo
from backend.database import Database

ROME = "library/Reis Italië/rome.jpg"
VENICE = "library/Reis Italië/venetië.jpg"
OPA = "library/Familie/opa.jpg"


def _row(db, path):
    """(rating, games, votes); games is counted from the comparison history."""
    with db._connect() as conn:
        return elo._rating_row(conn, str(path))


# ==================== THE MATH ====================

def test_expected_score_follows_the_elo_formula():
    assert elo.expected_score(1000, 1000) == pytest.approx(0.5)
    assert elo.expected_score(1400, 1000) == pytest.approx(1 / (1 + 10 ** (-1)))   # 0.909
    assert elo.expected_score(1000, 1400) == pytest.approx(1 / (1 + 10 ** 1))      # 0.091
    for a, b in ((1000, 1200), (1600, 900), (100, 2500)):
        assert elo.expected_score(a, b) + elo.expected_score(b, a) == pytest.approx(1.0)


def test_k_factor_drops_from_32_to_16_after_30_comparisons():
    assert [elo.k_factor(g) for g in (0, 1, 29)] == [32, 32, 32]
    assert [elo.k_factor(g) for g in (30, 31, 500)] == [16, 16, 16]
    assert (elo.K_NEW, elo.K_SETTLED, elo.K_SETTLE_GAMES, elo.RATING_START, elo.RATING_FLOOR) == (32, 16, 30, 1000, 100)


def test_equal_ratings_move_by_half_k_and_the_update_is_symmetric():
    assert elo.elo_update(1000, 1000, "a") == (1016, 984, 32, 32)
    assert elo.elo_update(1000, 1000, "b") == (984, 1016, 32, 32)
    assert elo.elo_update(1000, 1000, "a", 30, 45) == (1008, 992, 16, 16)
    rng = random.Random(7)
    for _ in range(2000):
        a, b = rng.randint(300, 2500), rng.randint(300, 2500)
        games = rng.choice((0, 10, 40))
        winner = rng.choice("ab")
        new_a, new_b, _, _ = elo.elo_update(a, b, winner, games, games)
        assert (new_a - a) == -(new_b - b), (a, b, winner)
        assert (new_a > a) == (winner == "a") or new_a == a
        assert abs(new_a - a) <= elo.k_factor(games)


def test_an_upset_moves_more_than_an_expected_win():
    favourite, underdog = 1400, 1000
    expected_win = elo.elo_update(favourite, underdog, "a")[0] - favourite
    upset = elo.elo_update(favourite, underdog, "b")[1] - underdog
    assert expected_win == round(32 * (1 - elo.expected_score(1400, 1000))) == 3
    assert upset == round(32 * (1 - elo.expected_score(1000, 1400))) == 29


def test_each_side_uses_its_own_k():
    # A new photo (K 32) beats a settled one (K 16) of the same rating
    assert elo.elo_update(1000, 1000, "a", 0, 50) == (1016, 992, 32, 16)


def test_the_rating_never_drops_below_the_floor():
    assert elo.elo_update(100, 100, "b") == (100, 116, 32, 32)
    assert elo.elo_update(105, 105, "b")[0] == 100           # 105 - 16 stops at the floor
    assert elo.elo_update(110, 1000, "b")[0] == 110          # an expected loss: round(32 * -0.006) = 0
    with pytest.raises(ValueError):
        elo.elo_update(1000, 1000, "c")


# ==================== RECORDING, ATOMICITY, UNDO ====================

def test_a_comparison_updates_both_ratings_and_keeps_the_history(client, app, vault):
    db = app.state.db
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    r = client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "a"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["a"] == {"path": str(rome), "before": 1000, "rating": 1016, "games": 1, "k": 32}
    assert body["b"] == {"path": str(venice), "before": 1000, "rating": 984, "games": 1, "k": 32}
    assert body["scope"] == str((vault / "library").resolve())
    assert tuple(_row(db, rome)) == (1016, 1, 0) and tuple(_row(db, venice)) == (984, 1, 0)
    with db._connect() as conn:
        row = conn.execute("SELECT path_a, path_b, winner, rating_a_before, rating_a_after, rating_b_before, "
                           "rating_b_after, existed_a, existed_b FROM elo_comparisons").fetchone()
    assert row == (str(rome), str(venice), "a", 1000, 1016, 1000, 984, 0, 0)


def test_comparisons_and_votes_share_one_scale(client, app, vault):
    db = app.state.db
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    assert client.post("/api/shows/vote", json={"path": str(rome), "vote": "up"}).json()["new_rating"] == 1050
    body = client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "b"}).json()
    # 1050 against 1000: Rome was expected to win (E 0.57), so it loses round(32 * 0.57) = 18
    assert body["a"]["rating"] == 1050 - round(32 * elo.expected_score(1050, 1000)) == 1032
    assert body["b"]["rating"] == 1000 + round(32 * elo.expected_score(1050, 1000)) == 1018
    assert tuple(_row(db, rome)) == (1032, 1, 1)
    # An up-vote after a comparison still adds 50, and the vote counter is untouched by comparisons
    assert client.post("/api/shows/vote", json={"path": str(venice), "vote": "up"}).json() == {
        "status": "ok", "new_rating": 1068, "votes": 1}


def test_bad_comparisons_are_refused(client, app, vault):
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    for body, code in (
        ({"a": str(rome), "b": str(venice), "winner": "c"}, 400),
        ({"a": str(rome), "b": str(rome), "winner": "a"}, 400),
        ({"a": str(rome), "b": str(rome.parent / "weg.jpg"), "winner": "a"}, 404),
        ({"a": str(rome), "b": str(rome.parent), "winner": "a"}, 404),
        ({"a": str(rome), "b": "/etc/passwd", "winner": "a"}, 403),
        ({"a": str(rome), "b": str(vault / "RAW/audio/toon 🎵.mp3"), "winner": "a"}, 404),
        ({"a": str(rome), "b": str(venice), "winner": "a", "folder": "/etc"}, 403),
    ):
        assert client.post("/api/shows/compare", json=body).status_code == code, body
    with app.state.db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM elo_comparisons").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM file_ratings").fetchone()[0] == 0


def test_concurrent_comparisons_are_applied_one_after_the_other(client, app):
    """16 threads compare the same two photos at once: every history row must start from the
    ratings the previous one ended with (no two read the same old ratings), and the final
    ratings and game counts must equal a replay of the history one by one."""
    db = app.state.db
    a, b = "/v/race/a.jpg", "/v/race/b.jpg"
    barrier = threading.Barrier(16)
    errors = []

    def worker(n):
        try:
            barrier.wait()
            for i in range(8):
                elo.record_comparison(db, a, b, "a" if (n + i) % 3 else "b", "/v/race")
        except Exception as e:   # pragma: no cover - shown in the assert below
            errors.append(e)
        finally:
            db.release_thread()
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with db._connect() as conn:
        history = conn.execute("SELECT winner, rating_a_before, rating_a_after, rating_b_before, rating_b_after, "
                               "games_a_before, games_b_before FROM elo_comparisons ORDER BY id").fetchall()
    assert len(history) == 128
    rating_a = rating_b = 1000
    for games, (winner, a_before, a_after, b_before, b_after, ga, gb) in enumerate(history):
        assert (a_before, b_before, ga, gb) == (rating_a, rating_b, games, games)
        rating_a, rating_b, _, _ = elo.elo_update(rating_a, rating_b, winner, games, games)
        assert (a_after, b_after) == (rating_a, rating_b)
    assert tuple(_row(db, a))[:2] == (rating_a, 128) and tuple(_row(db, b))[:2] == (rating_b, 128)


def test_a_failure_halfway_changes_nothing(client, app, monkeypatch):
    db = app.state.db
    db.apply_vote("/v/x/a.jpg", 50)
    real = elo.elo_update

    def broken(*args, **kw):
        real(*args, **kw)
        raise RuntimeError("boom")
    monkeypatch.setattr(elo, "elo_update", broken)
    with pytest.raises(RuntimeError):
        elo.record_comparison(db, "/v/x/a.jpg", "/v/x/b.jpg", "a")
    assert tuple(_row(db, "/v/x/a.jpg")) == (1050, 0, 1)
    assert _row(db, "/v/x/b.jpg") is None
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM elo_comparisons").fetchone()[0] == 0


def test_undo_restores_both_ratings_exactly(client, app, vault):
    db = app.state.db
    rome, venice, opa = ((vault / p).resolve() for p in (ROME, VENICE, OPA))
    # Venice already had a rating; Rome and Opa had none
    client.post("/api/shows/vote", json={"path": str(venice), "vote": "down"})
    first = client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "b"}).json()
    after_first = (tuple(_row(db, rome)), tuple(_row(db, venice)))
    second = client.post("/api/shows/compare", json={"a": str(opa), "b": str(rome), "winner": "a"}).json()

    r = client.post("/api/shows/compare/undo", json={"id": second["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["a"] == {"path": str(opa), "rating": 1000, "games": 0}
    assert (tuple(_row(db, rome)), tuple(_row(db, venice))) == after_first
    assert _row(db, opa) is None        # the row the comparison made is gone again

    # Undo without an id: the newest one in the scope (the library)
    r = client.post("/api/shows/compare/undo", json={})
    assert r.status_code == 200 and r.json()["id"] == first["id"]
    assert _row(db, rome) is None
    assert tuple(_row(db, venice)) == (950, 0, 1)
    assert client.post("/api/shows/compare/undo", json={}).status_code == 404
    assert client.post("/api/shows/compare/undo", json={"id": first["id"]}).status_code == 404
    assert client.post("/api/shows/compare/undo", json={"id": "1"}).status_code == 400
    # Ids that cannot be a row: refused, never a 500 (sqlite cannot take a number this big)
    for bad in (99999999999999999999999, 2 ** 63, 0, -1, True):
        r = client.post("/api/shows/compare/undo", json={"id": bad})
        assert r.status_code == 400 and r.json()["detail"] == "Ongeldige vergelijking", (bad, r.text)


def test_undo_is_refused_when_a_photo_changed_since(client, app, vault):
    db = app.state.db
    rome, venice, opa = ((vault / p).resolve() for p in (ROME, VENICE, OPA))
    first = client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "a"}).json()
    client.post("/api/shows/compare", json={"a": str(rome), "b": str(opa), "winner": "a"})
    r = client.post("/api/shows/compare/undo", json={"id": first["id"]})
    assert r.status_code == 409 and "rome.jpg" in r.json()["detail"]
    # A vote counts as a change too
    third = client.post("/api/shows/compare", json={"a": str(venice), "b": str(opa), "winner": "b"}).json()
    client.post("/api/shows/vote", json={"path": str(opa), "vote": "up"})
    assert client.post("/api/shows/compare/undo", json={"id": third["id"]}).status_code == 409
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM elo_comparisons").fetchone()[0] == 3


def test_ratings_and_history_follow_a_move_and_undo_still_works(client, app, vault):
    db = app.state.db
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    comp = client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "a"}).json()
    r = client.post("/api/move-file", json={"source_path": str(rome),
                                            "destination_folder": str((vault / "library/Familie").resolve())})
    assert r.status_code == 200, r.text
    moved = r.json()["new_path"]
    assert _row(db, rome) is None and tuple(_row(db, moved)) == (1016, 1, 0)
    with db._connect() as conn:
        assert conn.execute("SELECT path_a FROM elo_comparisons").fetchone()[0] == moved
    board = client.get("/api/shows/leaderboard").json()
    assert [i["path"] for i in board["items"]] == [moved, str(venice)]
    assert client.post("/api/shows/compare/undo", json={"id": comp["id"]}).status_code == 200
    assert _row(db, moved) is None and _row(db, venice) is None


# ==================== PAIRS ====================

def _cands(n, rating=1000, games=0):
    return [{"path": f"/v/p/{i:03d}.jpg", "rating": rating, "games": games, "size": 1000 + i,
             "taken": None, "taken_source": None} for i in range(n)]


def test_pairs_prefer_photos_with_few_comparisons():
    cands = _cands(40, games=60)
    for c in cands[:4]:
        c["games"] = 0
    fresh = {c["path"] for c in cands[:4]}
    rng = random.Random(1)
    hits = sum(bool(fresh & {a["path"], b["path"]}) for a, b in (elo.choose_pair(cands, rng=rng) for _ in range(300)))
    assert hits > 270     # 4 of 40 photos, yet in (nearly) every pair


def test_the_opponent_has_a_similar_rating():
    cands = _cands(200)
    for i, c in enumerate(cands):
        c["rating"] = 400 + i * 10          # 400 .. 2390
    rng = random.Random(2)
    for _ in range(300):
        a, b = elo.choose_pair(cands, rng=rng)
        assert abs(a["rating"] - b["rating"]) <= elo.NEIGHBOURS * 10


def test_never_the_same_pair_twice_in_a_row():
    cands = _cands(3)
    rng = random.Random(3)
    prev = None
    for _ in range(200):
        pair = elo.choose_pair(cands, avoid=[prev] if prev else [], rng=rng)
        key = {pair[0]["path"], pair[1]["path"]}
        assert key != prev
        prev = key
    # Two photos and the pair just shown: no pair at all
    assert elo.choose_pair(_cands(2), avoid=[[c["path"] for c in _cands(2)]]) is None
    assert elo.choose_pair(_cands(1)) is None


def test_copies_and_bursts_are_never_paired():
    cands = _cands(10)
    cands[1]["size"] = cands[0]["size"]                     # an exact copy of 0
    burst = cands[2:7]                                      # a series of 5 frames, 1 s apart
    for i, c in enumerate(burst):
        c.update(taken=100.0 + i, taken_source="exif")
    cands[7].update(taken=101.0, taken_source="mtime")      # file dates do not count
    cands[8].update(taken=107.5, taken_source="exif")       # 3.5 s after the last frame: its own photo
    cands[9].update(taken=102.0, taken_source="video")
    cands[9]["path"] = "/v/q/clip.jpg"                      # same moment, other folder
    assert elo.near_duplicate(cands[0], cands[1])
    # Without the series marks only neighbours count; with them the whole chain is one series
    assert not elo.near_duplicate(burst[0], burst[4])
    elo.mark_series(cands)
    assert len({c["series"] for c in burst}) == 1 and burst[0]["series"] is not None
    assert all(elo.near_duplicate(a, b) for a in burst for b in burst if a is not b)
    assert cands[7]["series"] is None and cands[8]["series"] is None and cands[9]["series"] is None
    assert not elo.near_duplicate(burst[0], cands[7])
    assert not elo.near_duplicate(burst[4], cands[8])
    assert not elo.near_duplicate(burst[2], cands[9])
    rng = random.Random(4)
    pairs = set()
    for _ in range(400):
        a, b = elo.choose_pair(cands, rng=rng)
        assert not elo.near_duplicate(a, b)
        assert not (a in burst and b in burst), (a["path"], b["path"])
        pairs.add(frozenset((a["path"], b["path"])))
    assert len(pairs) > 20
    # Unmarked candidates are marked by choose_pair itself
    fresh = _cands(5)
    for i, c in enumerate(fresh):
        c.update(taken=50.0 + i, taken_source="exif")
    assert elo.choose_pair(fresh, rng=rng) is None
    assert elo.choose_pair(cands[:2]) is None


def test_with_equal_ratings_the_opponents_come_from_the_whole_list():
    # A fresh library: every photo at 1000. The opponent must not always be one of the
    # alphabetically first photos (it was: by_rating is sorted by rating, then path).
    cands = _cands(400)
    rng = random.Random(7)
    seen = {}
    for _ in range(2000):
        for c in elo.choose_pair(cands, rng=rng):
            seen[c["path"]] = seen.get(c["path"], 0) + 1
    assert len(seen) > 380
    first_eight = sum(seen.get(c["path"], 0) for c in cands[:8])
    assert first_eight < 4000 * 8 / 400 * 3, first_eight          # at most 3x their fair share
    assert max(seen.values()) < 40

    # Twenty folders, with Elo updates as a user would make them: no folder takes over
    cands = [{"path": f"/v/{2000 + i // 50}/{i % 50:03d}.jpg", "rating": 1000, "games": 0,
              "size": 5000 + i, "taken": None, "taken_source": None} for i in range(1000)]
    rng = random.Random(8)
    per_folder = {}
    for _ in range(300):
        a, b = elo.choose_pair(cands, rng=rng)
        for c in (a, b):
            folder = c["path"].split("/")[2]
            per_folder[folder] = per_folder.get(folder, 0) + 1
        new_a, new_b, _, _ = elo.elo_update(a["rating"], b["rating"], "a", a["games"], b["games"])
        a.update(rating=new_a, games=a["games"] + 1)
        b.update(rating=new_b, games=b["games"] + 1)
    assert len(per_folder) == 20
    assert max(per_folder.values()) < 600 * 3 / 20, per_folder    # under 3x the fair share


def test_a_registered_collection_is_a_scope_of_its_own(client, app, vault, monkeypatch):
    lib = (vault / "library").resolve()
    rome, venice, opa = str(lib / "Reis Italië/rome.jpg"), str(lib / "Reis Italië/venetië.jpg"), str(lib / "Familie/opa.jpg")
    monkeypatch.setattr(elo, "_scopes", {})
    albums = {"7": {"title": "Mooiste", "paths": [rome, opa, "/etc/passwd", str(lib / "Familie")]}}
    elo.register_scope("album", lambda value: albums.get(value))
    assert elo.scope_kinds() == ["album"]
    with pytest.raises(ValueError):
        elo.register_scope("al bum", lambda value: None)

    body = client.get("/api/shows/compare/pair", params={"scope": "album:7"}).json()
    assert body["scope"] == {"key": "album:7", "path": None, "title": "Mooiste"}
    assert body["stats"] == {"photos": 2, "compared": 0, "comparisons": 0}
    assert {p["path"] for p in body["pair"]} == {rome, opa}
    r = client.post("/api/shows/compare", json={"a": rome, "b": opa, "winner": "b", "scope": "album:7"})
    assert r.status_code == 200 and r.json()["scope"] == "album:7"
    board = client.get("/api/shows/leaderboard", params={"scope": "album:7"}).json()
    assert [i["path"] for i in board["items"]] == [opa, rome] and board["scope"]["key"] == "album:7"
    client.post("/api/shows/compare", json={"a": venice, "b": str(lib / "xss <img src=x onerror=window.__xss=1>.jpg"),
                                            "winner": "a"})   # in the library, newer
    undone = client.post("/api/shows/compare/undo", json={"scope": "album:7"}).json()
    assert undone["scope"] == "album:7" and undone["stats"] == {"photos": 2, "compared": 0, "comparisons": 0}
    for bad, status in (("album:8", 404), ("map:1", 404), ("album", 400)):
        assert client.get("/api/shows/compare/pair", params={"scope": bad}).status_code == status, bad


def test_the_undo_answers_the_progress_of_its_scope(client, app, vault):
    italy = (vault / "library/Reis Italië").resolve()
    rome, venice = str(italy / "rome.jpg"), str(italy / "venetië.jpg")
    comp = client.post("/api/shows/compare", json={"a": rome, "b": venice, "winner": "a", "folder": str(italy)}).json()
    undone = client.post("/api/shows/compare/undo", json={"id": comp["id"]}).json()
    assert undone["stats"] == {"photos": 2, "compared": 0, "comparisons": 0}


def test_the_frames_of_a_burst_are_never_shown_side_by_side(client, app, vault):
    from test_media_index import _index
    from fixtures.vault_builder import BURST
    _index(client, full=False)           # the index knows the EXIF capture times
    folder = (vault / BURST[0]).resolve().parent
    frames = {(vault / rel).resolve().name for rel in BURST}
    seen = set()
    for _ in range(60):
        pair = client.get("/api/shows/compare/pair", params={"folder": str(folder)}).json()["pair"]
        names = {p["name"] for p in pair}
        assert len(names & frames) <= 1, names
        seen |= names
    assert seen - frames                 # the other photo in that folder does come


def test_a_registered_check_also_rules_pairs_out(monkeypatch):
    monkeypatch.setattr(elo, "_near_duplicate_checks", [])
    elo.register_near_duplicate_check(lambda a, b: {a["path"][-7:], b["path"][-7:]} == {"000.jpg", "001.jpg"})
    cands = _cands(3)
    rng = random.Random(5)
    for _ in range(100):
        a, b = elo.choose_pair(cands, rng=rng)
        assert {a["path"], b["path"]} != {cands[0]["path"], cands[1]["path"]}


def test_pair_api_stays_in_the_scope_and_skips_the_last_pair(client, app, vault):
    italy = (vault / "library/Reis Italië").resolve()
    r = client.get("/api/shows/compare/pair", params={"folder": str(italy)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == {"key": str(italy), "path": str(italy), "title": "Reis Italië"}
    assert {p["name"] for p in body["pair"]} == {"rome.jpg", "venetië.jpg"}
    assert body["pair"][0]["rating"] == 1000 and body["stats"] == {"photos": 2, "compared": 0, "comparisons": 0}
    a, b = (p["path"] for p in body["pair"])
    # Skipped (prev) or just compared: two photos give no new pair
    assert client.get("/api/shows/compare/pair", params={"folder": str(italy), "prev": [a, b]}).json()["reason"] == "no_pair"
    client.post("/api/shows/compare", json={"a": a, "b": b, "winner": "a", "folder": str(italy)})
    again = client.get("/api/shows/compare/pair", params={"folder": str(italy)}).json()
    assert again["pair"] is None and again["reason"] == "no_pair"
    assert again["stats"] == {"photos": 2, "compared": 2, "comparisons": 1}
    # The whole library has more photos; one photo alone is too few
    lib = client.get("/api/shows/compare/pair").json()
    assert lib["scope"]["title"] == "Hele bibliotheek" and lib["stats"]["photos"] == 4 and lib["pair"]
    assert client.get("/api/shows/compare/pair", params={"folder": str(vault / "library/Familie")}).json()["reason"] == "too_few"
    assert client.get("/api/shows/compare/pair", params={"folder": "/etc"}).status_code == 403


def test_disabled_show_folders_and_vanished_files_do_not_take_part(client, app, vault):
    from backend.routes import shows
    lib = (vault / "library").resolve()
    client.post("/api/shows/settings", json={"path": str(lib / "Familie"), "enabled": False})
    shows.forget_candidates()
    assert client.get("/api/shows/compare/pair").json()["stats"]["photos"] == 3
    # A photo that is gone (removed outside the app) is never offered
    shutil.move(str(lib / "Reis Italië/rome.jpg"), str(vault.parent / "rome-elders.jpg"))
    for _ in range(10):
        pair = client.get("/api/shows/compare/pair").json()["pair"]
        assert all(p["name"] != "rome.jpg" for p in pair)


def test_pairs_come_from_the_index_once_it_is_there(client, app, vault, monkeypatch):
    from test_media_index import _index
    from backend.routes import shows
    lib = (vault / "library").resolve()
    shutil.copy2(lib / "Familie/opa.jpg", lib / "Familie/opa kopie.jpg")   # an exact copy
    _index(client, full=False)
    monkeypatch.setattr(shows, "collect_show_files", lambda *a: pytest.fail("walked although the index is there"))
    seen = set()
    for _ in range(60):
        pair = client.get("/api/shows/compare/pair").json()["pair"]
        names = {p["name"] for p in pair}
        assert names != {"opa.jpg", "opa kopie.jpg"}
        seen |= names
    assert seen == {"rome.jpg", "venetië.jpg", "opa.jpg", "opa kopie.jpg", "xss <img src=x onerror=window.__xss=1>.jpg"}


def test_a_folder_made_after_the_last_index_pass_still_has_its_photos(client, app, vault, monkeypatch):
    from test_media_index import _index
    from backend import media_index
    _index(client, full=False)
    new = (vault / "library/Nieuw na de index").resolve()
    new.mkdir()
    for name, extra in (("a.jpg", b"1"), ("b.jpg", b"22")):
        (new / name).write_bytes((vault / OPA).read_bytes() + extra)   # distinct sizes: no copies
    asked = []
    monkeypatch.setattr(media_index, "request_update", lambda: asked.append(1))
    body = client.get("/api/shows/compare/pair", params={"folder": str(new)}).json()
    assert body["pair"] and {p["name"] for p in body["pair"]} == {"a.jpg", "b.jpg"}
    assert asked                         # and the index is asked to catch up


def test_leaderboard_ranks_by_rating_with_wins(client, app, vault):
    rome, venice, opa = ((vault / p).resolve() for p in (ROME, VENICE, OPA))
    client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "a"})
    client.post("/api/shows/compare", json={"a": str(opa), "b": str(rome), "winner": "b"})
    board = client.get("/api/shows/leaderboard").json()
    assert board["total"] == 3
    assert [(i["rank"], i["name"], i["games"], i["wins"]) for i in board["items"]] == [
        (1, "rome.jpg", 2, 2), (2, "opa.jpg", 1, 0), (3, "venetië.jpg", 1, 0)]
    assert board["items"][0]["rating"] > board["items"][1]["rating"] > board["items"][2]["rating"]
    italy = client.get("/api/shows/leaderboard", params={"folder": str(rome.parent), "limit": 1}).json()
    assert italy["total"] == 2 and [i["name"] for i in italy["items"]] == ["rome.jpg"]
    assert client.get("/api/shows/leaderboard", params={"limit": 0}).status_code == 422


# ==================== MIGRATION ====================

def test_migration_50_adds_the_comparison_history_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
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
                     "VALUES ('index', '{}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO media (path, folder, name, kind, ext, size, mtime_ns, added_at, indexed_at, seen_at) "
                     "VALUES ('/v/RAW/a.jpg', '/v/RAW', 'a.jpg', 'photo', '.jpg', 10, 1, 1, 1, 1)")
        conn.execute("INSERT INTO recovery_actions (kind, title, created_at) VALUES ('viewer_move', 'x', 1)")
        conn.execute("INSERT INTO recovery_moves (action_id, src_path, dst_path, moved_at) VALUES (1, '/v/a', '/v/b', 1)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    assert before["sqlite_sequence"] and before["file_ratings"] and before["recovery_moves"]

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 50
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        assert {r[1] for r in conn.execute("PRAGMA table_info(elo_comparisons)")} >= {
            "path_a", "path_b", "winner", "rating_a_before", "rating_a_after", "rating_b_before",
            "rating_b_after", "games_a_before", "games_b_before", "created_at"}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v12-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    assert db.get_recovery_moves(1)[0]["dst_path"] == "/v/b"
    # The old rating takes part in comparisons from where it was
    result = elo.record_comparison(db, "/v/RAW/a.jpg", "/v/RAW/c.jpg", "b")
    assert result["a"]["before"] == 1150 and result["a"]["games"] == 1
    assert elo.undo_comparison(db, result["id"])["a"]["rating"] == 1150
    assert db.get_rating("/v/RAW/a.jpg") == 1150 and db.get_rating("/v/RAW/c.jpg") == 1000
    db.close_all()
