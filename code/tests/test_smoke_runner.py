"""
pytest -m smoke (tests/test_smoke_browser.py) keeps to its time limit: a smoke that hangs is stopped at
the limit, not when it ends by itself, together with what it started (its server), and its temp folder
goes. A fake smoke stands in for the browser one, so this runs in a plain pytest run.
"""
import json
import os
import sys
import textwrap
import time

import pytest

import test_smoke_browser as runner

HANGING_SMOKE = textwrap.dedent("""
    import json, os, subprocess, sys, tempfile, time
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])   # like its uvicorn server
    work = tempfile.mkdtemp(prefix="datavault-smoke-", dir=os.environ.get("DATAVAULT_SMOKE_TMP") or None)
    with open(sys.argv[1], "w") as f:
        json.dump({"child": child.pid, "work": work,
                   "wall": os.environ.get("DATAVAULT_SMOKE_WALL_SECONDS")}, f)
    print("step: hangs here", flush=True)
    time.sleep(120)
""")


def _gone(pid, within=10.0):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def _fake_smoke(tmp_path):
    script = tmp_path / "hanging_smoke.py"
    script.write_text(HANGING_SMOKE)
    return script, tmp_path / "seen.json"


def test_a_hanging_smoke_is_stopped_at_the_limit_with_its_server(tmp_path):
    script, seen = _fake_smoke(tmp_path)
    started = time.monotonic()
    # (its temp folder goes under tmp_path, not into the system temp folder it never gets to clean)
    env = dict(os.environ, DATAVAULT_SMOKE_TMP=str(tmp_path))
    returncode, output = runner.run_smoke([sys.executable, "-u", str(script), str(seen)], env, 3, cwd=tmp_path)
    spent = time.monotonic() - started
    assert returncode is None
    assert spent < 20, f"stopped after {spent:.1f} s instead of at the 3 s limit"
    assert "step: hangs here" in output
    assert _gone(json.loads(seen.read_text())["child"]), "the smoke's server outlived it"


def test_pytest_smoke_fails_at_its_limit_and_removes_the_temp_folder(tmp_path, monkeypatch):
    script, seen = _fake_smoke(tmp_path)
    monkeypatch.setattr(runner, "SMOKE", script)
    monkeypatch.setattr(runner, "TIMEOUT", 3)
    monkeypatch.setattr(runner, "_smoke_python", lambda: sys.executable)
    monkeypatch.delenv("DATAVAULT_SMOKE_WALL_SECONDS", raising=False)
    # (the real test passes 'webkit' as the argument; the fake writes what it saw next to it)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "REPO", tmp_path)
    real_run = runner.run_smoke
    monkeypatch.setattr(runner, "run_smoke", lambda cmd, env, timeout: real_run(cmd[:-1] + [str(seen)], env, timeout,
                                                                                  cwd=tmp_path))
    started = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match="longer than 3 s"):
        runner.test_browser_smoke_webkit()
    assert time.monotonic() - started < 20
    info = json.loads(seen.read_text())
    assert _gone(info["child"])
    assert not os.path.exists(info["work"]), "the smoke's temp folder was left behind"
    # The smoke's own watchdog is told to stop before pytest has to
    assert float(info["wall"]) < 3
