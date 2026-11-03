"""
DataVault Dashboard - Playing a track in the Spotify app on this Mac

Pasha's play_song shows a card that starts the track in the Spotify desktop app, through the
app's own AppleScript dictionary (osascript): DataVault itself goes online for nothing. The
URI is the only input and must be a plain spotify:track:<22 characters>; it reaches osascript
as an argument, never inside the script text. The first time, macOS asks whether DataVault may
control Spotify (Privacy en beveiliging > Automatisering); without that, or without the app,
the card offers the track on open.spotify.com instead.

Home starts the same track once when the dashboard opens (Instellingen > Home), with
if_idle: when Spotify is already playing something, that keeps playing (PlayError 'busy').
pause() and state() never start Spotify: they only talk to an app that is already running.

Spotify jumps to the front when it starts a track (twice, within half a second). For play
and pause alike, a small
helper (_KEEP_FRONT) runs alongside: it remembers which app was in front (Safari with the
dashboard) and brings that back each time Spotify takes over, for 3 s (10 s when Spotify still
has to start), so the song plays in the background and the request is not slower.
"""
import re
import select
import subprocess
from typing import Callable, Optional

BUNDLE_ID = "com.spotify.client"
URI_RE = re.compile(r"^spotify:track:([A-Za-z0-9]{22})$")
TIMEOUT = 20.0

# A Spotify that was not running yet needs a moment before it takes 'play track'
_FIND = ["-e", "on run argv", "-e", "POSIX path of (path to application id (item 1 of argv))", "-e", "end run"]
_PLAY = ["-e", "on run argv",
         "-e", f'if application id "{BUNDLE_ID}" is not running then',
         "-e", f'tell application id "{BUNDLE_ID}" to launch',
         "-e", "delay 3",
         "-e", "end if",
         "-e", f'tell application id "{BUNDLE_ID}" to play track (item 1 of argv)',
         "-e", "end run"]
# The same, but a Spotify that is playing already keeps its own track
_PLAY_IF_IDLE = ["-e", "on run argv",
                 "-e", f'if application id "{BUNDLE_ID}" is running then',
                 "-e", f'tell application id "{BUNDLE_ID}" to if player state is playing then return "busy"',
                 "-e", "else",
                 "-e", f'tell application id "{BUNDLE_ID}" to launch',
                 "-e", "delay 3",
                 "-e", "end if",
                 "-e", f'tell application id "{BUNDLE_ID}" to play track (item 1 of argv)',
                 "-e", 'return "playing"',
                 "-e", "end run"]
# Brings the app that was in front back whenever Spotify takes over; 'klaar' (on stderr) once it
# knows that app, so the track only starts after that
_KEEP_FRONT = ["-e", "set f to (path to frontmost application as text)",
               "-e", f'set s to (path to application id "{BUNDLE_ID}" as text)',
               "-e", "if f is s then return",
               "-e", f'if application id "{BUNDLE_ID}" is running then',
               "-e", "set n to 30",
               "-e", "else",
               "-e", "set n to 100",
               "-e", "end if",
               "-e", 'log "klaar"',
               "-e", "repeat n times",
               "-e", "delay 0.1",
               "-e", "if (path to frontmost application as text) is s then tell application f to activate",
               "-e", "end repeat"]
KEEP_FRONT_READY = 1.0           # seconds to wait for 'klaar' at most
_PAUSE = ["-e", f'if application id "{BUNDLE_ID}" is running then tell application id "{BUNDLE_ID}" to pause']
# 'playing<tab>spotify:track:...', 'paused<tab>...' or 'stopped<tab>' (also when Spotify is not running)
_STATE = ["-e", f'if application id "{BUNDLE_ID}" is not running then return "stopped" & tab',
          "-e", f'tell application id "{BUNDLE_ID}"',
          "-e", "set s to (player state as text)",
          "-e", "try",
          "-e", "return s & tab & (id of current track)",
          "-e", "end try",
          "-e", "return s & tab",
          "-e", "end tell"]

MESSAGES = {
    "not_installed": "Spotify staat niet op deze Mac; ik open het nummer in de webspeler.",
    "not_allowed": "DataVault mag Spotify nog niet bedienen. Zet het aan in Systeeminstellingen > "
                   "Privacy en beveiliging > Automatisering.",
    "timeout": "Spotify reageerde niet op tijd.",
    "failed": "Spotify kon het nummer niet starten.",
    "busy": "Spotify speelt al iets; dat laat ik aan.",
}


class PlayError(Exception):
    def __init__(self, code: str):
        super().__init__(MESSAGES[code])
        self.code = code


def web_url(uri: str) -> str:
    m = URI_RE.match(uri or "")
    return f"https://open.spotify.com/track/{m.group(1)}" if m else ""


def _osascript(args, run: Callable) -> subprocess.CompletedProcess:
    try:
        return run(["osascript", *args], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise PlayError("timeout")
    except OSError:          # no osascript: not a Mac
        raise PlayError("not_installed")


def _refused(done: subprocess.CompletedProcess) -> PlayError:
    return PlayError("not_allowed" if "-1743" in (done.stderr or "") else "failed")


def _keep_in_front(spawn: Callable):
    """Start the _KEEP_FRONT helper and wait until it knows the front app; None when it cannot start."""
    try:
        helper = spawn(["osascript", *_KEEP_FRONT], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
    except OSError:
        return None
    if getattr(helper, "stderr", None) is not None:
        try:
            select.select([helper.stderr], [], [], KEEP_FRONT_READY)
        except (OSError, ValueError):
            pass
        helper.stderr.close()
    return helper


def _stop(helper) -> None:
    if helper is not None:
        try:
            helper.terminate()
        except OSError:
            pass


def play(uri: str, run: Callable = subprocess.run, if_idle: bool = False,
         spawn: Optional[Callable] = None) -> None:
    """
    Start uri in the Spotify app, which stays in the background; PlayError(code) when that
    cannot happen, 'busy' when if_idle and Spotify is playing something already. ValueError for
    a bad uri. spawn starts the _KEEP_FRONT helper (subprocess.Popen; none with a fake run).
    """
    if not isinstance(uri, str) or not URI_RE.match(uri):
        raise ValueError("Geen geldige Spotify-track")
    if _osascript([*_FIND, BUNDLE_ID], run).returncode != 0:
        raise PlayError("not_installed")
    if spawn is None and run is subprocess.run:
        spawn = subprocess.Popen
    helper = _keep_in_front(spawn) if spawn else None
    try:
        done = _osascript([*(_PLAY_IF_IDLE if if_idle else _PLAY), uri], run)
    except PlayError:
        _stop(helper)
        raise
    if done.returncode != 0:
        _stop(helper)
        raise _refused(done)
    if if_idle and (done.stdout or "").strip() == "busy":
        _stop(helper)
        raise PlayError("busy")


def pause(run: Callable = subprocess.run, spawn: Optional[Callable] = None) -> None:
    """Pause Spotify if it is running (never starts it); it stays in the background, like play."""
    if spawn is None and run is subprocess.run:
        spawn = subprocess.Popen
    helper = _keep_in_front(spawn) if spawn else None
    try:
        done = _osascript(_PAUSE, run)
    except PlayError:
        _stop(helper)
        raise
    if done.returncode != 0:
        _stop(helper)
        raise _refused(done)


def state(run: Callable = subprocess.run) -> dict:
    """{'playing': bool, 'uri': the current track or None}; a Spotify that is not running is not playing."""
    done = _osascript(_STATE, run)
    if done.returncode != 0:
        raise _refused(done)
    status, _, uri = (done.stdout or "").strip("\n").partition("\t")
    return {"playing": status.strip() == "playing", "uri": uri.strip() if URI_RE.match(uri.strip()) else None}
