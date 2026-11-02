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

FRESH = (1000.0, elo.RD_START, elo.VOL_START)


def test_expected_score_is_the_elo_curve_widened_by_uncertainty():
    assert elo.expected_score(1000, 1000) == pytest.approx(0.5)
    assert elo.expected_score(1400, 1000) == pytest.approx(1 / (1 + 10 ** (-1)), abs=1e-4)   # 0.909 (rd 0)
    for a, b in ((1000, 1200), (1600, 900), (100, 2500)):
        assert elo.expected_score(a, b) + elo.expected_score(b, a) == pytest.approx(1.0)
    # An uncertain opponent pulls the expectation towards a coin flip
    assert 0.5 < elo.expected_score(1400, 1000, 350) < elo.expected_score(1400, 1000, 50) < 0.91


def test_glicko2_matches_the_reference_step():
    """Glickman's worked example (1500/200/0.06 against 1400/30, 1550/100, 1700/300 in one
    period gives 1464.06 / 151.52 / 0.05999): our one-game update applies the same formulas,
    so playing that period game by game with the same pre-period state for every game and
    combining v and delta must give the same numbers. Checked here through the parts."""
    import math
    scale = elo.GLICKO_SCALE
    mu, phi, sigma = 0.0, 200 / scale, 0.06
    games = [((1400 - 1500) / scale, 30 / scale, 1), ((1550 - 1500) / scale, 100 / scale, 0),
             ((1700 - 1500) / scale, 300 / scale, 0)]
    v_inv, delta_sum = 0.0, 0.0
    for mu_j, phi_j, s in games:
        g = elo._g(phi_j)
        e = 1 / (1 + math.exp(-g * (mu - mu_j)))
        v_inv += g * g * e * (1 - e)
        delta_sum += g * (s - e)
    v = 1 / v_inv
    new_sigma = elo._new_volatility(phi, sigma, v * delta_sum, v)
    phi_star = math.sqrt(phi ** 2 + new_sigma ** 2)
    new_phi = 1 / math.sqrt(1 / phi_star ** 2 + 1 / v)
    new_mu = mu + new_phi ** 2 * delta_sum
    assert 1500 + new_mu * scale == pytest.approx(1464.06, abs=0.01)
    assert new_phi * scale == pytest.approx(151.52, abs=0.01)
    assert new_sigma == pytest.approx(0.05999, abs=1e-5)


def test_two_new_photos_move_far_and_symmetrically():
    (a, rd_a, _), (b, rd_b, _) = elo.glicko_pair(FRESH, FRESH, "a")
    assert a == pytest.approx(1162.3, abs=0.1) and b == pytest.approx(837.7, abs=0.1)
    assert a - 1000 == pytest.approx(1000 - b) and rd_a == rd_b < elo.RD_START
    # The old Elo moved them 16 points; a new photo now finds its level in a few comparisons
    assert a - 1000 > 10 * 16


def test_a_draw_between_equals_changes_no_rating_but_adds_certainty():
    (a, rd_a, _), (b, rd_b, _) = elo.glicko_pair(FRESH, FRESH, "draw")
    assert a == pytest.approx(1000) and b == pytest.approx(1000)
    assert rd_a < elo.RD_START and rd_b < elo.RD_START
    # A draw against a stronger photo still lifts the weaker one
    (low, _, _), (high, _, _) = elo.glicko_pair((900.0, 120.0, 0.06), (1100.0, 120.0, 0.06), "draw")
    assert low > 900 and high < 1100


def test_settled_photos_move_little_and_an_upset_moves_more():
    settled = lambda r: (float(r), 80.0, 0.06)
    win = elo.glicko_pair(settled(1400), settled(1000), "a")[0][0] - 1400
    upset = elo.glicko_pair(settled(1400), settled(1000), "b")[1][0] - 1000
    assert 0 < win < 5 and upset > 25
    # A new photo against a settled one: the new one moves a lot, the settled one hardly
    (new, rd_new, _), (old, rd_old, _) = elo.glicko_pair(FRESH, (1000.0, 60.0, 0.06), "a")
    assert new - 1000 > 150 and 0 < 1000 - old < 10 and rd_old == pytest.approx(60, abs=1)


def test_the_rating_scale_keeps_its_floor_and_outcomes_are_checked():
    assert elo.public_rating(50.4) == elo.RATING_FLOOR == 100
    assert elo.public_rating(1162.31) == 1162
    assert (elo.RATING_START, elo.RD_START) == (1000, 350.0)
    with pytest.raises(ValueError):
        elo.glicko_pair(FRESH, FRESH, "c")
    # Deviation stays within its bounds however long a photo plays
    state = FRESH
    rng = random.Random(1)
    for _ in range(400):
        state = elo.glicko_pair(state, (1000.0, 50.0, 0.06), rng.choice("ab"))[0]
    assert elo.RD_MIN <= state[1] < 100


def test_ratings_far_apart_never_divide_by_zero_or_overflow():
    """Votes have no upper cap, so two photos can end up thousands of points apart."""
    import math
    for gap in (6_500, 10_000, 200_000):
        for outcome in elo.OUTCOMES:
            for one, two in (((1000.0 + gap, 30.0, 0.06), (1000.0, 30.0, 0.06)),
                             ((1000.0, 30.0, 0.06), (1000.0 + gap, 30.0, 0.06))):
                states = elo.glicko_pair(one, two, outcome)
                assert all(math.isfinite(x) for st in states for x in st)
        far, near = {"mu": 1000.0 + gap, "rd": 30.0}, {"mu": 1000.0, "rd": 30.0}
        assert 0 <= elo.information(far, near) < 1e-6 and 0 <= elo.information(near, far) < 1e-6
        assert 0 < elo.win_probability(near, far) < 1e-9 and 0 < 1 - elo.win_probability(far, near) < 1e-9
        assert 0 < elo.expected_score(1000, 1000 + gap) < 1e-9
    # An upset between photos 10,000 points apart still moves both the right way
    (a, _, _), (b, _, _) = elo.glicko_pair((1000.0, 30.0, 0.06), (11_000.0, 30.0, 0.06), "a")
    assert a > 1000 and b < 11_000


def test_information_is_largest_for_uncertain_close_pairs():
    c = lambda mu, rd: {"mu": mu, "rd": rd}
    assert elo.information(c(1000, 350), c(1000, 350)) > elo.information(c(1000, 350), c(1400, 350))
    assert elo.information(c(1000, 350), c(1000, 350)) > elo.information(c(1000, 80), c(1000, 80))
    assert elo.information(c(1000, 200), c(1050, 200)) == pytest.approx(elo.information(c(1050, 200), c(1000, 200)))
    assert elo.settled_share([c(1000, 350)]) == 0 and elo.settled_share([c(1000, elo.RD_SETTLED)]) == 1
    assert 0 < elo.settled_share([c(1000, 350), c(1000, 60)]) == pytest.approx(0.5)


# ==================== RECORDING, ATOMICITY, UNDO ====================

def _new_pair_ratings(outcome="a"):
    (a, _, _), (b, _, _) = elo.glicko_pair(FRESH, FRESH, outcome)
    return elo.public_rating(a), elo.public_rating(b)


def test_a_comparison_updates_both_ratings_and_keeps_the_history(client, app, vault):
    db = app.state.db
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    r = client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "a"})
    assert r.status_code == 200, r.text
    body = r.json()
    win, lose = _new_pair_ratings()
    assert (win, lose) == (1162, 838)
    assert {k: body["a"][k] for k in ("path", "before", "rating", "games")} == {
        "path": str(rome), "before": 1000, "rating": win, "games": 1}
    assert body["b"]["rating"] == lose and body["a"]["rd"] == pytest.approx(290.3, abs=0.1)
    assert body["scope"] == str((vault / "library").resolve()) and body["duplicate"] is False
    assert body["stats"]["comparisons"] == 1 and 0 < body["stats"]["settled"] < 1
    assert body["progress"]["session"] == 1 and body["progress"]["today"] == 1 and body["progress"]["streak"] == 1
    assert tuple(_row(db, rome)) == (win, 1, 0) and tuple(_row(db, venice)) == (lose, 1, 0)
    with db._connect() as conn:
        row = conn.execute("SELECT path_a, path_b, winner, rating_a_before, rating_a_after, rating_b_before, "
                           "rating_b_after, existed_a, existed_b, model FROM elo_comparisons").fetchone()
        model = conn.execute("SELECT mu, rd, games, public FROM elo_ratings WHERE path = ?", (str(rome),)).fetchone()
    assert row == (str(rome), str(venice), "a", 1000, win, 1000, lose, 0, 0, "glicko2")
    assert model[0] == pytest.approx(1162.31, abs=0.01) and model[2:] == (1, win)


def test_a_draw_is_a_comparison_of_its_own(client, app, vault):
    db = app.state.db
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    body = client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "draw"}).json()
    assert body["winner"] == "draw" and body["a"]["rating"] == body["b"]["rating"] == 1000
    assert body["a"]["rd"] < elo.RD_START
    assert tuple(_row(db, rome)) == (1000, 1, 0)
    board = client.get("/api/shows/leaderboard").json()
    assert {(i["name"], i["draws"], i["wins"], i["losses"]) for i in board["items"]} == {
        ("rome.jpg", 1, 0, 0), ("venetië.jpg", 1, 0, 0)}


def test_the_same_client_token_counts_once(client, app, vault):
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    payload = {"a": str(rome), "b": str(venice), "winner": "a", "client_token": "t-1"}
    first = client.post("/api/shows/compare", json=payload).json()
    again = client.post("/api/shows/compare", json=payload).json()
    assert again["id"] == first["id"] and again["duplicate"] is True and again["a"]["rating"] == first["a"]["rating"]
    with app.state.db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM elo_comparisons").fetchone()[0] == 1
    for bad in ("", "x" * 65, 12, "a\nb"):
        assert client.post("/api/shows/compare", json={**payload, "client_token": bad}).status_code == 400, bad


def test_comparisons_and_votes_share_one_scale(client, app, vault):
    db = app.state.db
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    assert client.post("/api/shows/vote", json={"path": str(rome), "vote": "up"}).json()["new_rating"] == 1050
    body = client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "b"}).json()
    # Rome starts from its voted 1050 and loses to Venice (1000): the vote is part of its rating
    (a, _, _), (b, _, _) = elo.glicko_pair((1050.0, elo.RD_START, elo.VOL_START), FRESH, "b")
    assert body["a"]["before"] == 1050 and body["a"]["rating"] == round(a) and body["b"]["rating"] == round(b)
    assert tuple(_row(db, rome)) == (round(a), 1, 1)
    # An up-vote after a comparison still adds exactly 50, and the model sees it as a shift
    voted = client.post("/api/shows/vote", json={"path": str(venice), "vote": "up"}).json()
    assert voted == {"status": "ok", "new_rating": round(b) + 50, "votes": 1}
    with db._connect() as conn:
        state = elo._state(conn, str(venice))
    assert state["mu"] == pytest.approx(b + 50) and state["rating"] == round(b) + 50 and state["games"] == 1


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
    state the previous one ended with (no two read the same old state), and the final
    state must equal a replay of the history one by one."""
    db = app.state.db
    a, b = "/v/race/a.jpg", "/v/race/b.jpg"
    barrier = threading.Barrier(16)
    errors = []

    def worker(n):
        try:
            barrier.wait()
            for i in range(8):
                elo.record_comparison(db, a, b, ("a", "b", "draw")[(n + i) % 3], "/v/race")
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
        history = conn.execute("SELECT winner, mu_a_before, rd_a_before, mu_b_before, rd_b_before, mu_a_after, "
                               "mu_b_after, games_a_before, games_b_before FROM elo_comparisons ORDER BY id").fetchall()
    assert len(history) == 128
    sa = sb = FRESH
    for games, (winner, mua, rda, mub, rdb, mua2, mub2, ga, gb) in enumerate(history):
        assert (mua, rda, mub, rdb, ga, gb) == pytest.approx((sa[0], sa[1], sb[0], sb[1], games, games))
        sa, sb = elo.glicko_pair(sa, sb, winner)
        assert (mua2, mub2) == pytest.approx((sa[0], sb[0]))
    assert tuple(_row(db, a))[:2] == (elo.public_rating(sa[0]), 128)


def test_a_failure_halfway_changes_nothing(client, app, monkeypatch):
    db = app.state.db
    db.apply_vote("/v/x/a.jpg", 50)
    real = elo.glicko_pair

    def broken(*args, **kw):
        real(*args, **kw)
        raise RuntimeError("boom")
    monkeypatch.setattr(elo, "glicko_pair", broken)
    with pytest.raises(RuntimeError):
        elo.record_comparison(db, "/v/x/a.jpg", "/v/x/b.jpg", "a")
    assert tuple(_row(db, "/v/x/a.jpg")) == (1050, 0, 1)
    assert _row(db, "/v/x/b.jpg") is None
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM elo_comparisons").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM elo_ratings").fetchone()[0] == 0


def _model(db, path):
    with db._connect() as conn:
        return conn.execute("SELECT mu, rd, vol, public, games FROM elo_ratings WHERE path = ?", (str(path),)).fetchone()


def test_undo_restores_both_ratings_and_the_model_exactly(client, app, vault):
    db = app.state.db
    rome, venice, opa = ((vault / p).resolve() for p in (ROME, VENICE, OPA))
    # Venice already had a rating; Rome and Opa had none
    client.post("/api/shows/vote", json={"path": str(venice), "vote": "down"})
    first = client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "b"}).json()
    after_first = (tuple(_row(db, rome)), tuple(_row(db, venice)), _model(db, rome), _model(db, venice))
    second = client.post("/api/shows/compare", json={"a": str(opa), "b": str(rome), "winner": "a"}).json()

    r = client.post("/api/shows/compare/undo", json={"id": second["id"]})
    assert r.status_code == 200, r.text
    assert {k: r.json()["a"][k] for k in ("path", "rating", "games")} == {"path": str(opa), "rating": 1000, "games": 0}
    assert r.json()["progress"]["session"] == 1
    assert (tuple(_row(db, rome)), tuple(_row(db, venice)), _model(db, rome), _model(db, venice)) == after_first
    assert _row(db, opa) is None and _model(db, opa) is None   # the rows the comparison made are gone again

    # Undo without an id: the newest one in the scope (the library)
    r = client.post("/api/shows/compare/undo", json={})
    assert r.status_code == 200 and r.json()["id"] == first["id"]
    assert _row(db, rome) is None and _model(db, rome) is None and _model(db, venice) is None
    assert tuple(_row(db, venice)) == (950, 0, 1)
    assert client.post("/api/shows/compare/undo", json={}).status_code == 404
    assert client.post("/api/shows/compare/undo", json={"id": first["id"]}).status_code == 404
    assert client.post("/api/shows/compare/undo", json={"id": "1"}).status_code == 400
    # Ids that cannot be a row: refused, never a 500 (sqlite cannot take a number this big)
    for bad in (99999999999999999999999, 2 ** 63, 0, -1, True):
        r = client.post("/api/shows/compare/undo", json={"id": bad})
        assert r.status_code == 400 and r.json()["detail"] == "Ongeldige vergelijking", (bad, r.text)


def test_many_undos_in_a_row_walk_back_to_the_start(client, app, vault):
    db = app.state.db
    paths = [str((vault / p).resolve()) for p in (ROME, VENICE, OPA)]
    snapshots, ids = [], []
    rng = random.Random(11)
    for _ in range(12):
        snapshots.append([(_row(db, p), _model(db, p)) for p in paths])
        a, b = rng.sample(paths, 2)
        ids.append(client.post("/api/shows/compare", json={"a": a, "b": b, "winner": rng.choice(("a", "b", "draw"))}).json()["id"])
    for comparison_id, snapshot in zip(reversed(ids), reversed(snapshots)):
        assert client.post("/api/shows/compare/undo", json={"id": comparison_id}).status_code == 200
        assert [(_row(db, p), _model(db, p)) for p in paths] == snapshot


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
    client.post("/api/shows/compare/skip", json={"a": str(rome), "b": str(venice)})
    r = client.post("/api/move-file", json={"source_path": str(rome),
                                            "destination_folder": str((vault / "library/Familie").resolve())})
    assert r.status_code == 200, r.text
    moved = r.json()["new_path"]
    assert _row(db, rome) is None and tuple(_row(db, moved)) == (1162, 1, 0)
    assert _model(db, rome) is None and _model(db, moved)[4] == 1
    with db._connect() as conn:
        assert conn.execute("SELECT path_a FROM elo_comparisons").fetchone()[0] == moved
        assert moved in conn.execute("SELECT path_a, path_b FROM elo_skips").fetchone()
    board = client.get("/api/shows/leaderboard").json()
    assert [i["path"] for i in board["items"]] == [moved, str(venice)]
    assert client.post("/api/shows/compare/undo", json={"id": comp["id"]}).status_code == 200
    assert _row(db, moved) is None and _row(db, venice) is None


def test_trash_moves_a_photo_out_of_the_duel_and_the_recovery_center_brings_it_back(client, app, vault):
    db = app.state.db
    rome, venice = (vault / ROME).resolve(), (vault / VENICE).resolve()
    client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "a"})
    r = client.post("/api/shows/compare/trash", json={"path": str(rome)})
    assert r.status_code == 200, r.text
    body = r.json()
    trashed = Path(body["trash_path"])
    assert not rome.exists() and trashed.is_file() and trashed.parent.name == "TRASH"
    # Nothing is deleted: the score goes along, and the photo is out of every new pair
    assert tuple(_row(db, trashed)) == (1162, 1, 0)
    pairs = client.get("/api/shows/compare/pair", params={"count": 4}).json()["pairs"]
    assert str(rome) not in {p["path"] for pair in pairs for p in pair}
    entry = next(e for e in client.get("/api/recovery").json()["entries"] if e.get("label") == "Uit 'Welke is mooier?'")
    assert entry["undoable"] and entry["undo"]["body"] == {"action_id": body["action_id"]}
    # A second time, or a photo already in the trash, is refused
    assert client.post("/api/shows/compare/trash", json={"path": str(rome)}).status_code == 404
    assert client.post("/api/shows/compare/trash", json={"path": str(trashed)}).status_code == 400
    assert client.post("/api/shows/compare/trash", json={"path": 7}).status_code == 400
    r = client.post("/api/recovery/undo", json={"action_id": body["action_id"]})
    assert r.status_code == 200 and r.json()["restored"] == 1
    assert rome.is_file() and not trashed.exists() and tuple(_row(db, rome)) == (1162, 1, 0)


# ==================== PAIRS ====================

def _cands(n, rating=1000, games=0, rd=None):
    out = []
    for i in range(n):
        c = elo.new_candidate(f"/v/p/{i:03d}.jpg", rating)
        c.update(games=games, size=1000 + i)
        if rd is not None:
            c["rd"] = rd
        elif games:
            c["rd"] = 80.0
        out.append(c)
    return out


def test_pairs_prefer_uncertain_photos():
    cands = _cands(40, games=60)
    for c in cands[:4]:
        c.update(games=0, rd=elo.RD_START)
    fresh = {c["path"] for c in cands[:4]}
    rng = random.Random(1)
    hits = sum(bool(fresh & {a["path"], b["path"]}) for a, b in (elo.choose_pair(cands, rng=rng) for _ in range(300)))
    assert hits > 240     # 4 of 40 photos, yet in nearly every pair


def test_the_opponent_has_a_similar_rating():
    cands = _cands(200, rd=120.0)
    for i, c in enumerate(cands):
        c["mu"] = c["rating"] = 400 + i * 10          # 400 .. 2390
    rng = random.Random(2)
    gaps = []
    for _ in range(300):
        a, b = elo.choose_pair(cands, rng=rng)
        gaps.append(abs(a["mu"] - b["mu"]))
    gaps.sort()
    assert gaps[len(gaps) // 2] <= 60 and gaps[int(len(gaps) * 0.9)] <= elo.NEIGHBOURS * 10 * 2


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


def test_recent_photos_and_earlier_pairs_are_held_back():
    cands = _cands(30)
    rng = random.Random(9)
    memory = elo.PairMemory()
    shown = []
    for _ in range(60):
        a, b = elo.choose_pair(cands, rng=rng, memory=memory)
        shown.append((a["path"], b["path"]))
        memory.add(a["path"], b["path"])
        for c, outcome in ((a, "a"), (b, "b")):
            c["games"] += 1
        (a["mu"], a["rd"], a["vol"]), (b["mu"], b["rd"], b["vol"]) = elo.glicko_pair(
            elo.state_of(a), elo.state_of(b), "a")
    # Never the same photo in two pairs in a row, and nearly never a pair twice
    for (a1, b1), (a2, b2) in zip(shown, shown[1:]):
        assert not {a1, b1} & {a2, b2}
    assert len({frozenset(p) for p in shown}) >= 57
    # A blocked (skipped) pair never comes
    memory = elo.PairMemory()
    memory.block(cands[0]["path"], cands[1]["path"])
    two = elo.choose_pairs(cands[:2], 1, rng=rng, memory=memory)
    assert two == []


def test_several_pairs_at_once_share_no_photo_and_skip_the_excluded():
    cands = _cands(12)
    rng = random.Random(4)
    exclude = [cands[0]["path"], cands[1]["path"]]
    pairs = elo.choose_pairs(cands, 3, rng=rng, exclude=exclude)
    photos = [c["path"] for pair in pairs for c in pair]
    assert len(pairs) == 3 and len(set(photos)) == 6 and not set(photos) & set(exclude)
    # More pairs than photos allow: as many as there are
    assert len(elo.choose_pairs(_cands(5), 4, rng=rng)) == 2


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
    for c in cands:
        c.pop("series", None)
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
        c.pop("series", None)
        c.update(taken=50.0 + i, taken_source="exif")
    assert elo.choose_pair(fresh, rng=rng) is None
    assert elo.choose_pair(cands[:2]) is None


def test_with_equal_ratings_the_opponents_come_from_the_whole_list():
    # A fresh library: every photo at 1000. The opponent must not always be one of the
    # alphabetically first photos.
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

    # Twenty folders, with updates as a user would make them: no folder takes over
    cands = [{**elo.new_candidate(f"/v/{2000 + i // 50}/{i % 50:03d}.jpg"), "size": 5000 + i} for i in range(1000)]
    rng = random.Random(8)
    memory = elo.PairMemory()
    per_folder = {}
    for _ in range(300):
        a, b = elo.choose_pair(cands, rng=rng, memory=memory)
        memory.add(a["path"], b["path"])
        for c in (a, b):
            folder = c["path"].split("/")[2]
            per_folder[folder] = per_folder.get(folder, 0) + 1
        (a["mu"], a["rd"], a["vol"]), (b["mu"], b["rd"], b["vol"]) = elo.glicko_pair(elo.state_of(a), elo.state_of(b), "a")
        a["games"] += 1
        b["games"] += 1
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
    assert body["stats"] == {"photos": 2, "compared": 0, "comparisons": 0, "settled": 0}
    assert {p["path"] for p in body["pair"]} == {rome, opa}
    r = client.post("/api/shows/compare", json={"a": rome, "b": opa, "winner": "b", "scope": "album:7"})
    assert r.status_code == 200 and r.json()["scope"] == "album:7"
    board = client.get("/api/shows/leaderboard", params={"scope": "album:7"}).json()
    assert [i["path"] for i in board["items"]] == [opa, rome] and board["scope"]["key"] == "album:7"
    client.post("/api/shows/compare", json={"a": venice, "b": str(lib / "xss <img src=x onerror=window.__xss=1>.jpg"),
                                            "winner": "a"})   # in the library, newer
    undone = client.post("/api/shows/compare/undo", json={"scope": "album:7"}).json()
    assert undone["scope"] == "album:7" and undone["stats"] == {"photos": 2, "compared": 0, "comparisons": 0, "settled": 0}
    for bad, status in (("album:8", 404), ("map:1", 404), ("album", 400)):
        assert client.get("/api/shows/compare/pair", params={"scope": bad}).status_code == status, bad


def test_smart_albums_are_scopes_and_top_n_becomes_an_album(client, app, vault):
    from test_media_index import _index
    _index(client, full=False)
    lib = (vault / "library").resolve()
    italy = lib / "Reis Italië"
    album = client.post("/api/library/albums", json={"name": "Italië", "filters": {"folder": str(italy)}})
    assert album.status_code == 201, album.text
    key = f"album:{album.json()['id']}"
    body = client.get("/api/shows/compare/pair", params={"scope": key}).json()
    assert body["scope"]["title"] == "Italië" and {p["name"] for p in body["pair"]} == {"rome.jpg", "venetië.jpg"}
    rome, venice = str(italy / "rome.jpg"), str(italy / "venetië.jpg")
    client.post("/api/shows/compare", json={"a": rome, "b": venice, "winner": "a", "scope": key})
    # Top 1 of the album as an album of its own: the album's filter plus 'at least this rating'
    made = client.post("/api/shows/leaderboard/album", json={"n": 1, "scope": key})
    assert made.status_code == 200, made.text
    top = made.json()
    assert top["name"] == "Top 1 · Italië" and top["sort"] == "-rating"
    assert top["filters"]["min_rating"] == 1162 and top["filters"]["kind"] == ["photo"]
    names = [i["name"] for i in client.get("/api/library/browse", params={"album": top["id"]}).json()["items"]]
    assert names == ["rome.jpg"]
    # The same again gets a free name; a folder scope works too; bad input is refused
    assert client.post("/api/shows/leaderboard/album", json={"n": 1, "scope": key}).json()["name"] == "Top 1 · Italië (2)"
    by_folder = client.post("/api/shows/leaderboard/album", json={"n": 2, "folder": str(italy)}).json()
    assert by_folder["filters"]["folder"] == "library/Reis Italië" and by_folder["filters"]["min_rating"] == 838
    for bad in ({"n": 0}, {"n": 201}, {"n": "3"}, {"n": 3, "year": "x"}, {"n": 3, "year": 1700}):
        assert client.post("/api/shows/leaderboard/album", json=bad).status_code == 400, bad


def test_an_album_of_videos_has_no_photos_to_compare(client, app, vault):
    """The album's own kind filter counts: 'Video's' never ranks the JPGs of its folder."""
    from test_media_index import _index
    _index(client, full=False)
    italy = (vault / "library/Reis Italië").resolve()
    videos = client.post("/api/library/albums", json={"name": "Filmpjes", "filters": {"kind": ["video"], "folder": str(italy)}})
    mixed = client.post("/api/library/albums", json={"name": "Alles", "filters": {"kind": ["photo", "video"], "folder": str(italy)}})
    assert videos.status_code == mixed.status_code == 201, videos.text
    key = f"album:{videos.json()['id']}"
    body = client.get("/api/shows/compare/pair", params={"scope": key}).json()
    assert body["pair"] is None and body["reason"] == "too_few" and body["stats"]["photos"] == 0
    assert client.get("/api/shows/leaderboard", params={"scope": key}).json()["items"] == []
    both = client.get("/api/shows/compare/pair", params={"scope": f"album:{mixed.json()['id']}"}).json()
    assert {p["name"] for p in both["pair"]} == {"rome.jpg", "venetië.jpg"}


def test_an_album_scope_is_built_once_per_version_and_follows_its_album(client, app, vault, monkeypatch):
    """An album's photos are looked up once per selection version (as a folder's are), not for every
    pair; a new photo, an edited album and a disabled show folder are all seen at once."""
    from test_media_index import _index
    _index(client, full=False)
    lib = (vault / "library").resolve()
    italy = lib / "Reis Italië"
    album = client.post("/api/library/albums", json={"name": "Italië", "filters": {"folder": str(italy)}}).json()
    key = f"album:{album['id']}"
    builds = []
    real = elo.mark_series
    monkeypatch.setattr(elo, "mark_series", lambda base: (builds.append(len(base)), real(base))[1])

    def photos():
        return client.get("/api/shows/compare/pair", params={"scope": key}).json()["stats"]["photos"]

    assert [photos() for _ in range(5)] == [2] * 5
    pair = client.get("/api/shows/compare/pair", params={"scope": key}).json()["pair"]
    client.post("/api/shows/compare", json={"a": pair[0]["path"], "b": pair[1]["path"], "winner": "a", "scope": key})
    client.get("/api/shows/leaderboard", params={"scope": key})
    assert builds == [2]                      # one lookup for six pair requests, a pick and the ranking
    # A new photo in the album's folder: the next index pass changes the version
    shutil.copy2(lib / "Familie/opa.jpg", italy / "napels.jpg")
    _index(client, full=False)
    assert photos() == 3 and builds[-1] == 3
    # The album itself edited: from three photos to the one of another folder
    r = client.patch(f"/api/library/albums/{album['id']}", json={"filters": {"folder": str(lib / "Familie")}})
    assert r.status_code == 200, r.text
    assert photos() == 1
    client.patch(f"/api/library/albums/{album['id']}", json={"filters": {"folder": str(italy)}})
    assert photos() == 3
    # A show folder switched off counts at once (without forget_candidates)
    client.post("/api/shows/settings", json={"path": str(italy), "enabled": False})
    assert photos() == 0
    client.post("/api/shows/settings", json={"path": str(italy), "enabled": True})
    assert photos() == 3


def test_perceptual_near_duplicates_from_step_3_4_are_never_paired(client, app, vault):
    from test_media_index import _index
    from backend.routes import shows
    _index(client, full=False)
    italy = (vault / "library/Reis Italië").resolve()
    with app.state.db._connect() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM media WHERE folder = ? ORDER BY path", (str(italy),))]
        assert len(ids) == 2
        conn.execute("INSERT INTO near_dup_pairs (a, b, kind, phash_d, dhash_d, color_d) VALUES (?, ?, 'near', 2, 2, 0.1)",
                     (min(ids), max(ids)))
    shows._near_pairs.update(at=0.0)
    body = client.get("/api/shows/compare/pair", params={"folder": str(italy)}).json()
    assert body["pair"] is None and body["reason"] == "no_pair"


def test_the_undo_answers_the_progress_of_its_scope(client, app, vault):
    italy = (vault / "library/Reis Italië").resolve()
    rome, venice = str(italy / "rome.jpg"), str(italy / "venetië.jpg")
    comp = client.post("/api/shows/compare", json={"a": rome, "b": venice, "winner": "a", "folder": str(italy)}).json()
    undone = client.post("/api/shows/compare/undo", json={"id": comp["id"]}).json()
    assert undone["stats"] == {"photos": 2, "compared": 0, "comparisons": 0, "settled": 0}


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
    assert {p["name"] for p in body["pair"]} == {"rome.jpg", "venetië.jpg"} and body["pairs"] == [body["pair"]]
    assert body["pair"][0]["rating"] == 1000 and body["pair"][0]["rd"] == elo.RD_START
    assert body["stats"] == {"photos": 2, "compared": 0, "comparisons": 0, "settled": 0}
    assert body["progress"] == {"session": 0, "today": 0, "streak": 0, "best_streak": 0, "goal": 20, "total": 0}
    a, b = (p["path"] for p in body["pair"])
    # Shown now (prev) or just compared: two photos give no new pair
    assert client.get("/api/shows/compare/pair", params={"folder": str(italy), "prev": [a, b]}).json()["reason"] == "no_pair"
    client.post("/api/shows/compare", json={"a": a, "b": b, "winner": "a", "folder": str(italy)})
    again = client.get("/api/shows/compare/pair", params={"folder": str(italy)}).json()
    assert again["pair"] is None and again["reason"] == "no_pair"
    assert again["stats"]["compared"] == 2 and again["stats"]["comparisons"] == 1
    # The whole library has more photos; one photo alone is too few
    lib = client.get("/api/shows/compare/pair", params={"count": 2}).json()
    assert lib["scope"]["title"] == "Hele bibliotheek" and lib["stats"]["photos"] == 4 and lib["pair"]
    assert len(lib["pairs"]) == 2 and len({p["path"] for pair in lib["pairs"] for p in pair}) == 4
    assert client.get("/api/shows/compare/pair", params={"folder": str(vault / "library/Familie")}).json()["reason"] == "too_few"
    assert client.get("/api/shows/compare/pair", params={"folder": "/etc"}).status_code == 403
    assert client.get("/api/shows/compare/pair", params={"count": 9}).status_code == 422


def _photos(folder, n):
    """n small, distinct JPEGs (different sizes: never copies of each other)."""
    import io
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        buf = io.BytesIO()
        Image.new("RGB", (32, 24), ((i * 37) % 256, (i * 91) % 256, (i * 13) % 256)).save(buf, "JPEG")
        (folder / f"k{i:02d}.jpg").write_bytes(buf.getvalue() + b"\0" * (i + 1))
    return folder.resolve()


def _play_like_the_screen(client, folder, picks, rng, ring=8):
    """The duel screen in its fastest order: a pick shows the next queued pair and asks for
    new pairs before the pick is saved. Answers the pairs in the order they were shown."""
    flat = lambda pairs: [p for pair in pairs for p in pair]
    paths = lambda pair: [p["path"] for p in pair]

    def fetch(count, current, queue, shown):
        params = {"folder": str(folder), "count": count, "prev": current,
                  "exclude": current + flat(queue), "recent": flat(shown[-ring:])}
        r = client.get("/api/shows/compare/pair", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        busy = set(body["apart"]) if body["shared"] else set(current + flat(queue))
        for pair in body["pairs"]:
            if not busy & set(paths(pair)) and set(paths(pair)) not in [set(q) for q in queue + [current]]:
                queue.append(paths(pair))
                busy.update(paths(pair))

    queue = []
    fetch(3, [], queue, [])
    shown = [queue.pop(0)]
    for i in range(picks):
        picked = shown[-1]
        shown.append(queue.pop(0))
        if len(queue) < 2:
            fetch(3 - min(1, len(queue)), shown[-1], queue, shown)
        r = client.post("/api/shows/compare", json={"a": picked[0], "b": picked[1], "winner": rng.choice("ab"),
                                                    "folder": str(folder), "client_token": f"t{i}"})
        assert r.status_code == 200, r.text
    return [frozenset(p) for p in shown]


def test_a_pair_just_picked_does_not_come_back_while_its_save_is_on_the_way(client, app, vault):
    """The screen asks for new pairs before the pick is saved; the pairs it showed lately
    (recent=) keep the pair just picked away all the same."""
    rng = random.Random(3)
    small = _play_like_the_screen(client, _photos(vault / "library/acht", 8), 40, rng)
    for i, pair in enumerate(small):
        assert pair not in small[max(0, i - 6):i], (i, small[max(0, i - 6):i + 1])
    # In 8 photos the queue may share photos, so the duel goes round all 28 pairs instead
    # of the same dozen
    assert len(set(small)) >= 22
    # Four photos: every one of the 6 pairs comes by, never the same pair twice in a row
    tiny = _play_like_the_screen(client, _photos(vault / "library/vier", 4), 20, rng)
    assert len(set(tiny)) == 6 and all(a != b for a, b in zip(tiny, tiny[1:]))
    larger = _play_like_the_screen(client, _photos(vault / "library/dertig", 30), 40, rng)
    for i, pair in enumerate(larger):
        assert pair not in larger[max(0, i - 8):i], i
    assert len(set(larger)) >= 36


def test_a_skipped_pair_stays_away(client, app, vault):
    italy = (vault / "library/Reis Italië").resolve()
    rome, venice = str(italy / "rome.jpg"), str(italy / "venetië.jpg")
    assert client.post("/api/shows/compare/skip", json={"a": rome, "b": venice, "folder": str(italy)}).status_code == 200
    body = client.get("/api/shows/compare/pair", params={"folder": str(italy)}).json()
    assert body["pair"] is None and body["reason"] == "no_pair"
    assert client.post("/api/shows/compare/skip", json={"a": rome, "b": rome}).status_code == 400
    assert client.post("/api/shows/compare/skip", json={"a": rome, "b": "/etc/passwd"}).status_code == 403
    with app.state.db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM elo_comparisons").fetchone()[0] == 0


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


# ==================== LEADERBOARD, PROGRESS ====================

def test_leaderboard_ranks_by_rating_with_results_and_bands(client, app, vault):
    rome, venice, opa = ((vault / p).resolve() for p in (ROME, VENICE, OPA))
    client.post("/api/shows/compare", json={"a": str(rome), "b": str(venice), "winner": "a"})
    client.post("/api/shows/compare", json={"a": str(opa), "b": str(rome), "winner": "b"})
    board = client.get("/api/shows/leaderboard").json()
    assert board["total"] == 3 and 0 < board["settled"] < 1 and board["session_start"]
    assert [(i["rank"], i["name"], i["games"], i["wins"], i["losses"]) for i in board["items"]] == [
        (1, "rome.jpg", 2, 2, 0), (2, "opa.jpg", 1, 0, 1), (3, "venetië.jpg", 1, 0, 1)]
    assert board["items"][0]["rating"] > board["items"][1]["rating"] > board["items"][2]["rating"]
    top = board["items"][0]
    assert top["low"] == round(top["rating"] - 2 * top["rd"]) and top["high"] == round(top["rating"] + 2 * top["rd"])
    assert top["size"] == rome.stat().st_size
    # Everything was first compared in this session
    assert all(i["new"] and i["movement"] is None for i in board["items"])
    italy = client.get("/api/shows/leaderboard", params={"folder": str(rome.parent), "limit": 1}).json()
    assert italy["total"] == 2 and [i["name"] for i in italy["items"]] == ["rome.jpg"]
    assert client.get("/api/shows/leaderboard", params={"limit": 0}).status_code == 422
    assert client.get("/api/shows/leaderboard", params={"year": 99}).status_code == 400


def test_the_leaderboard_shows_movement_since_the_last_session(client, app, vault, monkeypatch):
    db = app.state.db
    rome, venice, opa = (str((vault / p).resolve()) for p in (ROME, VENICE, OPA))
    clock = [1_000_000.0]
    monkeypatch.setattr(elo.time, "time", lambda: clock[0])
    elo.record_comparison(db, rome, venice, "a")
    elo.record_comparison(db, venice, opa, "a")        # rome > venice > opa
    clock[0] += elo.SESSION_GAP + 3600                 # a new session, an hour after the last
    for _ in range(3):
        elo.record_comparison(db, opa, rome, "a")
        clock[0] += 10
    board = client.get("/api/shows/leaderboard").json()
    moved = {i["name"]: i["movement"] for i in board["items"]}
    assert board["items"][0]["name"] == "opa.jpg" and moved["opa.jpg"] == 2
    assert moved["rome.jpg"] < 0 and not any(i["new"] for i in board["items"])
    assert board["session_start"] == pytest.approx(1_000_000.0 + elo.SESSION_GAP + 3600)


def test_the_leaderboard_filters_by_year(client, app, vault):
    from test_media_index import _index
    _index(client, full=False)
    rome, venice, opa = (str((vault / p).resolve()) for p in (ROME, VENICE, OPA))
    client.post("/api/shows/compare", json={"a": rome, "b": venice, "winner": "a"})
    client.post("/api/shows/compare", json={"a": opa, "b": venice, "winner": "a"})
    board = client.get("/api/shows/leaderboard").json()
    years = {y["year"]: y["count"] for y in board["years"]}
    assert sum(years.values()) <= 3 and years
    year = max(years, key=years.get)
    only = client.get("/api/shows/leaderboard", params={"year": year}).json()
    assert only["year"] == year and only["total"] == years[year] and len(only["items"]) == years[year]


def test_progress_counts_the_session_the_day_and_the_streak(client, app, monkeypatch):
    db = app.state.db
    import datetime as dt
    today = dt.date.today()          # before time.time is replaced below (date.today() reads it)
    noon = lambda days_ago: dt.datetime.combine(today - dt.timedelta(days=days_ago), dt.time(12)).timestamp()
    clock = [noon(5)]
    monkeypatch.setattr(elo.time, "time", lambda: clock[0])
    for days_ago in (5, 4, 2, 1, 0):                   # 5-4, then 2-1-0: a streak of 3, best 3
        clock[0] = noon(days_ago)
        elo.record_comparison(db, f"/v/s/a{days_ago}.jpg", f"/v/s/b{days_ago}.jpg", "a")
    clock[0] = noon(0) + 60
    elo.record_comparison(db, "/v/s/c.jpg", "/v/s/d.jpg", "draw")
    p = elo.progress(db, now=noon(0) + 120, goal=20)
    assert p == {"session": 2, "today": 2, "streak": 3, "best_streak": 3, "goal": 20, "total": 6}
    # Long after the last comparison: no session, but yesterday still keeps the streak alive
    assert elo.progress(db, now=noon(0) + 86400)["session"] == 0
    assert elo.progress(db, now=noon(0) + 86400)["streak"] == 3
    assert elo.progress(db, now=noon(0) + 3 * 86400)["streak"] == 0


def _fresh_counts(db):
    from collections import Counter
    with db._connect() as conn:
        rows = conn.execute("SELECT path_a, path_b, scope FROM elo_comparisons").fetchall()
    return Counter(frozenset((a, b)) for a, b, _ in rows), Counter(s for _, _, s in rows)


def test_the_in_memory_history_follows_every_change(client, app):
    """Pair counts, scope counts and the session come from a summary kept in memory (no
    full read of the history per request); it must match the table after every change."""
    from backend import media_index
    db = app.state.db
    rng = random.Random(7)
    paths = [f"/v/library/h/{i}.jpg" for i in range(6)]

    def check():
        seen, scopes = _fresh_counts(db)
        assert +elo.pair_counts(db) == seen
        for scope in set(scopes) | {"/v/library", "album:1"}:
            assert elo.comparisons_in_scope(db, scope) == scopes.get(scope, 0)
        p = elo.progress(db)
        assert p["total"] == sum(seen.values())
        with db._connect() as conn:
            assert p["session"] == len(elo._session_times(conn, time.time()))

    check()
    ids = []
    for i in range(12):
        a, b = rng.sample(paths, 2)
        ids.append(elo.record_comparison(db, a, b, rng.choice("ab"), "/v/library" if i % 3 else "album:1")["id"])
        check()
    for _ in range(3):                              # undo, newest first
        elo.undo_comparison(db, ids.pop())
        check()
    elo.record_comparison(db, paths[0], paths[1], "draw", "/v/library")
    check()
    with db._connect() as conn:                     # a move renames the paths in the history
        media_index._carry_user_data(conn, paths[0], "/v/library/h/verplaatst.jpg")
    check()
    with db._connect() as conn:                     # a change made another way: built again
        conn.execute("DELETE FROM elo_comparisons WHERE id = ?", (ids[0],))
    check()
    # A memory built on the shared counts never changes them
    memory = elo.pair_memory(db, "/v/library")
    before = +elo.pair_counts(db)
    memory.add(paths[2], paths[3])
    assert +elo.pair_counts(db) == before


def test_the_session_goal_is_a_setting(client, app):
    assert client.put("/api/shows/compare/goal", json={"goal": 50}).json()["goal"] == 50
    assert client.get("/api/shows/compare/pair").json()["progress"]["goal"] == 50
    for bad in (0, 7, "20", True, None):
        assert client.put("/api/shows/compare/goal", json={"goal": bad}).status_code == 400, bad


# ==================== MIGRATIONS ====================

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


def _old_elo(rating_a, rating_b, winner, games_a, games_b):
    """The Elo rule before migration 160 (K 32 -> 16 after 30 games, floor 100)."""
    def change(r, o, s, k):
        e = 1.0 / (1.0 + 10.0 ** ((o - r) / 400.0))
        return max(-k, min(k, round(k * (s - e))))
    ka, kb = (32 if games_a < 30 else 16), (32 if games_b < 30 else 16)
    sa = 1.0 if winner == "a" else 0.0
    return (max(100, rating_a + change(rating_a, rating_b, sa, ka)),
            max(100, rating_b + change(rating_b, rating_a, 1 - sa, kb)), ka, kb)


def _legacy_history(conn, rng):
    """Comparisons as the old system wrote them, with votes in between and after."""
    paths = [f"/v/library/oud/{i:02d}.jpg" for i in range(8)]
    ratings = {p: 1000 for p in paths}
    games = {p: 0 for p in paths}
    existed = set()
    conn.execute("INSERT INTO file_ratings (path, rating, votes) VALUES (?, 1100, 2)", (paths[0],))
    ratings[paths[0]] = 1100
    existed.add(paths[0])
    t = 1_700_000_000.0
    for step in range(40):
        a, b = rng.sample(paths, 2)
        winner = rng.choice("ab")
        new_a, new_b, ka, kb = _old_elo(ratings[a], ratings[b], winner, games[a], games[b])
        conn.execute("""INSERT INTO elo_comparisons (path_a, path_b, winner, rating_a_before, rating_a_after,
            rating_b_before, rating_b_after, games_a_before, games_b_before, existed_a, existed_b, k_a, k_b, scope,
            created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '/v/library', ?)""",
                     (a, b, winner, ratings[a], new_a, ratings[b], new_b, games[a], games[b],
                      int(a in existed), int(b in existed), ka, kb, t + step * 60))
        for p, r in ((a, new_a), (b, new_b)):
            conn.execute("INSERT INTO file_ratings (path, rating, votes) VALUES (?, ?, 0) "
                         "ON CONFLICT(path) DO UPDATE SET rating = excluded.rating", (p, r))
            existed.add(p)
            ratings[p] = r
            games[p] += 1
        if step == 20:            # a vote between two comparisons of photo 3
            conn.execute("UPDATE file_ratings SET rating = rating + 50, votes = votes + 1 WHERE path = ?", (paths[3],))
            ratings[paths[3]] += 50
    # a vote after the last comparison, and two undone comparisons (the counter is higher than the last id)
    conn.execute("UPDATE file_ratings SET rating = rating - 50, votes = votes + 1 WHERE path = ?", (paths[5],))
    conn.execute("UPDATE sqlite_sequence SET seq = seq + 2 WHERE name = 'elo_comparisons'")
    return paths


def test_migration_160_replays_the_old_history_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 159, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) < 160
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO recovery_actions (kind, title, created_at) VALUES ('viewer_move', 'x', 1)")
        conn.execute("INSERT INTO recovery_moves (action_id, src_path, dst_path, moved_at) VALUES (1, '/v/a', '/v/b', 1)")
        paths = _legacy_history(conn, random.Random(160))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
        old_rows = conn.execute("SELECT id, path_a, path_b, winner, scope, created_at, existed_a, existed_b, k_a, k_b, "
                                "rating_a_before, rating_a_after, rating_b_before, rating_b_after "
                                "FROM elo_comparisons ORDER BY id").fetchall()
        old_seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'elo_comparisons'").fetchone()[0]
        old_votes = dict(conn.execute("SELECT path, votes FROM file_ratings"))
        old_ratings = dict(conn.execute("SELECT path, rating FROM file_ratings"))
    assert old_seq == old_rows[-1][0] + 2

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 160
        # Every table but the derived Elo values is exactly as it was
        for table, rows in before.items():
            if table in ("elo_comparisons", "file_ratings", "sqlite_sequence"):
                continue
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'elo_comparisons'").fetchone()[0] == old_seq
        others = [r for r in before["sqlite_sequence"] if r[0] != "elo_comparisons"]
        assert [r for r in conn.execute("SELECT * FROM sqlite_sequence ORDER BY 1") if r[0] != "elo_comparisons"] == others
        # The comparisons themselves are kept: id, who, against whom, outcome, scope, time
        new_rows = conn.execute("SELECT id, path_a, path_b, winner, scope, created_at, existed_a, existed_b, k_a, k_b, "
                                "rating_a_before, rating_a_after, rating_b_before, rating_b_after, model, mu_a_before, "
                                "rd_b_after FROM elo_comparisons ORDER BY id").fetchall()
        assert [r[:10] for r in new_rows] == [r[:10] for r in old_rows]
        assert all(r[14] == "glicko2" and r[15] is not None and r[16] is not None for r in new_rows)
        # Votes counts are untouched; photos without comparisons keep their rating
        assert dict(conn.execute("SELECT path, votes FROM file_ratings")) == old_votes
        compared = {p for r in old_rows for p in (r[1], r[2])}
        now_ratings = dict(conn.execute("SELECT path, rating FROM file_ratings"))
        for path, rating in old_ratings.items():
            if path not in compared:
                assert now_ratings[path] == rating, path
        games = dict(conn.execute("SELECT path, games FROM elo_ratings"))
        assert games == {p: sum((r[1] == p) + (r[2] == p) for r in old_rows) for p in compared}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # The ratings are what the new model makes of the same choices, with the votes where they were
    start = {p: 1000.0 for p in paths}
    start[paths[0]] = 1100.0                               # its votes before the first comparison
    state = {}
    for step, (_, a, b, winner, *_rest) in enumerate(old_rows):
        sa = state.get(a, (start[a], elo.RD_START, elo.VOL_START))
        sb = state.get(b, (start[b], elo.RD_START, elo.VOL_START))
        state[a], state[b] = elo.glicko_pair(sa, sb, winner)
        if step == 20:                                     # the vote between two comparisons
            if paths[3] in state:
                mu, rd, vol = state[paths[3]]
                state[paths[3]] = (mu + 50, rd, vol)
            else:
                start[paths[3]] += 50
    for path, (mu, _, _) in state.items():
        if path == paths[5]:
            continue
        assert db.get_rating(path) == elo.public_rating(mu), path
    assert db.get_rating(paths[5]) == elo.public_rating(state[paths[5]][0]) - 50     # the vote after the last one
    # The newest comparisons can be undone one by one, exactly
    newest = new_rows[-1]
    with db._connect() as conn:
        assert elo._state(conn, newest[1])["rating"] == newest[11] or newest[1] == paths[5]
    undone = elo.undo_comparison(db, newest[0]) if paths[5] not in newest[1:3] else None
    if undone:
        assert db.get_rating(newest[1]) == newest[10]
    # Replaying again changes nothing: the migration is deterministic
    with db._connect() as conn:
        snapshot = (conn.execute("SELECT * FROM elo_comparisons ORDER BY id").fetchall(),
                    conn.execute("SELECT path, rating FROM file_ratings ORDER BY path").fetchall(),
                    conn.execute("SELECT path, mu, rd, vol, public, games FROM elo_ratings ORDER BY path").fetchall())
        elo.replay_history(conn)
        again = (conn.execute("SELECT * FROM elo_comparisons ORDER BY id").fetchall(),
                 conn.execute("SELECT path, rating FROM file_ratings ORDER BY path").fetchall(),
                 conn.execute("SELECT path, mu, rd, vol, public, games FROM elo_ratings ORDER BY path").fetchall())
    assert again[0] == snapshot[0] and again[1] == snapshot[1]
    assert [r[:1] + r[4:] for r in again[2]] == [r[:1] + r[4:] for r in snapshot[2]]
    # A new comparison gets an id above the old counter, and a draw is allowed now
    result = elo.record_comparison(db, paths[1], paths[2], "draw")
    assert result["id"] == old_seq + 1
    db.close_all()


def test_migration_160_replays_an_old_comparison_between_far_apart_ratings(tmp_path, monkeypatch):
    """A photo voted up 200 times (no cap) against a fresh one: the replay must not crash,
    or the app would not start."""
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 159, monkeypatch)
    high, low = "/v/library/oud/top.jpg", "/v/library/oud/nieuw.jpg"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO file_ratings (path, rating, votes) VALUES (?, 11000, 200)", (high,))
        conn.execute("INSERT INTO file_ratings (path, rating, votes) VALUES (?, 1000, 0)", (low,))
        for i, winner in enumerate("ab"):
            conn.execute("""INSERT INTO elo_comparisons (path_a, path_b, winner, rating_a_before, rating_a_after,
                rating_b_before, rating_b_after, games_a_before, games_b_before, existed_a, existed_b, k_a, k_b,
                scope, created_at) VALUES (?, ?, ?, 11000, 11000, 1000, 1000, ?, ?, 1, 1, 32, 32, '/v/library', ?)""",
                         (high, low, winner, i, i, 1_700_000_000.0 + i))
    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION
        assert conn.execute("SELECT COUNT(*) FROM elo_comparisons WHERE model = 'glicko2'").fetchone()[0] == 2
        assert conn.execute("SELECT votes FROM file_ratings WHERE path = ?", (high,)).fetchone()[0] == 200
    assert db.get_rating(high) > 10_000 and elo.RATING_FLOOR <= db.get_rating(low) < db.get_rating(high)
    # And a new comparison between them is saved, not a server error
    result = elo.record_comparison(db, low, high, "a")
    assert result["a"]["rating"] >= result["a"]["before"]
    db.close_all()


def test_migration_161_only_adds_indexes_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 160, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == 160
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('elo_session_goal', '50', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO recovery_actions (kind, title, created_at) VALUES ('viewer_move', 'x', 1)")
    db = Database(db_path)
    # Ratings, comparisons (with undo data), skips and streak days made through the model
    elo.record_comparison(db, "/v/library/a.jpg", "/v/library/b.jpg", "a", "/v/library")
    elo.record_comparison(db, "/v/library/b.jpg", "/v/library/c.jpg", "draw", "/v/library")
    elo.record_skip(db, "/v/library/a.jpg", "/v/library/c.jpg", "/v/library")
    db.apply_vote("/v/library/d.jpg", 50)
    db.close_all()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM schema_version WHERE version = 161")
        conn.execute("DROP INDEX idx_file_ratings_state")
        conn.execute("DROP INDEX idx_elo_ratings_state")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    assert before["elo_comparisons"] and before["file_ratings"] and before["elo_ratings"] and before["elo_skips"]
    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 161
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_file_ratings_state", "idx_elo_ratings_state"} <= names
        plan = " ".join(r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT path, mu, rd, vol, public, games FROM elo_ratings WHERE path >= ? AND path < ?",
            ("/v/", "/w")))
        assert "COVERING INDEX" in plan
    # The newest comparison can still be undone exactly
    elo.undo_comparison(db)
    assert db.get_rating("/v/library/b.jpg") == _new_pair_ratings("a")[1]
    assert db.get_rating("/v/library/d.jpg") == 1050
    db.close_all()


def test_the_shortcut_overview_documents_the_duel_keys():
    from pathlib import Path as _P
    js = (_P(__file__).resolve().parents[1] / "frontend" / "elo.js").read_text(encoding="utf-8")
    group = js[js.index("addShortcutGroup('Welke is mooier?"):]
    for keys, text in (("['←', '1']", "linker"), ("['→', '2']", "rechter"), ("['spatie']", "gelijk"),
                       ("['s']", "overslaan"), ("['z']", "Terugdraaien")):
        assert keys in group and text in group, (keys, text)
