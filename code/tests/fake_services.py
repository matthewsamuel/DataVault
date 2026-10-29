"""
Stand-ins for Spotify and Last.fm (no test ever talks to the real ones): httpx handlers for
net.set_transport_factory(lambda: httpx.MockTransport(fake.handler)). Synthetic data only.
"""
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extended_play(ts: float, track: str, artist: str, ms: int = 200000, uri: str = None, **extra):
    play = {"ts": iso(ts), "platform": "ios", "ms_played": ms, "conn_country": "BE", "ip_addr": "10.0.0.1",
            "master_metadata_track_name": track, "master_metadata_album_artist_name": artist,
            "master_metadata_album_album_name": "Album", "spotify_track_uri": uri,
            "episode_name": None, "episode_show_name": None, "spotify_episode_uri": None,
            "reason_start": "trackdone", "reason_end": "trackdone", "shuffle": False, "skipped": False,
            "offline": False, "offline_timestamp": None, "incognito_mode": False}
    play.update(extra)
    return play


def legacy_play(ts: float, track: str, artist: str, ms: int = 200000):
    return {"endTime": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "artistName": artist, "trackName": track, "msPlayed": ms}


def write_export_zip(path: Path, extended=None, account=None, video=None, top="Spotify Extended Streaming History"):
    """A my_spotify_data.zip like Spotify's, with a PDF readme and the JSON files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{top}/ReadMeFirst_ExtendedStreamingHistory.pdf", b"%PDF-1.4 synthetic")
        if extended is not None:
            zf.writestr(f"{top}/Streaming_History_Audio_2023-2024_0.json", json.dumps(extended))
        if video is not None:
            zf.writestr(f"{top}/Streaming_History_Video_2023-2024.json", json.dumps(video))
        if account is not None:
            zf.writestr("Spotify Account Data/StreamingHistory_music_0.json", json.dumps(account))
            zf.writestr("Spotify Account Data/Userdata.json", json.dumps({"username": "synthetisch"}))
    return path


def track_item(played_at: float, uri: str, name: str, artist: str, duration: int = 180000):
    return {"played_at": iso(played_at).replace("Z", ".123Z"),
            "track": {"uri": uri, "id": uri.rsplit(":", 1)[-1], "name": name, "duration_ms": duration,
                      "artists": [{"name": artist}], "album": {"name": "Album"}},
            "context": {"type": "playlist", "uri": "spotify:playlist:abc"}}


class FakeSpotify:
    def __init__(self):
        self.requests = []
        self.items = []
        self.access_token = "access-1"
        self.refresh_tokens = {"refresh-1"}
        self.rotate_to = None            # a new refresh token the next refresh returns
        self.token_error = None          # e.g. 'invalid_grant'
        self.recent_status = 200
        self.retry_after = None          # seconds: answer 429 once
        self.expire_access = False       # answer 401 once

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "accounts.spotify.com" and request.url.path == "/api/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if self.token_error:
                return httpx.Response(400, json={"error": self.token_error, "error_description": "Refresh token revoked"})
            if form.get("grant_type") == "authorization_code":
                if not form.get("code_verifier") or form.get("code") != "good-code":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": self.access_token, "token_type": "Bearer",
                                                 "expires_in": 3600, "refresh_token": "refresh-1",
                                                 "scope": "user-read-recently-played"})
            if form.get("grant_type") == "refresh_token":
                if form.get("refresh_token") not in self.refresh_tokens:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.access_token = f"access-{len(self.requests)}"
                body = {"access_token": self.access_token, "token_type": "Bearer", "expires_in": 3600}
                if self.rotate_to:
                    self.refresh_tokens = {self.rotate_to}
                    body["refresh_token"] = self.rotate_to
                    self.rotate_to = None
                return httpx.Response(200, json=body)
            return httpx.Response(400, json={"error": "unsupported_grant_type"})
        if request.url.host == "api.spotify.com" and request.url.path == "/v1/me/player/recently-played":
            if self.retry_after is not None:
                wait, self.retry_after = self.retry_after, None
                return httpx.Response(429, headers={"Retry-After": str(wait)})
            if self.expire_access:
                self.expire_access = False
                return httpx.Response(401, json={"error": {"status": 401, "message": "The access token expired"}})
            if request.headers.get("Authorization") != f"Bearer {self.access_token}":
                return httpx.Response(401, json={"error": {"status": 401, "message": "Invalid access token"}})
            if self.recent_status != 200:
                return httpx.Response(self.recent_status, json={"error": {"status": self.recent_status}})
            limit = int(request.url.params.get("limit", 20))
            return httpx.Response(200, json={"items": self.items[:limit], "next": None, "limit": limit})
        return httpx.Response(404)


class FakeLastFm:
    def __init__(self, scrobbles=None, page_size=200):
        self.requests = []
        self.scrobbles = scrobbles or []   # (uts, artist, track), newest first like Last.fm
        self.error = None
        self.now_playing = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != "ws.audioscrobbler.com":
            return httpx.Response(404)
        params = request.url.params
        if self.error:
            return httpx.Response(200, json={"error": self.error, "message": "fout"})
        lo = int(params.get("from", 0))
        hi = int(params.get("to", 2 ** 40))
        limit = int(params.get("limit", 50))
        page = int(params.get("page", 1))
        rows = sorted([s for s in self.scrobbles if lo <= s[0] <= hi], reverse=True)
        pages = max(1, -(-len(rows) // limit))
        chunk = rows[(page - 1) * limit:page * limit]
        tracks = [{"artist": {"#text": a, "mbid": ""}, "name": t, "album": {"#text": "Album"},
                   "date": {"uts": str(u), "#text": "x"}} for u, a, t in chunk]
        if self.now_playing and page == 1:
            tracks.insert(0, {"artist": {"#text": self.now_playing[0]}, "name": self.now_playing[1],
                              "@attr": {"nowplaying": "true"}})
        if len(tracks) == 1:
            tracks = tracks[0]   # Last.fm sends a single scrobble as an object
        return httpx.Response(200, json={"recenttracks": {"track": tracks, "@attr": {
            "user": params.get("user"), "page": str(page), "perPage": str(limit), "totalPages": str(pages),
            "total": str(len(rows))}}})
