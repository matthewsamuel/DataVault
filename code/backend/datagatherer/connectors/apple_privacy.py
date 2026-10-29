"""
DataGatherer connector: Apple (privacy.apple.com, 'Vraag een kopie van je gegevens aan')

Apple prepares the copy in a few days and keeps it 14 days. It comes as zips (one per
category, sometimes zipped together again); export_kit.files reads zips inside zips in
memory. What is read now: 'Apple Music Play Activity.csv' (Apple Media Services), one row
per play with start time, duration, song and artist -> activity events of kind music.
They do NOT go into the Spotify tables: the Spotify dashboard stays Spotify only; the
Activiteit view shows both.

Apple Photos (the Photos library on this Mac) is not read by DataVault. The help names the
optional route: the open-source tool osxphotos ('osxphotos export <map> --update') into a
folder of the archive, which the media index then picks up like any folder. DataVault does
not depend on it.
"""
import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from backend.datagatherer import activity_store as store, export_kit
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption

PLAY_ACTIVITY = "apple music play activity"


def _iso(text: str) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def parse_play_activity(text: str) -> Iterator[Dict[str, Any]]:
    for row in csv.DictReader(io.StringIO(text)):
        ts = _iso(row.get("Event Start Timestamp") or row.get("Event End Timestamp") or "")
        song = (row.get("Song Name") or row.get("Content Name") or "").strip()
        if ts is None or not song:
            continue
        try:
            played = int(float(row.get("Play Duration Milliseconds") or 0))
        except ValueError:
            played = 0
        if played <= 0:
            continue
        artist = (row.get("Artist Name") or row.get("Container Artist Name") or "").strip()
        details: Dict[str, Any] = {"app": "Apple Music", "geluisterd_s": round(played / 1000)}
        if artist:
            details["artiest"] = artist[:200]
        album = (row.get("Album Name") or row.get("Container Album Name") or "").strip()
        if album:
            details["album"] = album[:200]
        title = f"{song} – {artist}" if artist else song
        yield store.event("music", ts, store.key("applemusic", ts, song, artist, played), title=title[:300],
                          details=details, end_ms=ts + played)


class ApplePrivacy(Connector):
    id = "apple_privacy"
    name = "Apple-gegevens"
    icon = "fab fa-apple"
    category = "Muziek"
    order = 40
    description = "Je Apple Music-luistergeschiedenis uit de kopie van je gegevens bij Apple (privacy.apple.com)."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Apple"
    record_label = ("luisterbeurt", "luisterbeurten")
    stale_days = 180
    settings = (export_kit.PASHA,)
    exports = (
        ExportKind(
            id="kopie", label="Kopie van je gegevens", request_url="https://privacy.apple.com/",
            patterns=("apple media services*.zip", "*.zip", "apple music play activity*.csv"),
            folder_label="Kopie van je gegevens", expected_days=(1, 7),
            steps=("Log in op de pagina die nu opent, in je eigen browser.",
                   "Kies 'Vraag een kopie van je gegevens aan' en vink 'Apple Media Services-gegevens' aan.",
                   "Apple mailt binnen ongeveer een week; download de zip-bestanden binnen 14 dagen.",
                   "DataVault vindt ze in Downloads en leest de Apple Music-geschiedenis in.")),
    )
    help = (
        HelpOption(title="Kopie aanvragen", effort="makkelijk",
                   steps=("Klik op Export aanvragen en kies 'Apple Media Services-gegevens'.",
                          "Download de zip als Apple mailt dat hij klaar is."),
                   note="Berichten (iMessage) zitten er niet in."),
        HelpOption(title="Apple Foto's (optioneel, met osxphotos)", effort="moeilijk",
                   steps=("Installeer zelf het open-source programma osxphotos (pip install osxphotos).",
                          "Exporteer met 'osxphotos export <map in je archief> --update' (nooit --cleanup).",
                          "DataVault ziet de foto's dan als gewone map in Bestanden."),
                   note="DataVault leest je Foto's-bibliotheek niet zelf en heeft osxphotos niet nodig."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names is not None:
            lowered = [n.lower() for n in names]
            if any(PLAY_ACTIVITY in n for n in lowered) or any(
                    n.endswith(".zip") and ("apple media services" in n or "apple_media_services" in n) for n in lowered):
                return "kopie"
            return None
        return "kopie" if PLAY_ACTIVITY in path.name.lower() and "Event Start Timestamp" in \
            export_kit.head(path, 8192).decode("utf-8", "ignore") else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        totals: Dict[str, Any] = {"added": 0, "seen": 0}
        found = False
        for name, read in export_kit.files(Path(raw["path"]), lambda n: PLAY_ACTIVITY in n.lower() and n.lower().endswith(".csv")):
            ctx.check()
            found = True
            stats = store.insert(ctx.db, self.id, raw["id"], parse_play_activity(read().decode("utf-8-sig", "replace")),
                                 check=ctx.check)
            export_kit.merge_stats(totals, stats)
        if not found:
            raise ConnectorError("Geen Apple Music-luistergeschiedenis gevonden in deze kopie")
        return totals

    def coverage(self, ctx: GatherContext) -> Coverage:
        return store.coverage(ctx.db, self.id)


CONNECTOR = ApplePrivacy()
