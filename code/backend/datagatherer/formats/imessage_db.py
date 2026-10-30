"""
iMessage/SMS: Messages' chat.db (~/Library/Messages/chat.db), always read from a copy

The tables and columns this reads (undocumented; they changed between macOS versions):

    message                   ROWID, guid (stable id), text, attributedBody, handle_id, service,
                              date (since 2001-01-01: seconds before macOS 10.13, nanoseconds
                              since), is_from_me, associated_message_guid,
                              associated_message_type (2000-2007: a tapback)
    handle                    ROWID, id (phone number or e-mail)
    chat                      ROWID, guid, chat_identifier, display_name, style (43 = group)
    chat_message_join         chat_id, message_id
    chat_handle_join          chat_id, handle_id
    attachment                ROWID, filename (~/Library/Messages/Attachments/…), mime_type, transfer_name
    message_attachment_join   message_id, attachment_id

On recent macOS the text column is often empty and the text is only in attributedBody, an
NSAttributedString in Apple's 'typedstream' format: decode_attributed_body() reads the
string out of it.
"""
import sqlite3
from typing import Any, Dict, Iterator, List, Optional

APPLE_EPOCH = 978307200
TAPBACKS = {2000: "❤️", 2001: "\U0001F44D", 2002: "\U0001F44E", 2003: "\U0001F602", 2004: "‼️",
            2005: "❓"}
EMOJI_TAPBACK, STICKER_TAPBACK = 2006, 2007    # iOS 18 / macOS 15: any emoji (associated_message_emoji), a sticker
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
    Every message (optionally only from since_unix) as {chat, key, ts_ms, is_me, sender,
    text, kind, attachments}, and tapbacks as {tapback: True, target, actor, reaction}.
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
           f"{pick('associated_message_emoji')}, {pick('date_retracted')} FROM message")
    args = ()
    if since_unix is not None:
        threshold = (since_unix - APPLE_EPOCH)
        sql += " WHERE (date >= ? OR (date > 0 AND date < 100000000000 AND date >= ?))"
        args = (int(threshold * 1e9), int(threshold))
    for (rowid, guid, text, body, handle_id, date, from_me, assoc, assoc_type, item_type, emoji,
         retracted) in conn.execute(sql + " ORDER BY date", args):
        unix = to_unix(date)
        if unix is None or not guid or rowid not in chat_of:
            continue
        sender = None if from_me else handles.get(handle_id)
        if assoc_type and 2000 <= assoc_type <= STICKER_TAPBACK and assoc:
            target = assoc.split("/", 1)[1] if "/" in assoc else assoc.split(":", 1)[-1]
            if assoc_type == EMOJI_TAPBACK:
                reaction = emoji.strip() if isinstance(emoji, str) and emoji.strip() else "\U0001F642"
            elif assoc_type == STICKER_TAPBACK:
                reaction = "sticker"
            else:
                reaction = TAPBACKS[assoc_type]
            yield {"tapback": True, "target": target, "actor": "ik" if from_me else (sender or ""),
                   "reaction": reaction}
            continue
        if assoc_type and assoc_type >= 3000:
            continue        # a tapback taken back: the earlier one stays (nothing is deleted)
        if not text:
            text = decode_attributed_body(body)
        if text:
            text = text.replace(OBJECT_REPLACEMENT, "").strip() or None
        attachments = files.get(rowid, [])
        kind = "system" if item_type else ("media" if attachments and not text else "text")
        if retracted and not text and not attachments:
            kind = "unsent"          # 'Undo send' (iOS 16+): the bubble says so instead of being empty
        yield {"chat": chat_of[rowid], "key": guid, "ts_ms": int(round(unix * 1000)), "is_me": bool(from_me),
               "sender": sender, "text": text, "kind": kind, "attachments": attachments}
