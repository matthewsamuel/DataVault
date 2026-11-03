"""
DataGatherer, iMessage: messages edited or unsent after they were read, and tapbacks taken
back. A later read updates the stored row (never a second row), keeps the earlier text in
edits, and marks a removed tapback instead of showing it. Old messages edited long after
they were sent are found by their date_edited. All chat.db files are synthetic, with the
real column names (fake_messages.make_chat_db); the real ~/Library/Messages is never read.
"""
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from backend.datagatherer import state
from backend.datagatherer.formats import imessage_db
from fake_messages import APPLE_EPOCH, T0, make_chat_db, summary_info, typedstream

DAY = 86400
THEM = "+32470000001"


def _wait(app, job_id, timeout=30):
    return app.state.jobs.wait(job_id, timeout=timeout)


def _sync(client, app):
    r = client.post("/api/datagatherer/sources/imessage/sync")
    assert r.status_code in (200, 201), r.text
    job = _wait(app, r.json()["job"]["id"])
    assert job["status"] == "done", job
    return app.state.jobs.result(job["id"])


def _rows(app, sql, args=()):
    with sqlite3.connect(str(app.state.config.DB_PATH)) as conn:
        return conn.execute(sql, args).fetchall()


def _db_file() -> Path:
    folder = Path(os.environ["DATAVAULT_GATHER_HOME"]) / "Library/Messages"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "chat.db"


def _write(messages):
    path = _db_file()
    for p in (path, path.with_name("chat.db-wal"), path.with_name("chat.db-shm")):
        if p.exists():
            p.unlink()          # the fake Messages app writing its next state (a temp folder, never the real one)
    make_chat_db(path, {1: THEM}, [{"rowid": 1, "guid": f"iMessage;-;{THEM}", "style": 45, "handles": [1]}], messages)


BEFORE = [
    {"rowid": 1, "guid": "G-1", "chat": 1, "handle": 1, "text": "Ben je thuis?", "ts": T0 + 10},
    {"rowid": 2, "guid": "G-2", "chat": 1, "me": True, "text": "Kom om 8 uur", "ts": T0 + 20},
    {"rowid": 3, "guid": "G-3", "chat": 1, "handle": 1, "body": typedstream("Iets wat ik terugneem"), "ts": T0 + 25},
    {"rowid": 4, "guid": "G-4", "chat": 1, "handle": 1, "assoc": "p:0/G-2", "assoc_type": 2000,
     "text": "Vond 'Kom om 8 uur' leuk", "ts": T0 + 30},
    {"rowid": 5, "guid": "G-5", "chat": 1, "handle": 1, "text": "Lang geleden", "ts": T0 - 30 * DAY},
    {"rowid": 6, "guid": "G-6", "chat": 1, "me": True, "assoc": "p:0/G-1", "assoc_type": 2001, "text": "👍", "ts": T0 + 40},
]


def _after():
    """The same chat.db later: G-2 edited, G-3 unsent, the ❤️ on G-2 taken back, my 👍 on G-1
    changed into 😂, and G-5 (a month old) edited today."""
    rows = [dict(m) for m in BEFORE]
    rows[1].update(text="Kom om 9 uur", edited=T0 + 100,
                   summary=summary_info([(T0 + 20, "Kom om 8 uur"), (T0 + 100, "Kom om 9 uur")]))
    rows[2].update(body=None, retracted=T0 + 200, summary=summary_info(unsent_parts=[0]))
    rows[4].update(text="Lang geleden (verbeterd)", edited=T0 + 400,
                   summary=summary_info([(T0 - 30 * DAY, "Lang geleden"), (T0 + 400, "Lang geleden (verbeterd)")]))
    rows += [
        {"rowid": 7, "guid": "G-7", "chat": 1, "handle": 1, "assoc": "p:0/G-2", "assoc_type": 3000,
         "text": "Verwijderde ❤️", "ts": T0 + 300},
        {"rowid": 8, "guid": "G-8", "chat": 1, "me": True, "assoc": "p:0/G-1", "assoc_type": 2003, "text": "😂",
         "ts": T0 + 310},
    ]
    return rows


def _messages(app):
    cols = ("msg_key", "body", "kind", "edited_at_ms", "retracted_at_ms", "edits", "reactions")
    return {r[0]: dict(zip(cols[1:], r[1:])) for r in _rows(app, f"SELECT {', '.join(cols)} FROM msg_messages")}


def _thread(client, app):
    conv = _rows(app, "SELECT id FROM msg_conversations WHERE source = 'imessage'")[0][0]
    return {m["body"] or m["kind"]: m for m in client.get(f"/api/messages/conversations/{conv}").json()["messages"]}


# ---------- reading chat.db ----------

def test_the_summary_info_gives_the_earlier_versions(tmp_path):
    blob = summary_info([(T0, "Eerst"), (T0 + 60, "Daarna"), (T0 + 120, "Nu")])
    assert imessage_db.edit_history(blob) == [{"at": T0 * 1000, "body": "Eerst"}, {"at": (T0 + 60) * 1000, "body": "Daarna"}]
    assert imessage_db.edit_history(b"bplist00 kapot") == [] and imessage_db.edit_history(None) == []
    assert not imessage_db.unsent_parts(blob) and imessage_db.unsent_parts(summary_info(unsent_parts=[0]))
    # Older macOS: no date_retracted, only date_edited and 'rp'
    path = tmp_path / "chat.db"
    make_chat_db(path, {1: THEM}, [{"rowid": 1, "guid": "c", "style": 45, "handles": [1]}], [
        {"rowid": 1, "guid": "U-1", "chat": 1, "handle": 1, "ts": T0, "edited": T0 + 30,
         "summary": summary_info(unsent_parts=[0])}])
    conn = sqlite3.connect(str(path))
    (msg,) = list(imessage_db.messages(conn))
    conn.close()
    assert msg["kind"] == "unsent" and msg["retracted_at_ms"] == (T0 + 30) * 1000 and msg["edited_at_ms"] is None


def test_an_edit_long_after_sending_is_read_by_its_edit_date(tmp_path):
    path = tmp_path / "chat.db"
    rows = _after()
    make_chat_db(path, {1: THEM}, [{"rowid": 1, "guid": "c", "style": 45, "handles": [1]}], rows)
    conn = sqlite3.connect(str(path))
    keys = {m.get("key") or m.get("target") for m in imessage_db.messages(conn, since_unix=T0 - DAY)}
    conn.close()
    assert "G-5" in keys                 # sent a month before 'since', edited after it


# ---------- importing ----------

def test_edits_unsends_and_removed_tapbacks_update_the_stored_rows(client, app):
    _write(BEFORE)
    first = _sync(client, app)
    assert first["added"] == 4
    before = _messages(app)
    assert before["G-2"]["body"] == "Kom om 8 uur" and json.loads(before["G-2"]["reactions"]) == [
        {"actor": THEM, "reaction": "❤️"}]
    ids = dict(_rows(app, "SELECT msg_key, id FROM msg_messages"))

    _write(_after())
    second = _sync(client, app)
    assert second["added"] == 0 and second["revised"] == 3
    assert dict(_rows(app, "SELECT msg_key, id FROM msg_messages")) == ids          # updated, never a second row
    after = _messages(app)
    edited = after["G-2"]
    assert edited["body"] == "Kom om 9 uur" and edited["edited_at_ms"] == (T0 + 100) * 1000
    assert json.loads(edited["edits"]) == [{"at": (T0 + 20) * 1000, "body": "Kom om 8 uur"}]
    unsent = after["G-3"]
    assert unsent["body"] is None and unsent["kind"] == "unsent" and unsent["retracted_at_ms"] == (T0 + 200) * 1000
    assert json.loads(unsent["edits"]) == [{"at": (T0 + 25) * 1000, "body": "Iets wat ik terugneem"}]   # kept, not shown
    old = after["G-5"]
    assert old["body"] == "Lang geleden (verbeterd)" and json.loads(old["edits"])[0]["body"] == "Lang geleden"
    # The ❤️ taken back stays in the list as removed; my 👍 became 😂
    assert json.loads(after["G-2"]["reactions"]) == [{"actor": THEM, "reaction": "❤️", "removed": True}]
    assert json.loads(after["G-1"]["reactions"]) == [{"actor": "ik", "reaction": "👍", "removed": True},
                                                     {"actor": "ik", "reaction": "😂"}]
    # Search finds the text as it is now, not the earlier or unsent one
    hits = lambda q: [h["id"] for h in client.get("/api/messages/search", params={"q": q}).json()["results"]]
    assert hits("9 uur") == [ids["G-2"]] and hits("8 uur") == [] and hits("terugneem") == []
    # What the Berichten view gets
    thread = _thread(client, app)
    assert thread["Kom om 9 uur"]["edited_at"] == T0 + 100 and thread["Kom om 9 uur"]["reactions"] == []
    assert thread["Kom om 9 uur"]["edits"] == [{"at": T0 + 20, "body": "Kom om 8 uur"}]
    assert thread["unsent"]["retracted_at"] == T0 + 200 and thread["unsent"]["edits"][0]["body"] == "Iets wat ik terugneem"
    assert thread["Ben je thuis?"]["reactions"] == [{"actor": "ik", "reaction": "😂"}]
    assert thread["Ben je thuis?"]["edited_at"] is None and thread["Ben je thuis?"]["edits"] == []
    assert _rows(app, "SELECT message_count FROM msg_conversations")[0][0] == 4

    # Reading again: nothing changes
    snapshot = _rows(app, "SELECT * FROM msg_messages ORDER BY id")
    third = _sync(client, app)
    assert third["added"] == 0 and third["revised"] == 0
    assert _rows(app, "SELECT * FROM msg_messages ORDER BY id") == snapshot


def test_an_older_cursor_reads_everything_once_for_edits_it_could_not_see(client, app):
    _write(BEFORE)
    _sync(client, app)
    # As the previous version left it: a cursor without 'v', far after the edit of G-5
    with app.state.db._connect() as conn:
        state.set_cursor(conn, "imessage", "chat_db", {"last": T0 + 90 * DAY, "via": "live", "at": time.time()})
    rows = [dict(m) for m in BEFORE]
    rows[4].update(text="Lang geleden (verbeterd)", edited=T0 + 5 * DAY)
    _write(rows)
    assert _sync(client, app)["revised"] == 1
    assert _messages(app)["G-5"]["body"] == "Lang geleden (verbeterd)"
    assert state.get_cursor(app.state.db, "imessage", "chat_db")["v"] == 2


def test_migration_196_adds_the_edit_columns_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from backend.database import Database
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 195, monkeypatch)
    now = time.time()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO msg_conversations (source, conv_key, updated_at) VALUES ('imessage', 'c', 1.0)")
        conn.execute("INSERT INTO msg_messages (source, conv_id, msg_key, origin, sent_at_ms, ts_precision, body, "
                     "reactions, first_seen_at) VALUES ('imessage', 1, 'G-1', 'imessage', 1, 'ms', 'Hoi', "
                     "'[{\"actor\": \"ik\", \"reaction\": \"❤️\"}]', 1.0)")
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO app_settings VALUES ('chat_model', '\"qwen2.5:3b\"', 5.0)")
        conn.execute("INSERT INTO jobs (type, params, status, processed, created_at, updated_at, checkpoint) "
                     "VALUES ('index', '{\"full\": true}', 'paused', 12, ?, ?, '{\"folder\": 3}')", (now, now))
        conn.execute("INSERT INTO elo_ratings VALUES ('/v/RAW/a.jpg', 1612.5, 80.0, 0.06, 1590, 7, 1.0)")
        conn.execute("INSERT INTO dg_cursors (source_id, stream, cursor, updated_at) VALUES "
                     "('imessage', 'chat_db', '{\"last\": 5}', 1.0)")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                             "AND name NOT IN ('schema_version', 'msg_messages') "
                                             "AND name NOT LIKE 'msg_fts%'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
        messages_before = conn.execute("SELECT * FROM msg_messages ORDER BY id").fetchall()
    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert {195, 196} <= {r[0] for r in conn.execute("SELECT version FROM schema_version")}
        for table, kept in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == kept, table
        columns = [r[1] for r in conn.execute("PRAGMA table_info(msg_messages)")]
        assert columns[-3:] == ["edited_at_ms", "retracted_at_ms", "edits"]
        after = conn.execute("SELECT * FROM msg_messages ORDER BY id").fetchall()
        assert [r[:-3] for r in after] == messages_before and after[0][-3:] == (None, None, None)
        assert conn.execute("SELECT rowid FROM msg_fts WHERE msg_fts MATCH 'hoi'").fetchall() == [(1,)]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    db.close_all()
