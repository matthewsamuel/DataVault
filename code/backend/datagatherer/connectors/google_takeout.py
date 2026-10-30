"""
DataGatherer connector: Google Takeout

Takeout exports are requested on takeout.google.com in the user's own browser, recognised
in Downloads (or in the synced folder of a scheduled Takeout) by their content (members
under 'Takeout/'), copied into data/<owner>/Google/<datum> Takeout/, and the existing
Takeouts in 'data/<owner>/RAW_google' are registered in place (folders with takeout-*.zip
parts or unpacked 'Takeout' folders: both are read without unpacking anything).

Reading turns what it knows into records (google_formats.route decides per file, by the
Dutch and English names):
    Mijn activiteit / My Activity       YouTube, Zoeken, Maps, Chrome, ... (JSON or HTML)
    YouTube: kijkgeschiedenis, zoekgeschiedenis (watch-history, search-history)
    Chrome: BrowserHistory.json
    Agenda / Calendar: *.ics
    Keep: one JSON per note
        -> activity_events (source google_takeout)
    Locatiegeschiedenis / Location History / Tijdlijn: Records.json, Semantic Location History
        -> location_points and location_visits (origin google_takeout), then photos without
           GPS get an estimated position (geotag.py)
Everything else in a Takeout (photos, mail, Drive) stays in the archive as it is.
"""
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import activity_store, export_kit, geotag, google_formats as gf, location_store, raw_store
from backend.datagatherer.base import Connector, Coverage, ExportKind, GatherContext, HelpOption

TAKEOUT_URL = "https://takeout.google.com/"
READ_EXTENSIONS = (".json", ".html", ".ics")


def _product(name: str) -> Optional[str]:
    """'Takeout/Mijn activiteit/…' (also below a folder or an inner zip) -> 'Mijn activiteit'."""
    name = name.replace("\\", "/")
    at = name.find("Takeout/")
    parts = name[at:].split("/") if at >= 0 else []
    if len(parts) >= 3 and parts[0] == "Takeout":
        return parts[1]
    return None


def merged_coverage(a: Coverage, b: Coverage) -> Coverage:
    if not b.records:
        return a
    if not a.records:
        return b
    return Coverage(records=a.records + b.records, first=min(a.first, b.first), last=max(a.last, b.last),
                    segments=a.segments + b.segments)


class GoogleTakeout(Connector):
    id = "google_takeout"
    name = "Google Takeout"
    icon = "fab fa-google"
    category = "Activiteit"
    order = 10
    description = "Je Google-gegevens uit Takeout: Mijn activiteit, YouTube, Chrome, Agenda, Keep en je oude Tijdlijn."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Google"
    existing_folders = ("RAW_google",)
    folder_patterns = ("*.zip", "*.tgz", "*.json", "*.html", "*.ics")   # what the fingerprint looks at (not every photo)
    record_label = ("activiteit", "activiteiten")
    stale_days = 120
    parser_version = 2
    settings = (
        export_kit.watch_setting(
            "Voor Takeout volgens schema: de map waarin Google Drive voor desktop je Takeout-exports zet. "
            "DataVault kopieert elke nieuwe export daaruit naar het archief.",
            placeholder="/Users/jij/Google Drive/Mijn Drive/Takeout"),
        export_kit.PASHA,
    )
    exports = (
        ExportKind(
            id="takeout", label="Takeout", request_url=TAKEOUT_URL,
            patterns=("takeout-*.zip", "takeout-*.tgz", "*.zip"), folder_label="Takeout", expected_days=(0, 2),
            steps=(
                "Log in op de pagina die nu opent, in je eigen browser.",
                "Kies wat je wilt exporteren (Mijn activiteit, YouTube, Chrome, Agenda, Keep) en het formaat JSON waar dat kan.",
                "Kies 'Eenmalig exporteren', .zip en een grootte, en klik op 'Export maken'.",
                "Google mailt een downloadlink (minuten tot een dag later); download de zip-bestanden.",
                "DataVault kopieert ze uit Downloads naar het archief. De originelen mag je daarna weggooien.",
            )),
    )
    help = (
        HelpOption(
            title="Takeout aanvragen", effort="makkelijk",
            steps=("Klik op Export aanvragen: Google Takeout opent in je browser.",
                   "Kies de producten en JSON, en download de zip als Google mailt dat hij klaar is."),
            note="De downloadlink blijft ongeveer een week geldig."),
        HelpOption(
            title="Takeout volgens schema", effort="gemiddeld",
            steps=("Kies in Takeout bij 'Bestemming' de optie 'Toevoegen aan Drive' en bij 'Frequentie' "
                   "'Elke 2 maanden exporteren (gedurende 1 jaar)'.",
                   "Installeer Google Drive voor desktop en maak de map 'Takeout' op deze Mac beschikbaar.",
                   "Open hier de instellingen van Google Takeout en vul bij 'Map om op te volgen' die map in.",
                   "Elke nieuwe export die Drive binnenhaalt, kopieert DataVault vanzelf naar het archief en leest hem in."),
            note="Na een jaar stel je het schema in Takeout opnieuw in. Je gegevens gaan dan via je eigen Google Drive."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names and any(_product(n) for n in names[:2000]):
            return "takeout"
        return None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        products: Counter = Counter()
        kinds: Counter = Counter()
        totals: Dict[str, Any] = {"added": 0, "seen": 0, "files": 0, "locations_added": 0, "visits_added": 0}
        skipped: List[str] = []
        wanted = lambda base: base.lower().endswith(READ_EXTENSIONS)
        for name, read in export_kit.files(Path(raw["path"]), wanted):
            product = _product(name)
            if product:
                products[product] += 1
            what = gf.route(name)
            if what is None:
                continue
            ctx.check()
            ctx.progress(message=f"Inlezen: {name.rsplit('/', 1)[-1]}")
            try:
                if what == "location":
                    try:     # a large Records.json is read as it goes, never whole
                        fmt, points, visits = location_store.parse_export_file(read)
                    except location_store.NotLocationHistory:
                        continue
                    stats = location_store.insert(ctx.db, self.id, raw["id"], fmt, points, visits, check=ctx.check)
                    totals["locations_added"] += stats["added"]
                    totals["visits_added"] += stats["visits_added"]
                    export_kit.merge_stats(totals, {"seen": stats["seen"], "covers_from": stats["covers_from"],
                                                    "covers_until": stats["covers_until"]})
                else:
                    stats = activity_store.insert(ctx.db, self.id, raw["id"], self._parse(what, name, read()),
                                                  check=ctx.check)
                    export_kit.merge_stats(totals, stats)
            except (ValueError, UnicodeDecodeError, raw_store.RawStoreError) as e:
                if len(skipped) < 10:
                    skipped.append(f"{name.rsplit('/', 1)[-1]}: {str(e)[:120]}")
                continue
            kinds[what] += stats["seen"]
            totals["files"] += 1
        if totals["locations_added"]:
            ctx.location_added = getattr(ctx, "location_added", 0) + totals["locations_added"]
        totals["products"] = dict(sorted(products.items()))
        totals["kinds"] = dict(kinds)
        if skipped:
            totals["skipped"] = skipped
        return totals

    @staticmethod
    def _parse(what: str, name: str, blob: bytes) -> List[Dict[str, Any]]:
        html = name.lower().endswith(".html")
        if what == "myactivity":
            return list((gf.parse_activity_html if html else gf.parse_activity_json)(blob, gf.product_of(name)))
        if what in ("watch", "search"):
            kind = "youtube_watch" if what == "watch" else "youtube_search"
            return list((gf.parse_activity_html if html else gf.parse_activity_json)(blob, "YouTube", kind))
        if what == "chrome":
            return list(gf.parse_chrome_takeout(blob))
        if what == "keep":
            return list(gf.parse_keep(blob, name))
        if what == "calendar":
            return list(gf.parse_ics(blob, name))
        return []

    def after_import(self, ctx: GatherContext) -> None:
        if getattr(ctx, "location_added", 0):
            geotag.run(ctx.db, check=ctx.check, progress=ctx.progress, full=True)

    def coverage(self, ctx: GatherContext) -> Coverage:
        return merged_coverage(activity_store.coverage(ctx.db, self.id), location_store.coverage(ctx.db, self.id))

    def notes(self, ctx: GatherContext) -> List[Dict[str, str]]:
        loc = location_store.coverage(ctx.db, self.id)
        if loc.records:
            return [{"tone": "info", "text": f"Ook {export_kit.plural(loc.records, 'locatiepunt', 'locatiepunten')} "
                                             "uit je oude Tijdlijn: te zien op de Kaart."}]
        return []


CONNECTOR = GoogleTakeout()
