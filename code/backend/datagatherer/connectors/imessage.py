"""
DataGatherer connector: iMessage and SMS (Messages on this Mac)

Reads ~/Library/Messages/chat.db from a copy (with its -wal and -shm, local_db.py). With
'Berichten in iCloud' on, the Mac has the whole history; with 'Doorsturen van sms' on the
iPhone, SMS lands there too. macOS protects the folder with Full Disk Access: the card
shows 'Toegang nodig' and the helper's steps until a copy can be read. No network.

A message's key is its guid (stable across devices and re-syncs). The text comes from the
text column, or from attributedBody (typedstream) on newer macOS. Tapbacks are added to the
message they react to, one per person, and a tapback taken back is marked removed. A
message edited or unsent after it was read is updated in place (its earlier text kept,
messages_store.revise_messages), also when that happened long after it was sent: those are
read by their date_edited/date_retracted. Attachments are recorded with their path on this Mac.
"""
import sqlite3
import time
from typing import Any, Dict, Optional

from backend.datagatherer import local_db, messages_store, state
from backend.datagatherer.base import Connector, ConnectorError, Coverage, GatherContext, HelpOption, SettingField
from backend.datagatherer.formats import imessage_db

SOURCE = "imessage"
OVERLAP_DAYS = 3
CURSOR = "chat_db"
CURSOR_VERSION = 2      # 2: edits, unsent messages and removed tapbacks; an older cursor reads everything once


class IMessage(Connector):
    id = "imessage"
    name = "iMessage en sms"
    icon = "fas fa-comment-sms"
    category = "Berichten"
    order = 20
    record_label = ("bericht", "berichten")
    description = "Je berichten uit de app Berichten op deze Mac: iMessage en, als je iPhone ze doorstuurt, sms."
    capabilities = frozenset({"local_import"})
    raw_folder = "iMessage"
    stale_days = 3
    holds_messages = True
    settings = (
        SettingField("pasha", "Pasha mag deze berichten lezen", kind="toggle", help="Standaard uit. Aan: Pasha mag je "
                     "iMessage- en sms-berichten gebruiken om je vragen te beantwoorden, alleen op deze Mac."),
    )
    help = (
        HelpOption(
            title="Berichten op deze Mac lezen", effort="makkelijk",
            steps=("Zet in Berichten > Instellingen > iMessage 'Berichten in iCloud' aan en klik op Synchroniseer nu.",
                   "Zet op je iPhone Instellingen > Berichten > Doorsturen van sms aan voor deze Mac.",
                   "Geef toegang zoals de kaart vraagt (Volledige schijftoegang voor de hulp) en klik op Bijwerken."),
            note="DataVault leest een kopie van chat.db en schrijft er nooit in. Er gaat niets over het internet."),
        HelpOption(
            title="Bijlagen die nog in iCloud staan", effort="gemiddeld",
            steps=("Open in Berichten een gesprek, klik op de naam bovenaan en kies Info.",
                   "Klik onderaan op 'Download alle bijlagen' om ze op deze Mac te zetten."),
            note="DataVault toont bijlagen met hun naam; het bestand blijft op je Mac staan."),
    )

    def access(self, ctx: GatherContext) -> Optional[Dict[str, Any]]:
        return local_db.access(ctx.config, SOURCE, "Berichten")

    def sync(self, ctx: GatherContext) -> Dict[str, Any]:
        try:
            copy, folder, via = local_db.take_snapshot(ctx.config, SOURCE)
        except local_db.AccessDenied:
            raise ConnectorError("Geen toegang tot Berichten: geef toegang zoals de kaart uitlegt")
        except local_db.NotFound:
            raise ConnectorError("Berichten (chat.db) staat niet op deze Mac")
        try:
            cursor = state.get_cursor(ctx.db, self.id, CURSOR) or {}
            since = cursor.get("last") - OVERLAP_DAYS * 86400 if cursor.get("last") else None
            if cursor.get("v", 1) < CURSOR_VERSION:
                since = None          # once: the messages edited or unsent before this version could see it
            conn = sqlite3.connect(str(copy))
            try:
                if not imessage_db.is_chat_db(conn):
                    raise ConnectorError("chat.db heeft een onbekende vorm; bijwerken lukt niet")
                stats = self._read(ctx, conn, since)
            finally:
                conn.close()
            if stats["covers_until"]:
                with ctx.db._connect() as c:
                    state.set_cursor(c, self.id, CURSOR, {"last": max(stats["covers_until"], cursor.get("last") or 0),
                                                          "via": via, "at": time.time(), "v": CURSOR_VERSION})
        except sqlite3.Error as e:
            raise ConnectorError(f"De kopie van chat.db is niet leesbaar ({e})") from e
        finally:
            local_db.finish_snapshot(ctx.config, SOURCE, folder, via)
        return {**stats, "via": via}

    def _read(self, ctx: GatherContext, conn: sqlite3.Connection, since: Optional[float]) -> Dict[str, Any]:
        chats = imessage_db.chats(conn)
        conv_ids: Dict[int, int] = {}
        rows, tapbacks = [], []
        lo = hi = None
        for m in imessage_db.messages(conn, since_unix=since):
            if m.get("tapback"):
                tapbacks.append(m)
                continue
            chat = chats.get(m["chat"])
            if chat is None:
                continue
            if m["chat"] not in conv_ids:
                conv_ids[m["chat"]] = messages_store.conversation(ctx.db, SOURCE, chat["guid"], chat["title"],
                                                                  chat["participants"], chat["is_group"])
            rows.append(messages_store.message_row(
                conv_ids[m["chat"]], m["key"], "imessage", m["ts_ms"], source=SOURCE, ts_precision="ms",
                sender=m["sender"], is_me=m["is_me"], body=m["text"], kind=m["kind"], attachments=m["attachments"],
                edited_at_ms=m["edited_at_ms"], retracted_at_ms=m["retracted_at_ms"], edits=m["edits"]))
            lo = m["ts_ms"] if lo is None or m["ts_ms"] < lo else lo
            hi = m["ts_ms"] if hi is None or m["ts_ms"] > hi else hi
            if len(rows) % 5000 == 0:
                ctx.check()
                ctx.progress(message=f"Berichten lezen: {len(rows):,}".replace(",", "."))
        added, seen = messages_store.insert_messages(ctx.db, SOURCE, rows, check=ctx.check)
        revised = messages_store.revise_messages(ctx.db, SOURCE, rows)
        messages_store.apply_reactions(ctx.db, SOURCE, tapbacks)
        return {"added": added, "seen": seen, "revised": revised, "conversations": len(conv_ids),
                "covers_from": lo / 1000 if lo else None, "covers_until": hi / 1000 if hi else None}

    def coverage(self, ctx: GatherContext) -> Coverage:
        return messages_store.coverage(ctx.db, source=SOURCE)


CONNECTOR = IMessage()
