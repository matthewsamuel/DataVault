"""
DataGatherer - the merged store of conversations and messages (msg_* tables, migration 90)

Every chat source writes here through the same few functions, so the Berichten view, the
search and Pasha read one store:

    conversation(db, source, conv_key, ...)   the conversation's id (made once; follows
                                              merged_into to the conversation it was merged into)
    insert_messages(db, source, rows)         INSERT OR IGNORE on (source, msg_key): importing
                                              the same export twice adds nothing
    revise_messages(db, source, rows)         a message edited or unsent since it was stored
                                              (iMessage): the row is updated, its earlier text
                                              kept in edits (migration 196), never a second row
    apply_reactions(db, source, events)       tapbacks in time order: one per person, a tapback
                                              taken back is marked removed (kept, not shown)
    reconcile_whatsapp(db)                    an 'Export chat' (.txt, minute precision) merged
                                              into the chat from the WhatsApp database: the
                                              conversation is merged, and every .txt message the
                                              database also has is marked superseded_by, never deleted
    search / conversations / thread           what the Berichten view shows
    assistant_context(db, question)           what Pasha may read: only sources the user switched on

The message keys (msg_key) are stable per source: the source's own id where it has one
(iMessage guid, WhatsApp stanza id), else a hash of conversation, time, sender and text.
"""
import hashlib
import json
import re
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.datagatherer import state
from backend.datagatherer.base import Coverage

BATCH = 2000
MESSAGE_COLUMNS = ("source", "conv_id", "msg_key", "origin", "sent_at_ms", "tz", "ts_precision", "sender", "is_me",
                   "body", "kind", "attachments", "reactions", "raw_import_id", "first_seen_at",
                   "edited_at_ms", "retracted_at_ms", "edits")
PASHA_SETTING = "pasha"            # the per-source toggle 'Pasha mag deze berichten lezen' (off by default)
TXT_PREFIX = "name:"               # the conv_key of a WhatsApp 'Export chat': the chat's name
HIT_START, HIT_END = "\u0002", "\u0003"   # marks around a search hit in a snippet (the page makes <mark> of it)


def key(*parts: Any) -> str:
    """A stable key from parts (sha1, hex)."""
    return hashlib.sha1("\x1f".join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()


def norm_text(text: Optional[str]) -> str:
    """Text for comparing: NFC, whitespace collapsed, case folded."""
    return " ".join(unicodedata.normalize("NFC", text or "").split()).casefold()


def message_sources() -> List[Any]:
    """The connectors whose source holds conversations (Connector.holds_messages), in tab order."""
    from backend.datagatherer import registry
    return [c for c in registry.connectors() if getattr(c, "holds_messages", False)]


# ---------- writing ----------

def conversation(db, source: str, conv_key: str, title: Optional[str] = None,
                 participants: Sequence[str] = (), is_group: Optional[bool] = None) -> int:
    """The id of the conversation (source, conv_key), made when new. A missing title or new
    participants are filled in. When it was merged into another one, that one's id."""
    now = time.time()
    people = sorted({p for p in participants if p})
    with db._connect() as conn:
        row = conn.execute("SELECT id, title, participants, merged_into FROM msg_conversations "
                           "WHERE source = ? AND conv_key = ?", (source, conv_key)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO msg_conversations (source, conv_key, title, participants, is_group, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source, conv_key, title, json.dumps(people, ensure_ascii=False),
                 int(bool(is_group) if is_group is not None else len(people) > 2), now))
            return cur.lastrowid
        conv_id, old_title, old_people, merged = row
        known = set(json.loads(old_people or "[]"))
        if (title and not old_title) or not set(people) <= known:
            conn.execute("UPDATE msg_conversations SET title = COALESCE(title, ?), participants = ?, updated_at = ? "
                         "WHERE id = ?", (title, json.dumps(sorted(known | set(people)), ensure_ascii=False), now, conv_id))
        return merged or conv_id


def message_row(conv_id: int, msg_key: str, origin: str, sent_at_ms: int, *, source: str, ts_precision: str = "s",
                tz: Optional[str] = None, sender: Optional[str] = None, is_me: Optional[bool] = None,
                body: Optional[str] = None, kind: str = "text", attachments: Optional[list] = None,
                reactions: Optional[list] = None, raw_import_id: Optional[int] = None,
                edited_at_ms: Optional[int] = None, retracted_at_ms: Optional[int] = None,
                edits: Optional[list] = None) -> Dict[str, Any]:
    return {
        "source": source, "conv_id": conv_id, "msg_key": msg_key, "origin": origin, "sent_at_ms": int(sent_at_ms),
        "tz": tz, "ts_precision": ts_precision, "sender": sender, "is_me": None if is_me is None else int(bool(is_me)),
        "body": body if body else None, "kind": kind,
        "attachments": json.dumps(attachments, ensure_ascii=False) if attachments else None,
        "reactions": json.dumps(reactions, ensure_ascii=False) if reactions else None,
        "raw_import_id": raw_import_id, "first_seen_at": time.time(),
        "edited_at_ms": edited_at_ms, "retracted_at_ms": retracted_at_ms,
        "edits": json.dumps(edits, ensure_ascii=False) if edits else None,
    }


def insert_messages(db, source: str, rows: Iterable[Dict[str, Any]], check=None) -> Tuple[int, int]:
    """INSERT OR IGNORE in batches (each one transaction); returns (added, seen)."""
    added = seen = 0
    batch: List[tuple] = []
    def flush():
        nonlocal added
        if not batch:
            return
        with db._connect() as conn:
            # rowcount, not total_changes: the FTS triggers' own rows are no new messages
            new = conn.executemany(f"INSERT OR IGNORE INTO msg_messages ({', '.join(MESSAGE_COLUMNS)}) "
                                   f"VALUES ({', '.join('?' * len(MESSAGE_COLUMNS))})", batch).rowcount
            if new:
                refresh_conversations(conn, {b[1] for b in batch})
                state.bump_version(conn, source)
        added += new
        batch.clear()

    for row in rows:
        seen += 1
        batch.append(tuple(row.get(c) for c in MESSAGE_COLUMNS))
        if len(batch) >= BATCH:
            flush()
            if check is not None:
                check()
    flush()
    return added, seen


def _merge_history(stored: Optional[str], new: Optional[str], old_body: Optional[str], old_at: int,
                   new_body: Optional[str]) -> Optional[str]:
    """The earlier versions of a message: what was stored, what the source lists, and the text that
    was stored until now when it differs from the new one. Oldest first, each text once."""
    history: List[Dict[str, Any]] = []
    for blob in (stored, new):
        try:
            items = json.loads(blob) if blob else []
        except ValueError:
            items = []
        history += [h for h in items if isinstance(h, dict) and h.get("body")]
    if old_body and old_body != new_body:
        history.append({"at": old_at, "body": old_body})
    seen, out = set(), []
    for h in sorted(history, key=lambda h: h.get("at") or 0):
        if h["body"] != new_body and h["body"] not in seen:
            seen.add(h["body"])
            out.append({"at": h.get("at"), "body": h["body"]})
    return json.dumps(out, ensure_ascii=False) if out else None


def revise_messages(db, source: str, rows: Iterable[Dict[str, Any]]) -> int:
    """
    Messages that were edited or unsent after they were stored (rows from message_row with
    edited_at_ms or retracted_at_ms): the stored row gets the new text, kind and times, and
    its earlier text goes into edits. A row that already says the same is left alone, so
    reading again changes nothing. Returns how many rows changed.
    """
    changed = 0
    touched = set()
    with db._connect() as conn:
        for row in rows:
            if row.get("edited_at_ms") is None and row.get("retracted_at_ms") is None:
                continue
            cur = conn.execute("SELECT id, conv_id, body, kind, sent_at_ms, edited_at_ms, retracted_at_ms, edits "
                               "FROM msg_messages WHERE source = ? AND msg_key = ?", (source, row["msg_key"])).fetchone()
            if cur is None:
                continue
            row_id, conv_id, body, kind, sent, edited, retracted, edits = cur
            if (edited, retracted, body, kind) == (row["edited_at_ms"], row["retracted_at_ms"], row["body"], row["kind"]):
                continue
            history = _merge_history(edits, row.get("edits"), body, edited or sent, row["body"])
            conn.execute("UPDATE msg_messages SET body = ?, kind = ?, edited_at_ms = ?, retracted_at_ms = ?, edits = ? "
                         "WHERE id = ?", (row["body"], row["kind"], row["edited_at_ms"], row["retracted_at_ms"],
                                          history, row_id))
            changed += 1
            touched.add(conv_id)
        if changed:
            refresh_conversations(conn, touched)
            state.bump_version(conn, source)
    return changed


def apply_reactions(db, source: str, events: Iterable[Dict[str, Any]]) -> int:
    """
    Tapbacks in time order ({target, actor, reaction, removed}): one per person per message,
    so a new one replaces that person's earlier one, and one taken back (removed; reaction
    None: whichever it was) is marked removed. Marked, not dropped: the list keeps what
    happened, the views leave removed ones out. The same events twice give the same result.
    Returns how many messages changed.
    """
    by_target: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        by_target.setdefault(ev["target"], []).append(ev)
    changed = 0
    with db._connect() as conn:
        for target, items in by_target.items():
            row = conn.execute("SELECT id, reactions FROM msg_messages WHERE source = ? AND msg_key = ?",
                               (source, target)).fetchone()
            if row is None:
                continue
            before = row[1] or "[]"
            current = [r for r in json.loads(before) if isinstance(r, dict)]
            for ev in sorted(items, key=lambda e: e.get("ts_ms") or 0):
                mine = [r for r in current if r.get("actor") == ev["actor"]]
                if ev.get("removed"):
                    for r in mine:
                        if ev["reaction"] is None or r.get("reaction") == ev["reaction"]:
                            r["removed"] = True
                    continue
                for r in mine:
                    if r.get("reaction") != ev["reaction"]:
                        r["removed"] = True
                same = next((r for r in mine if r.get("reaction") == ev["reaction"]), None)
                if same is not None:
                    same.pop("removed", None)
                else:
                    current.append({"actor": ev["actor"], "reaction": ev["reaction"]})
            after = json.dumps(current, ensure_ascii=False) if current else None
            if (after or "[]") != before:
                conn.execute("UPDATE msg_messages SET reactions = ? WHERE id = ?", (after, row[0]))
                changed += 1
        if changed:
            state.bump_version(conn, source)
    return changed


def refresh_conversations(conn, conv_ids: Iterable[int]) -> None:
    """First and last message and the count (without superseded messages) of these conversations."""
    now = time.time()
    for conv_id in conv_ids:
        first, last, count = conn.execute(
            "SELECT MIN(sent_at_ms), MAX(sent_at_ms), COUNT(*) FROM msg_messages "
            "WHERE conv_id = ? AND superseded_by IS NULL", (conv_id,)).fetchone()
        conn.execute("UPDATE msg_conversations SET first_ms = ?, last_ms = ?, message_count = ?, updated_at = ? "
                     "WHERE id = ?", (first, last, count, now, conv_id))


def add_reactions(db, source: str, reactions: Dict[str, List[Dict[str, str]]]) -> int:
    """Add reactions (msg_key -> [{actor, reaction}]) to messages already stored; each pair once."""
    changed = 0
    with db._connect() as conn:
        for msg_key, items in reactions.items():
            row = conn.execute("SELECT id, reactions FROM msg_messages WHERE source = ? AND msg_key = ?",
                               (source, msg_key)).fetchone()
            if row is None:
                continue
            current = json.loads(row[1] or "[]")
            seen = {(r.get("actor"), r.get("reaction")) for r in current}
            new = [r for r in items if (r.get("actor"), r.get("reaction")) not in seen]
            if new:
                conn.execute("UPDATE msg_messages SET reactions = ? WHERE id = ?",
                             (json.dumps(current + new, ensure_ascii=False), row[0]))
                changed += 1
        if changed:
            state.bump_version(conn, source)
    return changed


# ---------- WhatsApp: an 'Export chat' merged into the database's chat ----------

def _txt_body(row) -> str:
    """What a message says, for matching an export line to the database: the text, or 'media'."""
    return "\x00media" if row["kind"] == "media" else norm_text(row["body"])


def reconcile_whatsapp(db, source: str = "whatsapp") -> Dict[str, int]:
    """
    Merge every 'Export chat' conversation into the database chat with the same name (only
    when exactly one has it), then mark each .txt message that the database also has (same
    minute, same side, same text) as superseded by that message: one to one, never deleted.
    Safe to run any time; returns {merged, superseded}.
    """
    merged = superseded = 0
    with db._connect() as conn:
        targets: Dict[str, List[int]] = {}
        for conv_id, title in conn.execute(
                "SELECT id, title FROM msg_conversations WHERE source = ? AND conv_key NOT LIKE ? AND merged_into IS NULL",
                (source, TXT_PREFIX + "%")):
            if title:
                targets.setdefault(norm_text(title), []).append(conv_id)
        for conv_id, title in conn.execute(
                "SELECT id, title FROM msg_conversations WHERE source = ? AND conv_key LIKE ? AND merged_into IS NULL",
                (source, TXT_PREFIX + "%")).fetchall():
            match = targets.get(norm_text(title))
            if match and len(match) == 1:
                conn.execute("UPDATE msg_conversations SET merged_into = ? WHERE id = ?", (match[0], conv_id))
                conn.execute("UPDATE msg_messages SET conv_id = ? WHERE conv_id = ?", (match[0], conv_id))
                refresh_conversations(conn, [conv_id])
                merged += 1
        convs = [r[0] for r in conn.execute(
            "SELECT DISTINCT conv_id FROM msg_messages WHERE source = ? AND origin = 'whatsapp_txt' "
            "AND superseded_by IS NULL", (source,))]
        touched = set()
        for conv_id in convs:
            rows = conn.execute(
                "SELECT id, origin, sent_at_ms, ts_precision, is_me, body, kind, superseded_by FROM msg_messages "
                "WHERE conv_id = ? ORDER BY sent_at_ms, id", (conv_id,)).fetchall()
            cols = ("id", "origin", "sent_at_ms", "ts_precision", "is_me", "body", "kind", "superseded_by")
            rows = [dict(zip(cols, r)) for r in rows]
            exact: Dict[tuple, List[dict]] = {}
            used = {r["superseded_by"] for r in rows if r["superseded_by"]}
            for r in rows:
                if r["origin"] != "whatsapp_txt" and r["id"] not in used:
                    exact.setdefault((r["sent_at_ms"] // 60000, _txt_body(r)), []).append(r)
            if not exact:
                continue
            for r in rows:
                if r["origin"] != "whatsapp_txt" or r["superseded_by"]:
                    continue
                minute = r["sent_at_ms"] // 60000
                body = _txt_body(r)
                for m in (minute, minute - 1, minute + 1):
                    found = next((c for c in exact.get((m, body), [])
                                  if r["is_me"] is None or c["is_me"] is None or c["is_me"] == r["is_me"]), None)
                    if found:
                        exact[(m, body)].remove(found)
                        conn.execute("UPDATE msg_messages SET superseded_by = ? WHERE id = ?", (found["id"], r["id"]))
                        superseded += 1
                        touched.add(conv_id)
                        break
        if touched:
            refresh_conversations(conn, touched)
        if merged or superseded:
            state.bump_version(conn, source)
    return {"merged": merged, "superseded": superseded}


# ---------- reading ----------

def _conv_view(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"], "source": row["source"], "title": row["title"] or ", ".join(json.loads(row["participants"] or "[]")) or "Gesprek",
        "participants": json.loads(row["participants"] or "[]"), "is_group": bool(row["is_group"]),
        "first": row["first_ms"] / 1000 if row["first_ms"] is not None else None,
        "last": row["last_ms"] / 1000 if row["last_ms"] is not None else None,
        "count": row["message_count"],
    }


def _rows(conn, sql: str, args=()) -> List[Dict[str, Any]]:
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _source_filter(sources: Optional[Sequence[str]], column: str) -> Tuple[str, list]:
    if sources is None:
        return "", []
    if not sources:
        return " AND 0", []
    return f" AND {column} IN ({', '.join('?' * len(sources))})", list(sources)


def conversations(db, sources: Optional[Sequence[str]] = None, q: Optional[str] = None,
                  limit: int = 200, offset: int = 0) -> Dict[str, Any]:
    """Conversations, newest first (merged ones left out); q filters on title and participants."""
    where, args = _source_filter(sources, "source")
    if q:
        where += " AND (title LIKE ? ESCAPE '\\' OR participants LIKE ? ESCAPE '\\')"
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like]
    with db._connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM msg_conversations WHERE merged_into IS NULL AND message_count > 0{where}",
                             args).fetchone()[0]
        rows = _rows(conn, f"SELECT * FROM msg_conversations WHERE merged_into IS NULL AND message_count > 0{where} "
                           f"ORDER BY last_ms DESC, id DESC LIMIT ? OFFSET ?", args + [limit, offset])
    return {"total": total, "conversations": [_conv_view(r) for r in rows]}


def get_conversation(db, conv_id: int) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        rows = _rows(conn, "SELECT * FROM msg_conversations WHERE id = ?", (conv_id,))
    if not rows:
        return None
    row = rows[0]
    if row["merged_into"]:
        return get_conversation(db, row["merged_into"])
    return _conv_view(row)


def _message_view(row: Dict[str, Any]) -> Dict[str, Any]:
    attachments = json.loads(row["attachments"] or "[]")
    return {
        "id": row["id"], "sent_at": row["sent_at_ms"] / 1000, "tz": row["tz"], "precision": row["ts_precision"],
        "sender": row["sender"], "is_me": None if row["is_me"] is None else bool(row["is_me"]),
        "body": row["body"], "kind": row["kind"], "origin": row["origin"],
        "attachments": [{"index": i, "name": a.get("name"), "mime": a.get("mime"),
                         "preview": bool(a.get("raw_import_id") and a.get("member") and _is_image(a)),
                         "local": bool(a.get("local"))} for i, a in enumerate(attachments)],
        "reactions": [{"actor": r.get("actor"), "reaction": r.get("reaction")}
                      for r in json.loads(row["reactions"] or "[]") if isinstance(r, dict) and not r.get("removed")],
        "edited_at": row["edited_at_ms"] / 1000 if row.get("edited_at_ms") else None,
        "retracted_at": row["retracted_at_ms"] / 1000 if row.get("retracted_at_ms") else None,
        "edits": [{"at": e["at"] / 1000 if e.get("at") else None, "body": e.get("body")}
                  for e in json.loads(row.get("edits") or "[]") if isinstance(e, dict) and e.get("body")],
    }


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic")


def _is_image(attachment: Dict[str, Any]) -> bool:
    mime = attachment.get("mime") or ""
    name = (attachment.get("name") or "").lower()
    return mime.startswith("image/") or name.endswith(IMAGE_EXT)


def thread(db, conv_id: int, before: Optional[int] = None, around: Optional[int] = None,
           limit: int = 200) -> Dict[str, Any]:
    """
    Messages of a conversation in time order (superseded ones left out): the newest `limit`,
    or the ones before message id `before`, or about `limit` around message id `around`.
    has_more says whether older messages exist.
    """
    conv = get_conversation(db, conv_id)
    if conv is None:
        return {"conversation": None, "messages": [], "has_more": False}
    cid = conv["id"]
    base = "SELECT * FROM msg_messages WHERE conv_id = ? AND superseded_by IS NULL"
    with db._connect() as conn:
        if around is not None:
            pivot = conn.execute("SELECT sent_at_ms, id FROM msg_messages WHERE id = ? AND conv_id = ?", (around, cid)).fetchone()
            if pivot is None:
                around = None
        if around is not None:
            half = max(1, limit // 2)
            older = _rows(conn, base + " AND (sent_at_ms < ? OR (sent_at_ms = ? AND id < ?)) "
                          "ORDER BY sent_at_ms DESC, id DESC LIMIT ?", (cid, pivot[0], pivot[0], pivot[1], half))
            newer = _rows(conn, base + " AND (sent_at_ms > ? OR (sent_at_ms = ? AND id >= ?)) "
                          "ORDER BY sent_at_ms, id LIMIT ?", (cid, pivot[0], pivot[0], pivot[1], half + 1))
            rows = list(reversed(older)) + newer
            has_more = len(older) == half
            has_newer = len(newer) == half + 1
        else:
            if before is not None:
                pivot = conn.execute("SELECT sent_at_ms, id FROM msg_messages WHERE id = ?", (before,)).fetchone()
                pivot = pivot or (2 ** 62, 2 ** 62)
                rows = _rows(conn, base + " AND (sent_at_ms < ? OR (sent_at_ms = ? AND id < ?)) "
                             "ORDER BY sent_at_ms DESC, id DESC LIMIT ?", (cid, pivot[0], pivot[0], pivot[1], limit + 1))
            else:
                rows = _rows(conn, base + " ORDER BY sent_at_ms DESC, id DESC LIMIT ?", (cid, limit + 1))
            has_more = len(rows) > limit
            rows = list(reversed(rows[:limit]))
            has_newer = False
    return {"conversation": conv, "messages": [_message_view(r) for r in rows],
            "has_more": has_more, "has_newer": has_newer}


def get_attachment(db, message_id: int, index: int) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        row = conn.execute("SELECT attachments FROM msg_messages WHERE id = ?", (message_id,)).fetchone()
    if row is None or not row[0]:
        return None
    items = json.loads(row[0])
    return items[index] if 0 <= index < len(items) else None


def fts_query(text: str, any_word: bool = False) -> Optional[str]:
    """User text as an FTS5 query: every word as a quoted prefix ("foo"*), all of them (or any)."""
    words = [w for w in re.findall(r"\w+", unicodedata.normalize("NFC", text or "")) if w][:12]
    if not words:
        return None
    return (" OR " if any_word else " ").join(f'"{w}"*' for w in words)


def search(db, q: str, sources: Optional[Sequence[str]] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Messages matching q (all words, as prefixes), newest first, with a snippet around the hit."""
    query = fts_query(q)
    if query is None:
        return []
    where, args = _source_filter(sources, "m.source")
    with db._connect() as conn:
        rows = _rows(conn, f"""
            SELECT m.id, m.conv_id, m.source, m.sent_at_ms, m.sender, m.is_me,
                   snippet(msg_fts, 0, ?, ?, '…', 14) AS snippet, c.title, c.participants, c.merged_into
              FROM msg_fts JOIN msg_messages m ON m.id = msg_fts.rowid
              JOIN msg_conversations c ON c.id = m.conv_id
             WHERE msg_fts MATCH ? AND m.superseded_by IS NULL{where}
             ORDER BY m.sent_at_ms DESC LIMIT ?""", [HIT_START, HIT_END, query] + args + [limit])
    return [{"id": r["id"], "conversation": r["conv_id"], "source": r["source"], "sent_at": r["sent_at_ms"] / 1000,
             "sender": r["sender"], "is_me": None if r["is_me"] is None else bool(r["is_me"]),
             "snippet": r["snippet"] or "",
             "title": r["title"] or ", ".join(json.loads(r["participants"] or "[]")) or "Gesprek"} for r in rows]


def counts(db) -> Dict[str, Dict[str, int]]:
    """Per source: conversations and messages (without merged or superseded ones)."""
    with db._connect() as conn:
        rows = conn.execute("SELECT source, COUNT(*), SUM(message_count) FROM msg_conversations "
                            "WHERE merged_into IS NULL AND message_count > 0 GROUP BY source").fetchall()
    return {r[0]: {"conversations": r[1], "messages": r[2] or 0} for r in rows}


def coverage(db, source: Optional[str] = None, origins: Optional[Sequence[str]] = None,
             social_source: Optional[str] = None) -> Coverage:
    """What the messages of a source (or only some origins) and its social items cover."""
    records, first, last = 0, None, None
    with db._connect() as conn:
        if source or origins:
            where, args = "superseded_by IS NULL", []
            if source:
                where += " AND source = ?"
                args.append(source)
            if origins:
                where += f" AND origin IN ({', '.join('?' * len(origins))})"
                args += list(origins)
            n, lo, hi = conn.execute(f"SELECT COUNT(*), MIN(sent_at_ms), MAX(sent_at_ms) FROM msg_messages WHERE {where}",
                                     args).fetchone()
            records += n
            first, last = lo, hi
        if social_source:
            n, lo, hi = conn.execute("SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM social_items WHERE source = ?",
                                     (social_source,)).fetchone()
            records += n
            first = lo if first is None else (first if lo is None else min(first, lo))
            last = hi if last is None else (last if hi is None else max(last, hi))
    if not records:
        return Coverage()
    cov = Coverage(records=records, first=first / 1000, last=last / 1000)
    cov.segments = [(cov.first, cov.last, False)]
    return cov


# ---------- Pasha ----------

def pasha_sources(db) -> List[str]:
    """The message sources whose toggle 'Pasha mag deze berichten lezen' is on (off by default)."""
    return [c.id for c in message_sources() if state.get_settings(db, c.id).get(PASHA_SETTING) is True]


STOP_WORDS = {"wat", "wie", "waar", "wanneer", "hoe", "het", "een", "de", "en", "of", "ik", "je", "jij", "mij", "me",
              "mijn", "heb", "heeft", "had", "was", "zijn", "met", "van", "voor", "over", "die", "dat", "dit", "er",
              "niet", "nog", "ook", "aan", "op", "in", "te", "om", "naar", "zei", "zegt", "berichten", "bericht",
              "the", "and", "what", "who", "when", "did", "say"}


def assistant_context(db, question: str, limit: int = 8, max_len: int = 200) -> Optional[Dict[str, Any]]:
    """
    Messages Pasha may see for this question: only from sources the user switched on, the
    best FTS matches for the question's words. None when no source is switched on.
    """
    allowed = pasha_sources(db)
    if not allowed:
        return None
    words = [w for w in re.findall(r"\w+", question or "") if len(w) >= 3 and w.casefold() not in STOP_WORDS]
    hits: List[Dict[str, Any]] = []
    query = fts_query(" ".join(words), any_word=True) if words else None
    if query:
        where, args = _source_filter(allowed, "m.source")
        with db._connect() as conn:
            hits = _rows(conn, f"""
                SELECT m.sent_at_ms, m.source, m.sender, m.is_me, m.body, c.title
                  FROM msg_fts JOIN msg_messages m ON m.id = msg_fts.rowid
                  JOIN msg_conversations c ON c.id = m.conv_id
                 WHERE msg_fts MATCH ? AND m.superseded_by IS NULL{where}
                 ORDER BY bm25(msg_fts) LIMIT ?""", [query] + args + [limit])
    return {"sources": allowed, "messages": [{
        "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(h["sent_at_ms"] / 1000)),
        "source": h["source"], "conversation": h["title"] or "", "sender": "ik" if h["is_me"] else (h["sender"] or ""),
        "text": (h["body"] or "")[:max_len]} for h in hits]}
