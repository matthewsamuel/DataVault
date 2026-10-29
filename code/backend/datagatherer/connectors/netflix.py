"""
DataGatherer connector: Netflix (viewing activity)

Netflix has two exports, both requested by the user in their own browser:
  - 'Kijkactiviteit' on the account page (per profile): NetflixViewingHistory.csv with
    Title,Date (a day, no time; the date order follows the account's language);
  - 'Je persoonlijke gegevens downloaden': a zip with CONTENT_INTERACTION/ViewingActivity.csv
    (Profile Name, Start Time in UTC, Duration, Title, Device Type, Country, ...).
Both become activity events of kind video_watch; the key is the profile, the start time
and the title, so a second export of the same history adds nothing.
"""
import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import activity_store as store, export_kit
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption

FULL_HEADER = "Profile Name,Start Time,Duration"
SIMPLE_HEADER = "Title,Date"


def _duration_s(text: str) -> Optional[int]:
    parts = (text or "").split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    h, m, s = (int(p) for p in parts)
    return h * 3600 + m * 60 + s


def _simple_date(text: str) -> Optional[int]:
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y"):
        try:
            day = datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        return int(day.replace(hour=12, tzinfo=timezone.utc).timestamp() * 1000)
    return None


def parse_csv(text: str) -> List[Dict[str, Any]]:
    events = []
    first = text.split("\n", 1)[0]
    rows = csv.DictReader(io.StringIO(text))
    if first.startswith(FULL_HEADER):
        for row in rows:
            try:
                start = datetime.strptime((row.get("Start Time") or "").strip(), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            ts = int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
            title = (row.get("Title") or "").strip()
            if not title or (row.get("Supplemental Video Type") or "").strip():   # trailers and previews
                continue
            dur = _duration_s(row.get("Duration") or "")
            profile = (row.get("Profile Name") or "").strip()
            details = {k: v for k, v in (("profiel", profile), ("apparaat", (row.get("Device Type") or "").strip()),
                                         ("land", (row.get("Country") or "").strip())) if v}
            if dur is not None:
                details["duur_s"] = dur
            events.append(store.event("video_watch", ts, store.key("netflix", profile, ts, title), title=title,
                                      details=details, end_ms=ts + dur * 1000 if dur else None))
    elif first.startswith(SIMPLE_HEADER):
        for row in rows:
            title = (row.get("Title") or "").strip()
            ts = _simple_date(row.get("Date") or "")
            if title and ts is not None:
                events.append(store.event("video_watch", ts, store.key("netflix-dag", ts, title), title=title,
                                          details={"alleen_datum": True}))
    return events


class Netflix(Connector):
    id = "netflix"
    name = "Netflix"
    icon = "fas fa-film"
    category = "Activiteit"
    order = 50
    description = "Wat je op Netflix keek, uit je kijkactiviteit of je volledige Netflix-export."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Netflix"
    record_label = ("titel", "titels")
    stale_days = 90
    settings = (export_kit.PASHA,)
    exports = (
        ExportKind(
            id="kijkactiviteit", label="Kijkactiviteit", request_url="https://www.netflix.com/viewingactivity",
            patterns=("netflixviewinghistory*.csv", "viewingactivity*.csv", "netflix-report*.zip", "*.csv", "*.zip"),
            folder_label="Kijkactiviteit", expected_days=(0, 30),
            steps=("Log in op de pagina die nu opent en kies je profiel.",
                   "Scrol naar onder en klik op 'Alles downloaden': je krijgt meteen een CSV-bestand.",
                   "Voor alle profielen met kijkduur: vraag via Account > 'Je persoonlijke gegevens downloaden' de "
                   "volledige export aan (klaar binnen 30 dagen).",
                   "DataVault vindt het bestand in Downloads en leest het in.")),
    )
    help = (
        HelpOption(title="Kijkactiviteit downloaden", effort="makkelijk",
                   steps=("Klik op Export aanvragen en kies je profiel.", "Klik onderaan op 'Alles downloaden'."),
                   note="Meteen klaar, maar alleen de dag (geen uur) en per profiel."),
        HelpOption(title="Volledige Netflix-export", effort="gemiddeld",
                   steps=("Netflix > Account > Beveiliging en privacy > 'Je persoonlijke gegevens downloaden'.",
                          "Bevestig de mail; download de zip als Netflix laat weten dat hij klaar is."),
                   note="Met starttijd, duur, apparaat en alle profielen."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names is not None:
            return "kijkactiviteit" if any(n.endswith("CONTENT_INTERACTION/ViewingActivity.csv") for n in names) else None
        first = export_kit.first_line(export_kit.head(path, 512))
        return "kijkactiviteit" if first.startswith((FULL_HEADER, SIMPLE_HEADER)) else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        events: List[Dict[str, Any]] = []
        wanted = lambda n: n.lower().endswith(".csv") and ("viewing" in n.lower() or "netflix" in n.lower())
        for name, read in export_kit.files(Path(raw["path"]), wanted):
            ctx.check()
            events += parse_csv(read().decode("utf-8-sig", "replace"))
        if not events:
            raise ConnectorError("Geen Netflix-kijkactiviteit gevonden in dit bestand")
        return store.insert(ctx.db, self.id, raw["id"], events, check=ctx.check)

    def coverage(self, ctx: GatherContext) -> Coverage:
        return store.coverage(ctx.db, self.id)


CONNECTOR = Netflix()
