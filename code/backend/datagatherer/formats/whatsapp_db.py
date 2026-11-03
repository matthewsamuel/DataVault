"""
WhatsApp's ChatStorage.sqlite: the database of WhatsApp for Mac and, with the same Core
Data schema, of WhatsApp on the iPhone (from a backup)

Only read, always from a copy (never the live file): the caller opens a snapshot. The
schema is not documented; these are the tables and columns this reads:

    ZWACHATSESSION   Z_PK, ZCONTACTJID (the chat: 316…@s.whatsapp.net, …@g.us), ZPARTNERNAME,
                     ZSESSIONTYPE (0 one to one, 1 group)
    ZWAMESSAGE       Z_PK, ZCHATSESSION, ZISFROMME, ZFROMJID, ZMESSAGEDATE (seconds since
                     2001-01-01, Apple's epoch), ZTEXT, ZMESSAGETYPE, ZSTANZAID (WhatsApp's own
                     message id), ZGROUPMEMBER, ZMEDIAITEM
    ZWAGROUPMEMBER   Z_PK, ZMEMBERJID, ZCONTACTNAME
    ZWAMEDIAITEM     Z_PK, ZMEDIALOCALPATH (relative to the app's Message folder), ZTITLE
    ZWAPROFILEPUSHNAME  ZJID, ZPUSHNAME (optional: the name a contact gave themselves)

The key of a message is (chat JID, stanza id, from me): Z_PK is not stable after a re-link.
"""
import sqlite3
from typing import Any, Dict, Iterator, Optional

APPLE_EPOCH = 978307200
MEDIA_TYPES = {1: "image", 2: "video", 3: "audio", 4: "contact", 5: "location", 8: "document", 15: "sticker"}
SYSTEM_TYPES = {6, 10}


def is_chatstorage(conn: sqlite3.Connection) -> bool:
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {"ZWAMESSAGE", "ZWACHATSESSION"} <= names


def _columns(conn, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def chats(conn: sqlite3.Connection) -> Dict[int, Dict[str, Any]]:
    cols = _columns(conn, "ZWACHATSESSION")
    kind = "ZSESSIONTYPE" if "ZSESSIONTYPE" in cols else "0"
    out = {}
    for pk, jid, name, session in conn.execute(f"SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME, {kind} FROM ZWACHATSESSION"):
        if not jid:
            continue
        out[pk] = {"jid": jid, "title": name or jid.split("@", 1)[0],
                   "is_group": session == 1 or jid.endswith("@g.us")}
    return out


def _names(conn) -> Dict[str, str]:
    if "ZPUSHNAME" not in _columns(conn, "ZWAPROFILEPUSHNAME"):
        return {}
    return {jid: name for jid, name in conn.execute("SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME") if jid and name}


def messages(conn: sqlite3.Connection, since_unix: Optional[float] = None, media_root: Optional[str] = None
             ) -> Iterator[Dict[str, Any]]:
    """
    Every message (optionally only after since_unix) as {chat, key, ts_ms, is_me, sender,
    text, kind, attachments}; chat is the chat's Z_PK (see chats()). media_root is the
    folder ZMEDIALOCALPATH is relative to, for attachment paths on this Mac.
    """
    cols = _columns(conn, "ZWAMESSAGE")
    members = {}
    if _columns(conn, "ZWAGROUPMEMBER") >= {"Z_PK", "ZMEMBERJID"}:
        name_col = "ZCONTACTNAME" if "ZCONTACTNAME" in _columns(conn, "ZWAGROUPMEMBER") else "NULL"
        members = {pk: (jid, name) for pk, jid, name in
                   conn.execute(f"SELECT Z_PK, ZMEMBERJID, {name_col} FROM ZWAGROUPMEMBER")}
    media = {}
    if _columns(conn, "ZWAMEDIAITEM") >= {"Z_PK", "ZMEDIALOCALPATH"}:
        title_col = "ZTITLE" if "ZTITLE" in _columns(conn, "ZWAMEDIAITEM") else "NULL"
        media = {pk: (path, title) for pk, path, title in
                 conn.execute(f"SELECT Z_PK, ZMEDIALOCALPATH, {title_col} FROM ZWAMEDIAITEM")}
    push = _names(conn)
    chat_rows = chats(conn)
    pick = lambda c: c if c in cols else "NULL"   # noqa: E731 (older databases miss a column)
    sql = (f"SELECT Z_PK, ZCHATSESSION, ZISFROMME, {pick('ZFROMJID')}, ZMESSAGEDATE, ZTEXT, {pick('ZMESSAGETYPE')}, "
           f"{pick('ZSTANZAID')}, {pick('ZGROUPMEMBER')}, {pick('ZMEDIAITEM')} FROM ZWAMESSAGE WHERE ZMESSAGEDATE IS NOT NULL")
    args = ()
    if since_unix is not None:
        sql += " AND ZMESSAGEDATE >= ?"
        args = (since_unix - APPLE_EPOCH,)
    for pk, chat, from_me, from_jid, date, text, mtype, stanza, member, media_pk in conn.execute(sql + " ORDER BY ZMESSAGEDATE", args):
        info = chat_rows.get(chat)
        if info is None:
            continue
        is_me = bool(from_me)
        if is_me:
            sender = None
        elif info["is_group"]:
            jid, name = members.get(member, (from_jid, None))
            sender = name or push.get(jid or "") or (jid or "").split("@", 1)[0] or None
        else:
            sender = push.get(info["jid"]) or info["title"]
        attachments = []
        if media_pk in media and media[media_pk][0]:
            path, title = media[media_pk]
            attachments.append({"name": path.rsplit("/", 1)[-1], "local": True,
                                "path": f"{media_root.rstrip('/')}/{path}" if media_root else path})
            text = text or title
        kind = "system" if mtype in SYSTEM_TYPES else ("media" if (mtype in MEDIA_TYPES and not text) else "text")
        ts_ms = int(round((float(date) + APPLE_EPOCH) * 1000))
        key = f"s:{info['jid']}|{stanza}|{int(is_me)}" if stanza else f"z:{info['jid']}|{ts_ms}|{from_jid}|{text}"
        yield {"chat": chat, "key": key, "ts_ms": ts_ms, "is_me": is_me, "sender": sender, "text": text,
               "kind": kind, "attachments": attachments}
