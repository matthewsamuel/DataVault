"""
DataVault Dashboard - Tools of the assistant (Pasha)

The model answers in Dutch and may end its answer with <<ACTION: {json}>> tags. This module
is the only place where such a tag gets a meaning on the server:

- parse_actions() reads the tags and checks every one against an allowlist (TOOLS): an
  unknown action, an unknown or badly typed argument and an over-long text are dropped.
- Read-only tools (search, filters, on this day, places, series, jobs, recovery) run
  at once and answer a result for the chat: a strip of photos plus a link to the screen.
- Tools that would MOVE files (propose_move, propose_keep_best) and the export only
  PROPOSE: they make a pending action (PendingStore: an unguessable id, kept in memory,
  valid for PENDING_TTL seconds, usable once) and the chat shows a card with what would
  happen. Only POST /api/chat/actions/<id>/confirm, which takes nothing but the id, does
  the work, through the same code paths as the screens (so it is in the recovery center
  with an undo). The text of the model never reaches a path: files and folders are found
  in the library or the folder list of the vault, and every path goes through
  paths.resolve_in_vault. There is no tool that deletes.
"""
import json
import re
import secrets
import threading
import time
import unicodedata
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException

from backend import library_filters, media_index, near_dups, places, semantic

TAG = re.compile(r"<<ACTION:?\s*(\{[\s\S]*?\})\s*>>")
STRIP_FROM = re.compile(r"<<ACTION[\s\S]*$")
PENDING_TTL = 600.0
PENDING_MAX = 20
STRIP_ITEMS = 12          # photos in a result strip
CARD_ITEMS = 6            # first items listed on a confirmation card
MOVE_MAX = 50
SERIES_MAX = 10
EXPORT_MAX = 1000
SCAN_MAX = 20000          # points looked at for a country

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f<>  ]")


class ToolError(Exception):
    """A clear Dutch message for the chat (a prerequisite is missing, nothing found, ...)."""


# ---------- argument checks ----------

def _text(max_len: int = 120) -> Callable[[Any], str]:
    def check(v):
        if not isinstance(v, str):
            raise ValueError
        v = _CONTROL.sub("", unicodedata.normalize("NFC", v)).strip()[:max_len]
        if not v:
            raise ValueError
        return v
    return check


def _int(lo: int, hi: int) -> Callable[[Any], int]:
    def check(v):
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            v = int(v)
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise ValueError
        return v
    return check


def _day(v):
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise ValueError
    date.fromisoformat(v)
    return v


def _enum(*values: str) -> Callable[[Any], str]:
    def check(v):
        if v not in values:
            raise ValueError
        return v
    return check


def _bool(v):
    if not isinstance(v, bool):
        raise ValueError
    return v


FILTER_ARGS = {
    "kind": _enum("photo", "video"), "year": _int(1800, 2200), "month": _int(1, 12),
    "date_from": _day, "date_to": _day, "camera_contains": _text(60), "folder": _text(200),
    "min_rating": _int(-10 ** 6, 10 ** 6), "has_gps": _bool, "orientation": _enum("landscape", "portrait", "square"),
    "period": _enum("this_year", "last_year", "this_month", "last_30_days"),
}

# action -> (kind, {argument: check}, required arguments). kind: 'read' runs now, 'propose' makes a pending action
TOOLS: Dict[str, Tuple[str, Dict[str, Callable], Tuple[str, ...]]] = {
    "find_photos": ("read", {"query": _text(200), "year": _int(1800, 2200), "kind": _enum("photo", "video")}, ("query",)),
    "library_filter": ("read", dict(FILTER_ARGS), ()),
    "open_smart_album": ("read", {"name": _text(80)}, ("name",)),
    "on_this_day": ("read", {}, ()),
    "photos_at_place": ("read", {"place": _text(80), "year": _int(1800, 2200)}, ("place",)),
    "series_overview": ("read", {}, ()),
    "job_status": ("read", {}, ()),
    "recovery_summary": ("read", {}, ()),
    "propose_move": ("propose", {"query": _text(120), "path": _text(1000), "folder": _text(300),
                                 "limit": _int(1, MOVE_MAX)}, ("folder",)),
    "propose_keep_best": ("propose", {"count": _int(1, SERIES_MAX)}, ()),
    "propose_export": ("propose", dict(FILTER_ARGS, query=_text(200)), ()),
}


def clean_action(data: Any) -> Optional[Dict[str, Any]]:
    """One decoded tag -> {'action', args} when it is an allowlisted action with valid arguments, else None."""
    if not isinstance(data, dict) or data.get("action") not in TOOLS:
        return None
    _, spec, required = TOOLS[data["action"]]
    out: Dict[str, Any] = {"action": data["action"]}
    for key, check in spec.items():
        if key in data and data[key] is not None:
            try:
                out[key] = check(data[key])
            except (ValueError, TypeError):
                return None
    if any(r not in out for r in required):
        return None
    return out


def parse_actions(text: str) -> List[Dict[str, Any]]:
    """The valid server-side actions in a model answer, in order (at most 2)."""
    found = []
    for match in TAG.finditer(text or ""):
        try:
            action = clean_action(json.loads(match.group(1)))
        except ValueError:
            continue
        if action:
            found.append(action)
    return found[:2]


# ---------- pending actions ----------

class PendingStore:
    """Proposed file-moving actions waiting for the user's click: id -> record, expiring and single-use."""

    def __init__(self, ttl: float = PENDING_TTL):
        self.ttl = ttl
        self._items: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for key in [k for k, v in self._items.items() if v["expires"] <= now]:
            del self._items[key]

    def add(self, kind: str, title: str, payload: Dict[str, Any]) -> Tuple[str, float]:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            while len(self._items) >= PENDING_MAX:
                del self._items[min(self._items, key=lambda k: self._items[k]["expires"])]
            key = secrets.token_urlsafe(24)
            self._items[key] = {"kind": kind, "title": title, "payload": payload, "expires": now + self.ttl}
        return key, self.ttl

    def take(self, key: Any) -> Dict[str, Any]:
        """The record, removed: an id works once. 404 for an unknown, used or expired id."""
        with self._lock:
            self._purge(time.monotonic())
            record = self._items.pop(key, None) if isinstance(key, str) else None
        if record is None:
            raise HTTPException(status_code=404, detail="Deze actie bestaat niet (meer), is verlopen of is al uitgevoerd. "
                                                        "Vraag het Pasha opnieuw.")
        return record

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


pending = PendingStore()


# ---------- results ----------

def _strip_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": row["id"], "path": row["path"], "name": row["name"], "type": row["kind"],
            "timestamp": row.get("taken")}


def _items_by_ids(db, ids: List[int]) -> List[Dict[str, Any]]:
    rows = semantic._rows_by_id(db, ids)
    return [_strip_item(rows[i]) for i in ids if i in rows]


def _need_library(db) -> None:
    if not media_index.usable(db):
        raise ToolError("De bibliotheek wordt nog ingelezen. Probeer het zo meteen opnieuw.")


def _spec(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return library_filters.clean({k: v for k, v in args.items() if k in FILTER_ARGS})
    except library_filters.FilterError as e:
        raise ToolError(str(e))


def _library_result(ctx, title: str, spec: Dict[str, Any], empty: str) -> Dict[str, Any]:
    _need_library(ctx.db)
    try:
        query = library_filters.to_query(spec, ctx.config)
    except library_filters.FilterError as e:
        raise ToolError(str(e))
    except HTTPException:
        raise ToolError("Die map ligt niet in het archief.")
    total, _ = media_index.selection_info(ctx.db, query)
    if not total:
        raise ToolError(empty)
    rows = media_index.query(ctx.db, query, sort="-taken", limit=STRIP_ITEMS)
    return {"title": f"{total} {'bestand' if total == 1 else 'bestanden'}: {title}", "total": total,
            "items": [_strip_item(r) for r in rows],
            "link": {"view": "albums", "filters": spec, "label": "Alles bekijken"}}


class Context:
    def __init__(self, config, db, manager=None):
        self.config, self.db, self.manager = config, db, manager


def tool_find_photos(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    _need_library(ctx.db)
    filters: Dict[str, Any] = {"kind": [a["kind"]] if "kind" in a else ["photo", "video"]}
    if "year" in a:
        filters["date_from"] = f"{a['year']}-01-01"
        filters["date_to"] = f"{a['year'] + 1}-01-01"
    try:
        rows = semantic.search_photos(ctx.db, ctx.config, a["query"], filters, limit=STRIP_ITEMS)
    except semantic.NotReady as e:
        raise ToolError(f"Slim zoeken kan nog niet: {e}")
    if not rows:
        raise ToolError(f"Ik vond geen foto's bij \"{a['query']}\".")
    counts = semantic.counts(ctx.db)
    note = f" (nog {counts['pending']} foto's worden herkend)" if counts.get("pending") else ""
    return {"title": f"Foto's bij \"{a['query']}\"{note}", "total": len(rows), "items": [_strip_item(r) for r in rows],
            "link": {"view": "search", "query": a["query"], "label": "Meer in Slim zoeken"}}


def tool_library_filter(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    spec = _spec(a)
    if not spec:
        raise ToolError("Zeg erbij wat je wilt zien, bijvoorbeeld een jaar, een camera of een map.")
    return _library_result(ctx, "je selectie", spec, "Daar vond ik niets bij in je bibliotheek.")


def tool_open_smart_album(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    from backend import smart_albums
    wanted = places.fold(a["name"])
    albums = smart_albums.list_albums(ctx.db, with_hidden=True)
    match = next((x for x in albums if places.fold(x["name"]) == wanted), None) \
        or next((x for x in albums if wanted in places.fold(x["name"])), None)
    if match is None:
        names = ", ".join(x["name"] for x in albums[:8])
        raise ToolError(f"Ik ken geen album \"{a['name']}\"." + (f" Je hebt: {names}." if names else ""))
    result = _library_result(ctx, f"album {match['name']}", match["filters"], f"Het album {match['name']} is nog leeg.")
    result["link"] = {"view": "albums", "album": match["id"], "label": "Album openen"}
    return result


def tool_on_this_day(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    from backend.routes import albums as albums_route
    _need_library(ctx.db)
    today = date.today()
    filters = {"kind": ["photo", "video"], "capture_date": "known", "month_days": [(today.month, today.day)],
               "date_to": time.mktime((today.year, 1, 1, 0, 0, 0, 0, 0, -1))}
    groups = media_index.year_groups(ctx.db, filters, per_year=4)
    if not groups:
        raise ToolError("Op deze dag in eerdere jaren heb ik geen foto's gevonden.")
    items: List[Dict[str, Any]] = []
    for _, _, rows in groups:
        items.extend(_strip_item(r) for r in rows[:2])
    total = sum(n for _, n, _ in groups)
    return {"title": f"Op deze dag ({today.day}-{today.month}): {total} foto's in {len(groups)} jaar",
            "total": total, "items": items[:STRIP_ITEMS],
            "link": {"view": "otd", "month": today.month, "day": today.day, "label": "Foto op deze dag openen"}}


def _gps_rows(ctx: Context, place: places.Place, year: Optional[int]) -> Tuple[List[int], bool]:
    base: Dict[str, Any] = {"kind": ["photo", "video"]}
    if year:
        base["year"] = year
    filters = {**library_filters.to_query(base, ctx.config), "has_gps": True, "bbox": place.bbox}
    where, args = media_index._where(filters)
    with ctx.db._connect() as conn:
        rows = conn.execute(f"SELECT m.id, m.lat, m.lon FROM media m WHERE {where} ORDER BY m.taken DESC LIMIT ?",
                            (*args, SCAN_MAX + 1)).fetchall()
    capped = len(rows) > SCAN_MAX
    return [r[0] for r in rows[:SCAN_MAX] if place.contains(r[1], r[2])], capped


def tool_photos_at_place(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    _need_library(ctx.db)
    place = places.find(a["place"])
    if place is None:
        raise ToolError(f"Ik ken de plaats \"{a['place']}\" niet. Probeer een land of een grotere stad.")
    ids, capped = _gps_rows(ctx, place, a.get("year"))
    if not ids:
        with ctx.db._connect() as conn:
            anywhere = conn.execute("SELECT COUNT(*) FROM media WHERE lat IS NOT NULL AND missing = 0").fetchone()[0]
        if not anywhere:
            raise ToolError("Er zijn nog geen foto's met een locatie in je bibliotheek; zonder GPS kan ik geen plaats vinden.")
        raise ToolError(f"Geen foto's met een locatie in {place.name}.")
    where = "in" if place.kind == "country" else "bij"
    return {"title": f"{len(ids)}{'+' if capped else ''} foto's {where} {place.name}", "total": len(ids),
            "items": _items_by_ids(ctx.db, ids[:STRIP_ITEMS]),
            "link": {"view": "map", "bounds": place.bbox, "label": "Op de kaart tonen"}}


def tool_series_overview(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    _need_library(ctx.db)
    info = near_dups.counts(ctx.db)
    if not info.get("analysed"):
        raise ToolError("De reeksen zijn nog niet berekend. Open Duplicaten, kies Beste uit reeks en start de vergelijking.")
    data = near_dups.page(ctx.db, near_dups.DEFAULT_STRICTNESS, "size", 0, 6)
    series = data["items"]
    if not series:
        raise ToolError("Ik vond geen reeksen van bijna dezelfde foto's.")
    items = []
    for s in series[:STRIP_ITEMS]:
        best = next((m for m in s["members"] if m["id"] == s["best"]), s["members"][0])
        items.append({"id": best["id"], "path": best["path"], "name": best["name"], "type": "photo",
                      "caption": f"{s['count']} foto's"})
    return {"title": f"{data['total']} reeksen, {data.get('photos', 0)} foto's; de grootste hieronder (beste foto van elke reeks)",
            "total": data["total"], "items": items,
            "link": {"view": "series", "label": "Reeksen bekijken"}}


def tool_job_status(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    if ctx.manager is None:
        raise ToolError("Achtergrondtaken zijn niet beschikbaar.")
    jobs = ctx.manager.list(limit=10)
    active = [j for j in jobs if j["status"] in ("queued", "running", "paused", "waiting_for_vault")]
    lines = []
    for j in (active or jobs)[:5]:
        pct = f" {round(j['progress'] * 100)}%" if isinstance(j.get("progress"), (int, float)) and j["status"] == "running" else ""
        lines.append(f"{j.get('title') or j['type']}: {j['status']}{pct}")
    if not lines:
        return {"title": "Er zijn nog geen achtergrondtaken geweest.", "lines": []}
    head = f"{len(active)} taak bezig" if active else "Geen taak bezig; laatste taken"
    return {"title": head, "lines": lines}


def tool_recovery_summary(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    from backend import recovery
    data = recovery.entries(offset=0, limit=5)
    if not data["total"]:
        return {"title": "Er is nog niets verplaatst dat je kunt terugzetten.", "lines": [],
                "link": {"view": "recovery", "label": "Herstelcentrum openen"}}
    lines = [str(e.get("title") or e.get("kind")) for e in data["entries"]]
    return {"title": f"{data['total']} acties in het Herstelcentrum; de laatste", "lines": lines,
            "link": {"view": "recovery", "label": "Herstelcentrum openen"}}


READ_TOOLS: Dict[str, Callable[[Context, Dict[str, Any]], Dict[str, Any]]] = {
    "find_photos": tool_find_photos, "library_filter": tool_library_filter, "open_smart_album": tool_open_smart_album,
    "on_this_day": tool_on_this_day, "photos_at_place": tool_photos_at_place, "series_overview": tool_series_overview,
    "job_status": tool_job_status, "recovery_summary": tool_recovery_summary,
}


# ---------- proposals ----------

def _folder_for(ctx: Context, name: str):
    """The existing folder in the vault that name means (a path inside the vault, or a unique folder name)."""
    from backend.paths import resolve_in_vault
    root = ctx.config.VAULT_ROOT
    candidates = []
    if name.startswith("/") or ".." in name.split("/"):
        try:
            return resolve_in_vault(name, ctx.config, folder=True)
        except HTTPException:
            raise ToolError("Die map ligt niet in het archief; daar verplaats ik niets naartoe.")
    try:
        direct = resolve_in_vault(str(root / name), ctx.config, folder=True)
        if direct.is_dir():
            return direct
    except HTTPException:
        pass
    wanted = places.fold(name.rstrip("/").split("/")[-1])
    with ctx.db._connect() as conn:
        folders = [r[0] for r in conn.execute("SELECT DISTINCT folder FROM media WHERE missing = 0")]
    seen = set()
    for folder in folders:
        parts = folder.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub not in seen and places.fold(parts[i - 1]) == wanted:
                seen.add(sub)
    for sub in sorted(seen):
        try:
            found = resolve_in_vault(sub, ctx.config, folder=True)
        except HTTPException:
            continue
        if found.is_dir():
            candidates.append(found)
    if not candidates:
        raise ToolError(f"Ik vond de map \"{name}\" niet in het archief. Ik maak geen nieuwe mappen aan.")
    if len(candidates) > 1:
        raise ToolError(f"Er zijn meerdere mappen die \"{name}\" heten; noem het hele pad.")
    return candidates[0]


def _rel(ctx: Context, path) -> str:
    try:
        return str(path.relative_to(ctx.config.VAULT_ROOT)) or "."
    except ValueError:
        return str(path)


def propose_move(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    from backend.file_ops import same_folder
    from backend.paths import resolve_in_vault
    _need_library(ctx.db)
    dest = _folder_for(ctx, a["folder"])
    sources = []
    if "path" in a:
        try:
            src = resolve_in_vault(a["path"], ctx.config)
        except HTTPException:
            raise ToolError("Dat bestand ligt niet in het archief; daar blijf ik van af.")
        if src.is_symlink() or not src.is_file():
            raise ToolError("Dat bestand bestaat niet (meer).")
        sources.append(src)
    elif "query" in a:
        result = semantic.search_filename(ctx.db, a["query"], {}, 0, a.get("limit", MOVE_MAX))
        for row in result["items"]:
            try:
                sources.append(resolve_in_vault(row["path"], ctx.config))
            except HTTPException:
                continue
    else:
        raise ToolError("Zeg welk bestand (of welke bestandsnaam) naar de map moet.")
    sources = [s for s in sources if not same_folder(dest, s.parent)][:MOVE_MAX]
    if not sources:
        raise ToolError("Ik vond geen bestanden om te verplaatsen (of ze staan al in die map).")
    moves = [{"src": str(s), "dst_folder": str(dest)} for s in sources]
    title = f"{len(moves)} {'bestand' if len(moves) == 1 else 'bestanden'} verplaatsen naar {_rel(ctx, dest)}"
    return _pending(ctx, "move", title, {"moves": moves},
                    [{"path": m["src"], "name": s.name, "to": _rel(ctx, dest)} for m, s in zip(moves, sources)],
                    len(moves))


def propose_keep_best(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    _need_library(ctx.db)
    if not near_dups.counts(ctx.db).get("analysed"):
        raise ToolError("De reeksen zijn nog niet berekend. Open Duplicaten, kies Beste uit reeks en start de vergelijking.")
    data = near_dups.page(ctx.db, near_dups.DEFAULT_STRICTNESS, "size", 0, a.get("count", 1))
    groups, preview, loose = [], [], 0
    for s in data["items"]:
        keep = [s["best"]]
        losers = [m for m in s["members"] if m["id"] != s["best"]]
        groups.append({"members": [m["id"] for m in s["members"]], "keep": keep})
        loose += len(losers)
        for m in losers:
            preview.append({"path": m["path"], "name": m["name"], "to": "RAW_DUPLICATES/near-duplicates"})
    if not groups:
        raise ToolError("Ik vond geen reeksen om op te ruimen.")
    title = (f"In {len(groups)} {'reeks' if len(groups) == 1 else 'reeksen'} de beste foto houden en "
             f"{loose} andere naar RAW_DUPLICATES/near-duplicates verplaatsen")
    return _pending(ctx, "keep_best", title, {"groups": groups}, preview, loose)


def propose_export(ctx: Context, a: Dict[str, Any]) -> Dict[str, Any]:
    _need_library(ctx.db)
    if "query" in a:
        try:
            rows = semantic.search_photos(ctx.db, ctx.config, a["query"], {"kind": ["photo", "video"]}, limit=EXPORT_MAX)
        except semantic.NotReady as e:
            raise ToolError(f"Slim zoeken kan nog niet: {e}")
        paths = [r["path"] for r in rows]
    else:
        spec = _spec({k: v for k, v in a.items() if k in FILTER_ARGS})
        if not spec:
            raise ToolError("Zeg wat er geëxporteerd moet worden, bijvoorbeeld een jaar of een map.")
        try:
            query = library_filters.to_query(spec, ctx.config)
        except (library_filters.FilterError, HTTPException):
            raise ToolError("Die selectie kan ik niet gebruiken.")
        paths = [r["path"] for r in media_index.query(ctx.db, query, sort="-taken", limit=EXPORT_MAX)]
    if not paths:
        raise ToolError("Er is niets om te exporteren bij deze vraag.")
    title = f"{len(paths)} {'bestand' if len(paths) == 1 else 'bestanden'} exporteren (het exportvenster opent; daar kies je de map)"
    preview = [{"path": p, "name": p.rsplit("/", 1)[-1], "to": "export"} for p in paths]
    return _pending(ctx, "export", title, {"paths": paths}, preview, len(paths))


PROPOSE_TOOLS = {"propose_move": propose_move, "propose_keep_best": propose_keep_best, "propose_export": propose_export}


def _pending(ctx: Context, kind: str, title: str, payload: Dict[str, Any], preview: List[Dict[str, Any]],
             count: int) -> Dict[str, Any]:
    key, ttl = pending.add(kind, title, payload)
    return {"pending": {"id": key, "kind": kind, "title": title, "count": count, "expires_in": int(ttl),
                        "items": preview[:CARD_ITEMS], "more": max(0, len(preview) - CARD_ITEMS)}}


# ---------- running ----------

def run_action(ctx: Context, action: Dict[str, Any]) -> Dict[str, Any]:
    """The event for one validated action: {'tool', 'result': {...}} / {'tool', 'pending': {...}} / {'tool', 'error'}."""
    name = action["action"]
    try:
        if name in READ_TOOLS:
            return {"tool": name, "result": READ_TOOLS[name](ctx, action)}
        proposal = PROPOSE_TOOLS[name](ctx, action)
        return {"tool": name, **proposal}
    except ToolError as e:
        return {"tool": name, "error": str(e)}
    except Exception as e:   # never a stack trace in the chat
        print(f"Pasha-tool {name} mislukt: {e!r}")
        return {"tool": name, "error": "Dat lukte niet. Probeer het nog eens of gebruik het scherm zelf."}


# ---------- confirm ----------

def confirm(ctx: Context, action_id: Any) -> Dict[str, Any]:
    """Do a pending action (once). Everything goes through the code of the screens, so it can be undone."""
    record = pending.take(action_id)
    kind, payload = record["kind"], record["payload"]
    if kind == "move":
        return _do_move(ctx, payload)
    if kind == "keep_best":
        return _do_keep_best(ctx, payload)
    if kind == "export":
        rows = []
        for chunk_start in range(0, len(payload["paths"]), 200):
            chunk = payload["paths"][chunk_start:chunk_start + 200]
            with ctx.db._connect() as conn:
                found = conn.execute(f"SELECT id FROM media WHERE missing = 0 AND path IN ({','.join('?' * len(chunk))})",
                                     chunk).fetchall()
            rows.extend(r[0] for r in found)
        items = _items_by_ids(ctx.db, rows)
        from backend.routes.albums import item as album_item
        full = semantic._rows_by_id(ctx.db, rows)
        return {"kind": "export", "open_export": [album_item(full[i]) for i in rows if i in full],
                "message": f"Het exportvenster opent met {len(items)} bestanden."}
    raise HTTPException(status_code=400, detail="Onbekende actie")


def _do_move(ctx: Context, payload: Dict[str, Any]) -> Dict[str, Any]:
    """The same steps as POST /api/move-file (resolve in the vault, lock, move_no_overwrite, record),
    with all files of the proposal in ONE recovery action."""
    from backend import recovery
    from backend.file_ops import ARCHIVE_LOCK, FileOperationError, move_no_overwrite, same_folder
    from backend.paths import resolve_in_vault
    moved, failed = 0, []
    recorder = recovery.ActionRecorder(ctx.db, "viewer_move",
                                       f"Pasha: {len(payload['moves'])} verplaatst naar "
                                       f"{_rel(ctx, resolve_in_vault(payload['moves'][0]['dst_folder'], ctx.config, folder=True))}")
    for m in payload["moves"]:
        try:
            src = resolve_in_vault(m["src"], ctx.config)
            dest = resolve_in_vault(m["dst_folder"], ctx.config, folder=True)
            with ARCHIVE_LOCK:
                if src.is_symlink() or not src.is_file():
                    raise ToolError("Het bestand staat niet meer op zijn plek")
                if not dest.is_dir():
                    raise ToolError("De map bestaat niet meer")
                if same_folder(dest, src.parent):
                    raise ToolError("Het bestand staat al in deze map")
                recorder.folder = recorder.folder or str(src.parent)
                dst = move_no_overwrite(src, dest / src.name)
                recorder.add(src, dst)
            moved += 1
        except HTTPException as e:
            failed.append({"path": m["src"], "reason": str(e.detail)})
        except (ToolError, FileOperationError, OSError) as e:
            failed.append({"path": m["src"], "reason": str(e)})
    return {"kind": "move", "moved": moved, "failed": failed, "action_id": recorder.action_id,
            "message": f"{moved} verplaatst" + (f", {len(failed)} niet" if failed else "")
                       + ". Terugzetten kan in het Herstelcentrum."}


def _do_keep_best(ctx: Context, payload: Dict[str, Any]) -> Dict[str, Any]:
    moved, failed, actions = 0, [], []
    for g in payload["groups"]:
        try:
            r = near_dups.keep_best(ctx.db, ctx.config, g["members"], g["keep"])
        except near_dups.KeepError as e:
            failed.append({"reason": str(e)})
            continue
        moved += len(r["moved"])
        failed.extend({"path": f.get("path"), "reason": f["reason"]} for f in r["failed"])
        if r["action_id"]:
            actions.append(r["action_id"])
    return {"kind": "keep_best", "moved": moved, "failed": failed, "actions": actions,
            "message": f"{moved} foto's verplaatst" + (f", {len(failed)} niet" if failed else "") + ". Terugzetten kan in het Herstelcentrum."}
