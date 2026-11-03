"""
Berichten - where the other person of a chat can be found online (a profile link).

Instagram's export names people by their display name only ('Eliott Liberale'), never by
username. The username of a one-to-one chat comes from, in this order:
  1. a story of theirs you replied to in that chat: one of your own messages carries a link
     instagram.com/stories/<username>/... (not one of your own accounts);
  2. the chat's folder in the archived export (inbox/<name>_<thread id>): Instagram builds
     that name from the title, so it is a username only when it matches, dots and other
     signs left out, a link in the chat to a profile or story, or exactly one account you
     follow or that follows you (connections/followers_and_following/*.json, any export).
Without any there is no profile, only a web search for the name (search_url); a placeholder
name ('Instagram user', a deleted account) gets neither.
WhatsApp: a chat keyed by phone number (<digits>@s.whatsapp.net) opens at wa.me/<digits>.

Read-only, and nothing goes online here: the page opens a link only when the user clicks it.
The archived exports are read like the attachment previews (zip index and small JSON files),
once per export file and kept in memory.
"""
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote, quote_plus

from backend.datagatherer import raw_store, state
from backend.datagatherer.connectors.facebook import meta_account
from backend.datagatherer.formats.meta_dyi import PLACEHOLDER_NAMES

INSTAGRAM = "https://www.instagram.com/"
HANDLE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
STORY_LINK = re.compile(r"instagram\.com/stories/([A-Za-z0-9._]{1,30})/", re.I)
PROFILE_LINK = re.compile(r"instagram\.com/(?:stories/|_u/)?([A-Za-z0-9._]{1,30})", re.I)
CHAT_FOLDER = re.compile(r"(?:^|/)messages/(?:inbox|archived_threads|message_requests)/([^/]+)/[^/]+\.json$")
FOLLOW_FILE = re.compile(r"(?:^|/)followers_and_following/(?:following|followers)[^/]*\.json$")
MAX_JSON = 20 * 1024 * 1024
# A deleted or hidden account, also in the languages Instagram used for it in older exports
NO_PROFILE = PLACEHOLDER_NAMES | {"usuario de instagram", "utilisateur instagram", "instagram-nutzer"}


def squash(name: str) -> str:
    """'Eliott.Liberale' -> 'eliottliberale': how Instagram names a chat folder."""
    return re.sub(r"[^a-z0-9_]", "", name.casefold())


def _follow_names(value: Any) -> Set[str]:
    """The usernames in a following/followers file (old and new layouts)."""
    items = value
    if isinstance(value, dict):
        items = next((v for v in value.values() if isinstance(v, list)), [])
    out = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        names = [item.get("title")] + [s.get("value") for s in item.get("string_list_data") or [] if isinstance(s, dict)]
        out.update(n for n in names if isinstance(n, str) and HANDLE.match(n))
    return out


def _scan(names: List[str], read) -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    """(thread id -> folder name before the id, squashed username -> usernames)"""
    folders: Dict[str, str] = {}
    follows: Dict[str, Set[str]] = {}
    for name in names:
        m = CHAT_FOLDER.search(name)
        if m:
            head, sep, tail = m.group(1).rpartition("_")
            if sep and head and tail:
                folders.setdefault(tail, head.casefold())
        elif FOLLOW_FILE.search(name):
            data = read(name)
            try:
                value = json.loads(data) if data else None
            except ValueError:
                value = None
            for handle in _follow_names(value):
                follows.setdefault(squash(handle), set()).add(handle)
    return folders, follows


@lru_cache(maxsize=8)
def _export_index(path: str, is_folder: bool, size: int, mtime: float) -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    root = Path(path)
    try:
        if is_folder:
            names = [p.relative_to(root).as_posix() for pattern in ("**/messages/*/*/message_*.json",
                                                                     "**/followers_and_following/*.json")
                     for p in root.glob(pattern) if p.is_file() and not p.is_symlink()]

            def read(name):
                f = root / name
                return f.read_bytes() if f.stat().st_size <= MAX_JSON else None
            return _scan(names, read)
        with zipfile.ZipFile(root) as zf:
            infos = {i.filename: i for i in zf.infolist() if not i.is_dir()}

            def read(name):
                info = infos[name]
                return raw_store._read_member(zf, info) if info.file_size <= MAX_JSON else None
            return _scan(list(infos), read)
    except (OSError, zipfile.BadZipFile, raw_store.RawStoreError):
        return {}, {}


def export_index(raw: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    path = Path(raw["path"])
    try:
        st = path.stat()
    except OSError:
        return {}, {}
    return _export_index(str(path), bool(raw["is_folder"]), st.st_size, st.st_mtime)


def _instagram(db, conv: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        keys = [r[0] for r in conn.execute("SELECT conv_key FROM msg_conversations WHERE id = ? OR merged_into = ?",
                                           (conv["id"], conv["id"]))]
        linked = [(bool(r[0]), r[1]) for r in conn.execute("SELECT is_me, body FROM msg_messages WHERE conv_id = ? "
                                                         "AND body LIKE '%instagram.com/%'", (conv["id"],))]
        raw_ids = [r[0] for r in conn.execute("SELECT id FROM dg_raw_imports WHERE source_id = 'instagram'")]
    raws = [r for r in (state.get_raw(db, i) for i in raw_ids) if r]
    own = {a.casefold() for a in map(meta_account, raws) if a}

    # 1. Your reply to one of their stories
    seen: Dict[str, int] = {}
    for is_me, body in linked:
        for handle in STORY_LINK.findall(body or "") if is_me else ():
            if handle.casefold() not in own:
                seen[handle] = seen.get(handle, 0) + 1
    if seen:
        return {"handle": max(seen, key=seen.get), "how": "story"}

    # 2. The chat folder, when it is the (squashed) username in a link in the chat or of
    #    exactly one account you know
    heads: Set[str] = set()
    follows: Dict[str, Set[str]] = {}
    for raw in raws:
        folders, known = export_index(raw)
        heads.update(folders[k[2:]] for k in keys if k.startswith("t:") and k[2:] in folders)
        for squashed, handles in known.items():
            follows.setdefault(squashed, set()).update(handles)
    for head in heads:
        named = {h for _, body in linked for h in PROFILE_LINK.findall(body or "")
                 if squash(h) == head and h.casefold() not in own}
        if len(named) == 1:
            return {"handle": named.pop(), "how": "link"}
        if len(follows.get(head, ())) == 1:
            return {"handle": next(iter(follows[head])), "how": "follows"}
    return None


def profile(db, conv: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    {'url', 'handle', 'how', 'site'} for a one-to-one chat whose person can be found online,
    {'search_url', 'site'} when only a web search for the name is possible, else None.
    """
    if conv.get("is_group"):
        return None
    source = conv["source"]
    if source == "instagram":
        found = _instagram(db, conv)
        if found:
            return {**found, "site": "Instagram", "url": f"{INSTAGRAM}{quote(found['handle'])}/"}
        title = (conv.get("title") or "").strip()
        if not title or title.casefold() in NO_PROFILE:
            return None
        return {"site": "Instagram",
                "search_url": "https://www.google.com/search?q=" + quote_plus(f'site:instagram.com "{title}"')}
    if source == "whatsapp":
        with db._connect() as conn:
            row = conn.execute("SELECT conv_key FROM msg_conversations WHERE id = ?", (conv["id"],)).fetchone()
        m = re.fullmatch(r"(\d{6,15})@s\.whatsapp\.net", row[0] if row else "")
        if m:
            return {"site": "WhatsApp", "handle": f"+{m.group(1)}", "how": "number", "url": f"https://wa.me/{m.group(1)}"}
    return None
