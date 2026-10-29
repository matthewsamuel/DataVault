"""
DataGatherer - reading a Meta export into the stores (shared by facebook, instagram, messenger)

One export is owned by one connector (its dg_raw_imports row), but its contents may land
under another source: the Messenger chats in a Facebook export are stored as source
'messenger', next to the end-to-end encrypted chats of the Messenger secure storage
download. Both key a chat by its participants and a message by
(chat, timestamp_ms, sender, text), so the same message in both exports is stored once.

Social items (posts, comments, reactions, Instagram media) go to social_items with a key of
(kind, time, title, text, url, media names).
"""
import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from backend.datagatherer import messages_store, raw_store, state
from backend.datagatherer.base import ConnectorError, GatherContext
from backend.datagatherer.formats import meta_dyi

SOCIAL_KINDS = ("posts", "comments", "reactions", "ig_media", "ig_likes", "ig_comments")
META_SOURCES = ("facebook", "instagram", "messenger")
SOCIAL_COLUMNS = ("source", "item_key", "kind", "ts_ms", "title", "body", "url", "media", "raw_import_id", "first_seen_at")


def owner_names(db, extra: Optional[str] = None) -> Set[str]:
    """Every name the user goes by on Meta: 'Mijn naam' settings and names found in exports."""
    names = set()
    for source in META_SOURCES:
        settings = state.get_settings(db, source)
        for value in [settings.get("my_name")] + list(settings.get("detected_owner") or []):
            if isinstance(value, str) and value.strip():
                names.add(value.strip().casefold())
    if extra:
        names.add(extra.strip().casefold())
    return names


def remember_owner(db, source: str, name: Optional[str]) -> None:
    if not name:
        return
    settings = state.get_settings(db, source)
    known = list(settings.get("detected_owner") or [])
    if name not in known:
        settings["detected_owner"] = (known + [name])[-5:]
        state.set_settings(db, source, settings)


def _attachments(items: List[Dict[str, Any]], raw_id: int) -> List[Dict[str, Any]]:
    out = []
    for a in items:
        entry = {"name": a.get("name") or "bijlage", "raw_import_id": raw_id}
        if a.get("member"):
            entry["member"] = a["member"]
        if a.get("url"):
            entry["url"] = a["url"]
        mime = mimetypes.guess_type(entry["name"])[0]
        if mime:
            entry["mime"] = mime
        out.append(entry)
    return out


def insert_social(db, source: str, items: List[Dict[str, Any]], raw_id: int) -> int:
    now = time.time()
    rows = []
    for item in items:
        media = _attachments(item.get("media") or [], raw_id)
        for m, original in zip(media, item.get("media") or []):
            if original.get("metadata"):
                m["metadata"] = original["metadata"]
        item_key = messages_store.key(item["kind"], item["ts_ms"], item.get("title"), item.get("body"), item.get("url"),
                                      "|".join(m["name"] for m in media))
        rows.append((source, item_key, item["kind"], item["ts_ms"], item.get("title"), item.get("body"), item.get("url"),
                     json.dumps(media, ensure_ascii=False) if media else None, raw_id, now))
    if not rows:
        return 0
    with db._connect() as conn:
        before = conn.total_changes
        conn.executemany(f"INSERT OR IGNORE INTO social_items ({', '.join(SOCIAL_COLUMNS)}) "
                         f"VALUES ({', '.join('?' * len(SOCIAL_COLUMNS))})", rows)
        added = conn.total_changes - before
        if added:
            state.bump_version(conn, source)
    return added


def import_export(ctx: GatherContext, raw: Dict[str, Any], *, social_source: Optional[str], message_source: str,
                  origin: str, secure: bool = False) -> Dict[str, Any]:
    """
    Read one Meta export (zip or registered folder): every JSON file, classified by content.
    secure: the Messenger secure storage download (no mojibake there, camelCase schema).
    """
    db = ctx.db
    raw_id = raw["id"]
    threads: List[Dict[str, Any]] = []
    owner = None
    files = social_added = social_seen = 0
    lo = hi = None
    for name, read in raw_store.iter_export_files(Path(raw["path"]), lambda n: n.lower().endswith(".json")):
        ctx.check()
        value = meta_dyi.load(read(), mojibake=not secure)
        kind = meta_dyi.classify(name, value)
        if kind is None:
            continue
        files += 1
        if kind == "profile":
            owner = meta_dyi.owner_name(value) or owner
        elif kind == "thread":
            threads.append(meta_dyi.parse_thread(value, name))
        elif kind == "secure_thread":
            threads.append(meta_dyi.parse_secure_thread(value, name))
        elif kind in SOCIAL_KINDS and social_source:
            items = list(meta_dyi.social_items(kind, value))
            social_seen += len(items)
            social_added += insert_social(db, social_source, items, raw_id)
            for item in items:
                lo = item["ts_ms"] if lo is None or item["ts_ms"] < lo else lo
                hi = item["ts_ms"] if hi is None or item["ts_ms"] > hi else hi
        if files % 50 == 0:
            ctx.progress(message=f"Inlezen: {files} bestanden")
    if not files:
        raise ConnectorError("Geen Meta-gegevens in JSON gevonden in deze export. Koos je bij het aanvragen het "
                             "formaat JSON (niet HTML)?")
    # Who is 'me': the profile, else the one person in every chat of this export
    if owner is None and len(threads) >= 2:
        common = set.intersection(*[{p for p in t["participants"]} for t in threads if t["participants"]] or [set()])
        if len(common) == 1:
            owner = next(iter(common))
    remember_owner(db, ctx.connector.id, owner)
    me = owner_names(db, owner)
    added = seen = 0
    for i, thread in enumerate(threads):
        ctx.check()
        conv_key = meta_dyi.conv_key(thread)
        conv_id = messages_store.conversation(db, message_source, conv_key, thread.get("title"),
                                              thread["participants"], len(thread["participants"]) > 2)
        rows = []
        for m in thread["messages"]:
            msg_key = messages_store.key(conv_key, m["ts_ms"], m["sender"], messages_store.norm_text(m["text"]))
            rows.append(messages_store.message_row(
                conv_id, msg_key, origin, m["ts_ms"], source=message_source, ts_precision="ms", tz=None,
                sender=m["sender"], is_me=(m["sender"] or "").casefold() in me if me else None, body=m["text"],
                kind=m["kind"], attachments=_attachments(m["attachments"], raw_id), reactions=m["reactions"],
                raw_import_id=raw_id))
            lo = m["ts_ms"] if lo is None or m["ts_ms"] < lo else lo
            hi = m["ts_ms"] if hi is None or m["ts_ms"] > hi else hi
        n_added, n_seen = messages_store.insert_messages(db, message_source, rows, check=ctx.check)
        added += n_added
        seen += n_seen
        if i % 25 == 0:
            ctx.progress(message=f"Inlezen: {i + 1} van {len(threads)} gesprekken")
    return {"added": added + social_added, "seen": seen + social_seen, "messages_added": added,
            "items_added": social_added, "conversations": len(threads), "files": files,
            "covers_from": lo / 1000 if lo else None, "covers_until": hi / 1000 if hi else None}
