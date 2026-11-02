"""
The browser smoke test (tests/ui/smoke_webkit.py) as one pytest test, opt-in:

    venv/bin/python -m pytest -m smoke          # only the smoke (5-10 minutes)
    venv/bin/python -m pytest -m "smoke or not smoke"   # everything

Skipped in a plain 'pytest' run. It needs a Python with Playwright and its WebKit: $DATAVAULT_SMOKE_PYTHON,
else this Python, else 'python3' on the PATH. The smoke starts its own server on a throwaway vault
(DATAVAULT_TEST_PORTS keeps it inside a port range). Its output streams with -s and is shown in full when
it fails. Any problem the smoke reports fails this test; nothing is retried here.
"""
import os
import shutil
import subprocess
import sys
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


@pytest.mark.smoke
def test_browser_smoke_webkit():
    python = _smoke_python()
    if python is None:
        pytest.skip("no Python with Playwright found (set DATAVAULT_SMOKE_PYTHON, or pip install playwright)")
    lines = []
    env = {k: v for k, v in os.environ.items() if k not in NOT_FOR_THE_SMOKE}
    with subprocess.Popen([python, "-u", str(SMOKE), "webkit"], cwd=REPO, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, env=env) as proc:
        try:
            for line in proc.stdout:
                lines.append(line)
                sys.stdout.write(line)          # live with -s, else under 'Captured stdout' on a failure
            proc.wait(TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail(f"the smoke ran longer than {TIMEOUT} s")
    output = "".join(lines)
    failed = output[output.find("\nFAILED:"):] if "\nFAILED:" in output else output[-4000:]
    assert proc.returncode == 0 and "SMOKE OK" in output, f"the smoke failed (exit {proc.returncode}):{failed}"
