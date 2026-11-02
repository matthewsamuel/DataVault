"""
Back-ups (backend/datagatherer/backups.py): how long ago each source was last saved, worst
first, and the user's own list of sources DataGatherer cannot reach (an old iPad, a drive)
with where the back-up is and how far it is. Migration 197 only adds dg_own_sources.
"""
import sqlite3
import time

from backend.datagatherer import backups, state

DAY = 86400


def test_an_own_source_is_added_changed_removed_and_put_back(client):
    r = client.post("/api/datagatherer/own-sources", json={"name": "  Oude iPad  ", "kind": "toestel",
                                                          "location": "HDD back-up/ipad_2014"})
    assert r.status_code == 200
    ipad = r.json()["source"]
    assert ipad["name"] == "Oude iPad" and ipad["status"] == "te_doen" and ipad["health"] == "nooit"
    assert ipad["kind_label"] == "Toestel" and ipad["icon"] == "fas fa-mobile-screen"

    made = time.time() - 400 * DAY
    r = client.patch(f"/api/datagatherer/own-sources/{ipad['id']}", json={"status": "veilig", "backup_at": made})
    assert r.json()["source"]["health"] == "goed"          # an old device fully in a back-up stays fine
    r = client.patch(f"/api/datagatherer/own-sources/{ipad['id']}", json={"in_use": True})
    assert r.json()["source"]["health"] == "oud"           # still in use: that back-up is old

    removed = client.delete(f"/api/datagatherer/own-sources/{ipad['id']}").json()["removed"]
    assert client.get("/api/datagatherer/own-sources").json()["sources"] == []
    back = client.post("/api/datagatherer/own-sources/restore", json=removed).json()["source"]
    assert back["id"] == ipad["id"] and back["backup_at"] == made and back["in_use"] is True


def test_wrong_values_are_refused_with_a_dutch_message(client):
    for body in ({"name": ""}, {"kind": "toestel"}, {"name": "x", "kind": "raket"},
                 {"name": "x", "status": "klaar"}, {"name": "x", "backup_at": "gisteren"},
                 {"name": "x", "backup_at": time.time() + 30 * DAY}, {"name": "x" * 81}):
        r = client.post("/api/datagatherer/own-sources", json=body)
        assert r.status_code == 400, body
    assert client.patch("/api/datagatherer/own-sources/999", json={"name": "y"}).status_code == 404
    assert client.delete("/api/datagatherer/own-sources/999").status_code == 404


def test_the_overview_shows_saved_sources_and_own_ones_worst_first(client, config):
    from backend import database
    db = database.Database(config.DB_PATH)
    now = time.time()
    state.update(db, "chrome_history", last_success_at=now - 2 * DAY)
    state.update(db, "spotify", last_success_at=now - 60 * DAY)
    state.open_request(db, "facebook", "dyi", None)
    client.post("/api/datagatherer/own-sources", json={"name": "Usb-stick", "kind": "schijf"})
    client.post("/api/datagatherer/own-sources", json={"name": "Oude gsm", "status": "veilig",
                                                      "backup_at": now - 900 * DAY})

    data = client.get("/api/datagatherer/backups").json()
    keys = [i["key"] for i in data["items"]]
    health = {i["key"]: i["health"] for i in data["items"]}
    assert health["bron:chrome_history"] == "goed" and health["bron:spotify"] == "let_op"
    assert health["bron:facebook"] == "nooit" and data["items"][keys.index("bron:facebook")]["requested_at"]
    assert "bron:lastfm" not in keys                       # nothing saved, nothing asked: not shown
    own = [i for i in data["items"] if i["origin"] == "eigen"]
    assert {i["name"]: i["health"] for i in own} == {"Usb-stick": "nooit", "Oude gsm": "goed"}
    # Worst first: never saved before 'let op' before fine
    assert keys.index("bron:facebook") < keys.index("bron:spotify") < keys.index("bron:chrome_history")
    assert data["summary"]["own_total"] == 2 and data["summary"]["own_safe"] == 1
    assert data["summary"]["requested"] == 1


def test_migration_197_only_adds_the_own_sources_table(config, client):
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(dg_own_sources)")]
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= 197
    assert columns == list(backups.COLUMNS)
