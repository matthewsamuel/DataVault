"""
DataGatherer - the optional launchd agent

Spotify's 'recently played' only goes back 50 tracks, so plays are lost when the app is
closed for a long listening session. The user can (opt-in, in Instellingen) install a
launchd agent that runs a scheduled sync every 30 minutes while DataVault is closed:
`python -m backend.datagatherer.cli sync --config <config.json>`. It syncs only
connectors whose schedule is switched on (the network guard checks that again).

Install and uninstall happen ONLY on a click. The plist is generated here, written without
overwriting anything (an existing different file is refused), and loaded with launchctl.
Uninstall removes only our own plist (checked by its Label). Tests and test servers never
reach the real ~/Library/LaunchAgents or launchctl: with DATAVAULT_TESTING=1 the folder
must be set with DATAVAULT_LAUNCH_AGENTS_DIR (inside the temp directory) and launchctl is
replaced with set_runner().
"""
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional

LABEL = "nl.datavault.datagatherer"
INTERVAL_SECONDS = 1800
CODE_DIR = Path(__file__).resolve().parents[2]

_runner: Optional[Callable[[List[str]], int]] = None


class LaunchdError(Exception):
    """Something the user can act on; Dutch."""


def set_runner(runner: Optional[Callable[[List[str]], int]]) -> None:
    """Tests: what runs instead of /bin/launchctl (gets the argument list, returns the exit code)."""
    global _runner
    _runner = runner


def _testing() -> bool:
    return os.environ.get("DATAVAULT_TESTING") == "1"


def agents_dir() -> Path:
    custom = os.environ.get("DATAVAULT_LAUNCH_AGENTS_DIR")
    if _testing():
        temp = Path(tempfile.gettempdir()).resolve()
        if not custom or not Path(custom).resolve().is_relative_to(temp):
            raise LaunchdError("In testmodus alleen een LaunchAgents-map in de tijdelijke map")
        return Path(custom)
    return Path(custom) if custom else Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return agents_dir() / f"{LABEL}.plist"


def build_plist(config_path: Path, python: Optional[str] = None, interval: int = INTERVAL_SECONDS) -> bytes:
    log_dir = Path.home() / "Library" / "Logs" / "DataVault"
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [python or sys.executable, "-m", "backend.datagatherer.cli", "sync",
                             "--config", str(Path(config_path).resolve())],
        "WorkingDirectory": str(CODE_DIR),
        "StartInterval": int(interval),
        "RunAtLoad": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(log_dir / "datagatherer.log"),
        "StandardErrorPath": str(log_dir / "datagatherer.log"),
        "EnvironmentVariables": {"DATAVAULT_CONFIG": str(Path(config_path).resolve())},
    })


def _ours(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return plistlib.load(f).get("Label") == LABEL
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False


def _launchctl(args: List[str]) -> int:
    if _runner is not None:
        return _runner(args)
    if _testing():
        raise LaunchdError("In testmodus wordt launchctl nooit aangeroepen")
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, timeout=20).returncode


def status() -> dict:
    try:
        path = plist_path()
    except LaunchdError as e:
        return {"available": False, "installed": False, "path": None, "message": str(e)}
    installed = path.is_file() and _ours(path)
    return {"available": sys.platform == "darwin" or _testing(), "installed": installed, "path": str(path),
            "interval_minutes": INTERVAL_SECONDS // 60}


def install(config_path: Path, python: Optional[str] = None) -> dict:
    path = plist_path()
    data = build_plist(config_path, python)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise LaunchdError("Er staat al een DataVault-agent met andere instellingen: schakel die eerst uit")
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    if not _testing():
        # launchd writes the agent's output here, but does not make the folder itself
        (Path.home() / "Library" / "Logs" / "DataVault").mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    _launchctl(["bootout", f"gui/{uid}/{LABEL}"])          # an older load of the same file; failing is fine
    code = _launchctl(["bootstrap", f"gui/{uid}", str(path)])
    if code != 0:
        raise LaunchdError(f"launchctl kon de agent niet laden (code {code})")
    return status()


def uninstall() -> dict:
    path = plist_path()
    if not path.exists():
        return status()
    if not _ours(path):
        raise LaunchdError("Dat bestand is niet van DataVault; het blijft staan")
    _launchctl(["bootout", f"gui/{os.getuid()}/{LABEL}"])
    os.unlink(path)   # our own generated plist (checked above), never anything of the user
    return status()
