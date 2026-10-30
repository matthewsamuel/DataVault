"""
DataGatherer - reading a Meta export into the stores (shared by facebook, instagram, messenger)

One export is owned by one connector (its dg_raw_imports row), but its contents may land
under another source: the Messenger chats in a Facebook export are stored as source
'messenger', next to the end-to-end encrypted chats of the Messenger secure storage
download.

A DYI chat is keyed by its folder id (meta_dyi.conv_key), so a newer export where a member
left, a participant was renamed or the group got a new name lands in the same chat, and two
groups with the same members stay apart. The secure storage download has no such id: its
chat is linked to the one DYI chat with the same people (resolve_conversation). Within a
chat a message is the same when its time in ms, sender and text are (import_export skips
those), so overlapping exports and both downloads store every message once.

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


def _people(participants) -> frozenset:
    return frozenset(p.casefold() for p in participants if p)


def resolve_conversation(db, source: str, thread: Dict[str, Any]) -> int:
    """
    The conversation of a thread: by its key when known, else made, and then linked
    (merged_into) to the chat it is the same as:
    - a DYI chat ('t:') to the secure storage chat (or a chat stored before folder ids
      were used) with the same people, 'p:'; a group only when no other DYI chat is linked
      to that one yet and the names match;
    - a DYI one-to-one chat to the one other DYI chat with the same two people (Meta gives
      the pre-encryption copy of a chat its own folder);
    - a secure storage chat ('p:') to the one DYI chat with the same people.
    Placeholder names ('Facebook user') never link anything.
    """
    key = meta_dyi.conv_key(thread)
    people = thread["participants"]
    title = thread.get("title")
    is_group = len(_people(people)) > 2
    with db._connect() as conn:
        known = conn.execute("SELECT 1 FROM msg_conversations WHERE source = ? AND conv_key = ?",
                             (source, key)).fetchone()
    if known:
        return messages_store.conversation(db, source, key, title, people, is_group)
    target = None
    pkey = meta_dyi.participant_key(people)
    if pkey and not key.startswith("f:"):
        wanted = _people(people)
        with db._connect() as conn:
            same = [(cid, ckey, ctitle) for cid, ckey, ctitle, cpeople in conn.execute(
                        "SELECT id, conv_key, title, participants FROM msg_conversations "
                        "WHERE source = ? AND merged_into IS NULL", (source,))
                    if _people(json.loads(cpeople or "[]")) == wanted]
            if key.startswith("t:"):
                exact = [(cid, ctitle) for cid, ckey, ctitle in same if ckey == pkey]
                if exact:
                    cid, ctitle = exact[0]
                    taken = conn.execute("SELECT 1 FROM msg_conversations WHERE source = ? AND merged_into = ? "
                                         "AND conv_key LIKE 't:%'", (source, cid)).fetchone()
                    same_name = not ctitle or not title or ctitle.casefold() == title.casefold()
                    if not is_group or (not taken and same_name):
                        target = cid
                elif not is_group:
                    dyi = [cid for cid, ckey, _ in same if ckey.startswith("t:")]
                    target = dyi[0] if len(dyi) == 1 else None
            else:
                dyi = [cid for cid, ckey, _ in same if ckey.startswith("t:")]
                target = dyi[0] if len(dyi) == 1 else None
    conv_id = messages_store.conversation(db, source, key, title, people, is_group)
    if target is None or target == conv_id:
        return conv_id
    with db._connect() as conn:
        conn.execute("UPDATE msg_conversations SET merged_into = ? WHERE id = ?", (target, conv_id))
        conn.execute("UPDATE msg_messages SET conv_id = ? WHERE conv_id = ?", (target, conv_id))
        row = conn.execute("SELECT title, participants FROM msg_conversations WHERE id = ?", (target,)).fetchone()
        merged = sorted(set(json.loads(row[1] or "[]")) | {p for p in people if p})
        conn.execute("UPDATE msg_conversations SET title = COALESCE(title, ?), participants = ? WHERE id = ?",
                     (title, json.dumps(merged, ensure_ascii=False), target))
        messages_store.refresh_conversations(conn, [conv_id, target])
    return target


def _content(sent_at_ms: int, sender: Optional[str], body: Optional[str]) -> tuple:
    return (int(sent_at_ms), sender or "", messages_store.norm_text(body))


def claim_messages(db, source: str, conv_id: int, thread: Dict[str, Any], origin: str) -> int:
    """
    Chats stored before folder ids were used were keyed by their people, so two groups with
    the same members ended up as one. Move this thread's own messages (same time, sender and
    text, from this kind of export) out of such a chat into conv_id. Returns how many moved.
    """
    pkey = meta_dyi.participant_key(thread["participants"])
    if not pkey:
        return 0
    with db._connect() as conn:
        row = conn.execute("SELECT id, merged_into FROM msg_conversations WHERE source = ? AND conv_key = ?",
                           (source, pkey)).fetchone()
        if row is None or row[0] == conv_id or row[1] == conv_id:
            return 0
        wanted = {_content(m["ts_ms"], m["sender"], m["text"]) for m in thread["messages"]}
        ids = [mid for mid, ts, sender, body in conn.execute(
                   "SELECT id, sent_at_ms, sender, body FROM msg_messages WHERE conv_id = ? AND origin = ?",
                   (row[0], origin))
               if _content(ts, sender, body) in wanted]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            conn.execute(f"UPDATE msg_messages SET conv_id = ? WHERE id IN ({', '.join('?' * len(chunk))})",
                         [conv_id] + chunk)
        if ids:
            messages_store.refresh_conversations(conn, [row[0], conv_id])
            state.bump_version(conn, source)
    return len(ids)


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
        conv_id = resolve_conversation(db, message_source, thread)
        if thread.get("thread_id"):
            claim_messages(db, message_source, conv_id, thread, origin)
        with db._connect() as conn:
            stored = {_content(*r) for r in conn.execute(
                "SELECT sent_at_ms, sender, body FROM msg_messages WHERE source = ? AND conv_id = ?",
                (message_source, conv_id))}
        rows = []
        skipped = 0
        for m in thread["messages"]:
            lo = m["ts_ms"] if lo is None or m["ts_ms"] < lo else lo
            hi = m["ts_ms"] if hi is None or m["ts_ms"] > hi else hi
            content = _content(m["ts_ms"], m["sender"], m["text"])
            if content in stored:           # already in this chat (an earlier export, or the other download)
                skipped += 1
                continue
            stored.add(content)
            msg_key = messages_store.key("conv", conv_id, m["ts_ms"], m["sender"], messages_store.norm_text(m["text"]))
            rows.append(messages_store.message_row(
                conv_id, msg_key, origin, m["ts_ms"], source=message_source, ts_precision="ms", tz=None,
                sender=m["sender"], is_me=(m["sender"] or "").casefold() in me if me else None, body=m["text"],
                kind=m["kind"], attachments=_attachments(m["attachments"], raw_id), reactions=m["reactions"],
                raw_import_id=raw_id))
        n_added, n_seen = messages_store.insert_messages(db, message_source, rows, check=ctx.check)
        added += n_added
        seen += n_seen + skipped
        if i % 25 == 0:
            ctx.progress(message=f"Inlezen: {i + 1} van {len(threads)} gesprekken")
    return {"added": added + social_added, "seen": seen + social_seen, "messages_added": added,
            "items_added": social_added, "conversations": len(threads), "files": files,
            "covers_from": lo / 1000 if lo else None, "covers_until": hi / 1000 if hi else None}
