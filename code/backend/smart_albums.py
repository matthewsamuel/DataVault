"""
DataVault Dashboard - Smart albums

A smart album is a saved filter (backend/library_filters.py) with a name and a sort
order, in the table smart_albums (migration 20). It is a live query on the media
index: opening it asks the index again, so it always shows what is in the archive
now. Creating, changing, hiding or deleting an album only changes this table; no
file is ever copied, moved or changed.

The default albums (DEFAULTS) are added once, the first time the app starts with
this table (app_settings 'smart_albums_defaults'). The user can hide or delete them
like any album; restore_defaults() adds the missing ones again and shows them all.
A default whose name the user has given to an album of their own comes back with a
number after it ("Video's (2)"): two albums never share a name.
When a later version changes a default's filter (UPGRADES), an unchanged default is
updated once; one the user changed is left alone.

Counts: count() of every album for the sidebar is cached per album until the index
changes (media_index.generation()) or COUNT_SECONDS pass (ratings and Keurer decisions
are not part of the generation), so the sidebar stays fast on a big archive.
"""
import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from backend import library_filters, media_index

DEFAULTS_KEY = "smart_albums_defaults"
DEFAULTS_VERSION = 2
MAX_ALBUMS = 200
MAX_NAME = 80
COUNT_SECONDS = 30.0

# (builtin key, name, filter, sort, icon)
DEFAULTS: List[Tuple[str, str, Dict[str, Any], str, str]] = [
    ("best", "Beste foto's", {"kind": ["photo"], "min_rating": 1050}, "-rating", "fa-star"),
    ("videos", "Video's", {"kind": ["video"]}, "-taken", "fa-video"),
    ("undated", "Zonder datum", {"capture_date": "missing"}, "-taken", "fa-calendar-xmark"),
    ("located", "Met locatie", {"has_gps": True}, "-taken", "fa-location-dot"),
    ("this_year", "Dit jaar", {"period": "this_year", "capture_date": "known"}, "-taken", "fa-calendar-day"),
    ("iphone", "iPhone", {"camera_contains": "iPhone"}, "-taken", "fa-mobile-screen"),
]
# (version, builtin key, filter before, filter after): applied once to an untouched default
UPGRADES: List[Tuple[int, str, Dict[str, Any], Dict[str, Any]]] = [
    # 'Dit jaar' counted files dated only by the day they were copied (version 2)
    (2, "this_year", {"period": "this_year"}, {"period": "this_year", "capture_date": "known"}),
]
DEFAULT_ICON = "fa-images"
# The icons an album may have (shown as a Font Awesome class; never free text)
ICONS = ("fa-images", "fa-star", "fa-video", "fa-calendar-xmark", "fa-location-dot", "fa-calendar-day",
         "fa-mobile-screen", "fa-heart", "fa-camera", "fa-mountain-sun", "fa-umbrella-beach", "fa-users",
         "fa-paw", "fa-plane", "fa-music", "fa-folder")

# The name check and the write of an album happen under this lock, so two requests at
# the same time can never both take one name (the app is one process)
_write_lock = threading.Lock()

COLUMNS = ("id", "name", "filters", "sort", "icon", "builtin", "hidden", "position", "created_at", "updated_at")


class AlbumError(ValueError):
    """An album that cannot be saved (Dutch message for the user); status is the HTTP code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _row(row) -> Dict[str, Any]:
    album = dict(zip(COLUMNS, row))
    try:
        album["filters"] = json.loads(album["filters"])
    except (TypeError, ValueError):
        album["filters"] = {}
    album["hidden"] = bool(album["hidden"])
    return album


def ensure_defaults(db) -> bool:
    """Add the default albums once (never again after the user deleted one). True if added."""
    try:
        done = db.get_setting(DEFAULTS_KEY, 0) or 0
        if done >= DEFAULTS_VERSION:
            return False
    except Exception:
        return False
    if done:
        _upgrade_defaults(db, done)
        added = 0
    else:
        added = _add_defaults(db, unhide=False)
    db.set_setting(DEFAULTS_KEY, DEFAULTS_VERSION)
    return added > 0


def _upgrade_defaults(db, done: int) -> None:
    """A default's newer filter, for the defaults the user did not change (filter still the old one)."""
    now = time.time()
    with db._connect() as conn:
        for version, key, before, after in UPGRADES:
            if version > done:
                conn.execute("UPDATE smart_albums SET filters = ?, updated_at = ? WHERE builtin = ? AND filters = ?",
                             (json.dumps(after, sort_keys=True), now, key, json.dumps(before, sort_keys=True)))


def _free_name(conn, name: str) -> str:
    """name, or "name (2)", "name (3)", ... when an album of the user already has it."""
    candidate, n = name, 1
    while _name_taken(conn, candidate):
        n += 1
        candidate = f"{name} ({n})"
    return candidate


def _add_defaults(db, unhide: bool) -> int:
    now = time.time()
    added = 0
    with _write_lock, db._connect() as conn:
        for position, (key, name, filters, sort, icon) in enumerate(DEFAULTS):
            if conn.execute("SELECT 1 FROM smart_albums WHERE builtin = ?", (key,)).fetchone() is None:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO smart_albums (name, filters, sort, icon, builtin, hidden, position, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
                    (_free_name(conn, name), json.dumps(filters, sort_keys=True), sort, icon, key, position, now, now))
                added += cursor.rowcount
            if unhide:
                conn.execute("UPDATE smart_albums SET hidden = 0, updated_at = ? WHERE builtin = ? AND hidden = 1",
                             (now, key))
    return added


def restore_defaults(db) -> int:
    """Add the default albums that were deleted and show the hidden ones. Returns how many were added."""
    return _add_defaults(db, unhide=True)


def list_albums(db, with_hidden: bool = True) -> List[Dict[str, Any]]:
    sql = f"SELECT {', '.join(COLUMNS)} FROM smart_albums"
    if not with_hidden:
        sql += " WHERE hidden = 0"
    with db._connect() as conn:
        rows = conn.execute(sql + " ORDER BY position, id").fetchall()
    return [_row(r) for r in rows]


def get_album(db, album_id: int) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        row = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM smart_albums WHERE id = ?", (album_id,)).fetchone()
    return _row(row) if row else None


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise AlbumError("Geef het album een naam")
    name = " ".join(value.split())
    if not name:
        raise AlbumError("Geef het album een naam")
    if len(name) > MAX_NAME:
        raise AlbumError(f"Een naam is hoogstens {MAX_NAME} tekens")
    return name


def _filters(value: Any, config) -> Dict[str, Any]:
    try:
        spec = library_filters.clean(value or {})
    except library_filters.FilterError as e:
        raise AlbumError(str(e))
    # Stored relative to the vault, so the album keeps working when the disk is mounted elsewhere
    if spec.get("folder"):
        spec["folder"] = library_filters.relative_folder(spec["folder"], config)
    if spec.get("folders"):
        spec["folders"] = sorted({library_filters.relative_folder(f, config) for f in spec["folders"]})
    return spec


def _sort(value: Any) -> str:
    try:
        return library_filters.check_sort(value or library_filters.DEFAULT_SORT)
    except library_filters.FilterError as e:
        raise AlbumError(str(e))


def _icon(value: Any) -> str:
    if value in (None, ""):
        return DEFAULT_ICON
    if value not in ICONS:
        raise AlbumError("Onbekend icoon")
    return value


def _name_taken(conn, name: str, own_id: Optional[int] = None) -> bool:
    """Another album has this name, ignoring case (also beyond ASCII: 'Één' and 'één' are the same)."""
    wanted = name.casefold()
    return any(other.casefold() == wanted
               for (other,) in conn.execute("SELECT name FROM smart_albums WHERE id IS NOT ?", (own_id,)))


def create_album(db, config, data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise AlbumError("Verwacht een object met name, filters en sort")
    name = _name(data.get("name"))
    spec = _filters(data.get("filters"), config)
    sort = _sort(data.get("sort"))
    icon = _icon(data.get("icon"))
    now = time.time()
    with _write_lock, db._connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM smart_albums").fetchone()[0] >= MAX_ALBUMS:
            raise AlbumError(f"Er kunnen hoogstens {MAX_ALBUMS} albums zijn", 409)
        if _name_taken(conn, name):
            raise AlbumError("Er is al een album met deze naam", 409)
        position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM smart_albums").fetchone()[0]
        cursor = conn.execute(
            "INSERT INTO smart_albums (name, filters, sort, icon, builtin, hidden, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, 0, ?, ?, ?)",
            (name, json.dumps(spec, sort_keys=True), sort, icon, position, now, now))
        album_id = cursor.lastrowid
    return get_album(db, album_id)


def update_album(db, config, album_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """Change name, filters, sort, icon and/or hidden; keys that are not given stay as they are."""
    if not isinstance(data, dict):
        raise AlbumError("Verwacht een object")
    unknown = set(data) - {"name", "filters", "sort", "icon", "hidden"}
    if unknown:
        raise AlbumError(f"Onbekend veld: {', '.join(sorted(unknown))}")
    changes: Dict[str, Any] = {}
    if "name" in data:
        changes["name"] = _name(data["name"])
    if "filters" in data:
        changes["filters"] = json.dumps(_filters(data["filters"], config), sort_keys=True)
    if "sort" in data:
        changes["sort"] = _sort(data["sort"])
    if "icon" in data:
        changes["icon"] = _icon(data["icon"])
    if "hidden" in data:
        if not isinstance(data["hidden"], bool):
            raise AlbumError("hidden moet true of false zijn")
        changes["hidden"] = int(data["hidden"])
    with _write_lock, db._connect() as conn:
        if conn.execute("SELECT 1 FROM smart_albums WHERE id = ?", (album_id,)).fetchone() is None:
            raise AlbumError("Album niet gevonden", 404)
        if "name" in changes and _name_taken(conn, changes["name"], album_id):
            raise AlbumError("Er is al een album met deze naam", 409)
        if changes:
            changes["updated_at"] = time.time()
            sets = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE smart_albums SET {sets} WHERE id = ?", (*changes.values(), album_id))
    return get_album(db, album_id)


def delete_album(db, album_id: int) -> Dict[str, Any]:
    """Remove the album (only its definition: no file is touched). Returns what it was."""
    album = get_album(db, album_id)
    if album is None:
        raise AlbumError("Album niet gevonden", 404)
    with db._connect() as conn:
        conn.execute("DELETE FROM smart_albums WHERE id = ?", (album_id,))
    with _counts_lock:
        for key in [k for k in _counts if k[1] == album_id]:
            del _counts[key]
    return album


# ---------- counts ----------

# (database, album id or 'all') -> (filter json, index generation, time, count)
_counts: Dict[Tuple[str, Any], Tuple[str, int, float, int]] = {}
_counts_lock = threading.Lock()


def cached_count(db, key: Any, spec: Dict[str, Any], query: Dict[str, Any]) -> int:
    """media_index.count(query), cached for key until the filter or the index changes, or COUNT_SECONDS."""
    signature = json.dumps(spec, sort_keys=True)
    generation = media_index.generation()
    now = time.monotonic()
    key = (str(getattr(db, "db_path", id(db))), key)
    with _counts_lock:
        hit = _counts.get(key)
    if hit and hit[0] == signature and hit[1] == generation and now - hit[2] < COUNT_SECONDS:
        return hit[3]
    n = media_index.count(db, query)
    with _counts_lock:
        _counts[key] = (signature, generation, now, n)
    return n


def forget_counts() -> None:
    with _counts_lock:
        _counts.clear()
