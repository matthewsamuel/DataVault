"""
DataGatherer connector: Swarm by Foursquare (check-ins)

Swarm has no export button in DataVault's sense: the user asks Foursquare for a copy of
their data in the Swarm app (Instellingen > Privacy-instellingen > 'Initiate Data Download
Request'; the web login on foursquare.com is now the one for developers and companies, not
for Swarm accounts), and after a few days to two weeks a mail brings a zip with, among
others:

    checkins1.json, checkins2.json, ...   {"count": n, "items": [check-in, ...]}, 1000 per file
    visits.json, unconfirmed_visits.json  places the app noticed by itself (optional)
    *.csv, photos.json, pix/              the same data as CSV and the photos (not read here)

A check-in: {"id", "createdAt": "2019-07-14 10:00:00.000000" (UTC), "type": "checkin" or
"venueless", "shout", "timeZoneOffset" (minutes), "venue": {"id", "name", "url"}, "lat",
"lng", "hacc"}. JSON from the API (tools that downloaded the history earlier) has
createdAt in epoch seconds and the position in venue.location; both are read.

Every check-in becomes an activity event (kind checkin, key: the check-in id) and, with a
position, a location point and a visit at that place (origin swarm). The app's own visits
only add points. After reading, photos without GPS get an estimated position (geotag.py),
as with the Google Tijdlijn.
"""
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.datagatherer import activity_store as store, export_kit, geotag, location_store
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption

# Foursquare's own instructions ('Request copies of your personal data'); the request itself is made in the app
PRIVACY_URL = "https://foursquare.com/legal/privacy-center/"
_CHECKINS = re.compile(r"^checkins\d*\.json$", re.IGNORECASE)
_VISITS = re.compile(r"^(unconfirmed_)?visits\d*\.json$", re.IGNORECASE)
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _base(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def looks_like_checkins(head: bytes) -> bool:
    text = head[:4096].decode("utf-8", "ignore")
    return '"items"' in text and '"createdAt"' in text


def parse_time(value: Any) -> Optional[int]:
    """'2019-07-14 10:00:00.000000' (UTC, the export), epoch seconds (the API) or ISO 8601 -> UTC ms."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        n = int(float(value))
        return n if n > 10 ** 11 else n * 1000          # seconds, or already milliseconds
    text = str(value).strip()
    if _STAMP.match(text):
        try:
            dt = datetime.fromisoformat(text.replace(" ", "T")[:26])
        except ValueError:
            return None
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return location_store.parse_time(text)


def _position(item: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
    loc = venue.get("location") if isinstance(venue.get("location"), dict) else {}
    for holder in (item, loc):
        if holder.get("lat") is not None and holder.get("lng") is not None:
            return location_store._valid(holder.get("lat"), holder.get("lng"))
    return None


def _items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        resp = data.get("response")
        if isinstance(resp, dict) and isinstance(resp.get("checkins"), dict):
            data = resp["checkins"]
        data = data.get("items")
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def parse_checkins(data: Any) -> Tuple[List[Dict[str, Any]], List[tuple], List[dict]]:
    """(activity events, location points, visits) of one checkins file."""
    events, points, visits = [], [], []
    for item in _items(data):
        ts = parse_time(item.get("createdAt"))
        if ts is None:
            continue
        venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
        name = (venue.get("name") or "").strip() or None
        shout = (item.get("shout") or "").strip() or None
        pos = _position(item)
        if not (name or shout or pos):
            continue
        details = {"tekst": shout} if shout and name else {}      # without a place the text is the title
        if pos:
            details["lat"], details["lon"] = pos
        url = venue.get("url") or venue.get("canonicalUrl")
        url = url if isinstance(url, str) and url.startswith("https://") else None
        events.append(store.event("checkin", ts, store.key("swarm", item.get("id") or ts, name), title=name or shout,
                                  url=url, details=details))
        if pos:
            points.append((ts, pos[0], pos[1], location_store._num(item.get("hacc"))))
            visits.append({"start": ts, "end": ts, "lat": pos[0], "lon": pos[1], "place_id": venue.get("id"),
                           "name": name, "address": None, "semantic_type": "check-in", "probability": None})
    return events, points, visits


def parse_visits(data: Any) -> List[tuple]:
    """The places the app noticed by itself: only points (time and position), no names."""
    points = []
    for item in _items(data):
        ts = parse_time(item.get("createdAt") or item.get("timestamp") or item.get("arrivalTime"))
        pos = _position(item)
        if ts is not None and pos:
            points.append((ts, pos[0], pos[1], location_store._num(item.get("hacc"))))
    return points


class Swarm(Connector):
    id = "swarm"
    name = "Swarm"
    icon = "fab fa-foursquare"
    category = "Locatie"
    order = 20
    description = "Waar je incheckte met Swarm van Foursquare: de plek, het moment en wat je erbij schreef."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Swarm"
    record_label = ("check-in", "check-ins")
    # Swarm is an app from the past: an export with old check-ins is complete, not years 'achter'
    stale_days = 3650
    settings = (export_kit.PASHA,)
    exports = (
        ExportKind(
            id="gegevens", label="Foursquare-gegevens", request_url=PRIVACY_URL,
            patterns=("*.zip", "checkins*.json"), folder_label="Gegevens", expected_days=(3, 14),
            intro="Je vraagt de export aan in de Swarm-app op je iPhone: inloggen op de website van Foursquare "
                  "werkt niet meer voor Swarm-accounts. De knop opent de uitleg van Foursquare zelf.",
            steps=(
                "Open Swarm op je iPhone, ga naar je profiel en tik rechtsboven op het tandwiel (geen app meer? "
                "Installeer Swarm uit de App Store en log in met je oude account).",
                "Kies Privacy-instellingen, scrol naar onder en tik op 'Initiate Data Download Request' (in een "
                "Spaanse of andere app heet het net zo in die taal). Bevestig daarna de mail van Foursquare.",
                "Binnen enkele dagen tot twee weken krijg je een mail met een downloadlink. DataVault vindt de zip "
                "in Downloads en leest hem in. Het origineel blijft staan.",
            )),
    )
    help = (
        HelpOption(
            title="Export aanvragen in de Swarm-app", effort="makkelijk",
            steps=("Open Swarm op je iPhone, ga naar je profiel en tik rechtsboven op het tandwiel.",
                   "Kies Privacy-instellingen > 'Initiate Data Download Request'.",
                   "Download de zip uit de mail van Foursquare op deze Mac, of kies hem hier met Bestand kiezen."),
            note="De export bevat al je check-ins, ook die van jaren geleden, samen met je Foursquare-gegevens."),
        HelpOption(
            title="Swarm staat niet meer op je iPhone", effort="gemiddeld",
            steps=("Installeer Swarm opnieuw uit de App Store en log in met het mailadres van je oude account.",
                   "Wachtwoord vergeten? Tik bij het inloggen op 'Wachtwoord vergeten' en zet het opnieuw via je mail.",
                   "Vraag daarna de export aan zoals hierboven; je kunt de app nadien weer verwijderen."),
            note="Foursquare geeft je gegevens alleen als je kunt inloggen. Inloggen op foursquare.com zelf werkt "
                 "niet meer voor Swarm-accounts."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names is not None:
            members = [n for n in names if _CHECKINS.match(_base(n))]
            if not members:
                return None
            try:
                with zipfile.ZipFile(path) as zf, zf.open(members[0]) as member:
                    return "gegevens" if looks_like_checkins(member.read(4096)) else None
            except (zipfile.BadZipFile, OSError, KeyError):
                return None
        if not _CHECKINS.match(path.name):
            return None
        return "gegevens" if looks_like_checkins(export_kit.head(path)) else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        events: List[Dict[str, Any]] = []
        points: List[tuple] = []
        visits: List[dict] = []
        found = False
        wanted = lambda n: bool(_CHECKINS.match(n) or _VISITS.match(n))
        for name, read in export_kit.files(Path(raw["path"]), wanted):
            ctx.check()
            ctx.progress(message=f"Inlezen: {_base(name)}")
            try:
                data = json.loads(read().decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError):
                continue
            if _CHECKINS.match(_base(name)):
                found = True
                e, p, v = parse_checkins(data)
                events += e
                points += p
                visits += v
            else:
                points += parse_visits(data)
        if not found:
            raise ConnectorError("Geen Swarm-check-ins gevonden in dit bestand")
        stats = store.insert(ctx.db, self.id, raw["id"], events, check=ctx.check)
        loc = location_store.insert(ctx.db, self.id, raw["id"], "swarm", points, visits, check=ctx.check)
        if loc["added"] or loc["visits_added"]:
            ctx.location_added = getattr(ctx, "location_added", 0) + loc["added"] + loc["visits_added"]
        stats.update(points_added=loc["added"], visits_added=loc["visits_added"])
        return stats

    def after_import(self, ctx: GatherContext) -> None:
        if getattr(ctx, "location_added", 0):
            geotag.run(ctx.db, check=ctx.check, progress=ctx.progress, full=True)

    def coverage(self, ctx: GatherContext) -> Coverage:
        return store.coverage(ctx.db, self.id)


CONNECTOR = Swarm()
