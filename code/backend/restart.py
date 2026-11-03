"""
DataVault Dashboard - Restart this server process

POST /api/restart restarts only the process that received it (never anything
else on the machine, and no fixed port): a small helper process waits until
this PID has exited, then starts the server again with the same interpreter,
arguments, working directory and environment. This process then shuts down
the normal way (SIGTERM, so uvicorn finishes open requests first). If a request
keeps it alive (a chat stream, a long synchronous scan), a SIGINT makes uvicorn
stop waiting for it, and as a last resort the process ends itself with
os._exit (a synchronous request runs in a non-daemon worker thread that the
interpreter would otherwise wait for). That last step waits for ARCHIVE_LOCK,
so it never cuts an archive move in half.

The restarted server runs in its own session, so it outlives the Terminal
window that started it. Its output therefore never goes to that Terminal: once
the window closes the tty is revoked and every print would fail (the server
could not even start a second time). Output that went to a regular file (the
DataVault app's log) stays there; anything else goes to LOG_FILE.
"""
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional

from backend import file_ops

# How long the helper waits for the old process before giving up (it never kills anything).
# Well above HARD_EXIT_DELAY: the old process may still be finishing an archive move.
WAIT_SECONDS = 300
# Delay before this process stops, so the HTTP response still reaches the browser
EXIT_DELAY = 0.5
# Then a SIGINT: on a second signal uvicorn only forces its exit for SIGINT (a second
# SIGTERM is ignored), so it stops waiting for open requests and streams (chat)
FORCE_EXIT_DELAY = 10
# Last resort: end the process even if a synchronous request is still running
HARD_EXIT_DELAY = 15

# Where a restarted server writes its output when it cannot keep the old one (a Terminal)
LOG_FILE = Path(os.environ.get("DATAVAULT_LOG_FILE") or Path.home() / "Library/Logs/DataVault/server.log")
# Above this size the log is moved to server.log.1 (one older generation is kept)
LOG_MAX_BYTES = 10 * 1024 * 1024

# Runs in the helper process: wait for the old PID to exit, then become the new server.
# A zombie (exited, not yet reaped by its parent) counts as exited.
HELPER_SCRIPT = r"""
import json, os, stat, subprocess, sys, time
pid, argv, cwd, wait = int(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3], float(sys.argv[4])
log_file, log_max = sys.argv[5], int(sys.argv[6])

def say(msg):
    try:
        os.write(2, msg.encode())
    except OSError:
        pass  # no (more) terminal: nothing to tell

def keeps_output(fd):
    # A regular file (a log) or /dev/null stays usable for good; a tty, pipe or socket may not
    try:
        st = os.fstat(fd)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) or (stat.S_ISCHR(st.st_mode) and not os.isatty(fd))

def open_log():
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        try:
            if os.lstat(log_file).st_size > log_max:
                os.rename(log_file, log_file + ".1")  # the app's own log, never an archive file
        except FileNotFoundError:
            pass
        return os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    except OSError:
        return os.open(os.devnull, os.O_WRONLY)

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
        say(f"DataVault: proces {pid} stopte niet; er wordt geen tweede server gestart\n")
        sys.exit(1)
    time.sleep(0.1)
if not (keeps_output(1) and keeps_output(2)):
    say(f"DataVault: server herstart; de uitvoer staat voortaan in {log_file}\n")
    log_fd = open_log()
    for fd in (1, 2):
        if not keeps_output(fd):
            os.dup2(log_fd, fd)
    os.close(log_fd)
os.chdir(cwd)
os.execv(argv[0], argv)
"""


class RestartNotPossible(Exception):
    pass


class RestartInProgress(Exception):
    pass


# One restart per process: a second request (another tab, a script) must not start a
# second relaunch that would run the database setup next to the first one.
_restart_lock = threading.Lock()
_restart_scheduled = False


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
    """
    Start the waiting helper in its own session. It inherits stdout/stderr and the
    environment, and moves output that is not a regular file to LOG_FILE (see above).
    """
    return subprocess.Popen(
        [sys.executable, "-c", HELPER_SCRIPT, str(pid), json.dumps(argv), cwd, str(WAIT_SECONDS),
         str(LOG_FILE), str(LOG_MAX_BYTES)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        env=os.environ.copy(),
        close_fds=True,
    )


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def _hard_exit(exit=os._exit) -> None:
    """
    End this process now, whatever still runs in it. Waits for ARCHIVE_LOCK first and
    keeps it, so no archive move is in progress and none can start. (The database needs
    no care: SQLite rolls back an unfinished transaction on the next open.)
    """
    file_ops.ARCHIVE_LOCK.acquire()
    try:
        sys.stderr.write("DataVault: een verzoek liep nog; de server stopt nu toch voor de herstart\n")
        sys.stderr.flush()
        sys.stdout.flush()
    except Exception:
        pass  # no (more) terminal: nothing to tell
    exit(0)


def _exit_soon(pid: int) -> None:
    """
    Ask this process to stop: gracefully first, then forced if a request keeps it alive.
    The later stages are daemon timers, so they never delay an exit that already happens.
    """
    threading.Timer(EXIT_DELAY, _signal, args=(pid, signal.SIGTERM)).start()
    for delay, action, args in ((FORCE_EXIT_DELAY, _signal, (pid, signal.SIGINT)),
                                (HARD_EXIT_DELAY, _hard_exit, ())):
        timer = threading.Timer(delay, action, args=args)
        timer.daemon = True
        timer.start()


def schedule_restart(cwd: Optional[str] = None) -> dict:
    """
    Spawn the helper for this PID and stop this process shortly after.
    Raises RestartInProgress if a restart of this process was already scheduled.
    """
    global _restart_scheduled
    with _restart_lock:
        if _restart_scheduled:
            raise RestartInProgress("Herstart is al bezig")
        pid = os.getpid()
        argv = relaunch_command()
        _spawn_helper(pid, argv, cwd or os.getcwd())
        _restart_scheduled = True  # only once a helper really runs (a failed spawn may be retried)
    _exit_soon(pid)
    return {"status": "restarting", "pid": pid}
