"""
Spotify statistics: parsed once and cached (SPOTIFY-CHAT-3, PERF-9), no double counting
when both export formats are present (SPOTIFY-CHAT-4), a binge and an on-this-day list
that do not depend on file order (SPOTIFY-CHAT-13), year filter fixes (LIVE-PROBE-25)
and the chat's listening time (SPOTIFY-CHAT-12).
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.routes import spotify


def _extended(ts, track, artist, ms=200000):
    return {"ts": ts, "ms_played": ms, "master_metadata_track_name": track,
            "master_metadata_album_artist_name": artist, "platform": "ios"}


def _legacy(end_time, track, artist, ms=200000):
    return {"endTime": end_time, "msPlayed": ms, "trackName": track, "artistName": artist}


@pytest.fixture
def export(vault):
    """An empty data folder for the test's own export files."""
    folder = vault / "data"
    for f in folder.rglob("*.json"):
        f.unlink()
    return folder / "spotify"


def _write(folder, name, plays):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(plays))


def test_both_export_formats_are_counted_once(client, export):
    extended = [_extended(f"2023-03-0{d}T10:15:42Z", f"Track {d}", "Band") for d in range(1, 8)]
    # Account Data has the same plays (end time in whole minutes), plus one only it knows
    legacy = [_legacy(f"2023-03-0{d} 10:15", f"Track {d}", "Band") for d in range(1, 8)]
    legacy.append(_legacy("2023-03-09 22:00", "Alleen legacy", "Band"))
    _write(export, "Streaming_History_Audio_2023.json", extended)
    _write(export / "account", "StreamingHistory_music_0.json", legacy)
    # And the extended export copied once more into another folder
    _write(export / "kopie", "Streaming_History_Audio_2023.json", extended)
    data = client.get("/api/spotify/stats", params={"year": "all"}).json()
    assert data["total_plays"] == 8
    assert data["top_artists"][0]["count"] == 8


def test_the_binge_does_not_depend_on_file_order(client, export):
    # The same track 3x in a row, split over two files whose names sort the other way round
    _write(export, "Streaming_History_Audio_2023_10.json",
           [_extended("2023-05-01T10:00:00Z", "Herhaal", "X"), _extended("2023-05-01T10:04:00Z", "Herhaal", "X")])
    _write(export, "Streaming_History_Audio_2023_2.json",
           [_extended("2023-05-01T10:08:00Z", "Herhaal", "X"), _extended("2023-05-01T10:12:00Z", "Ander", "Y")])
    binge = client.get("/api/spotify/stats").json()["new_insights"]["top_binge"]
    assert binge == {"track": "Herhaal", "artist": "X", "count": 3}


def test_on_this_day_keeps_two_tracks_in_the_same_minute(client, export):
    now = datetime.now().astimezone()
    then = now.replace(year=now.year - 2, hour=12, minute=30, second=5)
    utc = then.astimezone(timezone.utc)
    _write(export, "Streaming_History_Audio_x.json", [
        _extended(utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "Eerste", "A", ms=5000),
        _extended((utc + timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M:%SZ"), "Tweede", "B", ms=5000),
    ])
    otd = client.get("/api/spotify/stats").json()["on_this_day"]
    assert [t["track"] for t in otd] == ["Eerste", "Tweede"]


def test_a_year_without_plays_shows_nothing_and_a_bad_year_is_refused(client, export):
    _write(export, "Streaming_History_Audio_x.json",
           [_extended("2022-01-01T10:00:00Z", f"T{i}", f"Artiest {i}") for i in range(5)]
           + [_extended("2023-01-01T10:00:00Z", "Nieuw", "Alleen 2023")])
    data = client.get("/api/spotify/stats", params={"year": "2023"}).json()
    assert [a["name"] for a in data["top_artists"]] == ["Alleen 2023"]
    assert [d["name"] for d in data["new_insights"]["discoveries"]] == ["Alleen 2023"]
    empty = client.get("/api/spotify/stats", params={"year": "1999"}).json()
    assert empty["top_artists"] == [] and empty["total_plays"] == 0
    for bad in ("abc", "20x3", "-5"):
        r = client.get("/api/spotify/stats", params={"year": bad})
        assert r.status_code == 400, bad
    assert client.get("/api/spotify/years").json()["years"] == [2022, 2023]


def test_the_export_is_parsed_once_until_a_file_changes(client, export, monkeypatch):
    _write(export, "Streaming_History_Audio_x.json", [_extended("2023-01-01T10:00:00Z", "T", "A")])
    calls = []
    real = spotify._parse_file
    monkeypatch.setattr(spotify, "_parse_file", lambda p: (calls.append(p), real(p))[1])
    for year in ("all", "2023", "all"):
        client.get("/api/spotify/stats", params={"year": year})
    client.get("/api/stats/spotify-on-this-day")
    assert len(calls) == 1
    path = export / "Streaming_History_Audio_x.json"
    _write(export, path.name, [_extended("2023-01-01T10:00:00Z", "T", "A"),
                               _extended("2023-01-02T10:00:00Z", "T", "A")])
    os.utime(path, (1700000000, 1700000000))
    assert client.get("/api/spotify/stats").json()["total_plays"] == 2
    assert len(calls) == 2


def test_the_chat_gets_the_total_listening_time(app, export):
    from backend.routes import chat
    _write(export, "Streaming_History_Audio_x.json",
           [_extended(f"2023-01-0{d}T10:00:00Z", "T", "A", ms=600000) for d in range(1, 4)])
    context = chat.get_vault_context()
    assert "Totaal Spotify luistertijd: 30 minuten" in context
