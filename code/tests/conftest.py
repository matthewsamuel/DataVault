"""
Shared pytest fixtures. Every test gets its own copy of a generated test vault
in a temp directory; DATAVAULT_TESTING=1 makes Config refuse any vault outside
the temp directory, so tests can never touch a real archive.
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

os.environ["DATAVAULT_TESTING"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["DATAVAULT_AUTO_INDEX"] = "0"   # tests start index passes themselves (see test_media_index)
os.environ["DATAVAULT_GATHER_DAEMON"] = "0"   # tests tick the DataGatherer watcher/schedules themselves
os.environ["DATAVAULT_AUTO_BACKUP"] = "0"  # tests make backups themselves (see test_backup)
os.environ.pop("DATAVAULT_CONFIG", None)

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CODE_DIR / "tests"))

from fixtures.vault_builder import build_test_vault, write_config  # noqa: E402


def pytest_collection_modifyitems(config, items):
    """The browser smoke (marker 'smoke') only runs when asked for: pytest -m smoke."""
    if "smoke" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="browser smoke: run it with 'pytest -m smoke'")
    for item in items:
        if "smoke" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch):
    """No test talks to a real Ollama: the chat's HTTP clients go to a FakeOllama."""
    import httpx
    from backend.routes import chat
    from helpers import FakeOllama
    fake = FakeOllama()
    transport = httpx.MockTransport(fake.handler)
    monkeypatch.setattr(chat, "_http_client", lambda **kw: httpx.Client(transport=transport, **kw))
    monkeypatch.setattr(chat, "_async_http_client", lambda **kw: httpx.AsyncClient(transport=transport, **kw))
    chat.forget_ollama_status()
    yield fake
    chat.forget_ollama_status()


@pytest.fixture(autouse=True)
def datagatherer_sandbox(monkeypatch, tmp_path):
    """
    No test reaches the real Keychain, the network, ~/Downloads, ~/Library/LaunchAgents or
    the real chat databases (DATAVAULT_GATHER_HOME is a temp folder):
    a fresh in-memory keyring, a network guard that refuses everything (tests install an
    httpx.MockTransport with net.set_transport_factory), a LaunchAgents folder and a
    launchctl stand-in in the temp folder, and nothing remembered between tests.
    """
    import keyring
    from backend.datagatherer import cards, daemon, keychain, launchd, net, oauth
    keyring.set_keyring(keychain.MemoryKeyring())
    net.set_transport_factory(None)
    calls = []
    launchd.set_runner(lambda args: calls.append(args) or 0)
    monkeypatch.setenv("DATAVAULT_LAUNCH_AGENTS_DIR", str(tmp_path / "LaunchAgents"))
    # The chat databases (iMessage, WhatsApp for Mac) are looked for in a made-up home folder
    monkeypatch.setenv("DATAVAULT_GATHER_HOME", str(tmp_path / "fake-home"))
    oauth.forget_all()
    daemon.forget_seen()
    cards.forget_existing_cache()
    yield calls
    net.set_transport_factory(None)
    launchd.set_runner(None)


@pytest.fixture(scope="session")
def pristine_vault(tmp_path_factory) -> Path:
    """Built once per session; never modified (tests get copies)."""
    return build_test_vault(tmp_path_factory.mktemp("pristine") / "vault")


@pytest.fixture
def vault(pristine_vault, tmp_path) -> Path:
    """A fresh, writable copy of the test vault for one test."""
    target = tmp_path / "vault"
    shutil.copytree(pristine_vault, target, symlinks=True)
    return target


@pytest.fixture
def config_file(vault, tmp_path) -> Path:
    return write_config(vault, tmp_path / "config.json")


@pytest.fixture
def config(config_file):
    from backend.config import Config
    return Config(str(config_file))


@pytest.fixture
def app(config_file):
    from backend.main import create_app
    return create_app(str(config_file))


@pytest.fixture
def client(app):
    """Like the real frontend: every request carries the session token (see api.js)."""
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        c.headers["X-DataVault-Token"] = c.get("/api/session").json()["token"]
        yield c


@pytest.fixture
def anon_client(app):
    """A client without the session token, like another website or a curl one-liner."""
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c
