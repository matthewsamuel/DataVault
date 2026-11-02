"""
Pasha's play_song: 'zet dat liedje op' plays the track of Home's 'Spotify op deze dag' in the
Spotify app on this Mac (backend/spotify_player.py, through osascript). The model only names
the tool; the chat card sends the track's URI to POST /api/spotify/play, and the URI is the
only thing that reaches osascript, as an argument. osascript is a fake here: nothing plays.
"""
import subprocess

import pytest

from backend import spotify_player
from test_assistant import _say, _tool
from test_spotify_covers import _last_year_now

URI = "spotify:track:4uLU6hMCjMI75M1A2tKUQC"


@pytest.fixture(autouse=True)
def no_export_files(vault):
    """Without export files the dashboard reads the plays from the database."""
    for f in (vault / "data").rglob("*.json"):
        f.unlink()


def _play(app, track="COMË N GO", artist="Yeat", uri=URI):
    with app.state.db._connect() as conn:
        conn.execute("INSERT INTO spotify_plays (play_key, source, ts_ms, ms_played, content, track, artist, "
                     "track_uri, first_seen_at) VALUES (?, 'export_extended', ?, 200000, 'track', ?, ?, ?, 0)",
                     (f"k-{track}", int(_last_year_now() * 1000), track, artist, uri))


class FakeOsascript:
    def __init__(self, find=0, play=0, stderr=""):
        self.calls, self.find, self.play, self.stderr = [], find, play, stderr

    def __call__(self, args, **kw):
        self.calls.append(args)
        code = self.find if len(self.calls) == 1 else self.play
        return subprocess.CompletedProcess(args, code, "", self.stderr if code else "")


def test_pasha_answers_the_track_home_shows_with_its_uri(client, app, fake_ollama):
    _play(app)
    home = client.get("/api/stats/spotify-on-this-day").json()["track"]
    t = _tool(_say(client, fake_ollama, {"action": "play_song"}, "Ik zet het op."))
    play = t["result"]["play"]
    assert (play["track"], play["artist"], play["year"]) == (home["track"], home["artist"], home["year"])
    assert play["uri"] == URI


def test_the_lead_in_alone_means_play_song():
    # In the tag format qwen2.5 often stops after the example's lead-in, without the tag
    from backend import assistant
    assert assistant.resolve_actions("Ik zet het op.")[0] == [{"action": "play_song"}]
    assert assistant.resolve_actions("Ik zet het op de kaart.")[0] == []
    assert assistant.resolve_actions('Ik zet het op. <<ACTION: {"action": "job_status"}>>')[0] == [{"action": "job_status"}]


def test_without_a_track_today_pasha_says_so(client, fake_ollama):
    t = _tool(_say(client, fake_ollama, {"action": "play_song"}))
    assert "speelde er niets" in t["error"]


def test_a_track_without_a_known_uri_still_gets_a_card(client, app, fake_ollama):
    _play(app, uri=None)
    play = _tool(_say(client, fake_ollama, {"action": "play_song"}))["result"]["play"]
    assert play["track"] == "COMË N GO" and play["uri"] is None


def test_the_uri_goes_to_osascript_as_an_argument_only():
    run = FakeOsascript()
    spotify_player.play(URI, run=run)
    find, play = run.calls
    assert find[0] == play[0] == "osascript"
    assert find[-1] == spotify_player.BUNDLE_ID and play[-1] == URI
    assert not any(URI in a for a in play[:-1])          # never inside the script text


@pytest.mark.parametrize("uri", ["", "spotify:album:4uLU6hMCjMI75M1A2tKUQC", URI + '" & quit', "spotify:track:abc",
                                 None, 42])
def test_anything_but_a_plain_track_uri_is_refused(uri):
    run = FakeOsascript()
    with pytest.raises(ValueError):
        spotify_player.play(uri, run=run)
    assert run.calls == []


@pytest.mark.parametrize("fake,code", [
    (FakeOsascript(find=1), "not_installed"),
    (FakeOsascript(play=1, stderr="execution error: Not authorized to send Apple events to Spotify. (-1743)"), "not_allowed"),
    (FakeOsascript(play=1, stderr="execution error: something (-1708)"), "failed"),
])
def test_a_failure_gets_a_code_and_a_dutch_message(fake, code):
    with pytest.raises(spotify_player.PlayError) as e:
        spotify_player.play(URI, run=fake)
    assert e.value.code == code and str(e.value) == spotify_player.MESSAGES[code]


def test_a_hanging_spotify_times_out():
    def hang(args, **kw):
        raise subprocess.TimeoutExpired(args, kw.get("timeout"))
    with pytest.raises(spotify_player.PlayError) as e:
        spotify_player.play(URI, run=hang)
    assert e.value.code == "timeout"


def test_the_play_route(client, anon_client, monkeypatch):
    played = []
    monkeypatch.setattr(spotify_player, "play", lambda uri: played.append(uri))
    assert client.post("/api/spotify/play", json={"uri": URI}).json() == {"played": True}
    assert played == [URI]

    def refuse(uri):
        raise spotify_player.PlayError("not_allowed")
    monkeypatch.setattr(spotify_player, "play", refuse)
    r = client.post("/api/spotify/play", json={"uri": URI}).json()
    assert r == {"played": False, "code": "not_allowed", "message": spotify_player.MESSAGES["not_allowed"],
                 "web": "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"}

    monkeypatch.undo()
    assert client.post("/api/spotify/play", json={"uri": "spotify:track:x"}).status_code == 400
    assert anon_client.post("/api/spotify/play", json={"uri": URI}).status_code == 403   # needs the session token
