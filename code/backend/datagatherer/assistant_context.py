"""
DataGatherer - what the assistant (Pasha) may know about a day, read-only and opt-in

When a chat question names a day ('waar was ik op 14 juli 2019?', 'wat deed ik gisteren?'),
the chat adds a few lines about that day to the system prompt: the places from the
location history and a short summary of the activity events. Only from sources whose
'Pasha mag deze gegevens gebruiken' setting the user switched ON (export_kit.PASHA, off by
default); the Spotify API plays never come here (its terms forbid it). Titles and place
names go in as quoted JSON data (chat.as_prompt_data), never as instructions.

Cheap: one indexed query per table for one local day, and nothing when no day is named.
"""
import re
from datetime import date, datetime, timedelta
from typing import List, Optional

from backend import places as places_nl
from backend.datagatherer import activity_store, export_kit, registry, timezones

MONTHS = {
    "januari": 1, "jan": 1, "january": 1, "februari": 2, "feb": 2, "february": 2, "maart": 3, "mrt": 3, "march": 3,
    "april": 4, "apr": 4, "mei": 5, "may": 5, "juni": 6, "jun": 6, "june": 6, "juli": 7, "jul": 7, "july": 7,
    "augustus": 8, "aug": 8, "august": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_WORDS = re.compile(r"\b(\d{1,2})\s+([a-zA-Z]+)\.?\s+(\d{4})\b")
_WORDS_EN = re.compile(r"\b([a-zA-Z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")
LOCATION_SOURCES = ("google_timeline", "google_takeout", "swarm")
MAX_PLACES = 8
MAX_TITLES = 6


def day_in(question: str, today: Optional[date] = None) -> Optional[date]:
    """The day a question is about, or None."""
    text = question.casefold()
    today = today or date.today()
    if "eergisteren" in text:
        return today - timedelta(days=2)
    if "gisteren" in text or "yesterday" in text:
        return today - timedelta(days=1)
    for regex, order in ((_ISO, "ymd"), (_NUMERIC, "dmy")):
        m = regex.search(text)
        if m:
            parts = dict(zip(order, (int(g) for g in m.groups())))
            try:
                return date(parts["y"], parts["m"], parts["d"])
            except ValueError:
                pass
    m = _WORDS.search(text)
    if m and m.group(2) in MONTHS:
        try:
            return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:
            pass
    m = _WORDS_EN.search(text)
    if m and m.group(1) in MONTHS:
        try:
            return date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            pass
    return None


def _hm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def lines(db, question: str, as_data, today: Optional[date] = None) -> List[str]:
    """Prompt lines for the day the question names (empty when none, or nothing is opted in)."""
    day = day_in(question or "", today)
    if day is None:
        return []
    allowed = [c.id for c in registry.connectors() if export_kit.pasha_allowed(db, c.id)]
    if not allowed:
        return []
    start, end = activity_store.local_day_bounds(day.isoformat())
    label = f"{day.day} {[k for k, v in MONTHS.items() if v == day.month][0]} {day.year}"
    out: List[str] = []
    location_allowed = [s for s in LOCATION_SOURCES if s in allowed]
    if location_allowed:
        marks = ", ".join("?" * len(location_allowed))
        with db._connect() as conn:
            visits = conn.execute(
                f"SELECT start_ms, end_ms, lat, lon, name, address FROM location_visits WHERE origin IN ({marks}) "
                f"AND start_ms < ? AND end_ms >= ? ORDER BY start_ms LIMIT ?",
                (*location_allowed, end, start, MAX_PLACES)).fetchall()
            points = conn.execute(
                f"SELECT ts_ms, lat, lon FROM location_points WHERE origin IN ({marks}) AND ts_ms >= ? AND ts_ms < ? "
                f"ORDER BY ts_ms", (*location_allowed, start, end)).fetchall()
        places = []
        for s, e, lat, lon, name, address in visits:
            where = name or address or places_nl.describe(lat, lon) or "onbekende plek"
            places.append(f"{_hm(max(s, start))}-{_hm(min(e, end - 1))} {where} ({lat:.3f}, {lon:.3f})")
        if places:
            out.append(f"Locatiegeschiedenis {label} (plekken met tijd, coördinaten): {as_data(places, MAX_PLACES, 160)}")
        elif points:
            countries = sorted({timezones.country_at(p[1], p[2]) or "?" for p in points[:: max(1, len(points) // 20)]})
            first, last = points[0], points[-1]
            out.append(f"Locatiegeschiedenis {label}: {len(points)} punten, eerste {_hm(first[0])} bij "
                       f"({first[1]:.3f}, {first[2]:.3f}), laatste {_hm(last[0])} bij ({last[1]:.3f}, {last[2]:.3f}); "
                       f"landen: {as_data(countries, 5, 60)}")
        else:
            out.append(f"Locatiegeschiedenis {label}: geen gegevens voor die dag.")
    activity_allowed = [s for s in allowed]
    events = activity_store.events(db, day.isoformat(), sources=activity_allowed, limit=400)
    if events:
        counts = {}
        for ev in events:
            counts[ev["kind_label"]] = counts.get(ev["kind_label"], 0) + 1
        summary = ", ".join(f"{n}× {k}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        titles = [f"{_hm(int(ev['ts'] * 1000))} {ev['kind_label']}: {ev['title']}" for ev in events if ev.get("title")]
        out.append(f"Activiteit {label}: {summary}. Voorbeelden: {as_data(titles, MAX_TITLES, 120)}")
    return out
