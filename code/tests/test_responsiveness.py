"""
The event loop is never blocked (SCAN-DUP-DB-1, PERF-1, PERF-5, MEDIA-THUMBS-TIMELINE-4,
KEURER-SHOWS-11, SPOTIFY-CHAT-3): only routes that truly await I/O are async.
"""
import inspect
import os
import subprocess
import sys
import threading
import time

import httpx
from fastapi.routing import APIRoute

from test_restart import CODE_DIR, _free_port, _wait_health

# Routes that may be 'async def': they await Ollama over HTTP and build their prompt
# (which reads the disk) via run_in_threadpool; DataGatherer's upload awaits the request body
# and does every disk step in the thread pool
ASYNC_ALLOWED = {"chat_stream", "chat_with_ollama", "upload"}
CHAT_ASYNC = {"chat_stream", "chat_with_ollama"}


def _api_routes(routes):
    """Every APIRoute, also inside included routers (FastAPI keeps those as one entry)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _api_routes(inner.routes)


def test_only_truly_async_routes_are_async_def(app):
    routes = list(_api_routes(app.routes))
    assert len(routes) > 40
    async_routes = {route.endpoint.__name__ for route in routes if inspect.iscoroutinefunction(route.endpoint)}
    assert async_routes == ASYNC_ALLOWED
    from backend.routes import chat
    for name in CHAT_ASYNC:
        source = inspect.getsource(getattr(chat, name))
        assert "build_system_prompt()" not in source and "run_in_threadpool(build_system_prompt" in source
    from backend.routes import datagatherer
    source = inspect.getsource(datagatherer.upload)
    # Receiving the stream is async; writing, recognising and starting the copy job run in the thread pool
    assert "run_in_threadpool(_start" in source and "run_in_threadpool(lambda: connector.detect" in source
    assert ".write(" not in source.replace("out.write, ", "")


SLOW_FACTORY = '''
import time
from backend import scanner
from backend.routes import spotify, timeline
from backend.main import create_app as _create_app

# Every file takes a while to hash and to read for the timeline, and parsing the
# Spotify export takes 4 s: long, blocking work, like a real archive on a USB disk
_hash = scanner.hash_file_xxh3
def _slow_hash(path):
    time.sleep(0.01)
    return _hash(path)
scanner.hash_file_xxh3 = _slow_hash

_scan = timeline._walk_media
def _slow_scan(roots, *args):
    time.sleep(5)
    return _scan(roots, *args)
timeline._walk_media = _slow_scan

_parse = spotify.parse_spotify_data
def _slow_parse(*args, **kwargs):
    time.sleep(4)
    return _parse(*args, **kwargs)
spotify.parse_spotify_data = _slow_parse


def create_app():
    return _create_app()
'''


def test_health_answers_fast_while_a_scan_job_and_slow_requests_run(vault, config_file, tmp_path):
    folder = vault / "Druk"
    folder.mkdir()
    for i in range(600):
        (folder / f"f{i:04d}.jpg").write_bytes(b"d" * 2048)  # one big group: 600 files to hash
    (tmp_path / "slowapp.py").write_text(SLOW_FACTORY)
    port = _free_port()
    env = dict(os.environ, DATAVAULT_TESTING="1", DATAVAULT_CONFIG=str(config_file),
               PYTHONPATH=os.pathsep.join([str(tmp_path), str(CODE_DIR)]),
               DATAVAULT_LOG_FILE=str(tmp_path / "restarted.log"))
    log = open(tmp_path / "server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "--factory", "slowapp:create_app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(CODE_DIR), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_health(base)
        token = httpx.get(f"{base}/api/session").json()["token"]
        r = httpx.post(f"{base}/api/jobs", headers={"X-DataVault-Token": token},
                       json={"type": "dup_scan", "params": {"folder": str(folder)}})
        job_id = r.json()["job"]["id"]

        slow_done = []
        def slow(url):
            slow_done.append(httpx.get(url, timeout=60).status_code)
        threads = [threading.Thread(target=slow, args=(url,), daemon=True) for url in (
            f"{base}/api/timeline/scan?folders={vault / 'RAW'}",
            f"{base}/api/spotify/stats",
            f"{base}/api/spotify/stats?year=2023",
        )]
        for t in threads:
            t.start()
        time.sleep(0.3)

        timings = []
        seen_running = False
        for _ in range(10):
            started = time.monotonic()
            assert httpx.get(f"{base}/api/health", timeout=5).status_code == 200
            timings.append(time.monotonic() - started)
            job = httpx.get(f"{base}/api/jobs/{job_id}", timeout=5).json()
            seen_running |= job["status"] == "running" and 0 < job["processed"] < 600
            time.sleep(0.1)
        assert max(timings) < 0.5, timings
        assert seen_running, "the scan job should have been running (and reporting progress) meanwhile"
        assert slow_done == [], "the slow requests should still have been running"

        for t in threads:
            t.join(30)
        assert slow_done == [200, 200, 200]
        deadline = time.time() + 60
        while time.time() < deadline:
            job = httpx.get(f"{base}/api/jobs/{job_id}").json()
            if job["status"] == "done":
                break
            time.sleep(0.2)
        assert job["status"] == "done" and job["summary"]["duplicates_found"] == 599
    finally:
        proc.terminate()  # our own test server
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        log.close()
