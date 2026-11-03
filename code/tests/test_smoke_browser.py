"""
The browser smoke test (tests/ui/smoke_webkit.py) as one pytest test, opt-in:

    venv/bin/python -m pytest -m smoke          # only the smoke (5-10 minutes)
    venv/bin/python -m pytest -m "smoke or not smoke"   # everything

Skipped in a plain 'pytest' run. It needs a Python with Playwright and its WebKit: $DATAVAULT_SMOKE_PYTHON,
else this Python, else 'python3' on the PATH. The smoke starts its own server on a throwaway vault
(DATAVAULT_TEST_PORTS keeps it inside a port range). Its output streams with -s and is shown in full when
it fails. Any problem the smoke reports fails this test; nothing is retried here.

Time limit: $DATAVAULT_SMOKE_TIMEOUT seconds of wall-clock time (default 1800), counted while the output
streams. The smoke's own watchdog gets a minute less (DATAVAULT_SMOKE_WALL_SECONDS), so normally it stops
first and cleans up itself (says where it hung, stops its server, removes its temp folder). Should the
smoke still be running at the limit, its whole process group goes (the smoke, its uvicorn server, the
WebKit driver) and the temp folder it worked in is removed here.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

SMOKE = Path(__file__).resolve().parent / "ui" / "smoke_webkit.py"
REPO = SMOKE.parents[3]
TIMEOUT = int(os.environ.get("DATAVAULT_SMOKE_TIMEOUT", "1800"))
# conftest.py turns these off or points them at a test's temp folder for the in-process tests; the
# smoke's server is a whole app (automatic index passes, the DataGatherer watcher, backups) and sets
# what it needs itself
NOT_FOR_THE_SMOKE = ("DATAVAULT_AUTO_INDEX", "DATAVAULT_GATHER_DAEMON", "DATAVAULT_AUTO_BACKUP", "DATAVAULT_CONFIG",
                     "DATAVAULT_GATHER_HOME", "DATAVAULT_LAUNCH_AGENTS_DIR", "OTEL_SDK_DISABLED")


def _has_playwright(python: str) -> bool:
    try:
        return subprocess.run([python, "-c", "import playwright.sync_api"], capture_output=True,
                              timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _smoke_python():
    candidates = [os.environ.get("DATAVAULT_SMOKE_PYTHON"), sys.executable, shutil.which("python3")]
    return next((c for c in candidates if c and _has_playwright(c)), None)


def _stop_group(proc):
    """
    Ends the smoke and everything it started (it runs in a session of its own): TERM, then KILL for
    whatever is left in the group, also a child that outlived the smoke itself.
    """
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            pass


def run_smoke(cmd, env, timeout, cwd=REPO):
    """
    Runs the smoke and streams its output. Returns (exit code, output), or (None, output) when it was
    still running after `timeout` seconds of wall-clock time; then its whole process group is stopped.
    """
    lines = []
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                            start_new_session=True)

    def pump():
        for line in proc.stdout:
            lines.append(line)
            sys.stdout.write(line)          # live with -s, else under 'Captured stdout' on a failure

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        _stop_group(proc)
        return None, "".join(lines)
    finally:
        reader.join(30)
        proc.stdout.close()
    return proc.returncode, "".join(lines)


@pytest.mark.smoke
def test_browser_smoke_webkit():
    python = _smoke_python()
    if python is None:
        pytest.skip("no Python with Playwright found (set DATAVAULT_SMOKE_PYTHON, or pip install playwright)")
    env = {k: v for k, v in os.environ.items() if k not in NOT_FOR_THE_SMOKE}
    # The smoke's watchdog stops a hung run a minute before this test would, with its own clean-up
    env.setdefault("DATAVAULT_SMOKE_WALL_SECONDS", str(max(TIMEOUT - 60, TIMEOUT * 0.8)))
    work = Path(tempfile.mkdtemp(prefix="datavault-smoke-run-"))
    env["DATAVAULT_SMOKE_TMP"] = str(work)      # its server, vault and logs go here
    try:
        returncode, output = run_smoke([python, "-u", str(SMOKE), "webkit"], env, TIMEOUT)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if returncode is None:
        pytest.fail(f"the smoke ran longer than {TIMEOUT} s and was stopped (with its server):{output[-4000:]}")
    failed = output[output.find("\nFAILED:"):] if "\nFAILED:" in output else output[-4000:]
    assert returncode == 0 and "SMOKE OK" in output, f"the smoke failed (exit {returncode}):{failed}"
