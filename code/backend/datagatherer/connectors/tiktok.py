"""
DataGatherer connector: TikTok (JSON export)

The user requests 'Download your data' in the TikTok app or on tiktok.com (format JSON)
and downloads it within 4 days. One file, user_data.json (sometimes user_data_tiktok.json,
or inside a zip). Two layouts are known: the older {"Activity": {"Video Browsing History":
{"VideoList": [...]}, "Like List": {"ItemFavoriteList": [...]}, "Search History":
{"SearchList": [...]}}} and the newer {"Your Activity": {"Watch History": {"VideoList":
[...]}, "Like List": ..., "Searches": {"SearchList": [...]}}}. Dates are 'YYYY-MM-DD
HH:MM:SS' in UTC.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from backend.datagatherer import activity_store as store, export_kit
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption

FILES = ("user_data.json", "user_data_tiktok.json")


def tt_time(text: Any) -> Optional[int]:
    if not isinstance(text, str):
        return None
    try:
        return int(datetime.strptime(text.strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def _section(data: Dict[str, Any], *names: str) -> Dict[str, Any]:
    for top in ("Your Activity", "Activity"):
        block = data.get(top)
        if isinstance(block, dict):
            for name in names:
                if isinstance(block.get(name), dict):
                    return block[name]
    return {}


def parse(data: Any) -> Iterator[Dict[str, Any]]:
    if not isinstance(data, dict):
        return
    for item in _section(data, "Watch History", "Video Browsing History").get("VideoList") or []:
        ts = tt_time((item or {}).get("Date"))
        link = (item or {}).get("Link") or (item or {}).get("VideoLink")
        if ts is not None and isinstance(link, str):
            yield store.event("video_watch", ts, store.key("tiktok-watch", ts, link), title="TikTok-video bekeken",
                              url=link, details={"app": "TikTok"})
    for item in _section(data, "Like List").get("ItemFavoriteList") or []:
        ts = tt_time((item or {}).get("Date") or (item or {}).get("date"))
        link = (item or {}).get("Link") or (item or {}).get("link")
        if ts is not None and isinstance(link, str):
            yield store.event("like", ts, store.key("tiktok-like", ts, link), title="TikTok-video leuk gevonden", url=link)
    for item in _section(data, "Searches", "Search History").get("SearchList") or []:
        ts = tt_time((item or {}).get("Date"))
        term = (item or {}).get("SearchTerm")
        if ts is not None and isinstance(term, str) and term.strip():
            yield store.event("search", ts, store.key("tiktok-search", ts, term), title=term.strip()[:200],
                              details={"app": "TikTok"})


class TikTok(Connector):
    id = "tiktok"
    name = "TikTok"
    icon = "fab fa-tiktok"
    category = "Sociaal"
    order = 70
    description = "Welke TikTok-video's je bekeek, leuk vond en wat je zocht, uit je TikTok-export."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "TikTok"
    record_label = ("item", "items")
    stale_days = 120
    settings = (export_kit.PASHA,)
    exports = (
        ExportKind(
            id="export", label="TikTok-export", request_url="https://www.tiktok.com/setting/download-your-data",
            patterns=("user_data*.json", "tiktok*.zip", "*.zip", "*.json"), folder_label="Export", expected_days=(1, 4),
            steps=("Log in op de pagina die nu opent (of: app > Profiel > Instellingen > Account > "
                   "'Download je gegevens').",
                   "Kies 'Alle gegevens' en het formaat JSON, en vraag de export aan.",
                   "Download hem binnen 4 dagen: DataVault vindt hem in Downloads.")),
    )
    help = (
        HelpOption(title="Export aanvragen", effort="makkelijk",
                   steps=("Klik op Export aanvragen, kies JSON.", "Download het bestand zodra TikTok het klaarzet."),
                   note="De download blijft 4 dagen beschikbaar."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names is not None:
            return "export" if any(n.rsplit("/", 1)[-1] in FILES for n in names) else None
        text = export_kit.head(path, 8192).decode("utf-8", "ignore")
        return "export" if text.lstrip().startswith("{") and ('"Your Activity"' in text or '"Activity"' in text) \
            and ('"Profile"' in text or '"Video Browsing History"' in text or '"Watch History"' in text) else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        events: List[Dict[str, Any]] = []
        for name, read in export_kit.files(Path(raw["path"]), lambda n: n in FILES or n.lower().startswith("user_data")):
            ctx.check()
            try:
                events += list(parse(json.loads(read().decode("utf-8-sig"))))
            except (ValueError, UnicodeDecodeError):
                continue
        if not events:
            raise ConnectorError("Geen TikTok-activiteit gevonden in dit bestand")
        return store.insert(ctx.db, self.id, raw["id"], events, check=ctx.check)

    def coverage(self, ctx: GatherContext) -> Coverage:
        return store.coverage(ctx.db, self.id)


CONNECTOR = TikTok()
