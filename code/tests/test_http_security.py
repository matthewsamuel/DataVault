"""
HTTP layer: no CORS, trusted hosts only, and a session token on every request
that can change something (so no other website can move or rate a file).
"""
import pytest

from fixtures.vault_builder import STRAND

EVIL = "https://evil.example"


def test_no_cors_headers_for_other_origins(client):
    for r in [
        client.get("/api/folders", headers={"Origin": EVIL}),
        client.get("/api/session", headers={"Origin": EVIL}),
        client.options("/api/move-file", headers={
            "Origin": EVIL,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-datavault-token,content-type",
        }),
    ]:
        assert "access-control-allow-origin" not in r.headers
        assert "access-control-allow-credentials" not in r.headers


@pytest.mark.parametrize("host", ["attacker.example", "evil.localhost.example", "127.0.0.2"])
def test_foreign_host_header_is_refused(client, host):
    """DNS rebinding: the page's own domain ends up in Host and must be refused."""
    assert client.get("/api/health", headers={"Host": host}).status_code == 400
    assert client.get("/api/session", headers={"Host": host}).status_code == 400
    r = client.post("/api/keurer/undo", json={"id": 1}, headers={"Host": host})
    assert r.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8802", "127.0.0.1"])
def test_own_host_is_accepted(client, host):
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_session_token_is_stable_per_process_and_not_cached(client, app):
    r1 = client.get("/api/session")
    r2 = client.get("/api/session")
    assert r1.json()["token"] == r2.json()["token"] == app.state.session_token
    assert len(r1.json()["token"]) >= 32
    assert "no-store" in r1.headers["cache-control"]


def test_session_token_refused_to_cross_site_fetch_metadata(client):
    assert client.get("/api/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/session", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert client.get("/api/session", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_every_new_app_gets_its_own_token(config_file):
    from backend.main import create_app
    assert create_app(str(config_file)).state.session_token != create_app(str(config_file)).state.session_token


def _mutating_requests(vault):
    photo = vault / STRAND
    return [
        ("post", "/api/move-file", {"json": {"source_path": str(photo), "destination_folder": str(vault / "library")}}),
        ("post", "/api/open-in-finder", {"json": {"path": str(photo)}}),
        ("post", "/api/keurer/decide", {"json": {"path": str(photo), "folder": str(photo.parent), "decision": "delete"}}),
        ("post", "/api/keurer/undo", {"json": {"id": 1}}),
        ("post", "/api/duplicates/delete_single", {"params": {"file_path": str(photo)}}),
        ("post", "/api/duplicates/scan_folder", {"params": {"folder_path": str(vault / "RAW")}}),
        ("post", "/api/raw_duplicates/undo_last", {}),
        ("post", "/api/raw_duplicates/cancel", {}),
        ("post", "/api/jobs", {"json": {"type": "thumbnails", "params": {"folder": str(vault / "RAW")}}}),
        ("post", "/api/jobs/1/cancel", {}),
        ("post", "/api/jobs/1/pause", {}),
        ("post", "/api/jobs/1/resume", {}),
        ("post", "/api/shows/settings", {"json": {"path": str(vault / "library"), "enabled": False}}),
        ("post", "/api/shows/vote", {"json": {"path": str(photo), "vote": "up"}}),
        ("post", "/api/chat", {"json": {"messages": []}}),
        ("post", "/api/restart", {}),
        ("put", "/api/folders", {}),
        ("delete", "/api/keurer/history", {}),
    ]


def test_mutating_requests_without_token_are_refused(anon_client, vault, config):
    photo = vault / STRAND
    for method, url, kwargs in _mutating_requests(vault):
        r = getattr(anon_client, method)(url, **kwargs)
        assert r.status_code == 403, (url, r.status_code, r.text)
        assert r.headers.get("x-datavault-auth") == "token"
    # Nothing happened: the photo is in place, nothing was trashed, no setting stored
    assert photo.exists()
    assert not config.TRASH_FOLDER.exists() or not any(config.TRASH_FOLDER.iterdir())
    assert anon_client.get("/api/shows/settings").json()["disabled_folders"] == []


def test_wrong_or_empty_token_is_refused(anon_client, vault):
    photo = vault / STRAND
    body = {"path": str(photo), "folder": str(photo.parent), "decision": "delete"}
    for token in ["", "nope", anon_client.get("/api/session").json()["token"][:-1]]:
        r = anon_client.post("/api/keurer/decide", json=body, headers={"X-DataVault-Token": token})
        assert r.status_code == 403
    assert photo.exists()


def test_token_in_query_or_cookie_is_not_enough(anon_client, vault):
    token = anon_client.get("/api/session").json()["token"]
    r = anon_client.post("/api/raw_duplicates/cancel", params={"X-DataVault-Token": token})
    assert r.status_code == 403
    anon_client.cookies.set("X-DataVault-Token", token)
    assert anon_client.post("/api/raw_duplicates/cancel").status_code == 403


def test_request_with_token_goes_through(anon_client, vault):
    token = anon_client.get("/api/session").json()["token"]
    photo = vault / STRAND
    r = anon_client.post(
        "/api/keurer/decide",
        json={"path": str(photo), "folder": str(photo.parent), "decision": "delete"},
        headers={"X-DataVault-Token": token},
    )
    assert r.status_code == 200, r.text
    assert not photo.exists()


def test_reads_need_no_token(anon_client):
    for url in ["/api/health", "/api/folders", "/api/keurer/stats", "/api/shows/settings", "/"]:
        assert anon_client.get(url).status_code == 200, url


# ---------- privacy: what the browser may load, and what the server log keeps ----------

def _csp(response):
    policy = response.headers.get("content-security-policy") or ""
    return {part.split()[0]: part.split()[1:] for part in (p.strip() for p in policy.split(";")) if part}


@pytest.mark.parametrize("url", ["/", "/index.html", "/app.js", "/api/health",
                                 "/api/datagatherer/spotify/callback?code=x&state=y"])
def test_every_response_carries_a_content_security_policy(client, url):
    """PRIV-3: the browser itself refuses any load or connection to another host."""
    policy = _csp(client.get(url))
    assert policy["default-src"] == ["'self'"]
    assert policy["connect-src"] == ["'self'"]
    # The one internet host: the opt-in OpenStreetMap tiles, images only
    assert policy["img-src"] == ["'self'", "data:", "blob:", "https://tile.openstreetmap.org"]
    for directive in ("script-src", "style-src", "font-src", "media-src", "worker-src"):
        assert not [s for s in policy[directive] if "://" in s or s in ("*", "https:", "http:", "wss:")], directive
    assert policy["object-src"] == ["'none'"] and policy["frame-ancestors"] == ["'none'"]


def test_query_strings_never_reach_the_access_log(app, caplog):
    """PRIV-5: an OAuth callback (?code=...&state=...) is logged as its path only."""
    import logging
    logger = logging.getLogger("uvicorn.access")
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        # uvicorn's own call (h11_impl / httptools_impl)
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:61242", "GET",
                    "/api/datagatherer/spotify/callback?code=AQB_fake_auth_code_123&state=zzz", "1.1", 200)
    text = caplog.text
    assert "/api/datagatherer/spotify/callback HTTP/1.1" in text
    assert "AQB_fake_auth_code_123" not in text and "state=" not in text
