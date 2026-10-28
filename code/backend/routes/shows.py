from fastapi import APIRouter, HTTPException, Query
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import heapq
import os
import random
import stat
import threading
import time
from backend import elo, media_index
from backend.config import Config, is_photo, is_video
from backend.paths import resolve_in_vault, vault_folder_or_none, walk_archive

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
# Other collections (albums) plug in with elo.register_scope(kind, resolve) and are asked
# for with scope=<kind>:<value> instead of folder; their photos are looked up in the index.

CANDIDATE_TTL = 60
LEADERBOARD_MAX = 200
MAX_COMPARISON_ID = 2 ** 63 - 1
_candidates_lock = threading.Lock()
_candidates_cache: Dict[str, Any] = {}


class _Scope:
    """Where a round runs: key (stored in the history), title, and either a folder or the
    paths of a registered collection."""

    def __init__(self, key: str, title: str, folder: Optional[Path] = None, paths: Optional[List[str]] = None):
        self.key, self.title, self.folder, self.paths = key, title, folder, paths

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
        return _Scope(found["key"], found["title"], paths=found["paths"])
    target = _scope_folder(folder)
    return _Scope(str(target), _scope_title(target), folder=target)


def _base_candidates(target: Path) -> List[Dict[str, Any]]:
    """Every photo below target that may take part: [{path, id, size, taken, taken_source,
    series}] (series: see elo.mark_series)."""
    folder = str(target)
    disabled = get_disabled_folders()
    if db is not None and media_index.usable(db):
        filters = {"kind": "photo", "folder": folder}
        key = ("index", media_index.selection_version(db, filters), tuple(disabled))
        with _candidates_lock:
            cached = _candidates_cache.get(folder)
        if cached and cached[0] == key:
            return cached[2]
        rows, offset = [], 0
        while True:
            page = media_index.query(db, filters, sort="path", offset=offset, limit=media_index.MAX_LIMIT)
            rows.extend(page)
            if len(page) < media_index.MAX_LIMIT:
                break
            offset += len(page)
        base = [{"path": r["path"], "id": r["id"], "size": r["size"], "taken": r["taken"],
                 "taken_source": r["taken_source"]}
                for r in rows if elo.is_elo_photo(r["path"]) and is_folder_enabled(Path(r["folder"]), disabled)]
    else:
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


def _collection_candidates(paths: List[str]) -> List[Dict[str, Any]]:
    """The photos of a registered collection that may take part (inside the vault, a photo
    a browser can show, not in a disabled show folder), with what the index knows of them."""
    root = str(config.VAULT_ROOT).rstrip("/") + "/"
    disabled = get_disabled_folders()
    wanted = sorted({p for p in paths if isinstance(p, str) and p.startswith(root) and elo.is_elo_photo(p)
                     and is_folder_enabled(Path(p).parent, disabled)})
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
    return base


def _scope_candidates(sc: _Scope) -> List[Dict[str, Any]]:
    if sc.folder is not None:
        base = _base_candidates(sc.folder)
        ratings = elo.ratings_below(db, str(sc.folder))
    else:
        base = _collection_candidates(sc.paths)
        ratings = elo.ratings_of(db, [item["path"] for item in base])
    return _with_ratings(base, ratings)


def _scope_stats(sc: _Scope, candidates: List[Dict[str, Any]]) -> Dict[str, int]:
    return {"photos": len(candidates), "compared": sum(1 for c in candidates if c["games"]),
            "comparisons": elo.comparisons_in_scope(db, sc.key)}


def forget_candidates() -> None:
    with _candidates_lock:
        _candidates_cache.clear()


def _with_ratings(base: List[Dict[str, Any]], ratings: Dict[str, Tuple[int, int, int]]) -> List[Dict[str, Any]]:
    out = []
    for item in base:
        rating, games, votes = ratings.get(item["path"], (RATING_START, 0, 0))
        out.append({**item, "rating": rating, "games": games, "votes": votes})
    return out


def _photo_info(item: Dict[str, Any]) -> Dict[str, Any]:
    p = Path(item["path"])
    return {"path": item["path"], "name": p.name, "folder": p.parent.name,
            "rating": item["rating"], "games": item["games"], "votes": item.get("votes", 0)}


def _require_db():
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")


@router.get("/compare/pair")
def compare_pair(folder: Optional[str] = None, scope: Optional[str] = None,
                 prev: Optional[List[str]] = Query(None)) -> Dict[str, Any]:
    """
    The next two photos to compare in a scope (folder, or scope=<kind>:<value> for a
    registered collection): {scope: {key, path, title}, pair: [a, b] or null, reason (when
    null: 'too_few' or 'no_pair'), stats: {photos, compared, comparisons}}.
    prev (twice): the pair on screen now, which never comes back straight away; neither
    does the last pair compared in this scope.
    """
    _require_db()
    sc = _scope(folder, scope)
    candidates = _scope_candidates(sc)
    avoid = []
    if prev and len(prev) == 2:
        avoid.append(prev)
    last = elo.last_comparison(db, sc.key)
    if last:
        avoid.append((last["path_a"], last["path_b"]))
    stats = _scope_stats(sc, candidates)
    pair, gone = None, False
    pool = candidates
    for _ in range(6):
        pair = elo.choose_pair(pool, avoid)
        if pair is None:
            break
        missing = [c["path"] for c in pair if not elo.still_there(c["path"])]
        if not missing:
            break
        # Deleted or moved outside the app since the index or walk saw it: try without it
        gone = True
        pool = [c for c in pool if c["path"] not in missing]
        pair = None
    if gone:
        forget_candidates()
        media_index.request_update()
    reason = None
    if pair is None:
        reason = "too_few" if len(pool) < 2 else "no_pair"
    return {
        "scope": sc.info(),
        "pair": [_photo_info(c) for c in pair] if pair else None,
        "reason": reason,
        "stats": stats,
    }


def _comparison_photo(value: Any) -> str:
    path = resolve_in_vault(value, config)
    if not elo.still_there(str(path)) or not elo.is_elo_photo(str(path)):
        raise HTTPException(status_code=404, detail="Foto niet gevonden")
    return str(path)


@router.post("/compare")
def compare(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save one comparison: { a, b, winner: 'a'|'b', folder? | scope? }. Both ratings change
    in one transaction (Elo, see backend/elo.py). Answers the history row with both new ratings.
    """
    _require_db()
    winner = payload.get("winner")
    if winner not in ("a", "b"):
        raise HTTPException(status_code=400, detail="Kies 'a' of 'b' als winnaar")
    a, b = _comparison_photo(payload.get("a")), _comparison_photo(payload.get("b"))
    if a == b:
        raise HTTPException(status_code=400, detail="Een foto kan niet tegen zichzelf")
    sc = _scope(payload.get("folder"), payload.get("scope"))
    return elo.record_comparison(db, a, b, winner, sc.key)


def _stats_for_key(key: Optional[str]) -> Optional[Dict[str, int]]:
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
    ratings go back to exactly what they were; 409 when one of the photos changed since.
    The answer has the scope's progress numbers again (stats).
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
    return result


@router.get("/leaderboard")
def leaderboard(folder: Optional[str] = None, scope: Optional[str] = None,
                limit: int = Query(50, ge=1, le=LEADERBOARD_MAX)) -> Dict[str, Any]:
    """
    The best-rated photos and videos in a scope (folder, or scope=<kind>:<value>): {scope,
    total (judged files), items: [{rank, path, name, folder, rating, games, votes, wins}]},
    highest rating first. Files that are gone or in a disabled show folder are left out.
    """
    _require_db()
    sc = _scope(folder, scope)
    disabled = get_disabled_folders()
    if sc.folder is not None:
        judged = elo.ratings_below(db, str(sc.folder))
    else:
        judged = elo.ratings_of(db, [p for p in sc.paths if isinstance(p, str)])
    rows = [(path, value) for path, value in judged.items()
            if is_show_media(path) and is_folder_enabled(Path(path).parent, disabled)]
    rows.sort(key=lambda item: (-item[1][0], -item[1][1], item[0]))
    items = []
    for path, (rating, games, votes) in rows:
        if len(items) >= limit:
            break
        if not elo.still_there(path):
            continue
        p = Path(path)
        items.append({"rank": len(items) + 1, "path": path, "name": p.name, "folder": p.parent.name,
                      "rating": rating, "games": games, "votes": votes, "wins": 0})
    if items:
        wins = elo.wins_of(db, [item["path"] for item in items])
        for item in items:
            item["wins"] = wins.get(item["path"], 0)
    return {"scope": sc.info(), "total": len(rows), "items": items}
