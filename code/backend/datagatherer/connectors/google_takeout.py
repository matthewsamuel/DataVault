"""
DataGatherer connector: Google Takeout (first part)

What this step does: Takeout exports are requested on takeout.google.com in the user's
own browser, recognised in Downloads by their content (members under 'Takeout/'), copied
into data/<owner>/Google/<datum> Takeout/, and the existing Takeouts in
'data/<owner>/RAW_google' are registered in place. Reading them registers which Google
products each export holds (by the Dutch and English folder names); turning their
contents into records (My Activity, YouTube, Maps, Chrome) is for a later step: the
import then simply reads the same registered exports again (parser_version goes up).
"""
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import raw_store
from backend.datagatherer.base import Connector, Coverage, ExportKind, GatherContext, HelpOption

TAKEOUT_URL = "https://takeout.google.com/"


def _product(name: str) -> Optional[str]:
    """'Takeout/Mijn activiteit/…' -> 'Mijn activiteit'."""
    parts = name.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "Takeout":
        return parts[1]
    return None


class GoogleTakeout(Connector):
    id = "google_takeout"
    name = "Google Takeout"
    icon = "fab fa-google"
    category = "Activiteit"
    description = "Je Google-gegevens (Mijn activiteit, YouTube, Maps, Chrome) uit Takeout-exports."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Google"
    existing_folders = ("RAW_google",)
    folder_patterns = ("*.zip", "*.tgz", "*.json", "*.html")   # what the fingerprint looks at (not every photo)
    stale_days = 120
    exports = (
        ExportKind(
            id="takeout", label="Takeout", request_url=TAKEOUT_URL,
            patterns=("takeout-*.zip", "takeout-*.tgz", "*.zip"), folder_label="Takeout", expected_days=(0, 2),
            steps=(
                "Log in op de pagina die nu opent, in je eigen browser.",
                "Kies wat je wilt exporteren (bijvoorbeeld Mijn activiteit, YouTube, Maps) en het formaat JSON waar dat kan.",
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
            steps=("Kies in Takeout 'Elke 2 maanden exporteren' (een jaar lang).",
                   "Download elke export als de mail komt: DataVault herkent hem in Downloads."),
            note="Na een jaar stel je het schema opnieuw in."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names and any(_product(n) for n in names[:2000]):
            return "takeout"
        return None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        products: Counter = Counter()
        path = Path(raw["path"])
        if raw["is_folder"]:
            for child in sorted(path.iterdir()) if path.is_dir() else []:
                if child.is_dir() and not child.name.startswith("."):
                    takeout = child / "Takeout" if (child / "Takeout").is_dir() else child
                    for product in takeout.iterdir() if takeout.is_dir() else []:
                        if product.is_dir():
                            products[product.name] += 1
                elif child.suffix.lower() == ".zip":
                    for name in raw_store.read_zip_names(child) or []:
                        if _product(name):
                            products[_product(name)] += 1
        else:
            for name in raw_store.read_zip_names(path) or []:
                if _product(name):
                    products[_product(name)] += 1
        ctx.check()
        return {"added": 0, "seen": 0, "products": dict(sorted(products.items())), "registered_at": time.time()}

    def coverage(self, ctx: GatherContext) -> Coverage:
        return Coverage()


CONNECTOR = GoogleTakeout()
