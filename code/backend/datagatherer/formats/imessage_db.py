"""
iMessage/SMS: Messages' chat.db (~/Library/Messages/chat.db), always read from a copy

The tables and columns this reads (undocumented; they changed between macOS versions):

    message                   ROWID, guid (stable id), text, attributedBody, handle_id, service,
                              date (since 2001-01-01: seconds before macOS 10.13, nanoseconds
                              since), is_from_me, associated_message_guid,
                              associated_message_type (2000-2007: a tapback, 3000-3007: that
                              tapback taken back), associated_message_emoji (2006/3006),
                              date_edited and date_retracted (macOS 13+, 0 when not),
                              message_summary_info (a binary plist; 'ec' holds every version
                              of an edited message per part: [{'d': date, 't': attributedBody}],
                              oldest first; 'rp' the parts that were unsent)
    handle                    ROWID, id (phone number or e-mail)
    chat                      ROWID, guid, chat_identifier, display_name, style (43 = group)
    chat_message_join         chat_id, message_id
    chat_handle_join          chat_id, handle_id
    attachment                ROWID, filename (~/Library/Messages/Attachments/…), mime_type, transfer_name
    message_attachment_join   message_id, attachment_id

On recent macOS the text column is often empty and the text is only in attributedBody, an
NSAttributedString in Apple's 'typedstream' format: decode_attributed_body() reads the
string out of it.

An edited message keeps its guid: the text column holds the newest version, and a later
read updates the stored row (messages_store.revise_messages) and keeps the earlier
versions. 'Undo send' leaves date_retracted and no text. A tapback taken back is a row of
its own (3000-3007) that removes the earlier one from the message it pointed at.
"""
import plistlib
import sqlite3
from typing import Any, Dict, Iterator, List, Optional

APPLE_EPOCH = 978307200
TAPBACKS = {2000: "❤️", 2001: "\U0001F44D", 2002: "\U0001F44E", 2003: "\U0001F602", 2004: "‼️",
            2005: "❓"}
EMOJI_TAPBACK, STICKER_TAPBACK = 2006, 2007    # iOS 18 / macOS 15: any emoji (associated_message_emoji), a sticker
REMOVED = 1000                                  # 3000-3007: the tapback 2000-2007 taken back
OBJECT_REPLACEMENT = "￼"


def decode_attributed_body(blob: Optional[bytes]) -> Optional[str]:
    """
    The plain text of an attributedBody (typedstream). After the class name NSString comes
    the type '+' and then the length: one byte, or 0x81 and two bytes (little endian), or
    0x82 and four bytes; then that many bytes of UTF-8.
    """
    if not blob:
        return None
    blob = bytes(blob)
    start = blob.find(b"NSString")
    if start < 0:
        return None
    plus = blob.find(b"+", start + 8, start + 8 + 16)
    if plus < 0:
        return None
    i = plus + 1
    if i >= len(blob):
        return None
    marker = blob[i]
    if marker == 0x81:
        length = int.from_bytes(blob[i + 1:i + 3], "little")
        i += 3
    elif marker == 0x82:
        length = int.from_bytes(blob[i + 1:i + 5], "little")
        i += 5
    else:
        length = marker
        i += 1
    data = blob[i:i + length]
    if len(data) != length:
        return None
    text = data.decode("utf-8", errors="replace")
    return text or None


def edit_history(blob: Optional[bytes]) -> List[Dict[str, Any]]:
    """
    The earlier versions of an edited message, from message_summary_info: [{at (UTC ms or
    None), body}], oldest first. 'ec' maps each part to its versions (oldest first, the last
    one is the text now). An unreadable or unknown blob gives [].
    """
    info = _summary(blob)
    edits = info.get("ec")
    out: List[Dict[str, Any]] = []
    if not isinstance(edits, dict):
        return out
    for _, versions in sorted(edits.items(), key=lambda kv: str(kv[0])):
        if not isinstance(versions, list):
            continue
        for version in versions[:-1]:
            if not isinstance(version, dict):
                continue
            raw = version.get("t")
            text = decode_attributed_body(raw) if isinstance(raw, (bytes, bytearray)) else (raw if isinstance(raw, str) else None)
            text = (text or "").replace(OBJECT_REPLACEMENT, "").strip()
            when = version.get("d")
            unix = to_unix(when) if isinstance(when, (int, float)) and not isinstance(when, bool) else None
            if text:
                out.append({"at": int(round(unix * 1000)) if unix else None, "body": text})
    return sorted(out, key=lambda e: e["at"] or 0)


def _summary(blob: Optional[bytes]) -> Dict[str, Any]:
    if not blob:
        return {}
    try:
        info = plistlib.loads(bytes(blob))
    except Exception:       # plistlib raises several kinds on a damaged blob; it is only extra history
        return {}
    return info if isinstance(info, dict) else {}


def unsent_parts(blob: Optional[bytes]) -> bool:
    """Whether message_summary_info says a part of the message was unsent ('rp')."""
    parts = _summary(blob).get("rp")
    return isinstance(parts, list) and bool(parts)


def _ms(date: Optional[int]) -> Optional[int]:
    unix = to_unix(date) if isinstance(date, (int, float)) and date else None
    return int(round(unix * 1000)) if unix else None


def to_unix(date: Optional[int]) -> Optional[float]:
    """message.date as Unix seconds: nanoseconds since 2001 on macOS 10.13 and later, seconds before."""
    if date is None or date == 0:
        return None
    value = float(date)
    if abs(value) > 1e11:
        value /= 1e9
    return value + APPLE_EPOCH


def is_chat_db(conn: sqlite3.Connection) -> bool:
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {"message", "chat", "chat_message_join", "handle"} <= names


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def chats(conn: sqlite3.Connection) -> Dict[int, Dict[str, Any]]:
    handles = {rowid: hid for rowid, hid in conn.execute("SELECT ROWID, id FROM handle")}
    members: Dict[int, List[str]] = {}
    for chat_id, handle_id in conn.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
        if handle_id in handles:
            members.setdefault(chat_id, []).append(handles[handle_id])
    cols = _columns(conn, "chat")
    style = "style" if "style" in cols else "NULL"
    out = {}
    for rowid, guid, ident, name, st in conn.execute(
            f"SELECT ROWID, guid, chat_identifier, display_name, {style} FROM chat"):
        people = sorted(set(members.get(rowid, [])))
        out[rowid] = {"guid": guid or ident or str(rowid), "title": name or ", ".join(people) or ident,
                      "participants": people, "is_group": st == 43 or len(people) > 1}
    return out


def messages(conn: sqlite3.Connection, since_unix: Optional[float] = None) -> Iterator[Dict[str, Any]]:
    """
    Every message (optionally only from since_unix, or edited or unsent since then) as
    {chat, key, ts_ms, is_me, sender, text, kind, attachments, edited_at_ms, retracted_at_ms,
    edits}, and tapbacks as {tapback: True, target, actor, reaction, removed, ts_ms}.
    """
    handles = {rowid: hid for rowid, hid in conn.execute("SELECT ROWID, id FROM handle")}
    chat_of: Dict[int, int] = {}
    for chat_id, message_id in conn.execute("SELECT chat_id, message_id FROM chat_message_join ORDER BY chat_id"):
        chat_of.setdefault(message_id, chat_id)
    files: Dict[int, List[Dict[str, Any]]] = {}
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if {"attachment", "message_attachment_join"} <= names:
        for message_id, filename, mime, transfer in conn.execute(
                "SELECT j.message_id, a.filename, a.mime_type, a.transfer_name FROM message_attachment_join j "
                "JOIN attachment a ON a.ROWID = j.attachment_id ORDER BY j.message_id, a.ROWID"):
            name = transfer or (filename or "").rsplit("/", 1)[-1] or "bijlage"
            files.setdefault(message_id, []).append({"name": name, "mime": mime, "local": True, "path": filename})
    cols = _columns(conn, "message")
    pick = lambda c: c if c in cols else "NULL"   # noqa: E731
    sql = (f"SELECT ROWID, guid, text, {pick('attributedBody')}, handle_id, date, is_from_me, "
           f"{pick('associated_message_guid')}, {pick('associated_message_type')}, {pick('item_type')}, "
           f"{pick('associated_message_emoji')}, {pick('date_retracted')}, {pick('date_edited')}, "
           f"{pick('message_summary_info')} FROM message")
    args: tuple = ()
    if since_unix is not None:
        threshold = (since_unix - APPLE_EPOCH)
        sql += " WHERE (date >= ? OR (date > 0 AND date < 100000000000 AND date >= ?)"
        args = (int(threshold * 1e9), int(threshold))
        for column in ("date_edited", "date_retracted"):     # an old message edited or unsent since
            if column in cols:
                sql += f" OR {column} >= ?"
                args += (int(threshold * 1e9),)
        sql += ")"
    for (rowid, guid, text, body, handle_id, date, from_me, assoc, assoc_type, item_type, emoji,
         retracted, edited, summary) in conn.execute(sql + " ORDER BY date", args):
        unix = to_unix(date)
        if unix is None or not guid or rowid not in chat_of:
            continue
        sender = None if from_me else handles.get(handle_id)
        removed = bool(assoc_type) and 2000 + REMOVED <= assoc_type <= STICKER_TAPBACK + REMOVED
        if assoc_type and (2000 <= assoc_type <= STICKER_TAPBACK or removed):
            if not assoc:
                continue
            target = assoc.split("/", 1)[1] if "/" in assoc else assoc.split(":", 1)[-1]
            base = assoc_type - REMOVED if removed else assoc_type
            if base == EMOJI_TAPBACK:
                reaction = emoji.strip() if isinstance(emoji, str) and emoji.strip() else None
                reaction = reaction or (None if removed else "\U0001F642")   # None: whichever emoji it was
            elif base == STICKER_TAPBACK:
                reaction = "sticker"
            else:
                reaction = TAPBACKS[base]
            yield {"tapback": True, "target": target, "actor": "ik" if from_me else (sender or ""),
                   "reaction": reaction, "removed": removed, "ts_ms": int(round(unix * 1000))}
            continue
        if assoc_type and assoc_type >= 3000:
            continue        # another kind of taken-back reaction: nothing to show
        if not text:
            text = decode_attributed_body(body)
        if text:
            text = text.replace(OBJECT_REPLACEMENT, "").strip() or None
        attachments = files.get(rowid, [])
        kind = "system" if item_type else ("media" if attachments and not text else "text")
        retracted_ms = _ms(retracted)
        if retracted_ms is None and edited and summary is not None and unsent_parts(summary):
            retracted_ms = _ms(edited)   # older macOS: only date_edited and 'rp' say it was unsent
        if retracted_ms and not text and not attachments:
            kind = "unsent"          # 'Undo send' (iOS 16+): the bubble says so instead of being empty
        yield {"chat": chat_of[rowid], "key": guid, "ts_ms": int(round(unix * 1000)), "is_me": bool(from_me),
               "sender": sender, "text": text, "kind": kind, "attachments": attachments,
               "edited_at_ms": None if retracted_ms and retracted_ms == _ms(edited) else _ms(edited),
               "retracted_at_ms": retracted_ms, "edits": edit_history(summary)}
