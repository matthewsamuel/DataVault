"""
DataGatherer connector: Safari-geschiedenis (local, this Mac)

local_import: Bijwerken copies ~/Library/Safari/History.db (and its -wal) into a
temporary folder in the app's cache, reads the visits newer than the last run and removes
the copy (browser_history.snapshot). With iCloud Safari the iPhone's visits are in it too.

macOS protects ~/Library/Safari: reading it needs Full Disk Access ('Volledige
schijftoegang'). Without it the card says how to give it, or how to pick a copy of
History.db by hand (Bestand kiezen: that copy is archived like an export and read from
the archive, read-only).
"""
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import activity_store, browser_history as bh, export_kit, state
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption

OVERLAP_MS = 86400000
NO_ACCESS = ("macOS geeft DataVault geen toegang tot je Safari-geschiedenis. Geef 'Volledige schijftoegang' "
             "(Systeeminstellingen > Privacy en beveiliging) aan het programma waarin DataVault draait, of kies "
             "een kopie van History.db met Bestand kiezen.")


class SafariHistory(Connector):
    id = "safari_history"
    name = "Safari-geschiedenis"
    icon = "fab fa-safari"
    category = "Activiteit"
    order = 31
    description = "De websites die je in Safari bezocht (met iCloud ook die van je iPhone), van je Mac gelezen."
    capabilities = frozenset({"local_import", "import"})
    raw_folder = "Safari"
    record_label = ("bezoek", "bezoeken")
    stale_days = 14
    settings = (export_kit.PASHA,)
    exports = (
        ExportKind(
            id="history_db", label="Kopie van History.db", request_url="https://support.apple.com/guide/mac-help/mchld6aa7d23/mac",
            patterns=("history*.db",), folder_label="History.db", expected_days=(0, 0),
            steps=("Open in de Finder de map ~/Library/Safari (Ga > Ga naar map).",
                   "Kopieer History.db naar je bureaublad en kies die kopie hier met Bestand kiezen.")),
    )
    help = (
        HelpOption(
            title="Bijwerken op deze Mac", effort="makkelijk",
            steps=("Klik op Bijwerken: DataVault kopieert History.db en leest de kopie.",
                   "Lukt dat niet, geef dan eerst Volledige schijftoegang (zie hieronder)."),
            note="Zet in Safari 'Verwijder geschiedenisonderdelen' op 'Handmatig' zodat niets verdwijnt."),
        HelpOption(
            title="Volledige schijftoegang geven", effort="gemiddeld",
            steps=("Open Systeeminstellingen > Privacy en beveiliging > Volledige schijftoegang.",
                   "Zet het programma aan waarin DataVault draait (bijvoorbeeld Terminal) en start DataVault opnieuw.",
                   "Klik daarna op Bijwerken."),
            note="Dit geeft brede toegang. Liever niet? Gebruik dan de kopie hieronder."),
        HelpOption(
            title="Een kopie kiezen", effort="gemiddeld",
            steps=("Finder > Ga > Ga naar map > ~/Library/Safari.",
                   "Kopieer History.db naar je bureaublad en kies hem hier met Bestand kiezen."),
            note="De kopie gaat naar het archief en wordt alleen gelezen."),
    )

    def _db(self) -> Optional[Path]:
        root = bh.safari_root()
        path = root / "History.db" if root else None
        return path

    def is_configured(self, ctx: GatherContext) -> bool:
        path = self._db()
        try:
            return path is not None and path.is_file()
        except PermissionError:
            return True

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names is not None or not bh.is_sqlite(export_kit.head(path, 32)):
            return None
        return "history_db" if {"history_items", "history_visits"} <= bh.tables(path) else None

    def sync(self, ctx: GatherContext) -> Dict[str, Any]:
        path = self._db()
        if path is None:
            raise ConnectorError("Safari-geschiedenis niet gevonden op deze Mac")
        cursor = state.get_cursor(ctx.db, self.id, "local") or {}
        since = max(0, int(cursor.get("last_ms", 0)) - OVERLAP_MS)
        ctx.progress(message="Safari-geschiedenis lezen")
        try:
            with bh.snapshot(path, ctx.config.CACHE_FOLDER) as copy:
                events = list(bh.safari_visits(copy, since))
        except (bh.NoAccess, PermissionError):
            raise ConnectorError(NO_ACCESS)
        except FileNotFoundError:
            raise ConnectorError("Safari-geschiedenis niet gevonden op deze Mac")
        except ValueError as e:
            raise ConnectorError(str(e))
        stats = activity_store.insert(ctx.db, self.id, None, events, check=ctx.check)
        if events:
            with ctx.db._connect() as conn:
                state.set_cursor(conn, self.id, "local", {"last_ms": max(e["ts_ms"] for e in events), "at": time.time()})
        return stats

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        path = Path(raw["path"])
        if raw["is_folder"] or not bh.is_sqlite(export_kit.head(path, 32)):
            raise ConnectorError("Dit is geen History.db van Safari")
        return activity_store.insert(ctx.db, self.id, raw["id"], bh.safari_visits(path), check=ctx.check)

    def coverage(self, ctx: GatherContext) -> Coverage:
        return activity_store.coverage(ctx.db, self.id)


CONNECTOR = SafariHistory()
