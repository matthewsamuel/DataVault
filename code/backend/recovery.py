"""
DataVault Dashboard - Recovery center (Herstelcentrum): every action that moved archive
files, in one list, each with its undo.

The app never deletes: it moves (file_ops.move_no_overwrite) and records how to move the
file back. Each feature keeps that record in its own way. A *provider* turns one kind of
record into entries of the recovery list (GET /api/recovery, routes/recovery.py) and says
which endpoint undoes an entry:

- 'keurer'      Keurer delete/move decisions (keurer_decisions; undo: POST /api/keurer/undo)
- 'duplicates'  duplicate cleanup runs (delete_runs; undo: POST /api/duplicates/undo)
- 'actions'     everything newer, in the shared tables of migration 12 (recovery_actions,
                recovery_moves; undo: POST /api/recovery/undo). The move in the viewer
                is the first; later features plug in here.

Adding a new kind of reversible move (near-duplicate 'keep best' runs, assistant moves, ...)
takes two things and no new table, endpoint or UI:

    from backend import recovery
    recovery.register_kind("near_dup", "Bijna-duplicaten")      # once, at import time

    with ARCHIVE_LOCK:                                          # around the checks and moves
        action = recovery.ActionRecorder(db, "near_dup", "Beste foto bewaard", folder=str(folder))
        for src in losers:
            dst = move_no_overwrite(src, target / src.name)
            action.add(src, dst)                                # right after each move

ActionRecorder.add records the move before the next one starts (a crash halfway leaves
every moved file undoable) and, if recording fails, moves the file straight back so no
file is ever moved without a record. The recovery center then lists the action under its
label, with every file and its undo; the undo moves each file back with
move_no_overwrite(rename_on_conflict=False): never over another file, and never a
different file that happens to have the same name (inode and size must match).

A feature with its own undo table can instead register a Provider (see routes/recovery.py
for the Keurer and duplicates ones).
"""
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.file_ops import (
    ARCHIVE_LOCK, CrossDeviceError, DestinationExistsError, FileOperationError, ProtectedPathError,
    move_no_overwrite,
)
from backend.paths import is_in_vault

# How many files of one entry the list shows; the rest is counted
FILES_PER_ENTRY = 20

# Labels of the kinds that record through ActionRecorder (Dutch, shown in the UI)
_KIND_LABELS: Dict[str, str] = {
    "viewer_move": "Verplaatst in de viewer",
}


def register_kind(kind: str, label: str) -> None:
    """Name a kind of action for the recovery list (e.g. 'near_dup' -> 'Bijna-duplicaten')."""
    _KIND_LABELS[kind] = label


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, "Verplaatst")


# ==================== PROVIDERS ====================

@dataclass
class Provider:
    """
    One source of entries for the recovery list.
    newest(limit) -> [(created_at, raw)] newest first, cheap (no disk access);
    entry(raw) -> the entry as the API returns it (checks the disk);
    count() -> how many entries there are in total.
    """
    name: str
    newest: Callable[[int], List[Tuple[float, Any]]]
    entry: Callable[[Any], Dict[str, Any]]
    count: Callable[[], int]


_providers: Dict[str, Provider] = {}


def register_provider(provider: Provider) -> None:
    _providers[provider.name] = provider


def providers() -> List[Provider]:
    return list(_providers.values())


def entries(offset: int = 0, limit: int = 50) -> Dict[str, Any]:
    """
    One chronological list (newest first) over every provider. Only the entries of the
    requested page are checked on disk.
    """
    rows: List[Tuple[float, str, Any]] = []
    total = 0
    for provider in providers():
        total += provider.count()
        rows.extend((created, provider.name, raw) for created, raw in provider.newest(offset + limit))
    rows.sort(key=lambda r: r[0] or 0, reverse=True)
    page = rows[offset:offset + limit]
    return {
        "entries": [_providers[name].entry(raw) for _, name, raw in page],
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < total else None,
    }


# ==================== FILE STATES ====================
#
# What stands in the way of moving one file back, with the reason in Dutch:
#   ok        it can go back
#   beside    it can go back, next to a file that took its old name (_RESTORED_001)
#   missing   it is no longer where it was moved to
#   other     a different file has its name there now
#   unknown   an old record that cannot be checked
#   occupied  its original place is taken (and this kind of undo never renames)
#   refused   the recorded paths are outside the vault

def state_reason(state: str, name: str, place: str) -> Optional[str]:
    return {
        "missing": f"{name} staat niet meer in {place}",
        "other": f"In {place} staat nu een ander bestand met de naam {name}",
        "unknown": "Oudere actie: niet te controleren of het nog hetzelfde bestand is; zet het zo nodig met de hand terug",
        "occupied": f"Op de oorspronkelijke plek staat al een bestand {name}; er wordt niets overschreven",
        "refused": "Dit pad ligt buiten het archief; er wordt niets teruggezet",
        "beside": f"Op de oorspronkelijke plek staat al een bestand {name}; het komt ernaast als {Path(name).stem}_RESTORED_001",
    }.get(state)


def moved_file_state(dst_path: str, ino: Optional[int], size: Optional[int]) -> str:
    """Is the moved file still at dst_path: ok, missing, other or unknown (no fingerprint)."""
    try:
        st = os.lstat(dst_path)
    except OSError:
        return "missing"
    if not stat.S_ISREG(st.st_mode):
        return "missing"
    if ino is None or size is None:
        return "unknown"
    if (st.st_ino, st.st_size) != (ino, size):
        return "other"
    return "ok"


def file_entry(src: str, dst: str, state: str, place: str) -> Dict[str, Any]:
    name = Path(src).name
    return {
        "from": src,
        "to": dst,
        "name": name,
        "state": state,
        "restorable": state in ("ok", "beside"),
        "reason": state_reason(state, Path(dst).name if state in ("missing", "other") else name, place),
    }


def place_of(dst: str, config) -> str:
    """Where a moved file went, as the reasons say it: 'de prullenmand', 'RAW_DUPLICATES' or the folder name."""
    from backend.file_ops import is_inside
    path = Path(dst)
    if config is not None and is_inside(path, config.TRASH_FOLDER):
        return "de prullenmand"
    if config is not None and is_inside(path, config.DUPLICATES_FOLDER):
        return "RAW_DUPLICATES"
    return f"de map {path.parent.name or path.parent}"


def summarize(files: List[Dict[str, Any]], total_files: int) -> Dict[str, Any]:
    """undoable, restorable and (when nothing can go back) the reason of the first file."""
    restorable = sum(1 for f in files if f["restorable"])
    reason = None
    if restorable == 0:
        first = next((f["reason"] for f in files if f["reason"]), None)
        reason = first or "Niets meer terug te zetten"
    return {"restorable": restorable, "undoable": restorable > 0, "reason": reason, "file_count": total_files}


# ==================== RECORDING (migration 12) ====================

def _fingerprint(path: Path) -> Tuple[Optional[int], Optional[int]]:
    try:
        st = os.lstat(path)
    except OSError:
        return None, None
    return st.st_ino, st.st_size


class ActionRecorder:
    """
    Records the files of one user action as they are moved (see the module docstring).
    Call add(src, dst) right after each move_no_overwrite, under ARCHIVE_LOCK.
    """

    def __init__(self, db, kind: str, title: str, folder: Optional[str] = None,
                 details: Optional[Dict[str, Any]] = None):
        self.db = db
        self.kind = kind
        self.title = title
        self.folder = folder
        self.details = json.dumps(details) if details else None
        self.action_id: Optional[int] = None

    def add(self, src: Path, dst: Path) -> int:
        """Record one move; if that fails, move the file straight back and raise."""
        ino, size = _fingerprint(Path(dst))
        try:
            self.action_id = self.db.record_recovery_move(
                self.action_id, self.kind, self.title, Path(src), Path(dst), ino, size,
                folder=self.folder, details=self.details,
            )
        except Exception:
            # Without a record the recovery center cannot find it: put it back now
            try:
                move_no_overwrite(Path(dst), Path(src), rename_on_conflict=False)
            except FileOperationError as back_err:
                print(f"ERROR: could not move {dst} back to {src}: {back_err}")
            raise
        return self.action_id


def _action_files(db, config, action: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for move in db.get_recovery_moves(action["id"]):
        state = _move_state(config, move)
        out.append({**file_entry(move["src_path"], move["dst_path"], state, place_of(move["dst_path"], config)),
                    "move_id": move["id"]})
    return out


def _move_state(config, move: Dict[str, Any]) -> str:
    if not (is_in_vault(move["src_path"], config, allow_internal=True)
            and is_in_vault(move["dst_path"], config, allow_internal=True)):
        return "refused"
    state = moved_file_state(move["dst_path"], move["dst_ino"], move["dst_size"])
    if state == "ok" and os.path.lexists(move["src_path"]):
        return "occupied"
    return state


def action_entry(db, config, action: Dict[str, Any]) -> Dict[str, Any]:
    files = _action_files(db, config, action)
    return {
        "key": f"action:{action['id']}",
        "source": action["kind"],
        "label": kind_label(action["kind"]),
        "title": action["title"],
        "folder": action["folder"],
        "created_at": action["created_at"],
        **summarize(files, len(files)),
        "files": [{k: v for k, v in f.items() if k != "move_id"} for f in files[:FILES_PER_ENTRY]],
        "note": None,
        "undo": {"url": "/api/recovery/undo", "body": {"action_id": action["id"]}},
    }


def actions_provider(db, config) -> Provider:
    return Provider(
        name="actions",
        newest=lambda limit: [(a["created_at"], a) for a in db.get_recovery_actions(limit)],
        entry=lambda action: action_entry(db, config, action),
        count=db.count_recovery_actions,
    )


class UndoError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def undo_action(db, config, action_id: int) -> Dict[str, Any]:
    """
    Move every file of one action back that can go back. Files that cannot (gone, another
    file in their place, original place taken) stay recorded with their reason, so they can
    be tried again later. Raises UndoError(404) for an unknown action and UndoError(409)
    when not a single file can go back.
    """
    with ARCHIVE_LOCK:
        action = db.get_recovery_action(action_id)
        if action is None or action["remaining"] == 0:
            raise UndoError(404, "Deze actie bestaat niet (meer) of is al teruggezet")
        files = _action_files(db, config, action)
        if not any(f["restorable"] for f in files):
            raise UndoError(409, summarize(files, len(files))["reason"])

        restored, skipped = [], []
        for f in files:
            if not f["restorable"]:
                skipped.append({"name": f["name"], "reason": f["reason"]})
                continue
            try:
                back = move_no_overwrite(Path(f["to"]), Path(f["from"]), rename_on_conflict=False)
            except DestinationExistsError:
                skipped.append({"name": f["name"], "reason": state_reason("occupied", f["name"], "")})
                continue
            except ProtectedPathError:
                skipped.append({"name": f["name"], "reason": "Toegang geweigerd: cache-map van de app"})
                continue
            except CrossDeviceError as e:
                skipped.append({"name": f["name"], "reason": f"Terugzetten naar een andere schijf wordt geweigerd: {e}"})
                continue
            except FileOperationError as e:
                skipped.append({"name": f["name"], "reason": f"Terugzetten mislukt: {e}"})
                continue
            db.delete_recovery_move(f["move_id"])
            restored.append({"name": f["name"], "restored_path": str(back)})

        left = db.get_recovery_action(action_id)
    return {
        "action_id": action_id,
        "restored": len(restored),
        "files": restored,
        "skipped": skipped,
        "remaining": left["remaining"] if left else 0,
        "finished_at": time.time(),
    }
