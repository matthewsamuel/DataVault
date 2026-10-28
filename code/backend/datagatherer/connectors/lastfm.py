"""
DataGatherer connector: Last.fm

A gap filler for Spotify: once Spotify scrobbling is linked in Last.fm, every play is kept
there, so plays the Spotify API missed (more than 50 since the last poll) are not lost.
user.getRecentTracks with an API key (no OAuth, no password) and the user name; 200 per
page, from= the last scrobble already stored (a full history the first time). The rows go
into spotify_plays as source 'lastfm'; spotify_store.reconcile() drops the ones an export
or an API play already has (by artist, title and time), so nothing is counted twice.

Last.fm stamps the START of a play and gives no duration: listening time is an estimate.
"""
import time
from typing import Any, Dict, List

from backend.datagatherer import spotify_store, state
from backend.datagatherer.base import Connector, ConnectorError, Coverage, GatherContext, HelpOption, SettingField, Setup

API_URL = "https://ws.audioscrobbler.com/2.0/"
PAGE_SIZE = 200
PAGE_PAUSE = 0.25          # Last.fm asks for at most a few requests per second
STREAM = "scrobbles"
ERRORS = {
    6: "Last.fm kent deze gebruikersnaam niet",
    10: "De API-sleutel klopt niet: controleer hem bij Instellingen",
    17: "Je Last.fm-profiel toont je geschiedenis niet: zet 'recent beluisterd' op zichtbaar",
    26: "De API-sleutel is uitgeschakeld door Last.fm",
}


class LastFm(Connector):
    id = "lastfm"
    name = "Last.fm"
    icon = "fab fa-lastfm"
    category = "Muziek"
    order = 20
    record_label = ("scrobble", "scrobbles")
    description = "Je scrobbles: een vangnet voor wat de Spotify-API niet meer teruggeeft."
    capabilities = frozenset({"api_sync"})
    allowed_hosts = ("ws.audioscrobbler.com",)
    raw_folder = "Last.fm"
    stale_days = 2
    launchd = True
    settings = (
        SettingField("username", "Gebruikersnaam", kind="text", required=True, placeholder="je Last.fm-naam"),
        SettingField("api_key", "API-sleutel", kind="secret", required=True,
                     help="Van last.fm/api/account/create; het 'shared secret' is niet nodig",
                     placeholder="32 tekens"),
    )
    setup = Setup(
        intro="Eén keer instellen. Last.fm bewaart alles vanaf het moment dat je Spotify koppelt, dus doe het liefst meteen.",
        steps=(
            "Maak een Last.fm-account als je er nog geen hebt.",
            "Ga in Last.fm naar Instellingen > Applicaties en klik bij 'Spotify Scrobbling' op Verbinden.",
            "Vraag een API-account aan: vul een naam in (bijvoorbeeld DataVault), laat de callback leeg en kopieer de "
            "'API key'. Het 'shared secret' heb je niet nodig.",
            "Vul hieronder je gebruikersnaam en de API-sleutel in en klik op Opslaan. Daarna haalt Bijwerken je scrobbles op.",
            "Laat je 'recent beluisterd' zichtbaar in je Last.fm-privacy-instellingen; anders geeft Last.fm niets terug.",
        ),
        fields=("username", "api_key"),
        note="De API-sleutel gaat alleen in de Sleutelhanger van macOS. Een wachtwoord is niet nodig.",
        links=(("API-account aanvragen", "https://www.last.fm/api/account/create"),
               ("Last.fm-applicaties", "https://www.last.fm/settings/applications")),
    )
    help = (
        HelpOption(
            title="Scrobbles ophalen", effort="makkelijk",
            steps=("Klik op Bijwerken: DataVault haalt alle nieuwe scrobbles op.",
                   "Of zet het schema aan: elk half uur zolang DataVault draait."),
            note="De eerste keer haalt DataVault je hele geschiedenis op; dat kan even duren."),
        HelpOption(
            title="Last.fm koppelen", effort="gemiddeld",
            steps=("Maak een Last.fm-account en koppel Spotify-scrobbling.",
                   "Vraag een API-sleutel aan en vul hem in bij Verbinden."),
            note="Last.fm kent alleen nummers van na het koppelen."),
    )

    def is_configured(self, ctx: GatherContext) -> bool:
        return ctx.secrets.has("api_key") and bool((ctx.settings.get("username") or "").strip())

    def sync(self, ctx: GatherContext) -> Dict[str, Any]:
        api_key = ctx.secrets.get("api_key")
        user = (ctx.settings.get("username") or "").strip()
        cursor = state.get_cursor(ctx.db, self.id, STREAM) or {}
        since = cursor.get("last_uts")
        until = int(time.time())
        added = seen = 0
        newest = since or 0
        page, pages = 1, 1
        now = time.time()
        with ctx.http() as http:
            while page <= pages:
                ctx.check()
                params = {"method": "user.getrecenttracks", "user": user, "api_key": api_key, "format": "json",
                          "limit": PAGE_SIZE, "page": page, "to": until}
                if since:
                    params["from"] = since + 1
                data = self._get(ctx, http, params)
                recent = data.get("recenttracks") or {}
                attr = recent.get("@attr") or {}
                try:
                    pages = max(1, int(attr.get("totalPages") or 1))
                except ValueError:
                    pages = 1
                tracks = recent.get("track") or []
                if isinstance(tracks, dict):   # one scrobble comes as an object, not a list
                    tracks = [tracks]
                rows = [r for r in (spotify_store.lastfm_row(t, now) for t in tracks) if r is not None]
                n_added, n_seen = spotify_store.insert_plays(ctx.db, rows, source_id=self.id)
                added += n_added
                seen += n_seen
                newest = max([newest] + [r["start_ms"] // 1000 for r in rows])
                ctx.progress(page, pages, f"Last.fm: pagina {page} van {pages}")
                page += 1
                if page <= pages:
                    ctx.sleep(PAGE_PAUSE)
        # Only after every page: a stopped run fetches the same range again (the keys drop doubles)
        with ctx.db._connect() as conn:
            state.set_cursor(conn, self.id, STREAM, {"last_uts": newest or since, "until": until})
        if added:
            spotify_store.reconcile(ctx.db)
        return {"added": added, "seen": seen, "pages": pages, "complete": True, "errors": 0}

    def _get(self, ctx: GatherContext, http, params: Dict[str, Any]) -> Dict[str, Any]:
        for attempt in range(3):
            response = http.get(API_URL, params=params)
            try:
                data = response.json()
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            code = data.get("error")
            if code in (8, 11, 16, 29) and attempt < 2:      # temporary, or 'rate limit exceeded'
                ctx.sleep(5 if code == 29 else 2)
                continue
            if code:
                raise ConnectorError(ERRORS.get(code, f"Last.fm gaf een fout: {data.get('message') or code}"),
                                     reconnect=code in (10, 26))
            if response.status_code != 200:
                raise ConnectorError(f"Last.fm antwoordde met fout {response.status_code}; probeer het later opnieuw")
            return data
        raise ConnectorError("Last.fm is even te druk; probeer het later opnieuw", retry_after=time.time() + 300)

    def coverage(self, ctx: GatherContext) -> Coverage:
        with ctx.db._connect() as conn:
            n, lo, hi = conn.execute("SELECT COUNT(*), MIN(start_ms), MAX(start_ms) FROM spotify_plays "
                                     "WHERE source = 'lastfm'").fetchone()
        if not n:
            return Coverage()
        return Coverage(records=n, first=lo / 1000, last=hi / 1000, estimated_since=lo / 1000,
                        segments=[(lo / 1000, hi / 1000, True)])

    def notes(self, ctx: GatherContext) -> List[Dict[str, str]]:
        return []


CONNECTOR = LastFm()
