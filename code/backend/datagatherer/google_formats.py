"""
DataGatherer - the file formats inside a Google Takeout, in Dutch and English

Takeout names its folders and files in the account's language. The user's Takeouts are
Dutch ('Mijn activiteit', 'Agenda', 'Keep', 'Chrome', 'YouTube en YouTube Music/
geschiedenis/kijkgeschiedenis.json', 'Locatiegeschiedenis', 'Tijdlijn', ...), newer or
other ones English ('My Activity', 'Calendar', 'YouTube and YouTube Music/history/
watch-history.json', 'Location History', ...). route() decides by the path (both
languages) what a file is; the parsers turn its content into activity events
(activity_store.event). My Activity comes as JSON or as HTML (Takeout's default), with
dates written out in the account's language ('1 jan. 2020, 10:00:00 CET').
"""
import html
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.datagatherer import activity_store as store

DEFAULT_ZONE = "Europe/Brussels"

# Folders of a Takeout that never hold activity (thousands of photo sidecars): skipped by path
SKIP_PARTS = ("google foto's", "google photos", "/drive/", "/mail/", "google fit", "/contacts/", "/contactpersonen/")

MY_ACTIVITY_FILES = ("myactivity", "mijnactiviteit", "mijn activiteit", "my activity", "activiteit", "my_activity")
WATCH_FILES = ("watch-history", "kijkgeschiedenis", "watch history")
SEARCH_FILES = ("search-history", "zoekgeschiedenis", "search history")
LOCATION_PARTS = ("location history", "locatiegeschiedenis", "tijdlijn", "timeline", "semantic location history")

PREFIXES = (   # (prefix, kind hint) of a My Activity title, English and Dutch
    ("Watched ", "watch"), ("Bekeken: ", "watch"), ("Bekeken ", "watch"), ("Heeft bekeken ", "watch"),
    ("Searched for ", "search"), ("Gezocht naar ", "search"), ("Gezocht: ", "search"), ("Gezocht ", "search"),
    ("Visited ", "visit"), ("Bezocht: ", "visit"), ("Bezocht ", "visit"),
    ("Viewed ", "view"), ("Bekeken ", "view"), ("Used ", "use"), ("Gebruikt ", "use"),
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "mrt": 3, "maa": 3, "apr": 4, "may": 5, "mei": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12,
}
TZ_ABBR = {"UTC": 0, "GMT": 0, "WET": 0, "WEST": 60, "BST": 60, "CET": 60, "CEST": 120, "EET": 120, "EEST": 180,
           "MSK": 180, "PST": -480, "PDT": -420, "MST": -420, "MDT": -360, "CST": -360, "CDT": -300,
           "EST": -300, "EDT": -240, "JST": 540, "AEST": 600, "AEDT": 660}


def norm(path: str) -> str:
    return unicodedata.normalize("NFC", path.replace("\\", "/")).casefold()


def route(path: str) -> Optional[str]:
    """What a file in a Takeout is: myactivity, watch, search, chrome, calendar, keep, location, or None."""
    p = "/" + norm(path)
    base = p.rsplit("/", 1)[-1]
    stem, _, ext = base.rpartition(".")
    if any(part in p for part in SKIP_PARTS):
        return None
    if ext in ("json", "html"):
        if stem in WATCH_FILES:
            return "watch"
        if stem in SEARCH_FILES:
            return "search"
        if stem in MY_ACTIVITY_FILES and ("/mijn activiteit/" in p or "/my activity/" in p or "/takeout/" in p):
            return "myactivity"
    if ext == "json":
        if stem in ("browserhistory", "browser history", "geschiedenis") and "/chrome/" in p:
            return "chrome"
        if "/keep/" in p:
            return "keep"
        if stem == "records" or any(part in p for part in LOCATION_PARTS):
            return "location"
    if ext == "ics" and ("/agenda/" in p or "/calendar/" in p):
        return "calendar"
    return None


def product_of(path: str) -> Optional[str]:
    """'Takeout/Mijn activiteit/YouTube/MijnActiviteit.json' -> 'YouTube'."""
    parts = path.replace("\\", "/").split("/")
    for i, part in enumerate(parts[:-1]):
        if norm(part) in ("mijn activiteit", "my activity") and i + 1 < len(parts) - 1:
            return parts[i + 1]
    return None


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def iso_ms(text: Any) -> Optional[int]:
    if not isinstance(text, str) or not text:
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _ms(dt)


def _strip_prefix(title: str) -> Tuple[str, Optional[str]]:
    for prefix, hint in PREFIXES:
        if title.startswith(prefix):
            return title[len(prefix):].strip(), hint
    return title, None


def _kind(header: str, product: Optional[str], hint: Optional[str], url: Optional[str]) -> str:
    h = norm(" ".join(filter(None, (header, product))))
    if "youtube" in h or (url and "youtube.com" in url):
        return "youtube_search" if hint == "search" else "youtube_watch"
    if "maps" in h:
        return "maps"
    if "chrome" in h:
        return "web_visit"
    if hint == "search" or "zoeken" in h or "search" in h:
        return "search"
    return "activity"


def activity_item(item: Dict[str, Any], product: Optional[str], forced_kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One My Activity entry (JSON shape: header, title, titleUrl, subtitles, time, products) -> event."""
    ts = iso_ms(item.get("time"))
    raw_title = item.get("title")
    if ts is None or not isinstance(raw_title, str):
        return None
    header = item.get("header") if isinstance(item.get("header"), str) else ""
    url = item.get("titleUrl") if isinstance(item.get("titleUrl"), str) else None
    title, hint = _strip_prefix(raw_title.strip())
    kind = forced_kind or _kind(header, product, hint, url)
    details: Dict[str, Any] = {}
    subs = [s.get("name") for s in item.get("subtitles") or [] if isinstance(s, dict) and isinstance(s.get("name"), str)]
    if subs:
        details["kanaal" if kind.startswith("youtube") else "extra"] = subs[0][:200]
    if header:
        details["product"] = header[:80]
    if any(isinstance(d, dict) and "Ads" in str(d.get("name", "")) for d in item.get("details") or []):
        details["advertentie"] = True
    # The same entry is in My Activity and in YouTube's own history file: one key for both
    return store.event(kind, ts, store.key(kind, ts, url or title), title=title, url=url, details=details)


def parse_activity_json(blob: bytes, product: Optional[str], forced_kind: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    data = json.loads(blob.decode("utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("Mijn activiteit: verwacht een lijst")
    for item in data:
        if isinstance(item, dict):
            ev = activity_item(item, product, forced_kind)
            if ev is not None:
                yield ev


# ---------- My Activity as HTML ----------

_DATE_EN = re.compile(r"^([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s+(\d{4}),\s+(\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)?\s*(.*)$")
_DATE_NL = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4}),?\s+(\d{1,2}):(\d{2}):(\d{2})\s*(.*)$")
_OFFSET = re.compile(r"^(?:GMT|UTC)?([+-])(\d{1,2}):?(\d{2})?$")


def parse_written_date(text: str) -> Optional[int]:
    """'Jan 1, 2020, 10:00:00 AM CET' or '1 jan. 2020, 10:00:00 CET' -> UTC ms."""
    text = " ".join(text.replace(" ", " ").replace("\xa0", " ").split())
    m = _DATE_EN.match(text)
    if m:
        mon, day, year, hh, mm, ss, ampm, tz = m.groups()
        hour = int(hh) % 12 + (12 if ampm == "PM" else 0) if ampm else int(hh)
    else:
        m = _DATE_NL.match(text)
        if not m:
            return None
        day, mon, year, hh, mm, ss, tz = m.groups()
        hour = int(hh)
    month = MONTHS.get(mon[:3].casefold())
    if not month:
        return None
    try:
        wall = datetime(int(year), month, int(day), hour, int(mm), int(ss))
    except ValueError:
        return None
    tz = (tz or "").strip()
    if tz in TZ_ABBR:
        return _ms(wall.replace(tzinfo=timezone(timedelta(minutes=TZ_ABBR[tz]))))
    off = _OFFSET.match(tz)
    if off:
        minutes = int(off.group(2)) * 60 + int(off.group(3) or 0)
        return _ms(wall.replace(tzinfo=timezone(timedelta(minutes=minutes if off.group(1) == "+" else -minutes))))
    return _ms(wall.replace(tzinfo=ZoneInfo(DEFAULT_ZONE)))


class _ActivityHTML(HTMLParser):
    """Collects the cells of a My Activity HTML page: header, the first content cell's lines and links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: List[Dict[str, Any]] = []
        self._cur: Optional[Dict[str, Any]] = None
        self._mode = None          # 'header' | 'content' | None
        self._depth = 0
        self._mode_depth = 0
        self._href = None

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            if self._mode == "content":
                self._cur["lines"].append("")
            return
        self._depth += 1
        cls = dict(attrs).get("class") or ""
        if tag == "div" and "outer-cell" in cls:
            self._cur = {"header": "", "lines": [""], "links": [], "done": False}
            self.items.append(self._cur)
        elif self._cur is not None and tag == "div" and "header-cell" in cls:
            self._mode, self._mode_depth = "header", self._depth
        elif self._cur is not None and tag == "div" and "content-cell" in cls and not self._cur["done"] \
                and "text-right" not in cls and "caption" not in cls:
            self._mode, self._mode_depth = "content", self._depth
        elif tag == "a" and self._mode == "content":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "br":
            return
        if tag == "a" and self._mode == "content":
            self._href = None
        if self._mode and self._depth == self._mode_depth:
            if self._mode == "content":
                self._cur["done"] = True
            self._mode = None
        self._depth -= 1

    def handle_data(self, data):
        if self._mode == "header":
            self._cur["header"] += data
        elif self._mode == "content":
            self._cur["lines"][-1] += data
            if self._href:
                self._cur["links"].append((self._href, data))


def parse_activity_html(blob: bytes, product: Optional[str], forced_kind: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    parser = _ActivityHTML()
    parser.feed(blob.decode("utf-8", "replace"))
    parser.close()
    for cell in parser.items:
        lines = [" ".join(line.replace("\xa0", " ").split()) for line in cell["lines"]]
        lines = [line for line in lines if line]
        if len(lines) < 2:
            continue
        ts = parse_written_date(lines[-1])
        if ts is None:
            continue
        links = cell["links"]
        item = {
            "header": " ".join(cell["header"].split()),
            "title": lines[0],
            "titleUrl": html.unescape(links[0][0]) if links else None,
            "subtitles": [{"name": lines[1]}] if len(lines) > 2 else [],
            "time": datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(),
        }
        ev = activity_item(item, product, forced_kind)
        if ev is not None:
            yield ev


# ---------- Chrome (Takeout), Keep, Agenda ----------

def parse_chrome_takeout(blob: bytes) -> Iterator[Dict[str, Any]]:
    data = json.loads(blob.decode("utf-8-sig"))
    rows = data.get("Browser History") if isinstance(data, dict) else None
    for row in rows or []:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            continue
        usec = row.get("time_usec")
        if not isinstance(usec, (int, float)) or isinstance(usec, bool) or usec <= 0:
            continue
        ts = int(usec // 1000)
        yield store.event("web_visit", ts, store.key("web_visit", ts, row["url"]), title=row.get("title") or row["url"],
                          url=row["url"], details={"browser": "Chrome", "bron": "Takeout"})


def parse_keep(blob: bytes, name: str) -> Iterator[Dict[str, Any]]:
    data = json.loads(blob.decode("utf-8-sig"))
    if not isinstance(data, dict) or data.get("isTrashed"):
        return
    usec = data.get("createdTimestampUsec") or data.get("userEditedTimestampUsec")
    if not isinstance(usec, (int, float)) or isinstance(usec, bool) or usec <= 0:
        return
    ts = int(usec // 1000)
    text = data.get("textContent") if isinstance(data.get("textContent"), str) else ""
    if not text and isinstance(data.get("listContent"), list):
        text = "\n".join(("☑ " if i.get("isChecked") else "☐ ") + str(i.get("text", ""))
                         for i in data["listContent"] if isinstance(i, dict))
    title = (data.get("title") or "").strip() or (text.strip().split("\n", 1)[0][:80] if text.strip() else "Notitie")
    labels = [lab.get("name") for lab in data.get("labels") or [] if isinstance(lab, dict) and lab.get("name")]
    details = {"tekst": text[:600]}
    if labels:
        details["labels"] = labels[:10]
    if data.get("isArchived"):
        details["gearchiveerd"] = True
    yield store.event("note", ts, store.key("note", ts, name.rsplit("/", 1)[-1]), title=title, details=details)


def _ics_lines(text: str) -> List[str]:
    lines: List[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _ics_unescape(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def ics_time(value: str, params: Dict[str, str]) -> Optional[Tuple[int, bool]]:
    """(UTC ms, all_day) of a DTSTART/DTEND value."""
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
            day = datetime.strptime(value[:8], "%Y%m%d")
            return _ms(day.replace(tzinfo=ZoneInfo(DEFAULT_ZONE))), True
        wall = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if value.endswith("Z"):
        return _ms(wall.replace(tzinfo=timezone.utc)), False
    zone = params.get("TZID", DEFAULT_ZONE).strip('"')
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo(DEFAULT_ZONE)
    return _ms(wall.replace(tzinfo=tz)), False


def parse_ics(blob: bytes, name: str) -> Iterator[Dict[str, Any]]:
    calendar = name.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    event: Optional[Dict[str, Any]] = None
    for line in _ics_lines(blob.decode("utf-8-sig", "replace")):
        if line == "BEGIN:VEVENT":
            event = {}
            continue
        if line == "END:VEVENT":
            if event and "DTSTART" in event:
                start = ics_time(*event["DTSTART"])
                if start is not None:
                    end = ics_time(*event["DTEND"]) if "DTEND" in event else None
                    summary = _ics_unescape(event.get("SUMMARY", ("", {}))[0]).strip() or "(geen titel)"
                    details: Dict[str, Any] = {"agenda": calendar[:80]}
                    if start[1]:
                        details["hele_dag"] = True
                    if "LOCATION" in event and event["LOCATION"][0].strip():
                        details["locatie"] = _ics_unescape(event["LOCATION"][0])[:200]
                    if "RRULE" in event:
                        details["herhaalt"] = True
                    uid = event.get("UID", ("", {}))[0]
                    rid = event.get("RECURRENCE-ID", ("", {}))[0]
                    yield store.event("calendar", start[0], store.key("calendar", uid or summary, rid, start[0]),
                                      title=summary, details=details, end_ms=end[0] if end else None)
            event = None
            continue
        if event is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name_part, *param_parts = head.split(";")
        params = {}
        for p in param_parts:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.upper()] = v
        key = name_part.upper()
        if key in ("DTSTART", "DTEND", "SUMMARY", "LOCATION", "UID", "RRULE", "RECURRENCE-ID"):
            event[key] = (value, params)
