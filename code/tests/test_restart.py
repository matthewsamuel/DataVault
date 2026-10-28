"""
/api/restart restarts only its own process: a helper waits for this PID to exit
and starts the server again with the same interpreter, arguments and environment.
No port-wide kill (the old version killed everything on port 8000).
"""
import inspect
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from backend import main as main_mod
from backend import restart

CODE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_real_restart(monkeypatch):
    calls = {"spawn": [], "exit": []}
    monkeypatch.setattr(restart, "_spawn_helper", lambda pid, argv, cwd: calls["spawn"].append((pid, argv, cwd)))
    monkeypatch.setattr(restart, "_exit_soon", lambda pid: calls["exit"].append(pid))
    monkeypatch.setattr(restart, "_restart_scheduled", False)
    return calls


def test_restart_relaunches_this_process_only(client, no_real_restart, monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "uvicorn", "--factory", "backend.main:create_app",
                                           "--host", "127.0.0.1", "--port", "8123"])
    r = client.post("/api/restart")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "restarting", "pid": os.getpid()}
    [(pid, argv, cwd)] = no_real_restart["spawn"]
    assert pid == os.getpid()
    assert argv == [sys.executable, "-m", "uvicorn", "--factory", "backend.main:create_app",
                    "--host", "127.0.0.1", "--port", "8123"]
    assert cwd == os.getcwd()
    assert no_real_restart["exit"] == [os.getpid()]


def test_concurrent_restarts_spawn_one_helper(client, no_real_restart, monkeypatch):
    """R-2.1b-2: a second request (another tab) gets 409 instead of a second relaunch."""
    import threading
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "uvicorn", "--factory", "backend.main:create_app"])
    spawned = threading.Event()

    def slow_spawn(pid, argv, cwd):
        time.sleep(0.2)  # both requests are inside schedule_restart at the same time
        no_real_restart["spawn"].append((pid, argv, cwd))
        spawned.set()
    monkeypatch.setattr(restart, "_spawn_helper", slow_spawn)
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(client.post("/api/restart").status_code))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409]
    assert len(no_real_restart["spawn"]) == 1 and no_real_restart["exit"] == [os.getpid()]
    r = client.post("/api/restart")
    assert r.status_code == 409 and r.json()["detail"] == "Herstart is al bezig"
    assert len(no_real_restart["spawn"]) == 1


def test_failed_spawn_can_be_retried(client, no_real_restart, monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", ["python", "-m", "uvicorn", "--factory", "backend.main:create_app"])

    def broken(pid, argv, cwd):
        raise OSError("no fork")
    monkeypatch.setattr(restart, "_spawn_helper", broken)
    with pytest.raises(OSError):
        client.post("/api/restart")
    assert restart._restart_scheduled is False and no_real_restart["exit"] == []


def test_restart_needs_the_session_token(anon_client, no_real_restart):
    assert anon_client.post("/api/restart").status_code == 403
    assert anon_client.get("/api/restart").status_code in (404, 405)  # GET never restarts
    assert no_real_restart["spawn"] == [] and no_real_restart["exit"] == []


def test_restart_refused_under_reload_worker(client, no_real_restart, monkeypatch):
    monkeypatch.setattr(sys, "orig_argv", [sys.executable, "-c", "from multiprocessing.spawn import spawn_main",
                                           "--multiprocessing-fork"])
    assert client.post("/api/restart").status_code == 409
    assert no_real_restart["spawn"] == []


def test_restart_code_never_kills_by_port():
    source = inspect.getsource(restart) + inspect.getsource(main_mod)
    assert "lsof" not in source and ":8000" not in source and "kill -9" not in source


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(base, timeout=20, not_instance=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base}/api/health", timeout=1)
            if r.status_code == 200 and r.json()["instance"] != not_instance:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("server did not come up")


def test_restart_end_to_end(config_file, tmp_path):
    """A real server on a free port restarts itself: new process, same port, new token."""
    port = _free_port()
    env = dict(os.environ, DATAVAULT_TESTING="1", DATAVAULT_CONFIG=str(config_file))
    log = open(tmp_path / "server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--factory", "backend.main:create_app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(CODE_DIR), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    new_pid = None
    try:
        first = _wait_health(base)
        assert first["pid"] == proc.pid
        token = httpx.get(f"{base}/api/session").json()["token"]
        assert httpx.post(f"{base}/api/restart").status_code == 403  # no token, nothing happens
        time.sleep(0.8)
        assert proc.poll() is None

        r = httpx.post(f"{base}/api/restart", headers={"X-DataVault-Token": token})
        assert r.status_code == 200 and r.json()["pid"] == proc.pid
        # The old process stopped by itself (uvicorn re-raises SIGTERM after a clean shutdown)
        assert proc.wait(timeout=15) in (0, -signal.SIGTERM)
        assert "Finished server process" in (tmp_path / "server.log").read_text()

        second = _wait_health(base, not_instance=first["instance"])
        new_pid = second["pid"]
        assert new_pid != proc.pid
        new_token = httpx.get(f"{base}/api/session").json()["token"]
        assert new_token != token
        assert httpx.post(f"{base}/api/raw_duplicates/cancel", headers={"X-DataVault-Token": token}).status_code == 403
        assert httpx.post(f"{base}/api/raw_duplicates/cancel", headers={"X-DataVault-Token": new_token}).status_code == 200
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        if new_pid is None:  # the test failed half-way: find a restarted server on our port
            time.sleep(1)
            try:
                new_pid = httpx.get(f"{base}/api/health", timeout=2).json()["pid"]
            except Exception:
                pass
        if new_pid and new_pid != proc.pid:
            try:
                os.kill(new_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        log.close()
