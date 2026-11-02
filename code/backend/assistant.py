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
import ast
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

# qwen2.5:3b mostly writes <<ACTION: {...}>>, but sometimes <|ACTION: {...}|>, <<ACTION: "job_status">>
# (only the name) or just the bare JSON object
TAG = re.compile(r'(?:<<|<\|)\s*ACTION:?\s*(\{[\s\S]*?\}|"[A-Za-z_]+")\s*(?:>>|\|>)')
BARE = re.compile(r'\{[^{}]*"action"\s*:[^{}]*\}')
UNFINISHED = re.compile(r'(?:<<|<\|)\s*ACTION[\s\S]*$|\{\s*"action"[^}]*$')
PENDING_TTL = 600.0
PENDING_MAX = 20
STRIP_ITEMS = 12          # photos in a result strip
CARD_ITEMS = 6            # first items listed on a confirmation card
MOVE_MAX = 50
SERIES_MAX = 10
EXPORT_MAX = 1000
SCAN_MAX = 20000          # points looked at for a country

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f<>  ]")


NOT_RUN = ("Dat kon ik niet uitvoeren. Probeer het anders te vragen, bijvoorbeeld \"foto's uit 2019\" "
           "of \"foto's van het strand\".")


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
        # An empty value is a field the model left blank ("date_from": ""), not a wrong one
        if key in data and data[key] is not None and data[key] != "":
            try:
                out[key] = check(data[key])
            except (ValueError, TypeError):
                return None
    if any(r not in out for r in required):
        return None
    return out


def strip_actions(text: str) -> str:
    """The answer without its action tags or bare action objects (what the user reads)."""
    text = TAG.sub("", _without_text_calls(text or ""))
    text = BARE.sub("", text)
    return UNFINISHED.sub("", text).strip()


def _decode(raw: str) -> Any:
    """One tag's JSON; a stray extra '}' or text after the object (a common slip of the model) is ignored."""
    try:
        return json.loads(raw)
    except ValueError:
        pass
    try:
        return json.JSONDecoder().raw_decode(raw.strip())[0]
    except ValueError:
        return None


def _candidates(text: str) -> List[str]:
    return [m.group(1) for m in TAG.finditer(text)] or [m.group(0) for m in BARE.finditer(text)]


def _date_filter(value: Any) -> Optional[Dict[str, Any]]:
    """'2019' / '2019-07' / '2019-07-14' -> library_filter arguments, else None."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re.fullmatch(r"\d{4}", value):
        return {"action": "library_filter", "year": value}
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", value)
    if m:
        return {"action": "library_filter", "year": m.group(1), "month": m.group(2)}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return {"action": "library_filter", "date_from": value, "date_to": value}
    return None


def _near_miss(data: Any) -> Optional[Dict[str, Any]]:
    """
    An action the model almost got right, rewritten to the real one (then checked like any other):
    find_photos with a date or only a year, show_photos with a year or month, a file-name search for a year.
    """
    if not isinstance(data, dict):
        return None
    name = data.get("action")
    if name == "find_photos" and not data.get("query"):
        for key in ("date", "day", "year"):
            mapped = _date_filter(data.get(key))
            if mapped:
                if data.get("kind"):
                    mapped["kind"] = data["kind"]
                return mapped
    if name == "show_photos" and not (isinstance(data.get("date"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["date"])):
        return _date_filter(data.get("date"))
    if name in ("search_photos", "find_photos"):
        return _date_filter(data.get("query"))
    return None


# Actions the chat screen runs itself (app.js CHAT_ACTIONS): the server leaves them to the page
CHAT_VIEWS = ("home", "viewer", "duplicates", "spotify", "shows", "keurer", "timeline")
CLIENT_ACTIONS: Dict[str, Dict[str, Callable]] = {
    "open_tab": {"target": _enum(*CHAT_VIEWS)},
    "scan_duplicates": {},
    "open_folder": {"target": _text(255)},
    "show_photos": {"date": _day},
    "search_photos": {"query": _text(100)},
    "open_file": {"path": _text(4096)},
}


def client_action(data: Any) -> Optional[Dict[str, Any]]:
    """A valid action for the chat screen itself ({'action', args}), else None."""
    if not isinstance(data, dict) or data.get("action") not in CLIENT_ACTIONS:
        return None
    out: Dict[str, Any] = {"action": data["action"]}
    for key, check in CLIENT_ACTIONS[data["action"]].items():
        value = data.get(key)
        if isinstance(value, str) and value != value.strip():
            return None
        try:
            out[key] = check(value)
        except (ValueError, TypeError):
            return None
        if out[key] != value:      # the page checks the raw value: only pass what it will accept as is
            return None
    return out


# ---------- Ollama's own tool calling ----------
#
# The same actions, offered as Ollama 'tools' (the 'tools' field of /api/chat). The model then
# answers with message.tool_calls instead of a <<ACTION>> tag; every call is turned into the same
# {'action', ...} object and goes through the same allowlist (clean_action, _near_miss,
# client_action) as a tag. Nothing here runs anything, and a proposal still only makes a card.
# Which format is the default, and why, is measured in docs/PASHA_TOOLS.md.

_STR, _INT, _BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}


def _p(base: Dict[str, Any], description: str, **extra) -> Dict[str, Any]:
    return {**base, "description": description, **extra}


_FILTER_PROPS = {
    "year": _p(_INT, "jaar, bijv. 2019"),
    "month": _p(_INT, "maand 1-12 (alleen met year)"),
    "kind": _p(_STR, "alleen foto's of alleen video's", enum=["photo", "video"]),
    "camera_contains": _p(_STR, "deel van de cameranaam, bijv. iPhone"),
    "folder": _p(_STR, "mapnaam"),
    "min_rating": _p(_INT, "minimale score (1000 = neutraal)"),
    "has_gps": _p(_BOOL, "alleen met locatie"),
    "orientation": _p(_STR, "liggend, staand of vierkant", enum=["landscape", "portrait", "square"]),
    "date_from": _p(_STR, "vanaf datum YYYY-MM-DD"),
    "date_to": _p(_STR, "tot en met datum YYYY-MM-DD"),
    "period": _p(_STR, "een vaste periode", enum=["this_year", "last_year", "this_month", "last_30_days"]),
}

# name -> (description, properties, required). Only names of TOOLS or CLIENT_ACTIONS (a test checks it).
# Not offered: show_photos (one day). As a tool the model took it for whole months ('juli 2019' ->
# 2019-07-01); a day is library_filter with date_from = date_to. As a tag it still works.
_NATIVE: Dict[str, Tuple[str, Dict[str, Any], Tuple[str, ...]]] = {
    "find_photos": ("Foto's zoeken op wat erop staat (strand, hond, verjaardagstaart, zonsondergang).",
                    {"query": _p(_STR, "wat er op de foto staat, in de woorden van de gebruiker"),
                     "year": _p(_INT, "jaar, alleen als de gebruiker het noemt"),
                     "kind": _FILTER_PROPS["kind"]}, ("query",)),
    "library_filter": ("Bestanden tonen op jaar, maand (juli 2019: year en month), periode, soort, camera, map, "
                       "score, stand of locatie. "
                       "Niet voor wat er op de foto staat en niet voor plaatsen.", dict(_FILTER_PROPS), ()),
    "open_smart_album": ("Een bestaand slim album openen op naam.", {"name": _p(_STR, "naam van het album")}, ("name",)),
    "on_this_day": ("Foto's van vandaag in eerdere jaren.", {}, ()),
    "photos_at_place": ("Foto's gemaakt in een stad of land (een echte plaatsnaam, zoals Parijs of Spanje).",
                        {"place": _p(_STR, "stad of land"), "year": _p(_INT, "jaar, alleen als genoemd")}, ("place",)),
    "series_overview": ("Overzicht van reeksen bijna-dezelfde foto's.", {}, ()),
    "job_status": ("Welke achtergrondtaken er lopen.", {}, ()),
    "recovery_summary": ("De laatste verplaatsingen en wat terug te zetten is.", {}, ()),
    "search_photos": ("Een bestand zoeken op bestandsnaam, bijv. IMG_1234.",
                      {"query": _p(_STR, "(deel van de) bestandsnaam")}, ("query",)),
    "open_folder": ("Een map in het archief openen.", {"target": _p(_STR, "mapnaam")}, ("target",)),
    "open_tab": ("Een scherm van de app openen.", {"target": _p(_STR, "het scherm", enum=list(CHAT_VIEWS))}, ("target",)),
    "scan_duplicates": ("De scan naar exact dubbele bestanden openen. Alleen als de gebruiker om dubbele bestanden vraagt.",
                        {}, ()),
    "propose_move": ("Voorstel: bestanden naar een bestaande map verplaatsen. De gebruiker bevestigt zelf.",
                     {"query": _p(_STR, "bestandsnaam of zoekwoord"), "folder": _p(_STR, "naam van de bestaande doelmap"),
                      "limit": _p(_INT, f"hoogstens zoveel bestanden (1-{MOVE_MAX})")}, ("folder",)),
    "propose_keep_best": ("Voorstel: van de grootste reeksen alleen de beste foto houden (de rest gaat naar een map en is "
                          "terug te zetten). De gebruiker bevestigt zelf.",
                          {"count": _p(_INT, f"aantal reeksen (1-{SERIES_MAX})")}, ()),
    "propose_export": ("Voorstel: bestanden exporteren (kopiëren naar een map die de gebruiker kiest).",
                       {**{k: _FILTER_PROPS[k] for k in ("year", "month", "kind", "folder", "date_from", "date_to")},
                        "query": _p(_STR, "wat er op de foto's staat")}, ()),
}

NATIVE_TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {"name": name, "description": description,
                                      "parameters": {"type": "object", "properties": props, "required": list(required)}}}
    for name, (description, props, required) in _NATIVE.items()
]


# A tool call the model wrote as text instead of as a call (qwen2.5 sometimes drops the
# <tool_call> wrapper, and then Ollama passes it on as content): {"name": ..., "arguments": {...}}
NAMED_CALL = re.compile(r'(?:<tool_call>\s*)?\{\s*"name"\s*:\s*"[A-Za-z_]+"\s*,\s*"(?:arguments|parameters)"\s*:')
_CALL_END = re.compile(r"\s*(?:</tool_call>)?")


# ... or in the style of the prompt's examples: find_photos(query="kat")
CODE_CALL = re.compile(r"\b(" + "|".join(_NATIVE) + r")\(([^()]*)\)")


def _code_call_args(raw: str) -> Optional[Dict[str, Any]]:
    """The keyword arguments of 'query="kat", year=2019' as literals (never evaluated as code), else None."""
    try:
        tree = ast.parse(f"f({raw})", mode="eval")
        call = tree.body
        if not isinstance(call, ast.Call) or call.args:
            return None
        return {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        return None


def _text_calls(text: str) -> Tuple[List[Dict[str, Any]], List[Tuple[int, int]]]:
    """The tool calls written as text, as Ollama calls, and where they stand (to strip them)."""
    calls, spans = [], []
    for m in CODE_CALL.finditer(text):
        args = _code_call_args(m.group(2)[:500])
        if args is not None:
            calls.append({"function": {"name": m.group(1), "arguments": args}})
            spans.append((m.start(), m.end()))
    for m in NAMED_CALL.finditer(text):
        start = text.index("{", m.start())
        try:
            obj, end = json.JSONDecoder().raw_decode(text, start)
        except ValueError:
            continue
        if isinstance(obj, dict):
            end += _CALL_END.match(text, end).end() - end
            calls.append({"function": {"name": obj.get("name"), "arguments": obj.get("arguments", obj.get("parameters"))}})
            spans.append((m.start(), end))
    return calls, spans


def _without_text_calls(text: str) -> str:
    for start, end in reversed(_text_calls(text)[1]):
        text = text[:start] + text[end:]
    return text


def call_to_action(call: Any) -> Any:
    """One Ollama tool call ({'function': {'name', 'arguments'}}) as the object a tag would hold, else None."""
    fn = call.get("function") if isinstance(call, dict) else None
    if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
        return None
    args = fn.get("arguments")
    if isinstance(args, str):           # some models send the arguments as a JSON string
        args = _decode(args)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return {**{k: v for k, v in args.items() if k != "action"}, "action": fn["name"]}


def as_tags(text: str, actions: List[Dict[str, Any]]) -> str:
    """The answer with its (checked) actions as <<ACTION>> tags: how the chat history keeps a native call."""
    tags = [f"<<ACTION: {json.dumps(a, ensure_ascii=False)}>>" for a in actions]
    body = TAG.sub("", text or "").strip()
    return "\n".join([body] + tags if body else tags)


def native_history(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    The conversation for Ollama's own tool calling: an earlier answer of Pasha that acted (the chat
    keeps it with <<ACTION>> tags) becomes an assistant message with tool_calls, the way the model
    itself would have written it. With the tags left in, the model starts writing tags in its text
    (measured in docs/PASHA_TOOLS.md). Only goes to the model; nothing here runs.
    """
    out = []
    for msg in messages:
        content = msg.get("content")
        if msg.get("role") == "assistant" and isinstance(content, str) and TAG.search(content):
            calls = []
            for raw in _candidates(content):
                data = _decode(raw)
                if isinstance(data, dict) and isinstance(data.get("action"), str):
                    calls.append({"function": {"name": data["action"],
                                               "arguments": {k: v for k, v in data.items() if k != "action"}}})
            if calls:
                # The model's template shows the calls only when the text is empty
                out.append({"role": "assistant", "content": "", "tool_calls": calls})
                continue
        out.append(msg)
    return out


def resolve_actions(text: str, calls: Any = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    """
    The answer's actions: (server actions, at most 2), (actions for the page, at most 1),
    and whether the model tried an action at all. When it tried but nothing is left, the chat
    must say so instead of showing only the model's 'Hier zijn ze.'. calls: Ollama's own tool
    calls (message.tool_calls); they come first, then any tags in the text.
    """
    text = text or ""
    server: List[Dict[str, Any]] = []
    client: List[Dict[str, Any]] = []
    calls = (calls if isinstance(calls, list) else []) + _text_calls(text)[0]
    raws = _candidates(text)
    for data in [call_to_action(c) for c in calls] + [_decode(raw) for raw in raws]:
        if isinstance(data, str) and (data in TOOLS or data in CLIENT_ACTIONS):
            data = {"action": data}          # <<ACTION: "job_status">>: just the name
        action = clean_action(data) or clean_action(_near_miss(data))
        page = None if action else client_action(data)
        if action:
            server.append(action)
        elif page:
            client.append(page)
    tried = bool(calls) or bool(raws) or bool(UNFINISHED.search(text)) or is_bare_claim(text)
    return server[:2], client[:1], tried


# The short lead-ins of the prompt's examples. An answer that is only one of these ('Dit vond ik.')
# promised a result but carries no action (the small model sometimes forgets the tag).
CLAIMS = {"hier zijn ze", "hier zijn ze dan", "hier is het", "dit vond ik", "alsjeblieft", "ik kijk het na",
          "even kijken", "dit zijn je reeksen", "ik stel het voor", "ik bereid het voor", "zoeken",
          "ik zoek het op", "ik heb het opgezocht"}


def is_bare_claim(text: str) -> bool:
    words = re.sub(r"[^\w\s']", " ", (text or "").lower())
    return " ".join(words.split()) in CLAIMS


def parse_actions(text: str) -> List[Dict[str, Any]]:
    """The valid server-side actions in a model answer, in order (at most 2)."""
    return resolve_actions(text)[0]


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
    said = a["place"][:1].upper() + a["place"][1:]      # the user's own spelling ('Parijs', not 'Paris')
    ids, capped = _gps_rows(ctx, place, a.get("year"))
    if not ids:
        with ctx.db._connect() as conn:
            anywhere = conn.execute("SELECT COUNT(*) FROM media WHERE lat IS NOT NULL AND missing = 0").fetchone()[0]
        if not anywhere:
            raise ToolError("Er zijn nog geen foto's met een locatie in je bibliotheek; zonder GPS kan ik geen plaats vinden.")
        raise ToolError(f"Geen foto's met een locatie in {said}.")
    where = "in" if place.kind == "country" else "bij"
    return {"title": f"{len(ids)}{'+' if capped else ''} foto's {where} {said}", "total": len(ids),
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
    groups, preview, loose, copies = [], [], 0, 0
    to = "RAW_DUPLICATES/near-duplicates"
    for s in data["items"]:
        keep = [s["best"]]
        losers = [m for m in s["members"] if m["id"] != s["best"]]
        groups.append({"members": [m["id"] for m in s["members"]], "keep": keep})
        # near_dups.keep_best moves each loser's byte-identical copies along: the card shows them too
        for m in losers:
            preview.append({"path": m["path"], "name": m["name"], "to": to})
            for c in m.get("copies") or []:
                preview.append({"path": c["path"], "name": c["path"].rsplit("/", 1)[-1], "to": f"{to} (exacte kopie)"})
                copies += 1
        loose += len(losers)
    if not groups:
        raise ToolError("Ik vond geen reeksen om op te ruimen.")
    total = loose + copies
    title = (f"In {len(groups)} {'reeks' if len(groups) == 1 else 'reeksen'} de beste foto houden en "
             f"{total} andere naar {to} verplaatsen")
    if copies:
        title += f" (waarvan {copies} {'exacte kopie' if copies == 1 else 'exacte kopieën'})"
    return _pending(ctx, "keep_best", title, {"groups": groups}, preview, total)


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
            return {"tool": name, "args": action, "result": READ_TOOLS[name](ctx, action)}
        proposal = PROPOSE_TOOLS[name](ctx, action)
        return {"tool": name, "args": action, **proposal}
    except ToolError as e:
        return {"tool": name, "args": action, "error": str(e)}
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
