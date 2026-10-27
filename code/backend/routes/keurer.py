"""
DataVault Dashboard - Foto Keurer (Tinder-style foto beoordeling)
"""
from fastapi import APIRouter, HTTPException, Query
from pathlib import Path
from typing import Dict, Any
import os
import random
import threading

from backend.config import Config, validate_path_in_vault, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS
from backend.file_ops import (
    move_no_overwrite, is_inside, FileOperationError, CrossDeviceError, DestinationExistsError,
)

router = APIRouter()

config: Config = None
db = None


def init_router(cfg: Config, database):
    global config, db
    config = cfg
    db = database


@router.get("/files")
def get_keurer_files(folder: str = Query(...)) -> Dict[str, Any]:
    """
    Geeft nog niet beoordeelde media-bestanden terug uit een map.
    Alleen foto's en video's (geen audio).
    Bestanden die niet geopend kunnen worden worden overgeslagen.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuratie niet geïnitialiseerd")

    folder_path = Path(folder)

    if not validate_path_in_vault(folder_path, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="Toegang geweigerd: pad buiten vault")

    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="Map niet gevonden")

    decided_set = set(db.get_keurer_decided_paths(str(folder_path)))
    media_extensions = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

    files = []
    skipped = 0

    try:
        entries = list(folder_path.iterdir())
    except PermissionError:
        raise HTTPException(status_code=403, detail="Kan map niet lezen")

    for item in sorted(entries, key=lambda x: x.name.lower()):
        try:
            if not item.is_file():
                continue
            if item.name.startswith('.'):
                continue
            if item.suffix.lower() not in media_extensions:
                continue

            path_str = str(item)
            if path_str in decided_set:
                continue

            # Controleer of het bestand leesbaar is (robuustheid)
            item.stat()

            files.append({
                "path": path_str,
                "name": item.name,
                "is_video": item.suffix.lower() in VIDEO_EXTENSIONS,
            })
        except (PermissionError, OSError):
            skipped += 1
            continue

    random.shuffle(files)

    return {
        "folder": str(folder_path),
        "files": files,
        "remaining": len(files),
        "decided": len(decided_set),
        "skipped": skipped,
    }


# Bestandsoperaties van de Keurer één voor één, zodat twee snelle kliks niet door elkaar lopen
_file_lock = threading.Lock()

UNDO_STACK_SIZE = 20


def _refuse(status: int, detail: str):
    raise HTTPException(status_code=status, detail=detail)


def _check_archive_path(path: Path, what: str):
    """Pad moet in de vault liggen en niet in de cache van de app."""
    if not validate_path_in_vault(path, config.VAULT_ROOT):
        _refuse(403, f"Toegang geweigerd: {what} buiten vault")
    if is_inside(path, config.CACHE_FOLDER):
        _refuse(403, f"Toegang geweigerd: {what} in de cache-map")


def _is_regular_file(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


def _file_info(path: Path) -> Dict[str, Any]:
    return {
        "path": str(path),
        "name": path.name,
        "is_video": path.suffix.lower() in VIDEO_EXTENSIONS,
    }


def _undo_entry(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": rec["id"],
        "decision": rec["decision"],
        "folder": rec["folder"],
        "decided_at": rec["decided_at"],
        "dest_path": rec["dest_path"],
        "file": _file_info(Path(rec["path"])),
    }


def _is_undoable(rec: Dict[str, Any]) -> bool:
    if rec["decision"] == "keep":
        return True
    dest = rec.get("dest_path")
    return bool(dest) and _is_regular_file(Path(dest)) and not os.path.lexists(rec["path"])


@router.post("/decide")
def decide_file(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sla een beslissing op voor een bestand en voer de bijbehorende verplaatsing uit.
    - keep:   alleen opslaan.
    - delete: bestand naar de prullenmand (TRASH) verplaatsen.
    - move:   bestand naar destination_folder verplaatsen.
    Verplaatsen gaat altijd via file_ops.move_no_overwrite: atomair, metadata
    blijft behouden en er wordt nooit iets overschreven. Waar het bestand
    terechtkomt, wordt in de database bewaard zodat undo server-side kan.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuratie niet geïnitialiseerd")

    path_str = body.get("path")
    folder_str = body.get("folder")
    decision = body.get("decision")

    if not path_str or not folder_str or decision not in ("keep", "delete", "move"):
        _refuse(400, "Ontbrekende of ongeldige velden")

    file_path = Path(path_str)
    folder_path = Path(folder_str)

    _check_archive_path(file_path, "pad")
    _check_archive_path(folder_path, "map")
    if file_path.parent.resolve() != folder_path.resolve():
        _refuse(400, "Bestand staat niet in de gekozen map")
    if not _is_regular_file(file_path):
        _refuse(404, "Bestand niet gevonden")

    with _file_lock:
        dest_path = None
        try:
            if decision == "delete":
                if is_inside(file_path, config.TRASH_FOLDER):
                    _refuse(400, "Bestand staat al in de prullenmand")
                config.TRASH_FOLDER.mkdir(parents=True, exist_ok=True)
                dest_path = move_no_overwrite(file_path, config.TRASH_FOLDER / file_path.name)

            elif decision == "move":
                dest_str = body.get("destination_folder")
                if not dest_str:
                    _refuse(400, "Geen doelmap opgegeven")
                dest_folder = Path(dest_str)
                _check_archive_path(dest_folder, "doelmap")
                if not dest_folder.is_dir():
                    _refuse(404, "Doelmap niet gevonden")
                if dest_folder.resolve() == file_path.parent.resolve():
                    _refuse(400, "Bestand staat al in deze map")
                dest_path = move_no_overwrite(file_path, dest_folder / file_path.name)
        except CrossDeviceError as e:
            _refuse(409, f"Verplaatsen naar een andere schijf wordt geweigerd: {e}")
        except FileOperationError as e:
            _refuse(500, f"Verplaatsen mislukt: {e}")

        try:
            decision_id = db.record_keurer_decision(
                path_str, folder_str, decision, str(dest_path) if dest_path else None
            )
        except Exception as e:
            # Zonder record kan undo het bestand niet terugvinden: zet het meteen terug
            if dest_path:
                try:
                    move_no_overwrite(dest_path, file_path, rename_on_conflict=False)
                except FileOperationError as back_err:
                    print(f"ERROR: kon {dest_path} niet terugzetten naar {file_path}: {back_err}")
            _refuse(500, f"Beslissing opslaan mislukt: {e}")

    return {
        "success": True,
        "id": decision_id,
        "decision": decision,
        "dest_path": str(dest_path) if dest_path else None,
        "trash_path": str(dest_path) if decision == "delete" else None,
        "stats": db.get_keurer_stats(),
    }


@router.get("/history")
def get_undo_history() -> Dict[str, Any]:
    """
    De laatste beslissingen die nog ongedaan gemaakt kunnen worden (nieuwste eerst),
    zodat de undo-stapel een herlaad van de pagina overleeft.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuratie niet geïnitialiseerd")

    recent = db.get_recent_keurer_decisions(limit=UNDO_STACK_SIZE * 5)
    entries = [_undo_entry(r) for r in recent if _is_undoable(r)][:UNDO_STACK_SIZE]
    return {"entries": entries}


@router.post("/undo")
def undo_decision(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Maak een beslissing ongedaan op basis van alleen haar id.
    Alle paden komen uit de database, nooit van de client, en worden opnieuw gecontroleerd:
    - het originele pad moet in de vault liggen en vrij zijn (anders 409);
    - bij 'delete' moet het bestand in TRASH staan, bij 'move' in de vault.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuratie niet geïnitialiseerd")

    decision_id = body.get("id")
    if not isinstance(decision_id, int) or isinstance(decision_id, bool):
        _refuse(400, "Ontbrekend of ongeldig beslissing-id")

    with _file_lock:
        rec = db.get_keurer_decision(decision_id)
        if not rec:
            _refuse(404, "Beslissing niet gevonden (al ongedaan gemaakt?)")

        original = Path(rec["path"])
        restored_path = None

        if rec["decision"] in ("delete", "move"):
            if not rec["dest_path"]:
                _refuse(409, "Van deze oudere beslissing is niet bewaard waar het bestand staat; "
                             "zet het zo nodig handmatig terug")
            source = Path(rec["dest_path"])

            _check_archive_path(original, "origineel pad")
            if rec["decision"] == "delete":
                if not is_inside(source, config.TRASH_FOLDER):
                    _refuse(403, "Toegang geweigerd: bestand staat niet in de prullenmand")
            else:
                _check_archive_path(source, "verplaatst bestand")

            if not _is_regular_file(source):
                place = "de prullenmand" if rec["decision"] == "delete" else "de doelmap"
                _refuse(410, f"{original.name} staat niet meer in {place}")
            if os.path.lexists(original):
                _refuse(409, f"Op de oorspronkelijke plek staat al een bestand {original.name}; "
                             "er wordt niets overschreven")

            try:
                move_no_overwrite(source, original, rename_on_conflict=False)
            except DestinationExistsError:
                _refuse(409, f"Op de oorspronkelijke plek staat al een bestand {original.name}; "
                             "er wordt niets overschreven")
            except CrossDeviceError as e:
                _refuse(409, f"Terugzetten naar een andere schijf wordt geweigerd: {e}")
            except FileOperationError as e:
                _refuse(500, f"Terugzetten mislukt: {e}")
            restored_path = str(original)

        db.delete_keurer_decision_by_id(decision_id)

    return {
        "success": True,
        "id": decision_id,
        "decision": rec["decision"],
        "folder": rec["folder"],
        "file": _file_info(original),
        "restored_path": restored_path,
        "stats": db.get_keurer_stats(),
    }


@router.get("/stats")
def get_keurer_stats() -> Dict[str, Any]:
    """
    Geeft streak, dagelijks voortgang en totaal terug.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuratie niet geïnitialiseerd")

    return db.get_keurer_stats()
