"""
DataGatherer connector: WhatsApp

Three ways in, merged into one source ('whatsapp') without duplicates:

1. WhatsApp for Mac (local_import): its ChatStorage.sqlite in the group container, copied
   with its -wal and -shm into the cache and read there (local_db.py). macOS protects the
   container (Full Disk Access): the server reads it when it may, otherwise the helper
   copies it (the card shows 'Toegang nodig' with the exact steps). No network, no contact
   with WhatsApp.
2. An iPhone backup: the same Core Data database (ChatStorage.sqlite) extracted from an
   encrypted Finder backup, picked with Bestand kiezen or dropped in Downloads (export
   'backup'). DataVault reads the extracted file; extracting it is documented in the help.
3. 'Export chat' on the phone (export 'chat'): a .zip with _chat.txt and media, or a .txt,
   in the phone's own date format (formats/whatsapp_txt.py).

The database routes key a message by WhatsApp's own id (chat JID + stanza id), so the Mac
and the iPhone copy add the same message once. An 'Export chat' has no ids and only
minutes: its chat is merged into the database chat with the same name, and its messages
the database also has are marked superseded (messages_store.reconcile_whatsapp).
"""
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from backend.datagatherer import local_db, messages_store, raw_store, state
from backend.datagatherer.base import Connector, ConnectorError, Coverage, ExportKind, GatherContext, HelpOption, SettingField
from backend.datagatherer.formats import whatsapp_db, whatsapp_txt

SOURCE = "whatsapp"
EXPORT_FAQ = "https://faq.whatsapp.com/1180414079177245"
BACKUP_HELP = "https://support.apple.com/guide/iphone/back-up-iphone-iph3ecf67d29/ios"
OVERLAP_DAYS = 3            # a sync re-reads this much before the newest message it had (late arrivals)
CURSOR = "mac"


def local_zone():
    """The zone an 'Export chat' was written in: this Mac's zone (TZ, /etc/localtime), else Europe/Brussels."""
    import os
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    name = os.environ.get("TZ")
    if not name:
        try:
            link = os.path.realpath("/etc/localtime")
            name = link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else None
        except OSError:
            name = None
    try:
        return ZoneInfo(name or "Europe/Brussels")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Brussels")


def _txt_member(names: List[str]) -> Optional[str]:
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if not n.startswith("__MACOSX/") and (base == "_chat.txt" or (base.lower().endswith(".txt")
                                                                        and base.lower().startswith("whatsapp"))):
            return n
    return None


def _open_ro(path: Path) -> sqlite3.Connection:
    """An archived database, read-only and immutable: nothing is ever written next to it."""
    # Percent-encode the path: a raw '#' or '?' would cut it short, drop mode=ro and make
    # SQLite create an empty file at the shortened path (inside the export folder).
    return sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True)


class WhatsApp(Connector):
    id = "whatsapp"
    name = "WhatsApp"
    icon = "fab fa-whatsapp"
    category = "Berichten"
    order = 10
    record_label = ("bericht", "berichten")
    description = "Je WhatsApp-chats van WhatsApp voor Mac, aangevuld met een iPhone-backup en losse chat-exports."
    capabilities = frozenset({"local_import", "export_request", "export_watch", "import"})
    raw_folder = "WhatsApp"
    existing_folders = ("RAW_whatsapp",)
    folder_patterns = ("*.txt", "*.zip", "*.sqlite")
    stale_days = 3
    holds_messages = True
    exports = (
        ExportKind(
            id="chat", label="Chat exporteren (op je iPhone)", request_url=EXPORT_FAQ,
            patterns=("whatsapp chat*.zip", "whatsapp-chat*.zip", "whatsapp*.txt", "*.zip", "*.txt"),
            folder_label="Chat-export", expected_days=(0, 1),
            steps=(
                "Open op je iPhone de chat in WhatsApp en tik bovenaan op de naam.",
                "Tik op Exporteer chat en kies Voeg media toe (of Zonder media voor een kleinere export).",
                "Kies AirDrop en je Mac: de zip komt in Downloads. De pagina die nu opent is de uitleg van WhatsApp.",
                "DataVault ziet de zip in Downloads, kopieert hem naar het archief en leest hem in. Doe dit per chat.",
            )),
        ExportKind(
            id="backup", label="ChatStorage.sqlite uit een iPhone-backup", request_url=BACKUP_HELP,
            patterns=("chatstorage*.sqlite", "*.sqlite"), folder_label="iPhone-backup", expected_days=(0, 1),
            steps=(
                "Maak in de Finder een versleutelde back-up van je iPhone (de pagina die opent legt het uit). "
                "Bewaar het back-upwachtwoord in je Sleutelhanger.",
                "Haal ChatStorage.sqlite uit de back-up met een hulpprogramma dat versleutelde back-ups kan lezen "
                "(bijvoorbeeld WhatsApp-Chat-Exporter, MIT-licentie, of iMazing). Zie Hulp voor de stappen.",
                "Zet ChatStorage.sqlite in Downloads of kies hem met Bestand kiezen: DataVault kopieert hem naar het "
                "archief en leest hem in.",
            )),
    )
    settings = (
        SettingField("my_name", "Je naam in WhatsApp", help="Zo herkent DataVault in een chat-export welke berichten van "
                     "jou zijn. In één-op-één-chats lukt dat ook zonder.", placeholder="Voornaam"),
        SettingField("pasha", "Pasha mag deze berichten lezen", kind="toggle", help="Standaard uit. Aan: Pasha mag je "
                     "WhatsApp-berichten gebruiken om je vragen te beantwoorden, alleen op deze Mac."),
    )
    help = (
        HelpOption(
            title="WhatsApp voor Mac lezen", effort="makkelijk",
            steps=("Installeer WhatsApp voor Mac en koppel hem aan je iPhone (Gekoppelde apparaten).",
                   "Geef toegang zoals de kaart vraagt (Volledige schijftoegang voor de hulp), en klik op Bijwerken.",
                   "Open WhatsApp voor Mac af en toe: na lange tijd zonder gebruik meldt WhatsApp hem af."),
            note="DataVault leest een kopie van de database; er is geen contact met WhatsApp en dus geen risico op een "
                 "blokkade. Eens per week bewaart DataVault die kopie ook in je archief (data/<eigenaar>/WhatsApp/). "
                 "De Mac heeft niet altijd je volledige geschiedenis: vul aan met een iPhone-backup."),
        HelpOption(
            title="Losse chat exporteren", effort="gemiddeld",
            steps=("Tik in de chat op de naam > Exporteer chat > Voeg media toe.",
                   "Stuur de zip met AirDrop naar je Mac: DataVault leest hem uit Downloads in."),
            note="WhatsApp beperkt een export tot ongeveer 10.000 berichten met media (40.000 zonder). Berichten die de "
                 "database ook heeft, worden niet dubbel getoond."),
        HelpOption(
            title="Volledige geschiedenis uit een iPhone-backup", effort="moeilijk",
            steps=("Sluit je iPhone aan, kies in de Finder 'Maak een reservekopie van alle gegevens op deze Mac' en "
                   "'Codeer lokale reservekopie'. Bewaar het wachtwoord in de Sleutelhanger.",
                   "Installeer WhatsApp-Chat-Exporter (pip install whatsapp-chat-exporter[ios_backup]) en voer uit: "
                   "wtsexporter -i -b ~/Library/Application\\ Support/MobileSync/Backup/<toestel> --decrypt "
                   "(je typt het back-upwachtwoord zelf in Terminal).",
                   "Kies het bestand ChatStorage.sqlite dat de tool uitpakt met Bestand kiezen op deze kaart.",
                   "Verwijder de uitgepakte kopie daarna zelf als je wilt: DataVault bewaart zijn eigen kopie in het "
                   "archief."),
            note="DataVault ontsleutelt zelf geen back-ups en vraagt nooit je back-upwachtwoord."),
    )

    # ---------- recognising exports ----------

    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        if names is not None:
            member = _txt_member(names)
            if member is None:
                return None
            try:
                with zipfile.ZipFile(path) as zf, zf.open(member) as f:
                    head = f.read(8192).decode("utf-8", errors="replace")
            except (zipfile.BadZipFile, OSError, KeyError, RuntimeError):
                return None
            return "chat" if whatsapp_txt.looks_like_chat(head) else None
        low = path.name.lower()
        if low.endswith(".txt"):
            try:
                with open(path, "rb") as f:
                    head = f.read(8192).decode("utf-8", errors="replace")
            except OSError:
                return None
            return "chat" if whatsapp_txt.looks_like_chat(head) else None
        if low.endswith(".sqlite"):
            try:
                with open(path, "rb") as f:
                    if f.read(16) != b"SQLite format 3\x00":
                        return None
                conn = _open_ro(path)
                try:
                    return "backup" if whatsapp_db.is_chatstorage(conn) else None
                finally:
                    conn.close()
            except (OSError, sqlite3.Error):
                return None
        return None

    # ---------- reading ----------

    def _owner(self, ctx: GatherContext) -> Optional[str]:
        value = ctx.settings.get("my_name")
        return value.strip() if isinstance(value, str) and value.strip() else None

    def import_raw(self, ctx: GatherContext, raw: Dict[str, Any]) -> Dict[str, Any]:
        path = Path(raw["path"])
        if raw["is_folder"]:
            totals = {"added": 0, "seen": 0}
            for child in sorted(path.iterdir()):
                kind = self.detect(child, raw_store.read_zip_names(child)) if child.is_file() else None
                if kind:
                    stats = self._import_file(ctx, child, kind, raw["id"])
                    totals["added"] += stats["added"]
                    totals["seen"] += stats["seen"]
            if not totals["seen"]:
                raise ConnectorError("Geen WhatsApp-chats gevonden in deze map")
            messages_store.reconcile_whatsapp(ctx.db)
            return totals
        # The chat's name from the export's own file name, not from the copy ('… Anna 2.zip')
        stats = self._import_file(ctx, path, raw["kind"], raw["id"], name=raw_store.original_name(raw))
        return stats

    def after_import(self, ctx: GatherContext) -> None:
        messages_store.reconcile_whatsapp(ctx.db)

    def _import_file(self, ctx: GatherContext, path: Path, kind: str, raw_id: int,
                     name: Optional[str] = None) -> Dict[str, Any]:
        if kind == "backup" or path.name.lower().endswith(".sqlite"):
            try:
                conn = _open_ro(path)
            except sqlite3.Error as e:
                raise ConnectorError(f"ChatStorage.sqlite is niet leesbaar ({e})") from e
            try:
                if not whatsapp_db.is_chatstorage(conn):
                    raise ConnectorError("Dit is geen WhatsApp-database (ChatStorage.sqlite)")
                return self._read_db(ctx, conn, "whatsapp_backup", raw_id)
            finally:
                conn.close()
        return self._import_txt(ctx, path, raw_id, name or path.name)

    def _import_txt(self, ctx: GatherContext, path: Path, raw_id: int, file_name: str) -> Dict[str, Any]:
        names = raw_store.read_zip_names(path)
        if names is not None:
            member = _txt_member(names)
            if member is None:
                raise ConnectorError("Geen chat (_chat.txt) gevonden in deze zip")
            text = b""
            for name, read in raw_store.iter_export_files(path, lambda n: n.lower().endswith(".txt")):
                if name == member:
                    text = read()      # inside the loop: the zip is still open here
                    break
            folder = member.rsplit("/", 1)[0] + "/" if "/" in member else ""
            media = {n.rsplit("/", 1)[-1]: n for n in names if n.startswith(folder) and n != member}
            title = whatsapp_txt.chat_name(file_name) or whatsapp_txt.chat_name(member)
        else:
            text = raw_store._read_file(path)
            media = {}
            title = whatsapp_txt.chat_name(file_name)
        zone = local_zone()
        parsed = whatsapp_txt.parse(text.decode("utf-8-sig", errors="replace"), zone)
        if not parsed["messages"]:
            raise ConnectorError("Deze chat-export bevat geen berichten die DataVault herkent")
        senders = {m["sender"] for m in parsed["messages"] if m["sender"]}
        title = title or Path(file_name).stem
        owner = self._owner(ctx)
        me = owner.casefold() if owner else None
        if me is None and len(senders) == 2 and title.casefold() in {s.casefold() for s in senders}:
            me = next(s for s in senders if s.casefold() != title.casefold()).casefold()
        conv_key = messages_store.TXT_PREFIX + title.casefold()
        conv_id = messages_store.conversation(ctx.db, SOURCE, conv_key, title, sorted(senders), len(senders) > 2)
        rows = []
        for m in parsed["messages"]:
            attachments = []
            for a in m["attachments"]:
                entry = {"name": a["name"]}
                if a["name"] in media:
                    entry.update({"member": media[a["name"]], "raw_import_id": raw_id})
                attachments.append(entry)
            msg_key = messages_store.key(conv_key, m["ts_ms"], m["sender"], messages_store.norm_text(m["text"]), m["n"])
            is_me = (m["sender"] or "").casefold() == me if (me and m["sender"]) else None
            rows.append(messages_store.message_row(
                conv_id, msg_key, "whatsapp_txt", m["ts_ms"], source=SOURCE, ts_precision=parsed["precision"],
                tz=zone.key, sender=None if is_me else m["sender"], is_me=is_me, body=m["text"], kind=m["kind"],
                attachments=attachments, raw_import_id=raw_id))
        added, seen = messages_store.insert_messages(ctx.db, SOURCE, rows, check=ctx.check)
        ts = [m["ts_ms"] for m in parsed["messages"]]
        return {"added": added, "seen": seen, "conversations": 1,
                "covers_from": min(ts) / 1000, "covers_until": max(ts) / 1000}

    def _read_db(self, ctx: GatherContext, conn: sqlite3.Connection, origin: str, raw_id: Optional[int],
                 since: Optional[float] = None, root: Optional[str] = None) -> Dict[str, Any]:
        chats = whatsapp_db.chats(conn)
        conv_ids: Dict[int, int] = {}
        rows = []
        lo = hi = None
        for m in whatsapp_db.messages(conn, since_unix=since, media_root=root):
            chat = chats[m["chat"]]
            if m["chat"] not in conv_ids:
                conv_ids[m["chat"]] = messages_store.conversation(ctx.db, SOURCE, chat["jid"], chat["title"],
                                                                  [chat["title"]], chat["is_group"])
            rows.append(messages_store.message_row(
                conv_ids[m["chat"]], m["key"], origin, m["ts_ms"], source=SOURCE, ts_precision="s",
                sender=m["sender"], is_me=m["is_me"], body=m["text"], kind=m["kind"],
                attachments=m["attachments"], raw_import_id=raw_id))
            lo = m["ts_ms"] if lo is None or m["ts_ms"] < lo else lo
            hi = m["ts_ms"] if hi is None or m["ts_ms"] > hi else hi
            if len(rows) % 5000 == 0:
                ctx.check()
                ctx.progress(message=f"WhatsApp lezen: {len(rows):,} berichten".replace(",", "."))
        added, seen = messages_store.insert_messages(ctx.db, SOURCE, rows, check=ctx.check)
        return {"added": added, "seen": seen, "conversations": len(conv_ids),
                "covers_from": lo / 1000 if lo else None, "covers_until": hi / 1000 if hi else None}

    # ---------- WhatsApp for Mac ----------

    def access(self, ctx: GatherContext) -> Optional[Dict[str, Any]]:
        return local_db.access(ctx.config, SOURCE, "WhatsApp voor Mac")

    def sync(self, ctx: GatherContext) -> Dict[str, Any]:
        try:
            copy, folder, via = local_db.take_snapshot(ctx.config, SOURCE)
        except local_db.AccessDenied:
            raise ConnectorError("Geen toegang tot WhatsApp voor Mac: geef toegang zoals de kaart uitlegt")
        except local_db.NotFound:
            raise ConnectorError("WhatsApp voor Mac staat niet op deze Mac (of werd nog nooit geopend)")
        try:
            cursor = state.get_cursor(ctx.db, self.id, CURSOR) or {}
            since = cursor.get("last") - OVERLAP_DAYS * 86400 if cursor.get("last") else None
            conn = sqlite3.connect(str(copy))   # a copy in our cache: its -wal is applied there
            try:
                if not whatsapp_db.is_chatstorage(conn):
                    raise ConnectorError("De WhatsApp-database heeft een onbekende vorm; bijwerken lukt niet")
                stats = self._read_db(ctx, conn, "whatsapp_mac", None, since=since, root=local_db.media_root(SOURCE))
            finally:
                conn.close()
            if stats["covers_until"]:
                with ctx.db._connect() as c:
                    state.set_cursor(c, self.id, CURSOR, {"last": max(stats["covers_until"], cursor.get("last") or 0),
                                                          "via": via, "at": time.time()})
            messages_store.reconcile_whatsapp(ctx.db)
            kept = self._keep(ctx, copy)
        except sqlite3.Error as e:
            raise ConnectorError(f"De kopie van de WhatsApp-database is niet leesbaar ({e})") from e
        finally:
            local_db.finish_snapshot(ctx.config, SOURCE, folder, via)
        return {**stats, "via": via, "kept": kept}

    def _keep(self, ctx: GatherContext, copy) -> Optional[str]:
        """The database itself into the vault (data/<owner>/WhatsApp/), weekly; a failure never fails the update."""
        try:
            kept = local_db.keep_copy(ctx.owner_folder, self.raw_folder, SOURCE, copy)
        except (OSError, sqlite3.Error) as e:
            ctx.progress(message=f"Kopie in het archief niet gemaakt ({e})")
            return None
        return str(kept) if kept else None

    def coverage(self, ctx: GatherContext) -> Coverage:
        return messages_store.coverage(ctx.db, source=SOURCE)


CONNECTOR = WhatsApp()
