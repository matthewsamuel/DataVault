"""
DataGatherer connector: Snapchat ('Mijn gegevens')

The user requests 'My Data' on accounts.snapchat.com (JSON, a date range), and downloads
the zip. Read from it:
  json/memories_history.json   {"Saved Media": [{Date, Media Type, Location, Download Link}]}
                               -> kind memory (the date, photo or video, and the position
                               when Snapchat stored one); the Download Link is NOT followed
                               (DataVault never goes online for this) and not stored
  json/chat_history.json       {"<vriend>": [{From, Media Type, Created, Content}]}
                               -> kind chat (who, when, the text)
Dates are 'YYYY-MM-DD HH:MM:SS UTC'.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from backend.datagatherer import activity_store as store, export_kit
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption


def snap_time(text: Any) -> Optional[int]:
    if not isinstance(text, str):
        return None
    text = text.strip().replace(" UTC", "")
    try:
        return int(datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        return None


def _latlon(text: Any) -> Optional[List[float]]:
    if not isinstance(text, str) or ":" not in text:
        return None
    try:
        lat, lon = (float(v) for v in text.split(":", 1)[1].split(","))
    except ValueError:
        return None
    if lat == 0 and lon == 0 or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return [round(lat, 6), round(lon, 6)]


def parse_memories(data: Any) -> Iterator[Dict[str, Any]]:
    for item in (data or {}).get("Saved Media") or []:
        if not isinstance(item, dict):
            continue
        ts = snap_time(item.get("Date"))
        if ts is None:
            continue
        media = str(item.get("Media Type") or "").strip()
        details: Dict[str, Any] = {"soort": "video" if media.lower() == "video" else "foto"}
        pos = _latlon(item.get("Location"))
        if pos:
            details["lat"], details["lon"] = pos
        title = "Video bewaard" if details["soort"] == "video" else "Foto bewaard"
        yield store.event("memory", ts, store.key("memory", ts, media, pos), title=title, details=details)


def parse_chats(data: Any) -> Iterator[Dict[str, Any]]:
    if not isinstance(data, dict):
        return
    for friend, messages in data.items():
        if not isinstance(messages, list):
            continue
        for n, msg in enumerate(messages):
            if not isinstance(msg, dict):
                continue
            ts = snap_time(msg.get("Created"))
            if ts is None:
                continue
            sender = str(msg.get("From") or "").strip()
            content = msg.get("Content") if isinstance(msg.get("Content"), str) else ""
            media = str(msg.get("Media Type") or "").strip()
            title = content.strip()[:200] or (media.capitalize() if media else "Bericht")
            yield store.event("chat", ts, store.key("chat", friend, ts, sender, content, media), title=title,
                              details={"gesprek": str(friend)[:100], "van": sender[:100]})


class Snapchat(Connector):
    id = "snapchat"
    name = "Snapchat"
    icon = "fab fa-snapchat"
    category = "Sociaal"
    order = 60
    description = "Je bewaarde Memories (datum en plek) en je chatgeschiedenis uit Snapchat 'Mijn gegevens'."
    capabilities = frozenset({"export_request", "export_watch", "import"})
    raw_folder = "Snapchat"
    record_label = ("item", "items")
    stale_days = 120
    settings = (export_kit.PASHA,)
    exports = (
        ExportKind(
            id="mijn_gegevens", label="Mijn gegevens", request_url="https://accounts.snapchat.com/accounts/downloadmydata",
            patterns=("mydata*.zip", "snapchat*.zip", "*.zip"), folder_label="Mijn gegevens", expected_days=(1, 7),
            steps=("Log in op de pagina die nu opent, in je eigen browser.",
                   "Kies 'Export JSON Files', vink Memories en Chat History aan en kies een periode.",
                   "Snapchat mailt als de export klaar is; download de zip.",
                   "DataVault vindt hem in Downloads en leest hem in.")),
    )
    help = (
        HelpOption(title="Mijn gegevens aanvragen", effort="makkelijk",
                   steps=("Klik op Export aanvragen, kies JSON en de periode sinds je vorige export.",
                          "Download de zip als de mail komt."),
                   note="Bewaar je Memories zelf als je meer dan 5 GB hebt: Snapchat beperkt gratis opslag."),
    )

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names is None:
            return None
        bases = {n.rsplit("/", 1)[-1] for n in names}
        return "mijn_gegevens" if bases & {"memories_history.json", "chat_history.json"} and \
            any("/" in n and n.split("/")[-2] == "json" for n in names) else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        totals: Dict[str, Any] = {"added": 0, "seen": 0}
        found = False
        for name, read in export_kit.files(Path(raw["path"]), lambda n: n in ("memories_history.json", "chat_history.json")):
            ctx.check()
            try:
                data = json.loads(read().decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError):
                continue
            found = True
            events = parse_memories(data) if name.endswith("memories_history.json") else parse_chats(data)
            export_kit.merge_stats(totals, store.insert(ctx.db, self.id, raw["id"], events, check=ctx.check))
        if not found:
            raise ConnectorError("Geen Memories of chats gevonden in deze Snapchat-export")
        return totals

    def coverage(self, ctx: GatherContext) -> Coverage:
        return store.coverage(ctx.db, self.id)


CONNECTOR = Snapchat()
