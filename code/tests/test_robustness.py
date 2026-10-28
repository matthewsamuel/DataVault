"""
Robustness (SCAN-DUP-DB-12, MEDIA-THUMBS-TIMELINE-16/-17, LIVE-PROBE-18, FRONTEND-ERR-1):
one error shape for every error, a 503 from every vault endpoint while the disk is gone
(also halfway through a request), recovery without a restart, an app that starts without
the vault, and a health check that reports what this Mac can do.
"""
import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import errors
from backend.routes import chat
from fixtures.vault_builder import STRAND

VAULT_GET_ENDPOINTS = [
    ("/api/folders", {}),
    ("/api/media", {"folder": "RAW"}),
    ("/api/file", {"path": STRAND}),
    ("/api/thumb", {"path": STRAND}),
    ("/api/keurer/files", {"folder": "RAW/2019/zomer vakantie"}),
    ("/api/keurer/stats", {}),
    ("/api/keurer/history", {}),
    ("/api/stats/storage", {}),
    ("/api/stats/storage-breakdown", {}),
    ("/api/stats/largest-files", {}),
    ("/api/stats/photo-on-this-day", {}),
    ("/api/stats/photos-by-date", {"date": "2019-07-14"}),
    ("/api/stats/search-photos", {"query": "strand"}),
    ("/api/stats/spotify-on-this-day", {}),
    ("/api/spotify/stats", {}),
    ("/api/spotify/years", {}),
    ("/api/timeline/folders", {}),
    ("/api/timeline/scan", {"folders": "RAW"}),
    ("/api/shows/stories", {}),
    ("/api/shows/settings", {}),
    ("/api/duplicates/runs", {}),
    ("/api/raw_duplicates", {}),
]


def _is_vault_503(r):
    body = r.json()
    return (r.status_code == 503 and body["error"]["code"] == "vault_unavailable"
            and body["error"]["message"] == errors.VAULT_UNREACHABLE)


def test_every_error_has_the_same_shape(client, anon_client, vault):
    r = client.get("/api/file", params={"path": "RAW/bestaat-niet.jpg"})
    assert r.status_code == 404
    assert r.json()["error"] == {"code": "not_found", "message": r.json()["detail"]}
    r = client.get("/api/file", params={"path": "/etc/passwd"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    r = client.get("/api/media")  # missing parameter
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert "folder" in r.json()["error"]["message"]
    r = client.post("/api/duplicates/undo", json={"run_id": 5})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    r = anon_client.post("/api/move-file", json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "session_token"
    r = client.get("/api/geen-route")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.get("/api/stats/photos-by-date", params={"date": "gisteren"})
    assert r.status_code == 400 and "JJJJ-MM-DD" in r.json()["error"]["message"]


def test_every_vault_endpoint_answers_503_while_the_disk_is_gone_and_recovers(client, vault, config):
    away = vault.with_name("vault_los")
    os.rename(vault, away)
    try:
        for url, params in VAULT_GET_ENDPOINTS:
            r = client.get(url, params=params)
            assert _is_vault_503(r), (url, r.status_code, r.text)
        for url, body in [("/api/keurer/decide", {"path": "x", "folder": "y", "decision": "keep"}),
                          ("/api/move-file", {"source_path": "a", "destination_folder": "b"}),
                          ("/api/shows/vote", {"path": "x", "vote": "up"}),
                          ("/api/duplicates/undo", {"run_id": "x"})]:
            assert _is_vault_503(client.post(url, json=body)), url
        health = client.get("/api/health").json()
        assert health["vault_available"] is False and health["vault_message"] == errors.VAULT_UNREACHABLE
        assert not vault.exists()  # nothing was recreated at the old path
    finally:
        os.rename(away, vault)
    # Back without a restart
    for url, params in VAULT_GET_ENDPOINTS:
        r = client.get(url, params=params)
        assert r.status_code == 200, (url, r.status_code, r.text)
    assert client.get("/api/health").json()["vault_available"] is True


def test_a_disk_that_goes_away_halfway_through_a_request_is_a_503(client, vault, monkeypatch):
    from backend import scanner
    away = vault.with_name("vault_weg")
    real = scanner.get_media_files

    def unplugged_halfway(folder, archive_filter):
        os.rename(vault, away)
        return [{"path": p.name} for p in Path(folder).iterdir()]  # FileNotFoundError now

    monkeypatch.setattr(scanner, "get_media_files", unplugged_halfway)
    try:
        assert _is_vault_503(client.get("/api/media", params={"folder": "RAW"}))
    finally:
        os.rename(away, vault)
    monkeypatch.setattr(scanner, "get_media_files", real)
    assert client.get("/api/media", params={"folder": "RAW"}).status_code == 200


def test_an_io_error_without_a_missing_vault_is_a_clear_json_error(client, vault, monkeypatch):
    from backend import scanner

    def broken(folder, archive_filter):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(scanner, "get_media_files", broken)
    r = client.get("/api/media", params={"folder": "RAW"})
    assert r.status_code == 403 and r.json()["error"]["message"] == "Geen toegang tot dit bestand of deze map"


def test_the_app_starts_without_the_vault_and_opens_it_when_it_comes_back(vault, config_file, config):
    from backend.main import create_app
    # A job a previous server left running: recovered once the database opens
    with TestClient(create_app(str(config_file))) as c:
        pass
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("INSERT INTO jobs (type, params, status, created_at, updated_at) "
                     "VALUES ('hashing', '{}', 'running', 1, 1)")
        conn.execute("INSERT INTO file_ratings (path, rating) VALUES ('/x.jpg', 1234)")
    away = vault.with_name("vault_niet_gemount")
    os.rename(vault, away)
    try:
        app = create_app(str(config_file))
        with TestClient(app) as c:
            c.headers["X-DataVault-Token"] = c.get("/api/session").json()["token"]
            health = c.get("/api/health").json()
            assert health["vault_available"] is False and health["database_ready"] is False
            assert _is_vault_503(c.get("/api/folders"))
            assert _is_vault_503(c.get("/api/keurer/stats"))
            assert _is_vault_503(c.get("/api/jobs"))
            assert not vault.exists()  # no folder was made at the vault's place
            os.rename(away, vault)
            # Routers were initialised anyway; the database opens on first use
            assert c.get("/api/folders").status_code == 200
            assert c.get("/api/keurer/stats").status_code == 200
            assert c.get("/api/health").json()["database_ready"] is True
            [job] = c.get("/api/jobs").json()["jobs"]
            assert job["status"] == "interrupted"
            assert app.state.db.get_rating("/x.jpg") == 1234
    finally:
        if away.exists():
            os.rename(away, vault)


def test_health_reports_capabilities_and_notices(client, monkeypatch, fake_ollama):
    from backend import main, thumbnail
    data = client.get("/api/health").json()
    caps = data["capabilities"]
    assert set(caps) == {"ffmpeg", "ffprobe", "pillow_heif", "sips", "chat"}
    assert caps["pillow_heif"] is True and caps["chat"]["model"] == chat.DEFAULT_MODEL
    real_which = main.shutil.which
    monkeypatch.setattr(main.shutil, "which", lambda name: None if name in ("ffmpeg", "ffprobe") else real_which(name))
    monkeypatch.setattr(thumbnail, "HEIF_SUPPORT", False)
    data = client.get("/api/health").json()
    assert data["capabilities"]["ffmpeg"] is False and data["capabilities"]["ffprobe"] is False
    codes = {n["code"]: n for n in data["notices"]}
    assert "brew install ffmpeg" in codes["ffmpeg"]["message"]
    assert "pillow_heif" in codes


def test_health_reports_the_chat_capability(client, fake_ollama):
    caps = client.get("/api/health").json()["capabilities"]
    assert caps["chat"]["reachable"] and caps["chat"]["model_installed"] and caps["chat"]["message"] is None
    fake_ollama.models = ["llama3.2:3b"]
    chat.forget_ollama_status()
    caps = client.get("/api/health").json()["capabilities"]
    assert caps["chat"]["model_installed"] is False and "ollama pull qwen2.5:3b" in caps["chat"]["message"]
    fake_ollama.down = True
    chat.forget_ollama_status()
    caps = client.get("/api/health").json()["capabilities"]
    assert caps["chat"]["reachable"] is False and "Ollama draait niet" in caps["chat"]["message"]


