"""
start.sh picks the browser from config.json ("browser"), Safari by default. plutil prints its
error on stdout when the key is missing; that text must never become the browser name.
Run on a temporary copy with --print-browser, which starts nothing: no server, no browser,
no port. Fake lsof/open/curl come first on PATH as a safety net.
"""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")


def _copy(tmp_path):
    work = tmp_path / "code"
    work.mkdir()
    shutil.copy2(CODE / "start.sh", work / "start.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "called.log"
    for tool in ("lsof", "open", "curl", "kill"):
        fake = bin_dir / tool
        fake.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\nexit 1\n')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return work, bin_dir, log


def _browser(work, bin_dir, config=None, env_config=None):
    if config is not None:
        (work / "config.json").write_text(config if isinstance(config, str) else json.dumps(config))
    env = {k: v for k, v in os.environ.items() if k != "DATAVAULT_CONFIG"}
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin"
    if env_config:
        env["DATAVAULT_CONFIG"] = str(env_config)
    r = subprocess.run(["/bin/bash", str(work / "start.sh"), "--print-browser"], cwd=work, env=env,
                       capture_output=True, text=True, timeout=30)
    return r


@pytest.mark.parametrize("config, expected", [
    ({"vault_root": "/tmp/x"}, "Safari"),                              # no 'browser': Safari
    ({"vault_root": "/tmp/x", "browser": "Firefox"}, "Firefox"),
    ({"vault_root": "/tmp/x", "browser": "Google Chrome"}, "Google Chrome"),
    ({"vault_root": "/tmp/x", "browser": ""}, "Safari"),              # empty: Safari
    ({"vault_root": "/tmp/x", "browser": "   "}, "Safari"),           # blank: Safari
    ({"vault_root": "/tmp/x", "browser": {"a": 1}}, "Safari"),        # not a string: Safari
    ({"vault_root": "/tmp/x", "browser": ["Firefox"]}, "Safari"),
    ({"vault_root": "/tmp/x", "browser": 5}, "Safari"),
    ({"vault_root": "/tmp/x", "browser": True}, "Safari"),
    ("{dit is geen json", "Safari"),                                    # unreadable config: Safari
])
def test_the_browser_comes_from_the_config_or_is_safari(tmp_path, config, expected):
    work, bin_dir, log = _copy(tmp_path)
    r = _browser(work, bin_dir, config)
    assert r.returncode == 0, r.stderr
    assert r.stdout == expected + "\n"                                   # one line, nothing of plutil's error
    assert not log.exists(), log.read_text()                             # nothing was started or stopped


def test_datavault_config_points_at_another_file(tmp_path):
    work, bin_dir, log = _copy(tmp_path)
    other = tmp_path / "ander.json"
    other.write_text(json.dumps({"browser": "Arc"}))
    assert _browser(work, bin_dir, {"browser": "Firefox"}, env_config=other).stdout == "Arc\n"
    other.write_text(json.dumps({"vault_root": "/tmp/x"}))
    assert _browser(work, bin_dir, None, env_config=other).stdout == "Safari\n"
    assert not log.exists()


def test_the_old_one_liner_really_was_wrong(tmp_path):
    """Why: '$(plutil ... 2>/dev/null || echo Safari)' kept plutil's stdout error text."""
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"vault_root": "/tmp/x"}))
    r = subprocess.run(["/bin/bash", "-c", 'B=$(plutil -extract browser raw -o - "$1" 2>/dev/null || echo "Safari"); '
                        'printf "%s" "$B"', "_", str(config)], capture_output=True, text=True, timeout=30)
    assert r.stdout != "Safari" and r.stdout.endswith("Safari")
    assert "plutil -extract browser raw -o - \"$CONFIG_FILE\" 2>/dev/null || echo" not in (CODE / "start.sh").read_text()
