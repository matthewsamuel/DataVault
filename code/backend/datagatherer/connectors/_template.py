"""
DataGatherer connector TEMPLATE (not loaded: the registry skips modules starting with '_').

Copy this file to connectors/<your_source>.py, rename the class and the id, fill in the
metadata and implement what your capabilities promise. docs/DATAGATHERER_CONNECTORS.md
explains every field; tests/test_datagatherer_core.py validates every connector (and this
template) with registry.validate.

This example is an export-only source: the user requests a CSV export on the source's
website, DataVault recognises it in Downloads by its header line, copies it into
data/<owner>/Voorbeeld/<datum> Kijkgeschiedenis/ and reads it. Reading stores nothing yet:
a real connector writes its records with INSERT OR IGNORE on a stable key into its own
table (a new migration), so importing the same export twice adds nothing.
"""
import csv
import io
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import raw_store
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption

HEADER = "Title,Date"          # the first line of the export: how it is recognised


class Voorbeeld(Connector):
    id = "voorbeeld"                                   # ^[a-z][a-z0-9_]{1,31}$: job types and URLs use it
    name = "Voorbeeld"                                 # shown on the card
    icon = "fas fa-film"                               # Font Awesome (solid 'fas' or brands 'fab')
    category = "Activiteit"                            # Muziek | Berichten | Sociaal | Activiteit | Locatie
    order = 100                                        # place within the category
    description = "Wat je bekeek, uit de export van Voorbeeld."   # one Dutch sentence
    capabilities = frozenset({"export_request", "export_watch", "import"})
    allowed_hosts = ()                                 # only for api_sync: the exact hosts it may contact
    raw_folder = "Voorbeeld"                           # data/<owner>/<raw_folder>/<datum> <soort>/
    existing_folders = ()                              # folders under data/<owner>/ to register in place
    record_label = ("titel", "titels")
    stale_days = 90                                    # older than this: 'achter'
    exports = (
        ExportKind(
            id="kijkgeschiedenis", label="Kijkgeschiedenis", request_url="https://www.example.com/account/export",
            patterns=("viewingactivity*.csv", "*.csv"), folder_label="Kijkgeschiedenis", expected_days=(0, 1),
            steps=(
                "Log in op de pagina die nu opent, in je eigen browser.",
                "Klik op 'Alles downloaden'.",
                "DataVault ziet het bestand in Downloads en leest het in. Het origineel mag je daarna weggooien.",
            )),
    )
    help = (                                           # from easy to hard (makkelijk, gemiddeld, moeilijk)
        HelpOption(title="Export aanvragen", effort="makkelijk",
                   steps=("Klik op Export aanvragen.", "Download het bestand: DataVault doet de rest."),
                   note="De export is meteen klaar."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        """Look at the content, not only the name: a zip by its member names, a file by its start."""
        if names is not None:
            return None
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                first = f.readline().strip()
        except OSError:
            return None
        return "kijkgeschiedenis" if first.startswith(HEADER) else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Read the export; never write to it. Return {added, seen, covers_from, covers_until}."""
        seen = 0
        lo = hi = None
        for name, read in raw_store.iter_export_files(Path(raw["path"]), lambda n: n.lower().endswith(".csv")):
            ctx.check()                                # cancel and pause work here
            text = read().decode("utf-8-sig", errors="replace")
            if not text.startswith(HEADER):
                continue
            for row in csv.DictReader(io.StringIO(text)):
                try:
                    ts = time.mktime(time.strptime(row["Date"], "%Y-%m-%d"))
                except (KeyError, ValueError, OverflowError):
                    continue
                seen += 1
                lo = ts if lo is None or ts < lo else lo
                hi = ts if hi is None or ts > hi else hi
                # A real connector stores the row here, keyed on (date, title), INSERT OR IGNORE
            ctx.progress(message=f"Voorbeeld inlezen: {seen} titels")
        if not seen:
            raise ConnectorError("Geen kijkgeschiedenis gevonden in dit bestand")
        return {"added": 0, "seen": seen, "covers_from": lo, "covers_until": hi}

    def coverage(self, ctx: GatherContext) -> Coverage:
        """What the stored records cover: records, first, last (epoch seconds)."""
        return Coverage()


CONNECTOR = Voorbeeld()
