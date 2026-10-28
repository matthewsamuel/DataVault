"""
Chat with Ollama: a model chosen in the UI and stored in app_settings (migration 10), and
a clear Dutch message for every failure instead of an endless spinner (SPOTIFY-CHAT-2,
LIVE-PROBE-13). Ollama is the FakeOllama from conftest (no real Ollama is used).
"""
import json
import sqlite3

import httpx
import pytest

from backend.database import Database
from backend.routes import chat


def _events(client, **body):
    body.setdefault("messages", [{"role": "user", "content": "hoi"}])
    r = client.post("/api/chat/stream", json=body)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def test_the_default_model_is_light_and_answers(client, fake_ollama):
    assert chat.DEFAULT_MODEL == "qwen2.5:3b"
    events = _events(client)
    assert "".join(e.get("token", "") for e in events) == "Hallo daar, ik ben Pasha."
    assert events[-1] == {"done": True}
    sent = json.loads(fake_ollama.requests[-1].content)
    assert sent["model"] == "qwen2.5:3b"


def test_the_model_is_chosen_in_the_ui_and_remembered(client, fake_ollama, app):
    fake_ollama.models = ["qwen2.5:3b", "llama3.2:3b"]
    data = client.get("/api/chat/models").json()
    assert data["current"] == "qwen2.5:3b" and data["default"] == "qwen2.5:3b" and data["reachable"]
    assert [m["name"] for m in data["models"]] == ["llama3.2:3b", "qwen2.5:3b"]
    assert client.post("/api/chat/model", json={"model": "llama3.2:3b"}).json() == {"current": "llama3.2:3b"}
    assert client.get("/api/chat/models").json()["current"] == "llama3.2:3b"
    _events(client)
    assert json.loads(fake_ollama.requests[-1].content)["model"] == "llama3.2:3b"
    # Stored in the database: a restarted server still knows it
    assert app.state.db.get_setting("chat_model") == "llama3.2:3b"
    # Not installed, or not a model name at all
    r = client.post("/api/chat/model", json={"model": "mistral:7b"})
    assert r.status_code == 400 and "ollama pull mistral:7b" in r.json()["detail"]
    for bad in ("", "../../etc", 5, None, "a b"):
        assert client.post("/api/chat/model", json={"model": bad}).status_code == 400
    assert client.post("/api/chat/stream", json={"messages": [], "model": "; rm"}).status_code == 400


def test_a_missing_model_gives_a_clear_message(client, fake_ollama):
    fake_ollama.models = []
    [event] = _events(client)
    assert event["code"] == "model_missing"
    assert "niet geïnstalleerd" in event["error"] and "ollama pull qwen2.5:3b" in event["error"]


def test_ollama_down_gives_a_clear_message(client, fake_ollama):
    fake_ollama.down = True
    [event] = _events(client)
    assert event["code"] == "ollama_down" and "Ollama draait niet" in event["error"]
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hoi"}]})
    assert r.status_code == 503 and "Ollama draait niet" in r.json()["detail"]


def test_a_timeout_gives_a_clear_message(client, fake_ollama):
    fake_ollama.chat_error = httpx.ReadTimeout("te traag")
    [event] = _events(client)
    assert event["code"] == "timeout" and "niet op tijd" in event["error"]


def test_an_error_halfway_or_a_stream_without_end_is_reported(client, fake_ollama):
    fake_ollama.chat_lines = [{"message": {"content": "Een lang antwoord dat begint"}, "done": False},
                              {"error": "out of memory"}]
    events = _events(client)
    assert events[-1]["code"] == "ollama_error" and "out of memory" in events[-1]["error"]
    fake_ollama.chat_lines = [{"message": {"content": "zonder einde"}, "done": False}]
    events = _events(client)
    assert events[-1]["code"] == "incomplete"
    fake_ollama.chat_lines = None
    fake_ollama.chat_status = 500
    assert _events(client)[-1]["code"] == "ollama_error"


def test_migration_10_adds_settings_and_keeps_every_user_row(tmp_path, monkeypatch):
    from backend import migrations
    from test_duplicates_runs import _migrate_to
    from test_foundation import _make_legacy_db
    db_path = tmp_path / "datavault.db"
    _make_legacy_db(db_path)
    _migrate_to(db_path, 9, monkeypatch)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO keurer_decisions (path, folder, decision, dest_path, dest_ino, dest_size) "
                     "VALUES ('/v/RAW/weg.jpg', '/v/RAW', 'delete', '/v/TRASH/weg.jpg', 77, 1234)")
        conn.execute("INSERT INTO media_dates VALUES ('/v/RAW/a.jpg', 1, 2, 3.0, 'exif', 4.0)")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version'")]
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}

    db = Database(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        assert migrations.get_schema_version(conn) == migrations.LATEST_VERSION >= 10
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows, table
        assert {r[1] for r in conn.execute("PRAGMA table_info(app_settings)")} == {"key", "value", "updated_at"}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(list((tmp_path / migrations.BACKUP_DIR_NAME).glob("datavault-v9-*.db"))) == 1
    assert db.get_rating("/v/RAW/a.jpg") == 1150
    assert db.get_keurer_stats()["streak"] == 3
    assert db.get_delete_operations("run-1")[0]["dst_path"] == "/v/RAW_DUPLICATES/b.jpg"
    assert db.get_recent_keurer_decisions(1)[0]["dest_ino"] == 77
    assert db.get_setting("chat_model", "standaard") == "standaard"
    db.set_setting("chat_model", "qwen2.5:3b")
    assert db.get_setting("chat_model") == "qwen2.5:3b"
    db.close_all()
