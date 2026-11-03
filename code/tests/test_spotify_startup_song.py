"""
Home plays the track of 'Spotify op deze dag' in the Spotify app once when the dashboard opens
(Instellingen > Home, on by default), but never over something Spotify is already playing
(if_idle). The card pauses it and asks now and then what Spotify plays. osascript is a fake
here: nothing plays.
"""
import subprocess

import pytest

from backend import spotify_player
from test_pasha_play_song import URI, _play, no_export_files  # noqa: F401 (fixture)


class FakeOsascript:
    """Answers each call in turn with (returncode, stdout, stderr)."""
    def __init__(self, *answers):
        self.calls, self.answers = [], list(answers)

    def __call__(self, args, **kw):
        self.calls.append(args)
        code, out, err = self.answers.pop(0) if self.answers else (0, "", "")
        return subprocess.CompletedProcess(args, code, out, err)


def test_if_idle_leaves_a_playing_spotify_alone():
    run = FakeOsascript((0, "", ""), (0, "busy\n", ""))
    with pytest.raises(spotify_player.PlayError) as e:
        spotify_player.play(URI, run=run, if_idle=True)
    assert e.value.code == "busy"
    script = " ".join(run.calls[1])
    assert "player state is playing" in script and run.calls[1][-1] == URI
    assert not any(URI in a for a in run.calls[1][:-1])


def test_if_idle_plays_when_spotify_is_quiet():
    run = FakeOsascript((0, "", ""), (0, "playing\n", ""))
    spotify_player.play(URI, run=run, if_idle=True)
    assert len(run.calls) == 2


def test_a_plain_play_never_asks_for_the_state():
    run = FakeOsascript()
    spotify_player.play(URI, run=run)
    assert "player state" not in " ".join(run.calls[1])


def test_pause_and_state_never_launch_spotify():
    run = FakeOsascript((0, "", ""), (0, f"playing\t{URI}\n", ""))
    spotify_player.pause(run=run)
    assert spotify_player.state(run=run) == {"playing": True, "uri": URI}
    for call in run.calls:
        assert "launch" not in " ".join(call) and "running then" in " ".join(call)


@pytest.mark.parametrize("out,expected", [
    ("stopped\t\n", {"playing": False, "uri": None}),
    ("paused\tspotify:track:4uLU6hMCjMI75M1A2tKUQC\n", {"playing": False, "uri": URI}),
    ("playing\tspotify:ad:123\n", {"playing": True, "uri": None}),
])
def test_state_reads_what_spotify_says(out, expected):
    assert spotify_player.state(run=FakeOsascript((0, out, ""))) == expected


def test_state_without_permission_has_a_code():
    run = FakeOsascript((1, "", "Not authorized to send Apple events to Spotify. (-1743)"))
    with pytest.raises(spotify_player.PlayError) as e:
        spotify_player.state(run=run)
    assert e.value.code == "not_allowed"


def test_home_gets_the_uri_of_the_track(client, app):
    _play(app)
    assert client.get("/api/stats/spotify-on-this-day").json()["track"]["uri"] == URI


def test_the_routes(client, monkeypatch):
    seen = []
    monkeypatch.setattr(spotify_player, "play", lambda uri, if_idle=False: seen.append(if_idle))
    client.post("/api/spotify/play", json={"uri": URI, "if_idle": True})
    client.post("/api/spotify/play", json={"uri": URI})
    assert seen == [True, False]

    def busy(uri, if_idle=False):
        raise spotify_player.PlayError("busy")
    monkeypatch.setattr(spotify_player, "play", busy)
    assert client.post("/api/spotify/play", json={"uri": URI, "if_idle": True}).json()["code"] == "busy"

    monkeypatch.setattr(spotify_player, "pause", lambda: None)
    assert client.post("/api/spotify/pause").json() == {"paused": True}
    monkeypatch.setattr(spotify_player, "state", lambda: {"playing": True, "uri": URI})
    assert client.get("/api/spotify/state").json() == {"playing": True, "uri": URI}

    def refuse():
        raise spotify_player.PlayError("not_allowed")
    monkeypatch.setattr(spotify_player, "state", refuse)
    assert client.get("/api/spotify/state").json() == {"playing": False, "uri": None, "code": "not_allowed"}


def test_the_setting_is_on_until_turned_off(client):
    assert client.get("/api/ui/settings").json()["startup_song"] is True
    assert client.put("/api/ui/settings", json={"startup_song": False}).json()["startup_song"] is False
    assert client.get("/api/ui/settings").json() == {"motion": None, "startup_song": False}
    assert client.put("/api/ui/settings", json={"startup_song": "nee"}).status_code == 400
    # Beweging kiezen laat de keuze staan
    assert client.put("/api/ui/settings", json={"motion": "rustig"}).json()["startup_song"] is False


class FakeHelper:
    stderr = None

    def __init__(self, log, args):
        self.log, self.args = log, args
        log.append("helper")

    def terminate(self):
        self.log.append("stopped")


def _spawn(log):
    return lambda args, **kw: FakeHelper(log, args)


def test_the_front_app_comes_back_while_spotify_starts_the_track():
    log = []
    run = FakeOsascript((0, "", ""), (0, "playing\n", ""))
    real_run = run.__call__
    run_logged = lambda args, **kw: (log.append("play" if URI in args else "find"), real_run(args, **kw))[1]
    spotify_player.play(URI, run=run_logged, spawn=_spawn(log))
    assert log == ["find", "helper", "play"]           # the helper knows the front app before the track starts
    script = " ".join(spotify_player._KEEP_FRONT)
    assert "path to frontmost application" in script and "activate" in script and "launch" not in script


@pytest.mark.parametrize("answer,code", [((0, "busy\n", ""), "busy"), ((1, "", "(-1708)"), "failed")])
def test_the_helper_stops_when_nothing_starts(answer, code):
    log = []
    with pytest.raises(spotify_player.PlayError) as e:
        spotify_player.play(URI, run=FakeOsascript((0, "", ""), answer), if_idle=True, spawn=_spawn(log))
    assert e.value.code == code and log == ["helper", "stopped"]


def test_no_helper_when_spotify_is_missing():
    log = []
    with pytest.raises(spotify_player.PlayError):
        spotify_player.play(URI, run=FakeOsascript((1, "", "")), spawn=_spawn(log))
    assert log == []


def test_a_fake_osascript_never_starts_a_real_helper(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("a real helper started"))
    spotify_player.play(URI, run=FakeOsascript())


def test_pausing_keeps_the_front_app_too():
    log = []
    spotify_player.pause(run=FakeOsascript(), spawn=_spawn(log))
    assert log == ["helper"]
