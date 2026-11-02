"""
DataVault Dashboard - Playing a track in the Spotify app on this Mac

Pasha's play_song shows a card that starts the track in the Spotify desktop app, through the
app's own AppleScript dictionary (osascript): DataVault itself goes online for nothing. The
URI is the only input and must be a plain spotify:track:<22 characters>; it reaches osascript
as an argument, never inside the script text. The first time, macOS asks whether DataVault may
control Spotify (Privacy en beveiliging > Automatisering); without that, or without the app,
the card offers the track on open.spotify.com instead.
"""
import re
import subprocess
from typing import Callable

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

MESSAGES = {
    "not_installed": "Spotify staat niet op deze Mac; ik open het nummer in de webspeler.",
    "not_allowed": "DataVault mag Spotify nog niet bedienen. Zet het aan in Systeeminstellingen > "
                   "Privacy en beveiliging > Automatisering.",
    "timeout": "Spotify reageerde niet op tijd.",
    "failed": "Spotify kon het nummer niet starten.",
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


def play(uri: str, run: Callable = subprocess.run) -> None:
    """Start uri in the Spotify app; PlayError(code) when that cannot happen. ValueError for a bad uri."""
    if not isinstance(uri, str) or not URI_RE.match(uri):
        raise ValueError("Geen geldige Spotify-track")
    if _osascript([*_FIND, BUNDLE_ID], run).returncode != 0:
        raise PlayError("not_installed")
    done = _osascript([*_PLAY, uri], run)
    if done.returncode != 0:
        raise PlayError("not_allowed" if "-1743" in (done.stderr or "") else "failed")
