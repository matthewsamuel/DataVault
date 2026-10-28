"""
DataGatherer connector: Spotify

Three ways in, merged in spotify_store.py:
- The privacy export (export_request + export_watch + import): Extended Streaming History
  (audio, video, podcasts and audiobooks, the whole account lifetime) and Account data
  (StreamingHistory*.json, one year). The richest and lasting copy.
- The Web API (api_sync): GET /v1/me/player/recently-played, the last 50 tracks, after a
  one-time OAuth consent (Authorization Code + PKCE, redirect to 127.0.0.1). Tokens live in
  the Keychain; a refresh may return a new refresh token, which is always stored. Since
  2026 a consent lasts 6 months: then the token endpoint answers invalid_grant and the card
  asks to connect again (with a warning 14 days before). A 429 is answered with Retry-After.
- Existing export folders anywhere below data/ (for the user: 'data/Matthew/Spotify
  Extended Streaming History'), registered in place: exactly what the dashboard read before.
"""
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from backend.datagatherer import raw_store, spotify_store, state
from backend.datagatherer.base import (Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption,
                                       SettingField, Setup)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
RECENT_URL = "https://api.spotify.com/v1/me/player/recently-played"
SCOPE = "user-read-recently-played"
CONSENT_DAYS = 182          # a refresh token expires 6 months after the consent (Spotify, 2026)
WARN_DAYS = 14
MAX_RETRY_WAIT = 30         # seconds: a longer Retry-After ends the run (the schedule waits)
PRIVACY_URL = "https://www.spotify.com/account/privacy/"


def _kind_of(name: str) -> Optional[str]:
    base = name.rsplit("/", 1)[-1]
    if not base.lower().endswith(".json"):
        return None
    if base.startswith("Streaming_History_Audio") or base.startswith("Streaming_History_Video"):
        return "extended"
    if base.startswith("StreamingHistory"):
        return "account"
    return None


def _history_file(name: str) -> bool:
    """The same files the dashboard's old parser read (plus the video history)."""
    return _kind_of(name) is not None


class Spotify(Connector):
    id = "spotify"
    name = "Spotify"
    icon = "fab fa-spotify"
    category = "Muziek"
    description = "Wat je beluisterde: de volledige geschiedenis uit je Spotify-export, aangevuld met de laatste nummers via de Spotify-API."
    capabilities = frozenset({"api_sync", "export_request", "export_watch", "import"})
    allowed_hosts = ("accounts.spotify.com", "api.spotify.com")
    raw_folder = "Spotify"
    existing_folders = ("Spotify Extended Streaming History",)
    stale_days = 2
    launchd = True
    extra_secret_keys = ("token",)
    exports = (
        ExportKind(
            id="extended", label="Extended streaming history", request_url=PRIVACY_URL,
            patterns=("my_spotify_data*.zip", "*spotify*.zip", "*.zip", "streaming_history_*.json",
                      "streaminghistory*.json"),
            folder_label="Extended streaming history", expected_days=(1, 30),
            steps=(
                "Log in op de pagina die nu opent, in je eigen browser. DataVault ziet je wachtwoord nooit.",
                "Vink 'Extended streaming history' aan (Accountgegevens mag erbij) en klik op 'Request data'.",
                "Bevestig de aanvraag met de link in de e-mail van Spotify.",
                "Meestal na 1 tot 5 dagen (soms tot 30) mailt Spotify dat je gegevens klaar zijn: download my_spotify_data.zip.",
                "DataVault ziet de zip in Downloads, kopieert hem naar het archief en leest hem in. Het origineel mag je daarna weggooien.",
            )),
        ExportKind(
            id="account", label="Accountgegevens (één jaar)", request_url=PRIVACY_URL,
            patterns=("my_spotify_data*.zip", "*spotify*.zip", "*.zip", "streaminghistory*.json"),
            folder_label="Accountgegevens", expected_days=(1, 5),
            steps=(
                "Log in op de pagina die nu opent, in je eigen browser.",
                "Vink 'Account data' aan en klik op 'Request data'; bevestig met de link in de e-mail.",
                "Na een paar dagen download je my_spotify_data.zip uit de e-mail van Spotify.",
                "DataVault kopieert hem uit Downloads naar het archief en leest hem in.",
            )),
    )
    settings = (
        SettingField("client_id", "Client ID", kind="secret", required=True,
                     help="Van je eigen app op developer.spotify.com (geen client secret nodig)",
                     placeholder="32 tekens, bv. 1a2b3c…"),
    )
    setup = Setup(
        intro="Eén keer instellen (ongeveer 5 minuten). Daarna haalt Bijwerken je laatste 50 nummers op, of doet het "
              "schema dat elk half uur voor je.",
        steps=(
            "Open het Spotify-ontwikkelaarsdashboard en log in met je eigen Spotify-account. Je hebt Spotify Premium "
            "nodig: zonder Premium werkt een ontwikkelaarsapp niet meer (sinds februari 2026).",
            "Klik op 'Create app'. Vul een naam in (bijvoorbeeld DataVault) en een korte beschrijving.",
            "Voeg bij 'Redirect URIs' precies {redirect_uri} toe en klik op 'Add'. Gebruik 127.0.0.1, niet localhost: "
            "Spotify weigert localhost.",
            "Vink bij de vraag welke API's je gebruikt 'Web API' aan, ga akkoord met de voorwaarden en klik op 'Save'.",
            "Open de app, ga naar 'User Management' en voeg je eigen naam en het e-mailadres van je Spotify-account toe.",
            "Kopieer op de pagina 'Basic Information' de Client ID en plak hem hieronder. Een client secret is niet nodig.",
            "Klik op 'Opslaan en verbinden' en geef in de browser toestemming voor 'recent beluisterd'.",
        ),
        fields=("client_id",),
        note="De Client ID en de toegangssleutels gaan alleen in de Sleutelhanger van macOS. Je wachtwoord komt nooit "
             "in DataVault. Elke 6 maanden vraagt Spotify je opnieuw om toestemming.",
        oauth=True,
        links=(("Spotify-ontwikkelaarsdashboard openen", "https://developer.spotify.com/dashboard"),),
    )
    help = (
        HelpOption(
            title="Export aanvragen (de volledige geschiedenis)", effort="makkelijk",
            steps=("Klik op Export aanvragen: de privacypagina van Spotify opent in je browser.",
                   "Vink 'Extended streaming history' aan en vraag hem aan; bevestig de e-mail.",
                   "Download de zip uit de e-mail als hij klaar is (meestal 1 tot 5 dagen): DataVault doet de rest."),
            note="Doe dit 2 tot 4 keer per jaar: de export is de blijvende, volledige kopie met echte luistertijd."),
        HelpOption(
            title="Elke dag bij met de Spotify-API", effort="gemiddeld",
            steps=("Spotify Premium en een gratis ontwikkelaarsapp (ongeveer 5 minuten, zie Verbinden).",
                   "Plak de Client ID en klik op Verbinden; geef toestemming in de browser.",
                   "Klik op Bijwerken, of zet het schema aan (elk half uur zolang DataVault draait; in Instellingen "
                   "ook als DataVault dicht is)."),
            note="De API geeft alleen je laatste 50 nummers, zonder podcasts en zonder echte luistertijd: die schat "
                 "DataVault met de lengte van het nummer tot de volgende export."),
        HelpOption(
            title="Gaten opvullen met Last.fm", effort="moeilijk",
            steps=("Maak een Last.fm-account en koppel Spotify-scrobbling in de Last.fm-instellingen.",
                   "Vraag een API-sleutel aan en vul hem in op de kaart van Last.fm.",
                   "Last.fm vult aan wat de API mist als je Mac lang uit stond."),
            note="Last.fm registreert alleen vanaf het moment dat je koppelt."),
    )

    # ---------- exports ----------

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names is not None:
            kinds = {_kind_of(n) for n in names} - {None}
            if not kinds:
                return None
            return "extended" if "extended" in kinds else "account"
        kind = _kind_of(path.name)
        if kind is None:
            return None
        try:
            with open(path, "rb") as f:
                head = f.read(4096).lstrip()
        except OSError:
            return None
        if head.startswith(b"[") and (b'"ts"' in head or b'"endTime"' in head or head.rstrip() == b"[]"):
            return kind
        return None

    def find_existing(self, ctx: GatherContext) -> List[Tuple[Path, str]]:
        """Every folder below data/ with Spotify history files, except DataGatherer's own copies."""
        from backend.paths import walk_archive
        data = raw_store.data_folder(ctx.config)
        if not data.is_dir():
            return []
        own = (ctx.owner_folder / self.raw_folder) if ctx.owner_folder else None
        found = []
        for root, dirs, files in walk_archive(data, ctx.config):
            dirs.sort()
            folder = Path(root)
            if own is not None and (folder == own or own in folder.parents):
                dirs[:] = []
                continue
            kinds = {_kind_of(n) for n in files} - {None}
            if kinds:
                found.append((folder, "extended" if "extended" in kinds else "account"))
                dirs[:] = []   # its subfolders are read with it
        return found

    folder_patterns = ("Streaming_History_*.json", "StreamingHistory*.json")

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        added = seen = files = 0
        kinds = {"extended": 0, "account": 0}
        lo = hi = None
        for name, read in raw_store.iter_export_files(Path(raw["path"]), _history_file):
            ctx.check()
            kind = _kind_of(name)
            try:
                plays = json.loads(read())
            except ValueError:
                raise ConnectorError(f"{name} is geen geldig JSON-bestand; is de export volledig gedownload?")
            if not isinstance(plays, list):
                continue
            files += 1
            video = "Streaming_History_Video" in name
            rows = []
            for play in plays:
                row = (spotify_store.extended_row(play, raw["id"], now, video) if kind == "extended"
                       else spotify_store.account_row(play, raw["id"], now))
                if row is None:
                    continue
                rows.append(row)
                ts = row["ts_ms"] / 1000
                lo = ts if lo is None or ts < lo else lo
                hi = ts if hi is None or ts > hi else hi
            n_added, n_seen = spotify_store.insert_plays(ctx.db, rows, check=ctx.check)
            added += n_added
            seen += n_seen
            kinds[kind] += n_seen
            ctx.progress(message=f"Spotify inlezen: {files} bestanden, {seen:,} plays".replace(",", "."))
        if not files:
            raise ConnectorError("Geen Spotify-luistergeschiedenis gevonden in deze export")
        return {"added": added, "seen": seen, "files": files, "kinds": kinds, "covers_from": lo, "covers_until": hi}

    def after_import(self, ctx: GatherContext) -> None:
        spotify_store.reconcile(ctx.db)

    # ---------- the Web API ----------

    def is_configured(self, ctx: GatherContext) -> bool:
        return ctx.secrets.has("client_id") and ctx.secrets.has("token")

    def authorize_url(self, ctx: GatherContext, redirect_uri: str, state_value: str, challenge: str) -> str:
        client_id = ctx.secrets.get("client_id")
        if not client_id:
            raise ConnectorError("Vul eerst de Client ID in")
        return AUTH_URL + "?" + urlencode({
            "response_type": "code", "client_id": client_id, "scope": SCOPE, "redirect_uri": redirect_uri,
            "state": state_value, "code_challenge_method": "S256", "code_challenge": challenge,
        })

    def finish_authorization(self, ctx: GatherContext, code: str, redirect_uri: str, verifier: str) -> None:
        client_id = ctx.secrets.get("client_id")
        if not client_id:
            raise ConnectorError("De Client ID ontbreekt; vul hem opnieuw in")
        with ctx.http() as http:
            r = http.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                           "redirect_uri": redirect_uri, "client_id": client_id,
                                           "code_verifier": verifier})
        data = _json(r)
        if r.status_code != 200 or not data.get("access_token") or not data.get("refresh_token"):
            raise ConnectorError(f"Spotify gaf geen toegang ({data.get('error_description') or data.get('error') or r.status_code})")
        now = time.time()
        self._store_token(ctx, {"access_token": data["access_token"], "refresh_token": data["refresh_token"],
                                "expires_at": now + int(data.get("expires_in", 3600)) - 60,
                                "scope": data.get("scope", SCOPE), "authorized_at": now})
        state.update(ctx.db, self.id, connected_at=now, needs_reconnect=0, last_error=None, status_note=None)

    def _token(self, ctx: GatherContext) -> Dict[str, Any]:
        try:
            token = json.loads(ctx.secrets.get("token") or "")
        except ValueError:
            token = None
        if not isinstance(token, dict) or not token.get("refresh_token"):
            raise ConnectorError("Spotify is niet verbonden: klik Verbinden", reconnect=True)
        return token

    def _store_token(self, ctx: GatherContext, token: Dict[str, Any]) -> None:
        ctx.secrets.set("token", json.dumps(token))

    def _refresh(self, ctx: GatherContext, http, token: Dict[str, Any]) -> Dict[str, Any]:
        r = http.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                                       "client_id": ctx.secrets.get("client_id") or ""})
        data = _json(r)
        if r.status_code == 400 and data.get("error") == "invalid_grant":
            raise ConnectorError("Spotify opnieuw verbinden: de toestemming is verlopen (elke 6 maanden) of ingetrokken",
                                 reconnect=True)
        if r.status_code == 429:
            self._too_many(ctx, r)
        if r.status_code != 200 or not data.get("access_token"):
            raise ConnectorError(f"Spotify vernieuwde de toegang niet ({data.get('error') or r.status_code})")
        token = {**token, "access_token": data["access_token"],
                 "expires_at": time.time() + int(data.get("expires_in", 3600)) - 60}
        if data.get("refresh_token"):
            token["refresh_token"] = data["refresh_token"]   # rotation: the old one may stop working
        self._store_token(ctx, token)
        return token

    def _too_many(self, ctx: GatherContext, response) -> None:
        try:
            wait = max(1, int(response.headers.get("Retry-After", "60")))
        except ValueError:
            wait = 60
        raise ConnectorError(f"Spotify vraagt om {wait} seconden te wachten; DataVault probeert het daarna opnieuw",
                             retry_after=time.time() + wait)

    def sync(self, ctx: GatherContext) -> Dict[str, Any]:
        token = self._token(ctx)
        previous = state.get_cursor(ctx.db, self.id, "recently_played") or {}
        with ctx.http() as http:
            if time.time() >= token.get("expires_at", 0):
                token = self._refresh(ctx, http, token)
            ctx.progress(0, None, "Recent beluisterd ophalen…")
            response = None
            for attempt in range(3):
                ctx.check()
                # Always the last 50: plays from an offline device can arrive later with an older
                # time, so 'after' alone would miss them; the keys drop what is already stored
                response = http.get(RECENT_URL, params={"limit": 50},
                                    headers={"Authorization": f"Bearer {token['access_token']}"})
                if response.status_code == 401 and attempt == 0:
                    token = self._refresh(ctx, http, token)
                    continue
                if response.status_code == 429:
                    try:
                        wait = int(response.headers.get("Retry-After", "60"))
                    except ValueError:
                        wait = 60
                    if wait <= MAX_RETRY_WAIT and attempt < 2:
                        ctx.progress(message=f"Spotify vraagt {wait} s te wachten…")
                        ctx.sleep(wait)
                        continue
                    self._too_many(ctx, response)
                break
        if response.status_code == 403:
            raise ConnectorError("Spotify weigert de toegang: staat je account onder 'User Management' van je app, "
                                 "en heb je nog Premium?")
        if response.status_code != 200:
            raise ConnectorError(f"Spotify antwoordde met fout {response.status_code}; probeer het later opnieuw")
        items = _json(response).get("items") or []
        now = time.time()
        rows = [r for r in (spotify_store.api_row(item, now) for item in items) if r is not None]
        newest = max((r["ts_ms"] for r in rows), default=previous.get("last_played_ms"))
        oldest = min((r["ts_ms"] for r in rows), default=None)
        last_known = spotify_store.last_api_play(ctx.db)
        # 50 new plays with none we knew: what came before them is gone from this route
        gap = bool(last_known and len(rows) >= 50 and oldest and oldest > last_known)
        added = spotify_store.insert_api_plays(ctx.db, rows, {"last_played_ms": newest, "fetched_at": now})
        if added:
            spotify_store.reconcile(ctx.db)
        state.update(ctx.db, self.id, status_note="gap" if gap else None)
        return {"added": added, "seen": len(rows), "gap_possible": gap, "complete": True, "errors": 0}

    # ---------- the card ----------

    def coverage(self, ctx: GatherContext) -> Coverage:
        c = spotify_store.coverage(ctx.db)
        return Coverage(records=c["plays"], first=c["first"], last=c["last"], estimated_since=c["estimated_since"],
                        segments=[(s["from"], s["until"], s["estimated"]) for s in c["segments"]])

    def consent_expires(self, ctx: GatherContext) -> Optional[float]:
        try:
            token = json.loads(ctx.secrets.get("token") or "")
        except ValueError:
            return None
        authorized = token.get("authorized_at") if isinstance(token, dict) else None
        return authorized + CONSENT_DAYS * 86400 if authorized else None

    def notes(self, ctx: GatherContext) -> List[Dict[str, str]]:
        notes = []
        st = state.get_state(ctx.db, self.id)
        expires = self.consent_expires(ctx)
        if expires and not st["needs_reconnect"]:
            days = math.ceil((expires - time.time()) / 86400)
            if days <= WARN_DAYS:
                notes.append({"tone": "warning", "text":
                              f"De koppeling met Spotify verloopt over {max(days, 0)} dag{'' if days == 1 else 'en'}: "
                              "klik dan op Opnieuw verbinden (Spotify vraagt elke 6 maanden toestemming)."})
        if st["status_note"] == "gap":
            notes.append({"tone": "warning", "text":
                          "Bij de laatste keer waren er meer dan 50 nieuwe nummers: wat daarvoor kwam, geeft de API niet "
                          "meer. Last.fm of je volgende export vult dat aan."})
        return notes


def _json(response) -> Dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


CONNECTOR = Spotify()
