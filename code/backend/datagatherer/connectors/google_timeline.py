"""
DataGatherer connector: Google Maps Tijdlijn (location history from the phone)

Since 2025 the Timeline lives only on the phone: Takeout no longer has it and there is no
API. The user exports it in the Google Maps app (iPhone: Instellingen > Persoonlijke
content > 'Tijdlijngegevens exporteren'), saves the JSON file in an iCloud Drive folder,
and DataVault follows that folder (the 'Map om op te volgen' setting) or finds it in
Downloads after an AirDrop. location_store.parse recognises the format by its content: the
iPhone list format, the Android semanticSegments/rawSignals format, and also the old
Takeout Records.json and Semantic Location History files, when the user drops those in.

After reading, photos without GPS get an estimated position from the new history
(geotag.py; only in the database, never in the files).
"""
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import export_kit, geotag, location_store, raw_store
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption

HELP_URL = "https://support.google.com/maps/answer/14169818"


class GoogleTimeline(Connector):
    id = "google_timeline"
    name = "Google Maps Tijdlijn"
    icon = "fas fa-route"
    category = "Locatie"
    order = 10
    description = "Waar je was, uit de Tijdlijn van Google Maps op je telefoon: punten, routes en bezochte plekken."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Google Tijdlijn"
    record_label = ("locatiepunt", "locatiepunten")
    stale_days = 60
    settings = (
        export_kit.watch_setting(
            "De iCloud Drive-map waarin je de Tijdlijn-export van je iPhone bewaart. DataVault leest elke nieuwe "
            "export daaruit in.",
            placeholder="/Users/jij/Library/Mobile Documents/com~apple~CloudDocs/DataVault"),
        export_kit.PASHA,
    )
    exports = (
        ExportKind(
            id="tijdlijn", label="Tijdlijn-export", request_url=HELP_URL,
            patterns=("*.json", "*.zip"), folder_label="Tijdlijn", expected_days=(0, 0),
            steps=(
                "Open op je iPhone de app Google Maps en tik rechtsboven op je profielfoto.",
                "Tik op Instellingen > Persoonlijke content > 'Tijdlijngegevens exporteren'.",
                "Kies 'Bewaar in Bestanden' en dan een map in iCloud Drive (bijvoorbeeld 'DataVault').",
                "DataVault vindt het bestand in die map (zet hem bij Instellingen) of in Downloads na AirDrop, "
                "en leest het in. Het origineel blijft staan.",
            )),
    )
    help = (
        HelpOption(
            title="Exporteren naar iCloud Drive", effort="makkelijk",
            steps=("Google Maps op je iPhone > profielfoto > Instellingen > Persoonlijke content > "
                   "'Tijdlijngegevens exporteren'.",
                   "Bewaar het bestand in een iCloud Drive-map, bijvoorbeeld 'DataVault'.",
                   "Vul die map één keer in bij Instellingen > 'Map om op te volgen'. Daarna leest DataVault "
                   "elke nieuwe export vanzelf in."),
            note="De Tijdlijn staat alleen op je telefoon; Google bewaart hem niet meer online."),
        HelpOption(
            title="Via AirDrop of Downloads", effort="makkelijk",
            steps=("Exporteer de Tijdlijn zoals hierboven en deel het bestand met AirDrop naar deze Mac.",
                   "Klik hier op In Downloads zoeken, of op Bestand kiezen."),
            note=""),
        HelpOption(
            title="Je oude Tijdlijn uit Takeout", effort="gemiddeld",
            steps=("Oude Takeouts hebben een map 'Locatiegeschiedenis' (of 'Location History') met Records.json "
                   "en Semantic Location History.",
                   "Die leest de kaart Google Takeout al in. Een los Records.json kun je hier ook kiezen."),
            note="Beide formaten komen in dezelfde locatiegeschiedenis, zonder dubbels."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names is not None:
            # A zipped Timeline export (shared from the phone as a zip): a JSON that looks like one
            if len(names) > 20 or any(n.startswith("Takeout/") for n in names):
                return None
            try:
                with zipfile.ZipFile(path) as zf:
                    for n in names:
                        if n.lower().endswith(".json"):
                            with zf.open(n) as member:
                                if location_store.looks_like_location_json(member.read(4096)):
                                    return "tijdlijn"
            except (zipfile.BadZipFile, OSError, KeyError):
                return None
            return None
        return "tijdlijn" if location_store.looks_like_location_json(export_kit.head(path)) else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        totals: Dict[str, Any] = {"added": 0, "seen": 0, "visits_added": 0}
        formats = set()
        for name, read in export_kit.files(Path(raw["path"]), lambda n: n.lower().endswith(".json")):
            ctx.check()
            ctx.progress(message=f"Inlezen: {name.rsplit('/', 1)[-1]}")
            try:
                fmt, points, visits = location_store.parse_export_file(read)
            except location_store.NotLocationHistory:
                continue
            except raw_store.RawStoreError as e:
                raise ConnectorError(str(e)) from e
            try:          # a streamed Records.json is only read here
                stats = location_store.insert(ctx.db, self.id, raw["id"], fmt, points, visits, check=ctx.check)
            except (location_store.NotLocationHistory, raw_store.RawStoreError) as e:
                raise ConnectorError(f"{name.rsplit('/', 1)[-1]}: {e}") from e
            formats.add(fmt)
            totals["visits_added"] += stats["visits_added"]
            export_kit.merge_stats(totals, stats)
        if not formats:
            raise ConnectorError("Geen Tijdlijn-gegevens gevonden in dit bestand")
        if totals["added"] or totals["visits_added"]:
            ctx.location_added = getattr(ctx, "location_added", 0) + totals["added"] + totals["visits_added"]
        totals["formats"] = sorted(formats)
        return totals

    def after_import(self, ctx: GatherContext) -> None:
        if getattr(ctx, "location_added", 0):
            geotag.run(ctx.db, check=ctx.check, progress=ctx.progress, full=True)

    def coverage(self, ctx: GatherContext) -> Coverage:
        return location_store.coverage(ctx.db, self.id)

    def notes(self, ctx: GatherContext) -> List[Dict[str, str]]:
        n = geotag.count(ctx.db)
        if n:
            return [{"tone": "info", "text": f"{export_kit.plural(n, 'foto zonder GPS heeft', 'foto’s zonder GPS hebben')} "
                                             "nu een geschatte locatie uit je Tijdlijn (alleen in DataVault, niet in de "
                                             "bestanden). Zet op de Kaart 'Locatie geschat uit Tijdlijn' aan."}]
        return []


CONNECTOR = GoogleTimeline()
