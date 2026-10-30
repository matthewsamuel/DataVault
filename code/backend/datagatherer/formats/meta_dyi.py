"""
Meta 'Download your information' (Facebook, Instagram, Messenger) in JSON

Shared by the facebook, instagram and messenger connectors. Every JSON file of an export is
classified by its CONTENT, not by its folder: Meta names the folders in the account's
language (your_facebook_activity/messages/inbox, or Dutch names such as
jouw_facebook_activiteit/berichten/inbox), and the layout changed over the years.

Meta writes text as UTF-8 bytes escaped as Latin-1 code points ('cafÃ©' for 'café'):
fix_mojibake() undoes that, only where the result is valid UTF-8.

What comes out (plain dicts, no database here):
    thread        {participants, title, messages[{sender, ts_ms, text, attachments, reactions, kind}]}
    social item   {kind: post|comment|reaction|media|story, ts_ms, title, body, url, media[]}
    owner         the account's own name (profile file), when the export has it

The Messenger 'secure storage' download (messages.zip, end-to-end encrypted chats) has its
own camelCase schema: parse_secure_thread() reads it into the same thread shape.
"""
import json
import re
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Folder names Meta uses in English and Dutch exports (only as hints: classification is by content)
FOLDER_NAMES = {
    "messages": ("messages", "berichten"),
    "inbox": ("inbox", "postvak_in", "postvak in"),
    "archived": ("archived_threads", "gearchiveerde_gesprekken"),
    "requests": ("message_requests", "berichtverzoeken"),
    "e2ee": ("e2ee_cutover",),
    "facebook": ("your_facebook_activity", "jouw_facebook_activiteit", "je_facebook_activiteit"),
    "instagram": ("your_instagram_activity", "jouw_instagram_activiteit", "je_instagram_activiteit"),
}
MESSAGE_FILE = re.compile(r"(?:^|/)(?:message|bericht)_\d+\.json$", re.I)
MEDIA_KEYS = ("photos", "videos", "audio_files", "files", "gifs")


def fix_mojibake(value: Any) -> Any:
    """'cafÃ©' -> 'café' (UTF-8 read as Latin-1), recursively; text that is not mojibake stays as it is."""
    if isinstance(value, str):
        if not value or value.isascii():
            return value
        try:
            return value.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value
    if isinstance(value, list):
        return [fix_mojibake(v) for v in value]
    if isinstance(value, dict):
        return {k: fix_mojibake(v) for k, v in value.items()}
    return value


def load(data: bytes, mojibake: bool = True) -> Any:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return None
    return fix_mojibake(value) if mojibake else value


def folder_parts(name: str) -> List[str]:
    return [p.casefold() for p in name.replace("\\", "/").split("/")[:-1]]


def has_folder(name: str, which: str) -> bool:
    return any(p in FOLDER_NAMES[which] for p in folder_parts(name))


# ---------- what a file is ----------

def classify(name: str, value: Any) -> Optional[str]:
    """'thread', 'secure_thread', 'posts', 'comments', 'reactions', 'ig_media', 'ig_likes',
    'ig_comments', 'profile' or None, from the content of one JSON file."""
    if isinstance(value, dict):
        if isinstance(value.get("messages"), list) and "participants" in value:
            msgs = value["messages"]
            if any(isinstance(m, dict) and ("senderName" in m or "isUnsent" in m) for m in msgs[:20]) \
                    or "threadName" in value:
                return "secure_thread"
            return "thread"
        if "profile_v2" in value or "profile_user" in value:
            return "profile"
        if isinstance(value.get("comments_v2"), list) or isinstance(value.get("comments_media_comments"), list):
            return "ig_comments" if "comments_media_comments" in value else "comments"
        if isinstance(value.get("reactions_v2"), list):
            return "reactions"
        if isinstance(value.get("likes_media_likes"), list):
            return "ig_likes"
        if any(isinstance(value.get(k), list) for k in ("ig_stories", "ig_reels_media", "ig_profile_picture")):
            return "ig_media"
        return None
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value[:20]):
        sample = value[:20]
        if all("string_map_data" in v for v in sample):
            return "ig_comments"
        if all("media" in v and isinstance(v["media"], list) for v in sample):
            return "ig_media"
        if all("uri" in v and "creation_timestamp" in v for v in sample):
            return "ig_media"
        if all("timestamp" in v for v in sample):
            datas = [d for v in sample for d in (v.get("data") or []) if isinstance(d, dict)]
            if any("reaction" in d for d in datas):
                return "reactions"
            if any("comment" in d for d in datas):
                return "comments"
            if any("post" in d for d in datas) or any("attachments" in v for v in sample):
                return "posts"
    return None


def owner_name(value: Any) -> Optional[str]:
    """The account's own name from a profile file (Facebook profile_v2, Instagram profile_user)."""
    if not isinstance(value, dict):
        return None
    profile = value.get("profile_v2")
    if isinstance(profile, dict):
        name = profile.get("name")
        if isinstance(name, dict) and isinstance(name.get("full_name"), str):
            return name["full_name"].strip() or None
    users = value.get("profile_user")
    if isinstance(users, list) and users and isinstance(users[0], dict):
        data = users[0].get("string_map_data") or {}
        for field in ("Name", "Naam", "Username", "Gebruikersnaam"):
            entry = data.get(field)
            if isinstance(entry, dict) and isinstance(entry.get("value"), str) and entry["value"].strip():
                return entry["value"].strip()
    return None


# ---------- messages ----------

def _participants(value: Any) -> List[str]:
    people = []
    for p in value or []:
        name = p.get("name") if isinstance(p, dict) else p
        if isinstance(name, str) and name.strip():
            people.append(name.strip())
    return people


def _ms(value: Any) -> Optional[int]:
    """A Meta timestamp as UTC ms: timestamp_ms, or seconds (creation_timestamp / timestamp)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value <= 0:
        return None
    value = int(value)
    if value < 10 ** 11:            # seconds
        value *= 1000
    return value if value < 10 ** 14 else None


def _reactions(items: Any) -> List[Dict[str, str]]:
    out = []
    for r in items or []:
        if isinstance(r, dict) and isinstance(r.get("reaction"), str):
            out.append({"actor": r.get("actor") if isinstance(r.get("actor"), str) else "", "reaction": r["reaction"]})
    return out


def _media(items: Any, base: str) -> List[Dict[str, Any]]:
    """[{name, member}] for media entries ({uri}); member is the path inside the export."""
    out = []
    for item in items or []:
        uri = item.get("uri") if isinstance(item, dict) else None
        if isinstance(uri, str) and uri and "://" not in uri:
            out.append({"name": uri.rsplit("/", 1)[-1], "member": resolve_member(uri, base)})
        elif isinstance(uri, str) and uri.startswith("https://"):
            out.append({"name": uri.rsplit("/", 1)[-1].split("?", 1)[0], "url": uri})
    return out


def resolve_member(uri: str, base: str) -> str:
    """A media uri ('your_facebook_activity/messages/inbox/x/photos/1.jpg' or './media/1.jpg')
    as a member path relative to the export's root; base is the folder of the JSON file."""
    uri = uri.replace("\\", "/")
    if uri.startswith("./"):
        uri = (base + "/" if base else "") + uri[2:]
    parts = []
    for part in uri.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def parse_thread(value: Dict[str, Any], name: str) -> Dict[str, Any]:
    """A DYI message_N.json (Facebook Messenger or Instagram direct) as a thread."""
    base = name.rsplit("/", 1)[0] if "/" in name else ""
    messages = []
    for m in value.get("messages") or []:
        if not isinstance(m, dict):
            continue
        ts = _ms(m.get("timestamp_ms"))
        if ts is None:
            continue
        attachments = []
        for key in MEDIA_KEYS:
            attachments += _media(m.get(key), "")
        sticker = m.get("sticker")
        if isinstance(sticker, dict):
            attachments += _media([sticker], "")
        text = m.get("content") if isinstance(m.get("content"), str) else None
        share = m.get("share")
        link = share.get("link") if isinstance(share, dict) and isinstance(share.get("link"), str) else None
        if link and (not text or link not in text):
            text = f"{text}\n{link}" if text else link
        kind = "media" if attachments and not text else ("system" if m.get("type") in ("Subscribe", "Unsubscribe") else "text")
        if m.get("is_unsent"):
            kind = "unsent"
        messages.append({"sender": (m.get("sender_name") or "").strip() or None, "ts_ms": ts, "text": text,
                         "attachments": attachments, "reactions": _reactions(m.get("reactions")), "kind": kind})
    thread_path = value.get("thread_path") if isinstance(value.get("thread_path"), str) and value["thread_path"] else base
    return {"participants": _participants(value.get("participants")),
            "title": value.get("title") if isinstance(value.get("title"), str) else None,
            "thread_path": thread_path, "thread_id": thread_id(thread_path or base), "messages": messages}


def parse_secure_thread(value: Dict[str, Any], name: str) -> Dict[str, Any]:
    """One chat of the Messenger 'secure storage' download (camelCase keys) as a thread."""
    base = name.rsplit("/", 1)[0] if "/" in name else ""
    messages = []
    for m in value.get("messages") or []:
        if not isinstance(m, dict):
            continue
        ts = _ms(m.get("timestamp_ms", m.get("timestamp")))
        if ts is None:
            continue
        attachments = _media(m.get("media"), base)
        for a in attachments:
            if "member" in a and isinstance(a["member"], str):
                a["member"] = resolve_member(a["member"], "")
        text = m.get("text") if isinstance(m.get("text"), str) else None
        kind = "unsent" if m.get("isUnsent") else ("media" if attachments and not text else "text")
        messages.append({"sender": (m.get("senderName") or "").strip() or None, "ts_ms": ts, "text": text,
                         "attachments": attachments, "reactions": _reactions(m.get("reactions")), "kind": kind})
    return {"participants": _participants(value.get("participants")),
            "title": value.get("threadName") if isinstance(value.get("threadName"), str) else None,
            "thread_path": name.rsplit(".", 1)[0], "thread_id": None, "messages": messages}


# Names Meta shows for a deactivated or deleted account: never a way to tell two chats apart
PLACEHOLDER_NAMES = frozenset({
    "facebook user", "facebook-gebruiker", "facebookgebruiker", "gebruiker van facebook",
    "instagram user", "instagram-gebruiker", "instagramgebruiker", "gebruiker van instagram",
    "deleted user", "verwijderde gebruiker", "messenger user", "messenger-gebruiker",
})


def thread_id(thread_path: Optional[str]) -> Optional[str]:
    """The stable id of a DYI chat from its folder ('inbox/familie_10158123' -> '10158123').
    The name before the id follows the chat's title (it changes when a group is renamed), the
    id does not; a chat that moved from inbox to archived_threads keeps it too."""
    folder = (thread_path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip()
    if not folder:
        return None
    head, sep, tail = folder.rpartition("_")
    return tail if sep and head and tail else folder


def participant_key(participants: List[str]) -> Optional[str]:
    """'p:anna|bob|me', or None when a participant is a placeholder name (or there are none)."""
    people = sorted({p.casefold() for p in participants if p})
    if not people or any(p in PLACEHOLDER_NAMES for p in people):
        return None
    return "p:" + "|".join(people)


def conv_key(thread: Dict[str, Any]) -> str:
    """
    The conversation key of a thread. A DYI chat by its folder id ('t:10158123'): members
    may leave or be renamed between two exports, and two different chats may have the same
    members. The secure storage download has no such id: there it is the participants
    ('p:…'; meta_import links that to the DYI chat with the same people), or, with a
    placeholder name among them, the chat file itself ('f:…').
    """
    if thread.get("thread_id"):
        return "t:" + thread["thread_id"]
    return participant_key(thread["participants"]) or \
        "f:" + (thread.get("thread_path") or thread.get("title") or "?").casefold()


# ---------- posts, comments, reactions, media ----------

def _post_text(item: Dict[str, Any]) -> Optional[str]:
    for d in item.get("data") or []:
        if isinstance(d, dict) and isinstance(d.get("post"), str) and d["post"].strip():
            return d["post"]
    return None


def _attachment_media(item: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    media, url = [], None
    for att in item.get("attachments") or []:
        for d in (att.get("data") if isinstance(att, dict) else None) or []:
            if not isinstance(d, dict):
                continue
            if isinstance(d.get("media"), dict):
                media += _media([d["media"]], "")
            ctx = d.get("external_context")
            if isinstance(ctx, dict) and isinstance(ctx.get("url"), str):
                url = ctx["url"]
    return media, url


def social_items(kind: str, value: Any) -> Iterator[Dict[str, Any]]:
    """The social items in one classified file (posts, comments, reactions, ig_media, ig_likes, ig_comments)."""
    if kind == "posts":
        for item in value:
            ts = _ms(item.get("timestamp"))
            if ts is None:
                continue
            media, url = _attachment_media(item)
            yield {"kind": "post", "ts_ms": ts, "title": item.get("title"), "body": _post_text(item), "url": url,
                   "media": media}
    elif kind == "comments":
        items = value.get("comments_v2") if isinstance(value, dict) else value
        for item in items or []:
            ts = _ms(item.get("timestamp")) if isinstance(item, dict) else None
            if ts is None:
                continue
            text = None
            for d in item.get("data") or []:
                c = d.get("comment") if isinstance(d, dict) else None
                if isinstance(c, dict) and isinstance(c.get("comment"), str):
                    text = c["comment"]
            yield {"kind": "comment", "ts_ms": ts, "title": item.get("title"), "body": text, "url": None, "media": []}
    elif kind == "reactions":
        items = value.get("reactions_v2") if isinstance(value, dict) else value
        for item in items or []:
            ts = _ms(item.get("timestamp")) if isinstance(item, dict) else None
            if ts is None:
                continue
            reaction = None
            for d in item.get("data") or []:
                r = d.get("reaction") if isinstance(d, dict) else None
                if isinstance(r, dict) and isinstance(r.get("reaction"), str):
                    reaction = r["reaction"]
            yield {"kind": "reaction", "ts_ms": ts, "title": item.get("title"), "body": reaction, "url": None, "media": []}
    elif kind == "ig_media":
        if isinstance(value, dict):
            entries = []
            for key in ("ig_stories", "ig_reels_media", "ig_profile_picture"):
                entries += [(key, e) for e in value.get(key) or []]
        else:
            entries = [("posts", e) for e in value]
        for key, entry in entries:
            if not isinstance(entry, dict):
                continue
            files = entry.get("media") if isinstance(entry.get("media"), list) else [entry]
            for f in files:
                if not isinstance(f, dict):
                    continue
                ts = _ms(f.get("creation_timestamp") or entry.get("creation_timestamp"))
                if ts is None:
                    continue
                media = _media([f], "")
                meta = f.get("media_metadata")
                if media and isinstance(meta, dict):
                    media[0]["metadata"] = meta
                yield {"kind": "story" if key == "ig_stories" else "media", "ts_ms": ts,
                       "title": None, "body": f.get("title") or entry.get("title") or None, "url": None, "media": media}
    elif kind == "ig_likes":
        for item in value.get("likes_media_likes") or []:
            for s in (item.get("string_list_data") if isinstance(item, dict) else None) or []:
                ts = _ms(s.get("timestamp")) if isinstance(s, dict) else None
                if ts is None:
                    continue
                yield {"kind": "reaction", "ts_ms": ts, "title": item.get("title"), "body": s.get("value"),
                       "url": s.get("href"), "media": []}
    elif kind == "ig_comments":
        items = value.get("comments_media_comments") if isinstance(value, dict) else value
        for item in items or []:
            data = item.get("string_map_data") if isinstance(item, dict) else None
            if not isinstance(data, dict):
                continue
            text = (data.get("Comment") or data.get("Opmerking") or {}).get("value")
            owner = (data.get("Media Owner") or data.get("Eigenaar van media") or {}).get("value")
            ts = _ms((data.get("Time") or data.get("Tijd") or {}).get("timestamp"))
            if ts is None:
                continue
            yield {"kind": "comment", "ts_ms": ts, "title": owner, "body": text, "url": None, "media": []}
