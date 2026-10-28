"""
DataVault Dashboard - Library filters

One filter format for the Albums screen, its address (#albums?kind=photo&year=2023,
so a view can be bookmarked), GET /api/library/browse and the smart albums saved in
the database (backend/smart_albums.py). A filter is a small object with only these
keys, all optional and combined with AND:

  kind             ['photo', 'video', 'audio']        any of them
  ext              ['.jpg', '.heic']                  any of them
  date_from        'YYYY-MM-DD'                       taken on or after this day
  date_to          'YYYY-MM-DD'                       taken on or before this day (included)
  year             2023                               taken in that year
  month            1..12                              in that month (of year, or of any year)
  period           'this_year' | 'last_year' | 'this_month' | 'last_30_days'   (from today)
  folder           'RAW/2019'                         a folder of the archive, with its subfolders
  folders          ['RAW/2019', 'library']            any of these folders ('op deze dag' with the
                                                      folders chosen on Home); not with folder
  recursive        false                              only the folder itself (or folders themselves)
  camera           'Apple iPhone 12'                  make + model, or just the model
  camera_contains  'iPhone'                           the make or model contains this text
  has_gps          true | false
  min_rating       1050                               Shows rating at least this
  keurer           'keep' | 'undecided'
  orientation      'landscape' | 'portrait' | 'square'
  capture_date     'known' | 'missing'                from EXIF/the video, or only the file date

Dates are local wall-clock days, the same as the capture dates in the index and the
timeline: a photo taken at 00:30 on 1 January counts for 1 January, whatever its UTC
time. clean() checks a filter (from JSON or from query parameters, where every value
is a string) and returns its normal form, the form that is stored and put in the
address. to_query() turns it into media_index filters. Nothing here touches a file.
"""
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend import media_index

KINDS = ("photo", "video", "audio")
PERIODS = ("this_year", "last_year", "this_month", "last_30_days")
KEURER = ("keep", "undecided")
CAPTURE = ("known", "missing")
LIST_KEYS = ("kind", "ext", "folders")
# (a folder name may contain a comma, so only these are split on commas in an address)
COMMA_KEYS = ("kind", "ext")
KEYS = ("kind", "ext", "date_from", "date_to", "year", "month", "period", "folder", "folders", "recursive",
        "camera", "camera_contains", "has_gps", "min_rating", "keurer", "orientation", "capture_date")
# Sorts offered to the user (media_index.SORTS also has path and random)
SORTS = ("-taken", "taken", "name", "-name", "-size", "size", "-rating", "rating", "-added", "added")
DEFAULT_SORT = "-taken"
MAX_TEXT = 200
MAX_EXTS = 30
MAX_FOLDERS = 300   # as many as the Home widget accepts (OTD_MAX_FOLDERS)
MIN_YEAR, MAX_YEAR = 1800, 2200
RATING_LIMIT = 10 ** 9
_EXT_RE = re.compile(r"^\.[a-z0-9]{1,10}$")


class FilterError(ValueError):
    """A filter that cannot be used; the message is Dutch and meant for the user."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise FilterError(f"{name} moet tekst zijn")
    value = value.strip()
    if not value or len(value) > MAX_TEXT or any(ord(c) < 32 for c in value):
        raise FilterError(f"Ongeldige {name}")
    return value


def _int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise FilterError(f"{name} moet een getal zijn")
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r"-?\d{1,12}", value):
            raise FilterError(f"{name} moet een getal zijn")
        value = int(value)
    if not isinstance(value, int):
        raise FilterError(f"{name} moet een getal zijn")
    if not low <= value <= high:
        raise FilterError(f"{name} moet tussen {low} en {high} liggen")
    return value


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "1", "ja"):
        return True
    if isinstance(value, str) and value.lower() in ("false", "0", "nee"):
        return False
    raise FilterError(f"{name} moet true of false zijn")


def _choice(value: Any, name: str, allowed: Iterable[str]) -> str:
    allowed = tuple(allowed)
    if value not in allowed:
        raise FilterError(f"{name} is een van: {', '.join(allowed)}")
    return value


def _day(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise FilterError(f"Ongeldige {name}, verwacht JJJJ-MM-DD")
    try:
        day = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        raise FilterError(f"Ongeldige {name}, verwacht JJJJ-MM-DD")
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise FilterError(f"{name} moet tussen {MIN_YEAR} en {MAX_YEAR} liggen")
    return day.strftime("%Y-%m-%d")


def _folder(value: Any) -> str:
    """A folder as given (relative to the vault, or absolute), without slashes at the ends."""
    folder = _text(value, "map")
    return folder.rstrip("/") if folder.startswith("/") and folder.strip("/") else folder.strip("/")


def _list(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        # From an address: kind=photo,video as well as kind=photo&kind=video
        return [part for part in value.split(",") if part.strip()]
    return [value]


def clean(spec: Any) -> Dict[str, Any]:
    """
    The normal form of a filter: only known keys, valid values, lists sorted and
    without doubles, empty values left out. Raises FilterError (Dutch) otherwise.
    """
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise FilterError("Een filter is een object met sleutels")
    unknown = sorted(str(k) for k in spec if k not in KEYS)
    if unknown:
        raise FilterError(f"Onbekend filter: {', '.join(unknown)}")
    out: Dict[str, Any] = {}
    for key in KEYS:
        value = spec.get(key)
        if value is None or value == "" or value == []:
            continue
        if key == "kind":
            kinds = {_choice(v, "Soort", KINDS) for v in _list(value)}
            if kinds and kinds != set(KINDS):
                out[key] = [k for k in KINDS if k in kinds]
        elif key == "ext":
            exts = set()
            for v in _list(value):
                v = _text(v, "bestandstype").lower()
                v = v if v.startswith(".") else "." + v
                if not _EXT_RE.match(v):
                    raise FilterError(f"Ongeldig bestandstype: {v}")
                exts.add(v)
            if len(exts) > MAX_EXTS:
                raise FilterError(f"Hoogstens {MAX_EXTS} bestandstypen")
            if exts:
                out[key] = sorted(exts)
        elif key in ("date_from", "date_to"):
            out[key] = _day(value, "begindatum" if key == "date_from" else "einddatum")
        elif key == "year":
            out[key] = _int(value, "Jaar", MIN_YEAR, MAX_YEAR)
        elif key == "month":
            out[key] = _int(value, "Maand", 1, 12)
        elif key == "period":
            out[key] = _choice(value, "Periode", PERIODS)
        elif key == "folder":
            folder = _folder(value)
            if folder:
                out[key] = folder
        elif key == "folders":
            folders = {f for f in (_folder(v) for v in ([value] if isinstance(value, str) else _list(value))) if f}
            if len(folders) > MAX_FOLDERS:
                raise FilterError(f"Hoogstens {MAX_FOLDERS} mappen")
            if folders:
                out[key] = sorted(folders)
        elif key == "recursive":
            if not _bool(value, "Submappen"):
                out[key] = False
        elif key in ("camera", "camera_contains"):
            out[key] = _text(value, "camera")
        elif key == "has_gps":
            out[key] = _bool(value, "Locatie")
        elif key == "min_rating":
            out[key] = _int(value, "Waardering", -RATING_LIMIT, RATING_LIMIT)
        elif key == "keurer":
            out[key] = _choice(value, "Keurer", KEURER)
        elif key == "orientation":
            out[key] = _choice(value, "Oriëntatie", media_index.ORIENTATIONS)
        elif key == "capture_date":
            out[key] = _choice(value, "Opnamedatum", CAPTURE)
    if "folder" in out and "folders" in out:
        raise FilterError("Kies één map of een lijst mappen, niet allebei")
    if "recursive" in out and "folder" not in out and "folders" not in out:
        del out["recursive"]
    if out.get("date_from") and out.get("date_to") and out["date_from"] > out["date_to"]:
        raise FilterError("De begindatum ligt na de einddatum")
    return out


def from_params(params: Iterable[Tuple[str, str]], reserved: Iterable[str] = ()) -> Dict[str, Any]:
    """A filter from query parameters ([(name, value)], a name may repeat for kind and ext)."""
    reserved = set(reserved)
    spec: Dict[str, Any] = {}
    for name, value in params:
        if name in reserved:
            continue
        if name in LIST_KEYS:
            spec.setdefault(name, []).extend(_list(value) if name in COMMA_KEYS else [value])
        elif name in spec:
            raise FilterError(f"{name} staat er twee keer in")
        else:
            spec[name] = value
    return clean(spec)


def check_sort(sort: Any) -> str:
    if sort not in SORTS:
        raise FilterError(f"Sorteren is een van: {', '.join(SORTS)}")
    return sort


def _midnight(day: date) -> float:
    """The start of a local day as a timestamp (the timeline and the index use local time)."""
    return datetime(day.year, day.month, day.day).timestamp()


def _month_start(year: int, month: int) -> date:
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def date_range(spec: Dict[str, Any], today: Optional[date] = None) -> Tuple[Optional[float], Optional[float]]:
    """[start, end) as timestamps for the date keys of a (clean) filter, None for no bound."""
    today = today or date.today()
    starts: List[date] = []
    ends: List[date] = []      # exclusive
    if spec.get("date_from"):
        starts.append(datetime.strptime(spec["date_from"], "%Y-%m-%d").date())
    if spec.get("date_to"):
        ends.append(datetime.strptime(spec["date_to"], "%Y-%m-%d").date() + timedelta(days=1))
    if spec.get("year"):
        year = spec["year"]
        if spec.get("month"):
            starts.append(date(year, spec["month"], 1))
            ends.append(_month_start(year, spec["month"] + 1))
        else:
            starts.append(date(year, 1, 1))
            ends.append(date(year + 1, 1, 1))
    period = spec.get("period")
    if period == "this_year":
        starts.append(date(today.year, 1, 1))
        ends.append(date(today.year + 1, 1, 1))
    elif period == "last_year":
        starts.append(date(today.year - 1, 1, 1))
        ends.append(date(today.year, 1, 1))
    elif period == "this_month":
        starts.append(date(today.year, today.month, 1))
        ends.append(_month_start(today.year, today.month + 1))
    elif period == "last_30_days":
        starts.append(today - timedelta(days=29))
        ends.append(today + timedelta(days=1))
    try:
        start = _midnight(max(starts)) if starts else None
        end = _midnight(min(ends)) if ends else None
    except (OverflowError, OSError, ValueError):
        raise FilterError("Deze datum kan niet gebruikt worden")
    return start, end


def to_query(spec: Dict[str, Any], config, today: Optional[date] = None) -> Dict[str, Any]:
    """
    media_index filters for a clean filter. The folder is checked with the one vault
    rule (paths.resolve_in_vault): outside the vault or in the app's own folders it
    raises HTTPException 403, also for a folder that no longer exists.
    """
    from backend.paths import resolve_in_vault
    query: Dict[str, Any] = {}
    for key in ("kind", "ext", "camera", "camera_contains", "has_gps", "min_rating", "keurer",
                "orientation", "capture_date"):
        if key in spec:
            query[key] = spec[key]
    start, end = date_range(spec, today)
    if start is not None:
        query["date_from"] = start
    if end is not None:
        query["date_to"] = end
    if spec.get("month") and not spec.get("year"):
        query["month"] = spec["month"]
    if spec.get("folder"):
        query["folder"] = str(resolve_in_vault(spec["folder"], config, folder=True))
        query["recursive"] = spec.get("recursive", True)
    elif spec.get("folders"):
        query["folders"] = [str(resolve_in_vault(f, config, folder=True)) for f in spec["folders"]]
        query["recursive"] = spec.get("recursive", True)
    return query


def relative_folder(folder: str, config) -> str:
    """
    A folder as it is stored in a filter: relative to the vault root ('RAW/2019').
    Checked like every path from a request (403 outside the vault).
    """
    from backend.paths import resolve_in_vault
    resolved = resolve_in_vault(folder, config, folder=True)
    for root in (config.VAULT_ROOT, Path(config.VAULT_ROOT).resolve()):
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return folder
