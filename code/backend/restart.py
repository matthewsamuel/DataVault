"""
DataVault Dashboard - Restart this server process

POST /api/restart restarts only the process that received it (never anything
else on the machine, and no fixed port): a small helper process waits until
this PID has exited, then starts the server again with the same interpreter,
arguments, working directory and environment. This process then shuts down
the normal way (SIGTERM, so uvicorn finishes open requests first).
"""
import json
import os
import signal
import subprocess
import sys
import threading
from typing import List, Optional

# How long the helper waits for the old process before giving up (it never kills anything)
WAIT_SECONDS = 30
# Delay before this process stops, so the HTTP response still reaches the browser
EXIT_DELAY = 0.5
# A second SIGTERM makes uvicorn stop without waiting for long-running streams (chat)
FORCE_EXIT_DELAY = 10

# Runs in the helper process: wait for the old PID to exit, then become the new server.
# A zombie (exited, not yet reaped by its parent) counts as exited.
HELPER_SCRIPT = r"""
import json, os, subprocess, sys, time
pid, argv, cwd, wait = int(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3], float(sys.argv[4])

def running():
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(state.strip()) and not state.strip().startswith("Z")

deadline = time.monotonic() + wait
while running():
    if time.monotonic() > deadline:
        sys.stderr.write(f"DataVault: proces {pid} stopte niet; er wordt geen tweede server gestart\n")
        sys.exit(1)
    time.sleep(0.1)
os.chdir(cwd)
os.execv(argv[0], argv)
"""


class RestartNotPossible(Exception):
    pass


def relaunch_command() -> List[str]:
    """The command line that started this process, with the same interpreter."""
    args = list(getattr(sys, "orig_argv", []) or [sys.executable, *sys.argv])
    if "--multiprocessing-fork" in args or any("multiprocessing" in a for a in args[1:3]):
        # A worker under `uvicorn --reload`: the reloader restarts it, relaunching it would clash
        raise RestartNotPossible(
            "De server draait met --reload; die herstart zichzelf bij elke wijziging"
        )
    return [sys.executable, *args[1:]]


def _spawn_helper(pid: int, argv: List[str], cwd: str) -> subprocess.Popen:
    """Start the waiting helper in its own session; it inherits stdout/stderr and the environment."""
    return subprocess.Popen(
        [sys.executable, "-c", HELPER_SCRIPT, str(pid), json.dumps(argv), cwd, str(WAIT_SECONDS)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        env=os.environ.copy(),
        close_fds=True,
    )


def _exit_soon(pid: int) -> None:
    """Ask this process to stop (graceful first, forced if a stream keeps it alive)."""
    threading.Timer(EXIT_DELAY, os.kill, args=(pid, signal.SIGTERM)).start()
    force = threading.Timer(FORCE_EXIT_DELAY, os.kill, args=(pid, signal.SIGTERM))
    force.daemon = True
    force.start()


def schedule_restart(cwd: Optional[str] = None) -> dict:
    """Spawn the helper for this PID and stop this process shortly after."""
    pid = os.getpid()
    argv = relaunch_command()
    _spawn_helper(pid, argv, cwd or os.getcwd())
    _exit_soon(pid)
    return {"status": "restarting", "pid": pid}
