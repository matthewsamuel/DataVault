"""
DataGatherer connector: Chrome-geschiedenis (local, this Mac)

local_import: Bijwerken copies the History database of every Chrome profile on this Mac
(browser_history.snapshot: Chrome keeps it locked, and it is never opened in place), reads
the visits that are newer than the last run (a cursor, with a day of overlap: the keys
absorb it) and removes its temporary copy. No network, no Full Disk Access needed. Chrome
keeps about 90 days locally, so the older history comes from Takeout (Chrome or My
Activity), on the Google Takeout card.
"""
import time
from typing import Any, Dict, List

from backend.datagatherer import activity_store, browser_history as bh, export_kit, state
from backend.datagatherer.base import Connector, ConnectorError, Coverage, GatherContext, HelpOption

OVERLAP_MS = 86400000


class ChromeHistory(Connector):
    id = "chrome_history"
    name = "Chrome-geschiedenis"
    icon = "fab fa-chrome"
    category = "Activiteit"
    order = 30
    description = "De websites die je in Chrome op deze Mac bezocht, rechtstreeks van je Mac gelezen."
    capabilities = frozenset({"local_import"})
    raw_folder = "Chrome"
    record_label = ("bezoek", "bezoeken")
    stale_days = 7
    settings = (export_kit.PASHA,)
    help = (
        HelpOption(
            title="Bijwerken op deze Mac", effort="makkelijk",
            steps=("Klik op Bijwerken: DataVault kopieert de geschiedenis van elk Chrome-profiel en leest de kopie.",
                   "Chrome mag gewoon openstaan; er gaat niets over het internet."),
            note="Chrome bewaart ongeveer 90 dagen. Werk dus minstens elke paar weken bij."),
        HelpOption(
            title="Oudere geschiedenis via Takeout", effort="gemiddeld",
            steps=("Vraag op de kaart Google Takeout een export aan met 'Chrome' erin.",
                   "De bezoeken daaruit komen in dezelfde Activiteit-tijdlijn."),
            note="Alleen als Chrome-synchronisatie aanstond."),
    )

    def is_configured(self, ctx: GatherContext) -> bool:
        return bool(bh.chrome_profiles(bh.chrome_root()))

    def sync(self, ctx: GatherContext) -> Dict[str, Any]:
        profiles = bh.chrome_profiles(bh.chrome_root())
        if not profiles:
            raise ConnectorError("Chrome staat niet op deze Mac (geen geschiedenis gevonden)")
        totals: Dict[str, Any] = {"added": 0, "seen": 0, "profiles": len(profiles)}
        for history in profiles:
            ctx.check()
            profile = history.parent.name
            cursor = state.get_cursor(ctx.db, self.id, profile) or {}
            since = max(0, int(cursor.get("last_ms", 0)) - OVERLAP_MS)
            ctx.progress(message=f"Chrome-profiel '{profile}' lezen")
            try:
                with bh.snapshot(history, ctx.config.CACHE_FOLDER) as copy:
                    events = list(bh.chrome_visits(copy, since, profile))
            except bh.NoAccess:
                raise ConnectorError("macOS gaf geen toegang tot de geschiedenis van Chrome")
            except ValueError as e:
                raise ConnectorError(str(e))
            stats = activity_store.insert(ctx.db, self.id, None, events, check=ctx.check)
            export_kit.merge_stats(totals, stats)
            if events:
                with ctx.db._connect() as conn:
                    state.set_cursor(conn, self.id, profile, {"last_ms": max(e["ts_ms"] for e in events),
                                                              "at": time.time()})
        return totals

    def coverage(self, ctx: GatherContext) -> Coverage:
        return activity_store.coverage(ctx.db, self.id)

    def notes(self, ctx: GatherContext) -> List[Dict[str, str]]:
        if not bh.chrome_profiles(bh.chrome_root()):
            return [{"tone": "info", "text": "Chrome is niet gevonden op deze Mac."}]
        return []


CONNECTOR = ChromeHistory()
