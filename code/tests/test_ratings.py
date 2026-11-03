"""
Shows ratings (the "ELO" score behind Shows and Feeling Lucky).

- A file nobody voted on is 1000. Every up-vote adds 50, every down-vote takes 50 off,
  and a rating never drops below 100. The vote counter counts every vote.
- Only an existing regular file inside the vault can get a vote: a missing file, a
  folder or a symlink gives 404 and leaves no row behind.
- Votes that arrive at the same time (two tabs, fast clicks) all count: the rating is
  changed in one SQL statement, never read in one request and written in the next.
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from backend.routes import shows
from fixtures.vault_builder import STRAND


def _vote(client, path, vote):
    return client.post("/api/shows/vote", json={"path": str(path), "vote": vote})


def _row(app, path):
    with app.state.db._connect() as conn:
        return conn.execute("SELECT rating, votes FROM file_ratings WHERE path = ?",
                            (str(path),)).fetchone()


def test_up_and_down_votes_move_the_rating_by_fifty(client, app, vault):
    photo = (vault / STRAND).resolve()
    r = _vote(client, photo, "up")
    assert r.status_code == 200, r.text
    assert r.json()["new_rating"] == 1050 and r.json()["votes"] == 1
    r = _vote(client, photo, "down")
    assert r.json()["new_rating"] == 1000 and r.json()["votes"] == 2
    r = _vote(client, photo, "down")
    assert r.json()["new_rating"] == 950 and r.json()["votes"] == 3
    assert tuple(_row(app, photo)) == (950, 3)


def test_a_first_down_vote_starts_from_1000(client, app, vault):
    photo = (vault / STRAND).resolve()
    assert _vote(client, photo, "down").json()["new_rating"] == 950
    assert tuple(_row(app, photo)) == (950, 1)


def test_n_up_votes_give_1000_plus_50_n(client, app, vault):
    photo = (vault / STRAND).resolve()
    for n in range(1, 13):
        body = _vote(client, photo, "up").json()
        assert body["new_rating"] == 1000 + 50 * n
        assert body["votes"] == n
    assert tuple(_row(app, photo)) == (1600, 12)


def test_the_rating_never_drops_below_100_but_every_vote_counts(client, app, vault):
    photo = (vault / STRAND).resolve()
    ratings = [_vote(client, photo, "down").json()["new_rating"] for _ in range(25)]
    assert ratings[17] == 100                     # 1000 - 18 * 50
    assert min(ratings) == 100 and ratings[-1] == 100
    assert tuple(_row(app, photo)) == (100, 25)
    # From the floor, one up-vote goes back up by 50
    assert _vote(client, photo, "up").json()["new_rating"] == 150


def test_votes_on_anything_but_a_file_leave_no_row(client, app, vault):
    folder = (vault / STRAND).resolve().parent
    link = folder / "link.jpg"
    os.symlink(folder / "strand 001.jpg", link)
    for target in (folder / "bestaat-niet.jpg", folder, link):
        r = _vote(client, target, "up")
        assert r.status_code == 404, (target, r.text)
        assert _row(app, target) is None
    for bad in ({"path": str(folder / "strand 001.jpg"), "vote": "maybe"},
                {"path": 5, "vote": "up"}, {"vote": "up"}):
        assert client.post("/api/shows/vote", json=bad).status_code == 400
    with app.state.db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM file_ratings").fetchone()[0] == 0


def test_concurrent_votes_all_count(client, app, vault, monkeypatch):
    """Two tabs voting at once: before, both read 1000 and both wrote 1050 (one vote lost,
    while the vote counter still said 2). Reading stays slow here to widen that window."""
    photo = (vault / STRAND).resolve()
    real_get = app.state.db.get_rating

    def slow_get(path):
        value = real_get(path)
        threading.Event().wait(0.02)
        return value
    monkeypatch.setattr(app.state.db, "get_rating", slow_get)

    ups = 40
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: _vote(client, photo, "up").status_code, range(ups)))
    assert codes == [200] * ups
    assert tuple(_row(app, photo)) == (1000 + 50 * ups, ups)


def test_apply_vote_is_atomic_across_threads(client, app):
    db = app.state.db
    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        for _ in range(10):
            db.apply_vote("/v/race.jpg", 50)
        db.release_thread()
    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert tuple(_row(app, "/v/race.jpg")) == (1000 + 50 * 160, 160)


def test_vote_constants_match_the_documented_rule():
    assert (shows.RATING_START, shows.VOTE_STEP, shows.RATING_FLOOR) == (1000, 50, 100)
