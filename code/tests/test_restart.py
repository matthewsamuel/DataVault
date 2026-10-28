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


def _stop(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def test_restart_survives_a_closed_terminal(config_file, tmp_path):
    """
    R-2.1b-1: started from a Terminal (stdout/stderr on a tty), restarted, then the
    Terminal window is closed (tty revoked). A second restart must still bring the
    server back: the restarted server writes to the log file, never to the old tty.
    """
    import pty
    import select
    import threading

    port = _free_port()
    log_file = tmp_path / "logs" / "server.log"
    env = dict(os.environ, DATAVAULT_TESTING="1", DATAVAULT_CONFIG=str(config_file),
               DATAVAULT_LOG_FILE=str(log_file))
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--factory", "backend.main:create_app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(CODE_DIR), env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
    )
    os.close(slave)
    terminal = bytearray()
    closed = threading.Event()

    def drain():  # the Terminal window: shows whatever the server prints
        while not closed.is_set():
            if select.select([master], [], [], 0.1)[0]:
                try:
                    terminal.extend(os.read(master, 65536))
                except OSError:
                    return
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    base = f"http://127.0.0.1:{port}"
    pids = {proc.pid}

    def restart_once():
        before = _wait_health(base)
        token = httpx.get(f"{base}/api/session").json()["token"]
        assert httpx.post(f"{base}/api/restart", headers={"X-DataVault-Token": token}).status_code == 200
        after = _wait_health(base, timeout=30, not_instance=before["instance"])
        pids.add(after["pid"])
        return after

    try:
        restart_once()
        assert proc.wait(timeout=15) in (0, -signal.SIGTERM)
        closed.set()
        reader.join()
        os.close(master)  # the Terminal window is closed: its tty is revoked
        time.sleep(0.3)
        assert httpx.get(f"{base}/api/folders").status_code == 200

        third = restart_once()
        assert httpx.get(f"{base}/api/folders").status_code == 200
        assert third["pid"] not in (proc.pid,)
        assert b"uitvoer staat voortaan in" in terminal
        assert log_file.read_text().count("Application startup complete") >= 2
    finally:
        closed.set()
        for pid in pids - {proc.pid}:
            _stop(pid)
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        try:
            os.close(master)
        except OSError:
            pass


def test_forced_stages_use_sigint_then_a_hard_exit(monkeypatch):
    """
    R-2.1b-5: uvicorn ignores a second SIGTERM; only a second SIGINT forces it to stop
    waiting. And a synchronous request keeps a non-daemon worker thread alive, so the
    process must finally end itself.
    """
    import threading
    events = []
    done = threading.Event()
    monkeypatch.setattr(restart, "EXIT_DELAY", 0.01)
    monkeypatch.setattr(restart, "FORCE_EXIT_DELAY", 0.05)
    monkeypatch.setattr(restart, "HARD_EXIT_DELAY", 0.1)
    monkeypatch.setattr(restart, "_signal", lambda pid, sig: events.append((pid, sig)))
    monkeypatch.setattr(restart, "_hard_exit", lambda: (events.append("hard"), done.set()))
    restart._exit_soon(12345)
    assert done.wait(5)
    assert events == [(12345, signal.SIGTERM), (12345, signal.SIGINT), "hard"]


def test_hard_exit_never_cuts_an_archive_move(monkeypatch):
    """The last-resort exit waits until no archive move holds ARCHIVE_LOCK."""
    import threading
    from backend import file_ops
    exits = []
    in_move, finish_move = threading.Event(), threading.Event()

    def fake_exit(code):
        exits.append(code)
        # The real exit keeps the lock (nothing may start a move any more); let the other tests have it
        file_ops.ARCHIVE_LOCK.release()

    def move():
        with file_ops.ARCHIVE_LOCK:
            in_move.set()
            finish_move.wait(5)
    mover = threading.Thread(target=move)
    mover.start()
    assert in_move.wait(5)
    stopper = threading.Thread(target=restart._hard_exit, kwargs={"exit": fake_exit})
    stopper.start()
    time.sleep(0.3)
    assert exits == []  # still waiting for the move
    finish_move.set()
    mover.join()
    stopper.join(5)
    assert exits == [0]


SLOW_FACTORY = '''
import time
from backend import restart
from backend.main import create_app as _create_app

# Short stages, so the test does not wait the real 10 and 15 seconds
restart.FORCE_EXIT_DELAY = {force}
restart.HARD_EXIT_DELAY = {hard}


def create_app():
    app = _create_app()

    def slow(sec: float = 120):
        # stands in for a long synchronous request (timeline scan, largest-files walk, du)
        time.sleep(sec)
        return {{"slept": sec}}
    app.add_api_route("/slow", slow, methods=["GET"])
    app.router.routes.insert(0, app.router.routes.pop())  # before the static files mount
    return app
'''


@pytest.mark.parametrize("force,hard", [(1.5, 60), (60, 3)], ids=["sigint", "hard-exit"])
def test_restart_with_a_slow_request_still_comes_back(config_file, tmp_path, force, hard):
    """
    R-2.1b-5: a synchronous request that runs far longer than the restart must not block it.
    Each forced stage on its own is enough: the SIGINT, and the hard exit behind it.
    """
    import threading
    (tmp_path / "slowfactory.py").write_text(SLOW_FACTORY.format(force=force, hard=hard))
    port = _free_port()
    env = dict(os.environ, DATAVAULT_TESTING="1", DATAVAULT_CONFIG=str(config_file),
               PYTHONPATH=os.pathsep.join([str(tmp_path), str(CODE_DIR)]),
               DATAVAULT_LOG_FILE=str(tmp_path / "restarted.log"))
    log = open(tmp_path / "server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--factory", "slowfactory:create_app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(CODE_DIR), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    new_pid = None
    try:
        first = _wait_health(base)
        def slow_request():
            try:
                httpx.get(f"{base}/slow", timeout=180)
            except httpx.HTTPError:
                pass  # cut off by the restart, as expected
        threading.Thread(target=slow_request, daemon=True).start()
        time.sleep(0.5)  # the request is now sleeping in a worker thread
        token = httpx.get(f"{base}/api/session").json()["token"]
        started = time.monotonic()
        assert httpx.post(f"{base}/api/restart", headers={"X-DataVault-Token": token}).status_code == 200
        proc.wait(timeout=20)
        assert time.monotonic() - started < 15
        second = _wait_health(base, timeout=30, not_instance=first["instance"])
        new_pid = second["pid"]
        assert new_pid != proc.pid
        if hard < force:
            assert "stopt nu toch voor de herstart" in (tmp_path / "server.log").read_text()
    finally:
        if proc.poll() is None:
            proc.kill()  # our own test server
            proc.wait(timeout=10)
        if new_pid is None:
            time.sleep(1)
            try:
                new_pid = httpx.get(f"{base}/api/health", timeout=2).json()["pid"]
            except Exception:
                pass
        if new_pid and new_pid != proc.pid:
            _stop(new_pid)
        log.close()
