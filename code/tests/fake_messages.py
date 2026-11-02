"""
Synthetic chat data with the real schemas and formats, for the DataGatherer message tests.
Nothing here comes from a real Mac, phone or account: every name, number and text is made up.

    make_chatstorage(path, chats, messages)     WhatsApp's ChatStorage.sqlite (Core Data tables)
    make_chat_db(path, chats, messages, ...)    Messages' chat.db, text or only attributedBody
    typedstream(text)                           an attributedBody blob (NSAttributedString)
    meta_export(path, layout, ...)              a Facebook 'Download your information' zip in JSON,
                                                with Meta's mojibake, English or Dutch folder names
    instagram_export(path, ...)                 an Instagram export zip
    secure_storage(path, ...)                   Messenger's secure storage download (messages.zip)
    WHATSAPP_TXT                                'Export chat' texts in four phone formats
"""
import io
import json
import plistlib
import sqlite3
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

APPLE_EPOCH = 978307200
T0 = 1_717_200_000          # 2024-06-01 00:00 UTC: a fixed moment for the synthetic messages


def mojibake(text: str) -> str:
    """What Meta writes: UTF-8 bytes shown as Latin-1 code points."""
    return text.encode("utf-8").decode("latin-1")


def tiny_jpeg() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (32, 24), (30, 160, 90)).save(out, "JPEG")
    return out.getvalue()


# ---------- WhatsApp ChatStorage.sqlite ----------

def make_chatstorage(path: Path, chats: List[Dict], messages: List[Dict], wal_messages: Optional[List[Dict]] = None):
    """
    chats: {pk, jid, name, group}; messages: {pk, chat, me, text, ts (unix), stanza, member?,
    media?}. wal_messages are written in WAL mode by a connection that stays open (returned),
    so they only exist in ChatStorage.sqlite-wal until it closes.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZCONTACTJID VARCHAR,
            ZPARTNERNAME VARCHAR, ZSESSIONTYPE INTEGER, ZLASTMESSAGEDATE TIMESTAMP, ZARCHIVED INTEGER);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZCHATSESSION INTEGER, ZISFROMME INTEGER,
            ZFROMJID VARCHAR, ZTOJID VARCHAR, ZMESSAGEDATE TIMESTAMP, ZSENTDATE TIMESTAMP, ZTEXT VARCHAR,
            ZMESSAGETYPE INTEGER, ZSTANZAID VARCHAR, ZGROUPMEMBER INTEGER, ZMEDIAITEM INTEGER, ZMESSAGESTATUS INTEGER);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMEMBERJID VARCHAR,
            ZCONTACTNAME VARCHAR);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZMEDIALOCALPATH VARCHAR,
            ZTITLE VARCHAR, ZFILESIZE INTEGER);
        CREATE TABLE ZWAPROFILEPUSHNAME (Z_PK INTEGER PRIMARY KEY, ZJID VARCHAR, ZPUSHNAME VARCHAR);
    """)
    for c in chats:
        conn.execute("INSERT INTO ZWACHATSESSION (Z_PK, ZCONTACTJID, ZPARTNERNAME, ZSESSIONTYPE) VALUES (?, ?, ?, ?)",
                     (c["pk"], c["jid"], c["name"], 1 if c.get("group") else 0))
        for i, (jid, name) in enumerate(c.get("members", [])):
            conn.execute("INSERT INTO ZWAGROUPMEMBER (Z_PK, ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME) VALUES (?, ?, ?, ?)",
                         (c["pk"] * 100 + i, c["pk"], jid, name))

    def put(c, m):
        media = None
        if m.get("media"):
            media = m["pk"] + 10000
            c.execute("INSERT INTO ZWAMEDIAITEM (Z_PK, ZMESSAGE, ZMEDIALOCALPATH) VALUES (?, ?, ?)",
                      (media, m["pk"], m["media"]))
        c.execute("INSERT INTO ZWAMESSAGE (Z_PK, ZCHATSESSION, ZISFROMME, ZFROMJID, ZMESSAGEDATE, ZTEXT, ZMESSAGETYPE, "
                  "ZSTANZAID, ZGROUPMEMBER, ZMEDIAITEM) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (m["pk"], m["chat"], 1 if m.get("me") else 0, m.get("from"), m["ts"] - APPLE_EPOCH, m.get("text"),
                   m.get("type", 1 if m.get("media") else 0), m["stanza"], m.get("member"), media))

    for m in messages:
        put(conn, m)
    conn.commit()
    conn.close()
    if not wal_messages:
        return None
    live = sqlite3.connect(str(path))
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("PRAGMA wal_autocheckpoint=0")
    for m in wal_messages:
        put(live, m)
    live.commit()
    return live          # keep it open: the rows stay in the -wal file


# ---------- iMessage chat.db ----------

def typedstream(text: str) -> bytes:
    """An attributedBody as Messages writes it (NSAttributedString in Apple's typedstream)."""
    data = text.encode("utf-8")
    if len(data) < 0x80:
        length = bytes([len(data)])
    elif len(data) < 0x10000:
        length = b"\x81" + len(data).to_bytes(2, "little")
    else:
        length = b"\x82" + len(data).to_bytes(4, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject"
            b"\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + data +
            b"\x86\x84\x02iI\x01\x05\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01\x92\x84\x96\x96\x1d"
            b"__kIMMessagePartAttributeName\x86\x92\x84\x84\x84\x08NSNumber\x00\x84\x84\x07NSValue\x00\x94\x84\x01*"
            b"\x84\x99\x99\x00\x86\x86\x86")


def apple_ns(unix: float) -> int:
    """A chat.db date (macOS 10.13+): nanoseconds since 2001-01-01."""
    return int((unix - APPLE_EPOCH) * 1e9)


def summary_info(versions=(), unsent_parts=()) -> bytes:
    """
    message_summary_info as Messages writes it: a binary plist; 'ec' holds every version of
    part 0 of an edited message (oldest first, the text as attributedBody, the date in
    nanoseconds since 2001), 'ep' the edited parts, 'rp' the unsent ones.
    """
    info = {"amc": 1, "ust": True}
    if versions:
        info["ec"] = {"0": [{"d": apple_ns(ts), "t": typedstream(text), "bcg": f"bcg-{i}"}
                            for i, (ts, text) in enumerate(versions)]}
        info["ep"] = [0]
    if unsent_parts:
        info["rp"] = list(unsent_parts)
    return plistlib.dumps(info, fmt=plistlib.FMT_BINARY)


def make_chat_db(path: Path, handles: Dict[int, str], chats: List[Dict], messages: List[Dict],
                 attachments: Optional[List[Dict]] = None):
    """
    handles: {rowid: 'id'}; chats: {rowid, guid, name?, style, handles[]}; messages: {rowid, guid,
    chat, handle, me, text?, body? (attributedBody only), ts (unix), seconds? (old date format),
    assoc?, assoc_type?, emoji?, retracted? (True: a second after ts, or a unix time), edited? (unix),
    summary? (message_summary_info bytes, see summary_info)}; attachments: {rowid, message, filename,
    mime, transfer}.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, id TEXT NOT NULL, country TEXT,
            service TEXT NOT NULL, uncanonicalized_id TEXT, person_centric_id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, style INTEGER,
            state INTEGER, account_id TEXT, properties BLOB, chat_identifier TEXT, service_name TEXT,
            room_name TEXT, display_name TEXT, group_id TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, text TEXT,
            replace INTEGER DEFAULT 0, service_center TEXT, handle_id INTEGER DEFAULT 0, subject TEXT, country TEXT,
            attributedBody BLOB, version INTEGER DEFAULT 0, type INTEGER DEFAULT 0, service TEXT, account TEXT,
            date INTEGER, date_read INTEGER, date_delivered INTEGER, is_from_me INTEGER DEFAULT 0,
            item_type INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0,
            associated_message_guid TEXT DEFAULT NULL, associated_message_type INTEGER DEFAULT 0,
            date_edited INTEGER DEFAULT 0, date_retracted INTEGER DEFAULT 0, associated_message_emoji TEXT,
            message_summary_info BLOB);
        CREATE TABLE chat_message_join (chat_id INTEGER REFERENCES chat (ROWID), message_id INTEGER
            REFERENCES message (ROWID), message_date INTEGER DEFAULT 0, PRIMARY KEY (chat_id, message_id));
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER, UNIQUE(chat_id, handle_id));
        CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL,
            created_date INTEGER DEFAULT 0, filename TEXT, uti TEXT, mime_type TEXT, transfer_state INTEGER DEFAULT 0,
            is_outgoing INTEGER DEFAULT 0, transfer_name TEXT, total_bytes INTEGER DEFAULT 0);
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER,
            UNIQUE(message_id, attachment_id));
    """)
    for rowid, hid in handles.items():
        conn.execute("INSERT INTO handle (ROWID, id, service) VALUES (?, ?, 'iMessage')", (rowid, hid))
    for c in chats:
        conn.execute("INSERT INTO chat (ROWID, guid, style, chat_identifier, display_name, service_name) "
                     "VALUES (?, ?, ?, ?, ?, 'iMessage')", (c["rowid"], c["guid"], c["style"], c["guid"].split(";")[-1],
                                                            c.get("name")))
        for h in c["handles"]:
            conn.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (c["rowid"], h))
    for m in messages:
        date = int(m["ts"] - APPLE_EPOCH) if m.get("seconds") else int((m["ts"] - APPLE_EPOCH) * 1e9)
        retracted = m.get("retracted")
        retracted = (date + 10 ** 9 if retracted is True else apple_ns(retracted)) if retracted else 0
        conn.execute("INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, date, is_from_me, "
                     "associated_message_guid, associated_message_type, associated_message_emoji, date_retracted, "
                     "date_edited, message_summary_info) "
                     "VALUES (?, ?, ?, ?, ?, 'iMessage', ?, ?, ?, ?, ?, ?, ?, ?)",
                     (m["rowid"], m["guid"], m.get("text"), m.get("body"), m.get("handle", 0), date,
                      1 if m.get("me") else 0, m.get("assoc"), m.get("assoc_type", 0), m.get("emoji"),
                      retracted, apple_ns(m["edited"]) if m.get("edited") else 0, m.get("summary")))
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)", (m["chat"], m["rowid"]))
    for a in attachments or []:
        conn.execute("INSERT INTO attachment (ROWID, guid, filename, mime_type, transfer_name) VALUES (?, ?, ?, ?, ?)",
                     (a["rowid"], f"att-{a['rowid']}", a["filename"], a["mime"], a["transfer"]))
        conn.execute("INSERT INTO message_attachment_join VALUES (?, ?)", (a["message"], a["rowid"]))
    conn.commit()
    conn.close()


# ---------- Meta ----------

def fb_thread(participants: List[str], messages: List[Dict], title: Optional[str] = None) -> Dict:
    return {
        "participants": [{"name": mojibake(p)} for p in participants],
        "messages": [{**{k: v for k, v in m.items() if k not in ("content", "sender_name", "reactions")},
                      "sender_name": mojibake(m["sender_name"]),
                      **({"content": mojibake(m["content"])} if "content" in m else {}),
                      **({"reactions": [{"reaction": mojibake(r["reaction"]), "actor": mojibake(r["actor"])}
                                        for r in m["reactions"]]} if "reactions" in m else {})}
                     for m in messages],
        "title": mojibake(title or ", ".join(participants[1:])), "is_still_participant": True,
        "thread_path": "inbox/" + (title or participants[-1]).lower().replace(" ", "") + "_10001",
    }


def meta_export(path: Path, layout: str = "en", threads: Optional[Dict[str, Dict]] = None, posts=None,
                comments=None, reactions=None, owner: str = "Matthias Voorbeeld", photos: Optional[Dict[str, bytes]] = None):
    """A Facebook DYI zip (JSON). layout 'en': your_facebook_activity/messages/inbox/…; 'nl': Dutch folder names."""
    act, msgs, inbox = (("your_facebook_activity", "messages", "inbox") if layout == "en"
                        else ("jouw_facebook_activiteit", "berichten", "postvak_in"))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("personal_information/profile_information/profile_information.json",
                    json.dumps({"profile_v2": {"name": {"full_name": mojibake(owner)}}}))
        for folder, thread in (threads or {}).items():
            thread = {**thread, "thread_path": f"inbox/{folder}"}      # Meta: the chat's own folder
            zf.writestr(f"{act}/{msgs}/{inbox}/{folder}/message_1.json", json.dumps(thread))
        if posts is not None:
            zf.writestr(f"{act}/posts/your_posts__check_ins__photos_and_videos_1.json", json.dumps(posts))
        if comments is not None:
            zf.writestr(f"{act}/comments_and_reactions/comments.json", json.dumps({"comments_v2": comments}))
        if reactions is not None:
            zf.writestr(f"{act}/comments_and_reactions/likes_and_reactions_1.json", json.dumps(reactions))
        for member, data in (photos or {}).items():
            zf.writestr(member, data)
    return path


def instagram_export(path: Path, posts=None, threads: Optional[Dict[str, Dict]] = None, likes=None,
                     owner: str = "matthias.voorbeeld"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("personal_information/personal_information/personal_information.json", json.dumps(
            {"profile_user": [{"string_map_data": {"Name": {"value": mojibake(owner)}}}]}))
        if posts is not None:
            zf.writestr("your_instagram_activity/content/posts_1.json", json.dumps(posts))
        for folder, thread in (threads or {}).items():
            thread = {**thread, "thread_path": f"inbox/{folder}"}
            zf.writestr(f"your_instagram_activity/messages/inbox/{folder}/message_1.json", json.dumps(thread))
        if likes is not None:
            zf.writestr("your_instagram_activity/likes/liked_posts.json", json.dumps({"likes_media_likes": likes}))
    return path


def secure_storage(path: Path, chats: Dict[str, Dict], media: Optional[Dict[str, bytes]] = None):
    """Messenger's 'Download secure storage data': one JSON per chat (camelCase) plus media/."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, chat in chats.items():
            zf.writestr(f"{name}.json", json.dumps(chat, ensure_ascii=False))
        for member, data in (media or {}).items():
            zf.writestr(member, data)
    return path


# ---------- WhatsApp 'Export chat' ----------

WHATSAPP_TXT = {
    # iPhone in Dutch: seconds, dashes, <bijgevoegd: …>
    "ios_nl": ("‎[01-06-2024 10:00:05] Anna Jansen: ‎Berichten en gesprekken worden end-to-end versleuteld.\n"
               "[01-06-2024 10:01:00] Anna Jansen: Hoi! Zin in koffie?\n"
               "[01-06-2024 10:02:30] Matthias: Ja, graag\nom 11 uur?\n"
               "[01-06-2024 10:05:00] Anna Jansen: ‎<bijgevoegd: 00000003-PHOTO-2024-06-01-10-05-00.jpg>\n"
               "[13-06-2024 18:30:00] Anna Jansen: Café was top ☕\n"),
    # iPhone in English (UK): slashes and a comma
    "ios_en": ("[01/06/2024, 10:01:00] Anna Jansen: Hoi! Zin in koffie?\n"
               "[02/06/2024, 09:00:00] Matthias: ‎image omitted\n"),
    # Android in Dutch: no seconds, ' - '
    "android_nl": ("01-06-2024 10:01 - Anna Jansen: Hoi! Zin in koffie?\n"
                   "01-06-2024 10:02 - Matthias: Ja, graag\nom 11 uur?\n"
                   "14-06-2024 08:15 - Anna Jansen: IMG-20240614-WA0001.jpg (bestand bijgevoegd)\n"),
    # Android in US English: month first, AM/PM
    "android_us": ("6/1/24, 10:01 AM - Anna Jansen: Hoi! Zin in koffie?\n"
                   "6/13/24, 6:30 PM - Matthias: See you\n"),
    # Android in French: ' à ' between date and time
    "android_fr": ("01/06/2024 à 10:01 - Anna Jansen: Hoi! Zin in koffie?\n"
                   "13/06/2024 à 21:34 - Matthias: Oui\n"),
}


def whatsapp_zip(path: Path, text: str, media: Optional[Dict[str, bytes]] = None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("_chat.txt", text)
        for name, data in (media or {}).items():
            zf.writestr(name, data)
    return path
