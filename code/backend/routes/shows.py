from fastapi import APIRouter, HTTPException, Query
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import heapq
import os
import random
import re
import sqlite3
import stat
import threading
import time
from backend import elo, media_index, recovery
from backend.config import Config, is_photo, is_video
from backend.file_ops import (
    move_no_overwrite, is_inside, ARCHIVE_LOCK, FileOperationError, CrossDeviceError, DestinationFolderError, ProtectedPathError,
)
from backend.paths import is_in_vault, resolve_in_vault, vault_folder_or_none, walk_archive

router = APIRouter()

# Global state
config: Config = None
db = None

def init_router(cfg: Config, database):
    """Initialize router with config and database"""
    global config, db
    config = cfg
    db = database

def get_disabled_folders() -> List[str]:
    """Get list of DISABLED folder paths from DB"""
    if not db:
        return []
    
    return db.get_disabled_show_folders()

def is_folder_enabled(path: Path, disabled_folders: List[str]) -> bool:
    """
    Check if folder is allowed.
    A folder is disabled if it matches a disabled path OR is a child of a disabled path.
    """
    path_str = str(path)
    for disabled in disabled_folders:
        if path_str == disabled or path_str.startswith(disabled + '/'):
            return False
    return True

# --- Which files a show plays ------------------------------------------------
#
# A show plays photos and videos (never audio), drawn from EVERY file below its folder,
# in a rating-weighted random order WITHOUT repeats: each file appears at most once,
# a higher rating makes a file come earlier. The walk only lists names; a file is
# stat'ed once it has been picked.

SHOW_MAX_FILES = 2000      # a folder show plays at most this many (picked from all files)
FEATURED_SIZE = 50         # Feeling Lucky
RATING_START = 1000        # the rating of a file nobody voted on yet
VOTE_STEP = 50             # one up-vote adds this, one down-vote subtracts it
RATING_FLOOR = 100         # a rating never drops below this


def is_show_media(path) -> bool:
    """Photos and videos; audio has no picture to show."""
    return is_photo(Path(path)) or is_video(Path(path))


def _log_walk_error(path, error):
    # One unreadable folder skips that folder only, never the rest of the walk
    print(f"Shows: kon {path} niet lezen: {error}")


def collect_show_files(root_path: Path, disabled_folders: List[str]) -> List[str]:
    """All photo and video paths below root_path, skipping disabled folders (and what
    walk_archive skips: the app's own folders, TRASH, RAW_DUPLICATES, hidden files and
    symlinks)."""
    paths = []
    for root, dirs, filenames in walk_archive(root_path, config, onerror=_log_walk_error):
        if not is_folder_enabled(Path(root), disabled_folders):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if is_folder_enabled(Path(root) / d, disabled_folders)]
        paths.extend(os.path.join(root, name) for name in filenames if is_show_media(name))
    return paths


def show_weight(rating: int) -> float:
    """rating ^ 1.5: a liked photo comes clearly more often, a disliked one still can."""
    return pow(max(1, rating), 1.5)


def weighted_order(paths: List[str], ratings: Dict[str, int], k: Optional[int] = None) -> List[str]:
    """
    A weighted random sample WITHOUT replacement (Efraimidis-Spirakis): every path gets
    the key random() ** (1 / weight) and the k largest keys win, in that order. Each
    path appears at most once; a higher rating makes it likely to come earlier.
    """
    k = len(paths) if k is None else min(k, len(paths))
    if k <= 0:
        return []
    keyed = ((random.random() ** (1.0 / show_weight(ratings.get(p, RATING_START))), p) for p in paths)
    return [p for _, p in heapq.nlargest(k, keyed)]


def describe_show_file(path: str, ratings: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """The file dict the slideshow needs, or None when the file is gone or unreadable."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    p = Path(path)
    return {
        "path": path,
        "name": p.name,
        "folder": p.parent.name,
        "size": st.st_size,
        "modified": st.st_mtime,
        "type": "video" if is_video(p) else "photo",
        "rating": ratings.get(path, RATING_START),
    }


def pick_show_files(paths: List[str], ratings: Dict[str, int], k: int) -> List[Dict[str, Any]]:
    """Weighted order without repeats, described; files that vanished are skipped."""
    files = []
    for path in weighted_order(paths, ratings):
        info = describe_show_file(path, ratings)
        if info:
            files.append(info)
            if len(files) >= k:
                break
    return files


@router.get("/stories")
def get_stories() -> Dict[str, Any]:
    """
    Shows from the library folder ONLY:
    - shows: every folder below library (not library itself) with photos or videos
      somewhere inside; the preview is a random one of its own files, or of its
      subfolders' files when it has none itself
    - featured: Feeling Lucky, a rating-weighted sample (no repeats) from ALL library files
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not initialized")
    
    if not config.VAULT_ROOT.exists():
        raise HTTPException(status_code=503, detail="Vault not available")
    
    # Shows come ONLY from the library folder, in the resolved spelling that settings and
    # votes are stored in (and never a 'library' symlink that points out of the vault)
    library_root = vault_folder_or_none("library", config)
    if library_root is None or not library_root.is_dir():
        return {"featured": None, "shows": []}
    
    disabled_folders = get_disabled_folders()
    root = str(library_root)

    # One walk: every show-media path, and per folder a random preview from its own files
    # or (reservoir sampling) from the files of its subfolders
    all_paths: List[str] = []
    order: List[str] = []                  # folders in walk order (parents before children)
    preview: Dict[str, str] = {}
    stack: Dict[str, List[str]] = {}       # a few more of a folder's own files: its cover stack
    seen: Dict[str, int] = {}              # media counted per folder for the reservoir
    own_media: set = set()                 # folders with photos or videos of their own

    for folder, dirs, filenames in walk_archive(library_root, config, onerror=_log_walk_error):
        dirs[:] = sorted(d for d in dirs if is_folder_enabled(Path(folder) / d, disabled_folders))
        if not is_folder_enabled(Path(folder), disabled_folders):
            dirs[:] = []
            continue
        if folder != root:
            order.append(folder)
        media = [os.path.join(folder, n) for n in filenames if is_show_media(n)]
        all_paths.extend(media)
        if not media:
            continue
        if folder != root:
            own_media.add(folder)
            preview[folder] = random.choice(media)
            others = [path for path in media if path != preview[folder]]
            stack[folder] = random.sample(others, min(2, len(others)))
        # Ancestors without media of their own take a random file from below them
        ancestor = os.path.dirname(folder)
        while len(ancestor) > len(root) and ancestor.startswith(root + os.sep):
            if ancestor not in own_media:
                for path in media:
                    seen[ancestor] = seen.get(ancestor, 0) + 1
                    if random.randrange(seen[ancestor]) == 0:
                        preview[ancestor] = path
            ancestor = os.path.dirname(ancestor)

    folder_shows = [
        {
            "title": Path(folder).name,
            "path": folder,
            "preview": {"path": preview[folder], "name": Path(preview[folder]).name},
            # Up to two more photos for the cover stack that fans out on hover (own files only)
            "stack": [{"path": path, "name": Path(path).name} for path in stack.get(folder, [])],
            "type": "folder_show",
        }
        for folder in sorted(order, key=str.lower) if folder in preview
    ]

    # Featured: weighted by the Shows votes, drawn from every file in the library
    featured_show = None
    ratings = db.get_all_ratings() if db else {}
    featured_files = pick_show_files(all_paths, ratings, FEATURED_SIZE)
    if featured_files:
        featured_show = {
            "title": "Verras me",
            "subtitle": f"{len(featured_files)} willekeurige foto's uit je archief",
            "files": featured_files,
            "type": "featured"
        }
    
    return {
        "featured": featured_show,
        "shows": folder_shows
    }

@router.post("/settings")
def update_settings(payload: Dict[str, Any]):
    """
    Update settings (enable/disable folders).
    Receives: { "path": "/path/to/folder", "enabled": true/false }
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")

    path = payload.get("path")
    enabled = payload.get("enabled")
    
    if path is None or enabled is None:
        raise HTTPException(status_code=400, detail="Missing path or enabled status")
    if not isinstance(path, str) or not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="Invalid path or enabled status")
    
    # Only folders inside the vault can be switched off (stored in one spelling)
    path = str(resolve_in_vault(path, config, folder=True))
    
    db.set_show_folder_disabled(path, not enabled)
    return {"status": "success", "message": f"Folder {'enabled' if enabled else 'disabled'}"}

@router.get("/settings")
def get_settings():
    """Get current settings"""
    return {"disabled_folders": get_disabled_folders()}

@router.post("/vote")
def vote_file(payload: Dict[str, Any]):
    """
    Save a vote (up/down) for a media file and update its ELO rating.
    Receives: { "path": "/path/to/file", "vote": "up"|"down" }
    """
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")

    path = payload.get("path")
    vote = payload.get("vote")

    if not path or not isinstance(path, str) or vote not in ("up", "down"):
        raise HTTPException(status_code=400, detail="Missing or invalid path/vote")

    target = resolve_in_vault(path, config)
    # Only an existing regular file gets a rating (no rows for typos, folders or symlinks)
    try:
        is_file = stat.S_ISREG(os.lstat(target).st_mode)
    except OSError:
        is_file = False
    if not is_file:
        raise HTTPException(status_code=404, detail="Bestand niet gevonden")
    path = str(target)

    # One atomic read-modify-write: two votes at the same moment (two tabs) both count
    change = VOTE_STEP if vote == "up" else -VOTE_STEP
    new_rating, votes = db.apply_vote(path, change, floor=RATING_FLOOR, start=RATING_START)

    return {"status": "ok", "new_rating": new_rating, "votes": votes}

@router.get("/folder-content")
def get_folder_content(path: str):
    """
    Photos and videos below a folder, for a "Folder Show": every file is a candidate,
    each appears at most once, in a rating-weighted random order (higher-rated files
    tend to come first). At most SHOW_MAX_FILES are returned.
    """
    # Inside the vault first, existence second: the same 403 for anything outside
    target_path = resolve_in_vault(path, config, folder=True)
         
    if not target_path.is_dir():
         raise HTTPException(status_code=404, detail="Folder not found")
    
    ratings = db.get_all_ratings() if db else {}
    paths = collect_show_files(target_path, get_disabled_folders())
    return pick_show_files(paths, ratings, SHOW_MAX_FILES)


# --- 'Welke is mooier?': comparisons and the leaderboard (backend/elo.py) ------
#
# A scope is a folder: a show (a folder below library), the whole library (no folder
# given), or any other folder in the vault. Its photos come from the media index once a
# pass has finished (cached until the selection changes), else from a walk (cached for
# CANDIDATE_TTL seconds). Disabled show folders never take part, as in the shows.
# Other collections plug in with elo.register_scope(kind, resolve) and are asked for with
# scope=<kind>:<value> instead of folder; their photos are looked up in the index. Smart
# albums are registered here ('album:<id>'), and the perceptual near-duplicates of step 3.4
# (near_dup_pairs) are never shown side by side.

CANDIDATE_TTL = 60
LEADERBOARD_MAX = 200
MAX_COMPARISON_ID = 2 ** 63 - 1
MAX_PAIRS = 4              # pairs per request (the screen keeps a few ready)
MAX_EXCLUDE = 16
MAX_RECENT_PAIRS = 8       # pairs the screen showed lately (recent=, two paths per pair)
MAX_TOKEN = 64
TOP_ALBUM_MAX = 200
GOAL_KEY = "elo_session_goal"
_candidates_lock = threading.Lock()
_candidates_cache: Dict[str, Any] = {}


class _Scope:
    """Where a round runs: key (stored in the history), title, and either a folder or the
    paths of a registered collection (with its library filter when it has one)."""

    def __init__(self, key: str, title: str, folder: Optional[Path] = None, paths: Optional[List[str]] = None,
                 filters: Optional[Dict[str, Any]] = None, version: Any = None):
        self.key, self.title, self.folder, self.paths, self.filters = key, title, folder, paths, filters
        self.version = version      # a collection's version (same version, same paths), or None

    def info(self) -> Dict[str, Any]:
        return {"key": self.key, "path": str(self.folder) if self.folder is not None else None, "title": self.title}


def _scope_folder(folder: Optional[str]) -> Path:
    """The folder a comparison round runs in: the given one (inside the vault) or library."""
    if folder:
        target = resolve_in_vault(folder, config, folder=True)
    else:
        target = vault_folder_or_none("library", config)
        if target is None:
            raise HTTPException(status_code=404, detail="De map library bestaat niet")
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Map niet gevonden")
    return target


def _scope_title(target: Path) -> str:
    library_root = vault_folder_or_none("library", config)
    return "Hele bibliotheek" if library_root is not None and target == library_root else target.name


def _scope(folder: Optional[str] = None, scope: Optional[str] = None) -> _Scope:
    """A folder scope, or a registered collection ('album:12'); 400/404 when it is not there."""
    if scope:
        if not isinstance(scope, str) or ":" not in scope:
            raise HTTPException(status_code=400, detail="Ongeldige collectie")
        found = elo.resolve_scope(scope)
        if found is None:
            raise HTTPException(status_code=404, detail="Collectie niet gevonden")
        return _Scope(found["key"], found["title"], paths=found["paths"], filters=found.get("filters"),
                      version=found.get("version"))
    target = _scope_folder(folder)
    return _Scope(str(target), _scope_title(target), folder=target)


def _base_candidates(target: Path) -> List[Dict[str, Any]]:
    """Every photo below target that may take part: [{path, id, size, taken, taken_source,
    series}] (series: see elo.mark_series)."""
    folder = str(target)
    disabled = get_disabled_folders()
    base = None
    if db is not None and media_index.usable(db):
        base = _index_candidates(target, disabled)
        # A folder made since the last pass is not in the index yet: then the walk answers
        # (and the index is asked to catch up), instead of 'too few photos'
        if len(base) < 2 and not media_index.fresh_for(db, [target]):
            media_index.request_update()
            base = None
    if base is None:
        key = ("walk", tuple(disabled))
        with _candidates_lock:
            cached = _candidates_cache.get(folder)
        if cached and cached[0] == key and time.monotonic() - cached[1] < CANDIDATE_TTL:
            return cached[2]
        base = [{"path": p, "id": None, "size": None, "taken": None, "taken_source": None}
                for p in collect_show_files(target, disabled) if elo.is_elo_photo(p)]
        elo.mark_series(base)
        with _candidates_lock:
            _candidates_cache[folder] = (key, time.monotonic(), base)
    return base


def _index_rows(filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows, offset = [], 0
    while True:
        page = media_index.query(db, filters, sort="path", offset=offset, limit=media_index.MAX_LIMIT)
        rows.extend(page)
        if len(page) < media_index.MAX_LIMIT:
            break
        offset += len(page)
    return rows


def _index_candidates(target: Path, disabled: List[str]) -> List[Dict[str, Any]]:
    """The photos below target as the media index knows them (cached until the selection changes)."""
    folder = str(target)
    filters = {"kind": "photo", "folder": folder}
    key = ("index", media_index.selection_version(db, filters), tuple(disabled))
    with _candidates_lock:
        cached = _candidates_cache.get(folder)
    if cached and cached[0] == key:
        return cached[2]
    base = [{"path": r["path"], "id": r["id"], "size": r["size"], "taken": r["taken"],
             "taken_source": r["taken_source"]}
            for r in _index_rows(filters) if elo.is_elo_photo(r["path"]) and is_folder_enabled(Path(r["folder"]), disabled)]
    elo.mark_series(base)
    with _candidates_lock:
        _candidates_cache[folder] = (key, time.monotonic(), base)
    return base


def _collection_candidates(paths: List[str], key: Optional[str] = None, version: Any = None) -> List[Dict[str, Any]]:
    """The photos of a registered collection that may take part (inside the vault, a photo
    a browser can show, not in a disabled show folder), with what the index knows of them.
    With a version (an album: its selection version), the list is kept until that changes, as
    for a folder: an album of 10,000 photos is not looked up again for every pair."""
    root = str(config.VAULT_ROOT).rstrip("/") + "/"
    disabled = get_disabled_folders()
    cache_key = ("collection", version, tuple(disabled), len(paths)) if key and version is not None else None
    if cache_key:
        with _candidates_lock:
            cached = _candidates_cache.get(key)
        if cached and cached[0] == cache_key and isinstance(cached[2], dict):
            return cached[2]["base"]
    enabled: Dict[str, bool] = {}

    def folder_ok(path: str) -> bool:
        parent = os.path.dirname(path)
        if parent not in enabled:
            enabled[parent] = is_folder_enabled(parent, disabled)
        return enabled[parent]

    wanted = sorted({p for p in paths if isinstance(p, str) and p.startswith(root) and elo.is_elo_photo(p)
                     and folder_ok(p)})
    known: Dict[str, Tuple] = {}
    if db is not None and wanted:
        with db._connect() as conn:
            for start in range(0, len(wanted), 400):
                chunk = wanted[start:start + 400]
                for row in conn.execute(
                        f"SELECT path, id, size, taken, taken_source FROM media WHERE missing = 0 "
                        f"AND path IN ({','.join('?' * len(chunk))})", chunk):
                    known[row[0]] = row[1:]
    base = []
    for path in wanted:
        media_id, size, taken, source = known.get(path, (None, None, None, None))
        base.append({"path": path, "id": media_id, "size": size, "taken": taken, "taken_source": source})
    elo.mark_series(base)
    if cache_key:
        with _candidates_lock:
            # Next to the album's own entry (its paths): key is '<scope>#base'
            _candidates_cache[key] = (cache_key, time.monotonic(), {"base": base})
    return base


def _scope_candidates(sc: _Scope) -> List[Dict[str, Any]]:
    if sc.folder is not None:
        base = _base_candidates(sc.folder)
        states = elo.states_below(db, str(sc.folder))
    else:
        base = _collection_candidates(sc.paths, f"{sc.key}#base" if sc.version is not None else None, sc.version)
        states = elo.states_of(db, [item["path"] for item in base])
    return elo.with_states(base, states)


def _scope_stats(sc: _Scope, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"photos": len(candidates), "compared": sum(1 for c in candidates if c["games"]),
            "comparisons": elo.comparisons_in_scope(db, sc.key),
            "settled": round(elo.settled_share(candidates), 4)}


def _goal() -> int:
    value = db.get_setting(GOAL_KEY, elo.GOAL_DEFAULT) if db else elo.GOAL_DEFAULT
    return value if value in elo.GOAL_CHOICES else elo.GOAL_DEFAULT


def _progress() -> Dict[str, Any]:
    return elo.progress(db, goal=_goal())


def forget_candidates() -> None:
    with _candidates_lock:
        _candidates_cache.clear()


def _photo_info(item: Dict[str, Any]) -> Dict[str, Any]:
    p = Path(item["path"])
    return {"path": item["path"], "name": p.name, "folder": p.parent.name,
            "rating": item["rating"], "games": item["games"], "votes": item.get("votes", 0),
            "rd": round(item.get("rd", elo.RD_START), 1)}


def _require_db():
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")


# ---------- smart albums as scopes, perceptual near-duplicates ----------

def _album_scope(value: str) -> Optional[Dict[str, Any]]:
    """'album:<id>': the photos of a smart album (a saved filter, asked of the index now)."""
    from backend import library_filters, smart_albums
    # ASCII digits only, without leading zeros: one key per album ('¹' or '٣' is not a number here)
    if not re.fullmatch(r"[1-9][0-9]{0,17}", value) or db is None:
        return None
    album = smart_albums.get_album(db, int(value))
    if album is None:
        return None
    try:
        query = library_filters.to_query(album["filters"], config)
    except (library_filters.FilterError, HTTPException):
        return {"title": album["name"], "paths": [], "filters": album["filters"]}
    # Only the album's photos: an album of videos (kind without 'photo') has none
    kinds = query.get("kind")
    if isinstance(kinds, str):
        kinds = [kinds]
    if kinds and "photo" not in kinds:
        return {"title": album["name"], "paths": [], "filters": album["filters"]}
    query["kind"] = ["photo"]
    key = f"album:{value}"
    version = (media_index.selection_version(db, query), album.get("updated_at"))
    with _candidates_lock:
        cached = _candidates_cache.get(key)
    if cached and cached[0] == version:
        paths = cached[2]
    else:
        paths = [r["path"] for r in _index_rows(query)]
        with _candidates_lock:
            _candidates_cache[key] = (version, time.monotonic(), paths)
    return {"title": album["name"], "paths": paths, "filters": album["filters"], "version": version}


_near_pairs: Dict[str, Any] = {"at": 0.0, "pairs": frozenset()}
NEAR_PAIRS_TTL = 60


def _near_pair_ids() -> frozenset:
    """The media id pairs step 3.4 found to be near-duplicates (near_dup_pairs), cached."""
    now = time.monotonic()
    if now - _near_pairs["at"] < NEAR_PAIRS_TTL or db is None:
        return _near_pairs["pairs"]
    try:
        with db._connect() as conn:
            pairs = frozenset((min(a, b), max(a, b)) for a, b in conn.execute("SELECT a, b FROM near_dup_pairs"))
    except Exception:
        pairs = frozenset()
    _near_pairs.update(at=now, pairs=pairs)
    return pairs


def _perceptual_near_duplicate(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    ida, idb = a.get("id"), b.get("id")
    if ida is None or idb is None:
        return False
    return (min(ida, idb), max(ida, idb)) in _near_pair_ids()


elo.register_scope("album", _album_scope)
elo.register_near_duplicate_check(_perceptual_near_duplicate)


# ---------- the routes ----------

def _paths_param(values: Optional[List[str]], limit: int) -> List[str]:
    return [v for v in (values or [])[:limit] if isinstance(v, str)]


def _shown_pairs(values: Optional[List[str]]) -> List[Tuple[str, str]]:
    flat = _paths_param(values, 2 * MAX_RECENT_PAIRS)
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2) if flat[i] != flat[i + 1]]


@router.get("/compare/pair")
def compare_pair(folder: Optional[str] = None, scope: Optional[str] = None,
                 prev: Optional[List[str]] = Query(None), exclude: Optional[List[str]] = Query(None),
                 recent: Optional[List[str]] = Query(None),
                 count: int = Query(1, ge=1, le=MAX_PAIRS)) -> Dict[str, Any]:
    """
    The next pairs to compare in a scope (folder, or scope=<kind>:<value> for a registered
    collection): {scope: {key, path, title}, pair: [a, b] or null, pairs: [[a, b], ...]
    (count of them, no photo twice), reason (when there is no pair: 'too_few' or 'no_pair'),
    stats: {photos, compared, comparisons, settled}, progress: {session, today, streak,
    best_streak, goal, total}}.
    prev (twice): the pair on screen now, which never comes back straight away; neither
    does the last pair compared in this scope. exclude: photos that must not be in the new
    pairs (on screen, or already waiting in the queue; two per pair); in a small collection
    only those on screen are kept out, in a tiny one none (shared: true; apart: the photos
    that were kept out). recent (two per pair, oldest
    first): the pairs the screen showed lately, also those whose choice is still being
    saved; they do not come back while other pairs can, and their photos are held back.
    """
    _require_db()
    sc = _scope(folder, scope)
    candidates = _scope_candidates(sc)
    avoid = []
    prev = _paths_param(prev, 2)
    if len(prev) == 2:
        avoid.append(prev)
    last = elo.last_comparison(db, sc.key)
    if last:
        avoid.append((last["path_a"], last["path_b"]))
    stats = _scope_stats(sc, candidates)
    memory = elo.pair_memory(db, sc.key)
    shown = _shown_pairs(recent)
    memory.shown(shown)
    excluded = _paths_param(exclude, MAX_EXCLUDE)
    # The pairs on screen and in the queue never come again now (exclude: two per pair)
    avoid = avoid + _shown_pairs(excluded)
    # In a small collection, keeping every queued photo out leaves so few that the same
    # handful of pairs comes round again and again: then only the photos on screen are
    # kept out (in a tiny one not even those), and the new pairs may share a photo with a
    # queued one (shared: true). apart: the photos the new pairs really leave out.
    known = {c["path"] for c in candidates}
    shared = False
    if len(known - set(excluded)) < 2 * count + 4:
        shared = True
        on_screen = [p for p in excluded if p in prev]
        excluded = on_screen if len(known - set(on_screen)) >= 4 else []
    # Rather a pair that was not on screen lately; in a small collection, where every pair
    # was, the newer ones are avoided first
    tries = [avoid + shown[-n:] if n else avoid for n in (len(shown), 4, 2, 0) if n <= len(shown)]
    tries = [t for i, t in enumerate(tries) if t not in tries[:i]]
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    gone = False
    pool = candidates
    for _ in range(6):
        for avoid_now in tries:
            pairs = elo.choose_pairs(pool, count, avoid_now, memory=memory, exclude=excluded)
            if pairs:
                break
        missing = [c["path"] for pair in pairs for c in pair if not elo.still_there(c["path"])]
        if not missing:
            break
        # Deleted or moved outside the app since the index or walk saw it: try without it
        gone = True
        pool = [c for c in pool if c["path"] not in missing]
        pairs = []
    if gone:
        forget_candidates()
        media_index.request_update()
    reason = None
    if not pairs:
        reason = "too_few" if len(pool) < 2 else "no_pair"
    described = [[_photo_info(c) for c in pair] for pair in pairs]
    return {
        "scope": sc.info(),
        "pair": described[0] if described else None,
        "pairs": described,
        "reason": reason,
        "shared": shared,
        "apart": [p for p in excluded if p in known],
        "stats": stats,
        "progress": _progress(),
    }


def _comparison_photo(value: Any) -> str:
    path = resolve_in_vault(value, config)
    if not elo.still_there(str(path)) or not elo.is_elo_photo(str(path)):
        raise HTTPException(status_code=404, detail="Foto niet gevonden")
    return str(path)


def _client_token(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not 0 < len(value) <= MAX_TOKEN or not value.isprintable():
        raise HTTPException(status_code=400, detail="Ongeldige client_token")
    return value


@router.post("/compare")
def compare(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save one comparison: { a, b, winner: 'a'|'b'|'draw', folder? | scope?, client_token? }.
    Both photos change in one transaction (Glicko-2, see backend/elo.py). Answers the
    history row with both new ratings and deviations, plus the scope's stats and the
    progress numbers. The same client_token twice counts once (duplicate: true).
    """
    _require_db()
    winner = payload.get("winner")
    if winner not in elo.OUTCOMES:
        raise HTTPException(status_code=400, detail="Kies 'a', 'b' of 'draw' als uitkomst")
    token = _client_token(payload.get("client_token"))
    a, b = _comparison_photo(payload.get("a")), _comparison_photo(payload.get("b"))
    if a == b:
        raise HTTPException(status_code=400, detail="Een foto kan niet tegen zichzelf")
    sc = _scope(payload.get("folder"), payload.get("scope"))
    result = elo.record_comparison(db, a, b, winner, sc.key, client_token=token)
    result["stats"] = _scope_stats(sc, _scope_candidates(sc))
    result["progress"] = _progress()
    return result


@router.post("/compare/skip")
def compare_skip(payload: Dict[str, Any]) -> Dict[str, Any]:
    """'Overslaan': { a, b, folder? | scope? }. Not a comparison; the pair stays away for a while."""
    _require_db()
    a, b = resolve_in_vault(payload.get("a"), config), resolve_in_vault(payload.get("b"), config)
    if a == b:
        raise HTTPException(status_code=400, detail="Een paar heeft twee foto's")
    sc = _scope(payload.get("folder"), payload.get("scope"))
    elo.record_skip(db, str(a), str(b), sc.key)
    return {"status": "ok"}


recovery.register_kind("elo_trash", "Uit 'Welke is mooier?'")


@router.post("/compare/trash")
def compare_trash(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    'Naar de prullenmand' in 'Welke is mooier?': { path }. The photo moves to the trash
    folder (TRASH), never deleted, as one action in the recovery center (Herstelcentrum),
    which puts it back. Its score and comparisons follow it there and back
    (media_index.record_move). Answers { action_id, trash_path }.
    """
    _require_db()
    source = resolve_in_vault(payload.get("path"), config)
    trash = config.TRASH_FOLDER
    # The recovery center only moves files back from inside the vault
    if not is_in_vault(trash, config, allow_internal=True):
        raise HTTPException(status_code=409, detail="De prullenmand staat buiten het archief; verwijderen kan hier niet")
    try:
        with ARCHIVE_LOCK:
            if source.is_symlink() or not source.is_file():
                raise HTTPException(status_code=404, detail="Foto niet gevonden")
            if is_inside(source, trash):
                raise HTTPException(status_code=400, detail="Deze foto staat al in de prullenmand")
            target = move_no_overwrite(source, trash / source.name)
            # If recording fails the file goes straight back and the move is refused
            recorder = recovery.ActionRecorder(db, "elo_trash", "Naar de prullenmand", folder=str(source.parent))
            try:
                recorder.add(source, target)
            except sqlite3.OperationalError:
                raise   # database gone with the disk: 503 (errors.py)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Verwijderen niet vastgelegd, de foto is teruggezet: {e}")
    except ProtectedPathError:
        raise HTTPException(status_code=403, detail="Toegang geweigerd: cache-map van de app")
    except CrossDeviceError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DestinationFolderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileOperationError as e:
        raise HTTPException(status_code=500, detail=f"Naar de prullenmand verplaatsen mislukt: {e}")
    forget_candidates()
    return {"action_id": recorder.action_id, "trash_path": str(target)}


def _stats_for_key(key: Optional[str]) -> Optional[Dict[str, Any]]:
    """The progress numbers of the scope a history row names, or None when that scope is gone."""
    if not key:
        return None
    try:
        sc = _scope(scope=key) if not key.startswith("/") else _scope(folder=key)
        return _scope_stats(sc, _scope_candidates(sc))
    except HTTPException:
        return None


@router.post("/compare/undo")
def compare_undo(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Take back a comparison: { id } (or { folder } / { scope }: the newest one there). Both
    photos go back to exactly what they were; 409 when one of them changed since. Undo
    again for the one before (newest first). The answer has the scope's stats and the
    progress numbers again.
    """
    _require_db()
    comparison_id = payload.get("id")
    if comparison_id is not None and (not isinstance(comparison_id, int) or isinstance(comparison_id, bool)
                                      or not 0 < comparison_id <= MAX_COMPARISON_ID):
        raise HTTPException(status_code=400, detail="Ongeldige vergelijking")
    scope = None
    if comparison_id is None:
        scope = _scope(payload.get("folder"), payload.get("scope")).key
    try:
        result = elo.undo_comparison(db, comparison_id, scope)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except elo.ComparisonConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    result["stats"] = _stats_for_key(result.get("scope"))
    result["progress"] = _progress()
    return result


@router.put("/compare/goal")
def set_goal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The session goal: { goal: 10 | 20 | 50 | 100 }."""
    _require_db()
    goal = payload.get("goal")
    if goal not in elo.GOAL_CHOICES or isinstance(goal, bool):
        raise HTTPException(status_code=400, detail="Kies 10, 20, 50 of 100 als doel")
    db.set_setting(GOAL_KEY, goal)
    return _progress()


def _taken_years(paths: List[str]) -> Dict[str, Optional[int]]:
    """{path: the year it was taken (local time)} from the index; None when unknown."""
    out: Dict[str, Optional[int]] = {}
    with db._connect() as conn:
        for start in range(0, len(paths), 400):
            chunk = paths[start:start + 400]
            for path, taken in conn.execute(
                    f"SELECT path, taken FROM media WHERE path IN ({','.join('?' * len(chunk))})", chunk):
                try:
                    out[path] = time.localtime(taken).tm_year if taken is not None else None
                except (OverflowError, OSError, ValueError):
                    out[path] = None
    return out


def _ranked(sc: _Scope, year: Optional[int]) -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[int, int]]:
    """The judged photos and videos of a scope, best first: [(path, state)], and how many
    of them there are per year (for the year filter)."""
    disabled = get_disabled_folders()
    if sc.folder is not None:
        judged = elo.states_below(db, str(sc.folder))
    else:
        judged = elo.states_of(db, [p for p in sc.paths if isinstance(p, str)])
    rows = [(path, st) for path, st in judged.items()
            if is_show_media(path) and is_folder_enabled(Path(path).parent, disabled)]
    years = _taken_years([p for p, _ in rows])
    per_year: Dict[int, int] = {}
    for path, _ in rows:
        y = years.get(path)
        if y is not None:
            per_year[y] = per_year.get(y, 0) + 1
    if year is not None:
        rows = [(p, st) for p, st in rows if years.get(p) == year]
    rows.sort(key=lambda item: (-item[1]["rating"], item[1]["rd"] if item[1]["games"] else elo.RD_START, item[0]))
    return rows, per_year


def _movement(rows: List[Tuple[str, Dict[str, Any]]]) -> Tuple[Optional[float], Dict[str, Optional[int]], set]:
    """Since the newest session began: how many places each photo moved (positive = up),
    and which photos were compared for the first time in it."""
    start = elo.session_start(db)
    if start is None or not rows:
        return start, {}, set()
    paths = [p for p, _ in rows]
    before = elo.ratings_at(db, paths, start)
    first = elo.first_compared(db, paths)
    newcomers = {p for p, t in first.items() if t >= start}
    old = [(p, before.get(p, st["rating"])) for p, st in rows if p not in newcomers]
    old.sort(key=lambda item: (-item[1], item[0]))
    old_rank = {p: i for i, (p, _) in enumerate(old)}
    new_rank = {p: i for i, (p, _) in enumerate((p, st) for p, st in rows if p not in newcomers)}
    return start, {p: old_rank[p] - new_rank[p] for p in new_rank}, newcomers


def _year_param(year: Optional[int]) -> Optional[int]:
    if year is not None and not 1800 <= year <= 2200:
        raise HTTPException(status_code=400, detail="Ongeldig jaar")
    return year


@router.get("/leaderboard")
def leaderboard(folder: Optional[str] = None, scope: Optional[str] = None,
                limit: int = Query(50, ge=1, le=LEADERBOARD_MAX), year: Optional[int] = None) -> Dict[str, Any]:
    """
    The best-rated photos and videos in a scope (folder, or scope=<kind>:<value>), optionally
    of one year: {scope, total (judged files), settled (0..1, the scope's photos), session_start,
    years: [{year, count}], items: [{rank, path, name, folder, size, rating, rd, low, high, games,
    votes, wins, draws, losses, movement, new}]}, highest rating first. low/high: the 95% band
    (rating -/+ 2 rd) for compared photos, null otherwise. movement: places moved since the
    newest session began (positive = up); new: first compared in that session. Files that are
    gone or in a disabled show folder are left out.
    """
    _require_db()
    sc = _scope(folder, scope)
    year = _year_param(year)
    rows, per_year = _ranked(sc, year)
    started, moved, newcomers = _movement(rows)
    items = []
    for path, st in rows:
        if len(items) >= limit:
            break
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        p = Path(path)
        compared = st["games"] > 0
        band = 2 * st["rd"]
        items.append({"rank": len(items) + 1, "path": path, "name": p.name, "folder": p.parent.name,
                      "size": info.st_size, "rating": st["rating"], "rd": round(st["rd"], 1) if compared else None,
                      "low": round(st["rating"] - band) if compared else None,
                      "high": round(st["rating"] + band) if compared else None,
                      "games": st["games"], "votes": st["votes"], "wins": 0, "draws": 0, "losses": 0,
                      "movement": moved.get(path), "new": path in newcomers})
    if items:
        results = elo.results_of(db, [item["path"] for item in items])
        for item in items:
            item.update(results.get(item["path"], {}))
    settled = elo.settled_share(_scope_candidates(sc)) if sc.folder is not None or sc.paths else 0.0
    return {"scope": sc.info(), "total": len(rows), "settled": round(settled, 4), "session_start": started,
            "year": year, "years": [{"year": y, "count": n} for y, n in sorted(per_year.items(), reverse=True)],
            "items": items}


@router.post("/leaderboard/album")
def leaderboard_album(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    'Top N als album': { n, folder? | scope?, year? } makes a smart album with the photos of
    this scope (and year) rated at least as high as number n now (a live album: it follows
    the ratings). Answers the album (201-like body) or 400/409 from the albums.
    """
    from backend import smart_albums
    _require_db()
    n = payload.get("n")
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= TOP_ALBUM_MAX:
        raise HTTPException(status_code=400, detail=f"Kies een aantal van 1 tot {TOP_ALBUM_MAX}")
    year = payload.get("year")
    if year is not None and (not isinstance(year, int) or isinstance(year, bool)):
        raise HTTPException(status_code=400, detail="Ongeldig jaar")
    year = _year_param(year)
    sc = _scope(payload.get("folder"), payload.get("scope"))
    rows, _ = _ranked(sc, year)
    photos = [(p, st) for p, st in rows if elo.is_elo_photo(p)]
    if not photos:
        raise HTTPException(status_code=400, detail="Er is nog geen ranglijst om een album van te maken")
    cutoff = photos[min(n, len(photos)) - 1][1]["rating"]
    if sc.folder is not None:
        filters: Dict[str, Any] = {"folder": str(sc.folder)}
    else:
        filters = dict(sc.filters or {})
        if not sc.filters:
            raise HTTPException(status_code=400, detail="Van deze collectie kan geen album gemaakt worden")
    filters["kind"] = ["photo"]
    filters["min_rating"] = max(cutoff, int(filters.get("min_rating") or 0))
    if year is not None:
        for key in ("date_from", "date_to", "period", "month"):
            filters.pop(key, None)
        filters["year"] = year
    base = f"Top {n} · {sc.title}" + (f" · {year}" if year is not None else "")
    for attempt in range(1, 20):
        name = base if attempt == 1 else f"{base} ({attempt})"
        try:
            return smart_albums.create_album(db, config, {"name": name[:smart_albums.MAX_NAME], "filters": filters,
                                                          "sort": "-rating", "icon": "fa-star"})
        except smart_albums.AlbumError as e:
            if getattr(e, "status", 400) == 409 and "naam" in str(e):
                continue
            raise HTTPException(status_code=getattr(e, "status", 400), detail=str(e))
    raise HTTPException(status_code=409, detail="Er is al een album met deze naam")
