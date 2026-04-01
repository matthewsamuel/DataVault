"""
DataVault Dashboard - Foto Keurer (Tinder-style foto beoordeling)
"""
from fastapi import APIRouter, HTTPException, Query
from pathlib import Path
from typing import Dict, Any
import random
from datetime import datetime

from backend.config import Config, validate_path_in_vault, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS

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


@router.post("/decide")
def decide_file(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sla een houd/verwijder-beslissing op voor een bestand.
    Bij 'delete' wordt het bestand naar de prullenmand verplaatst.
    Metadata wordt NIET gewijzigd.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuratie niet geïnitialiseerd")

    path_str = body.get("path")
    folder_str = body.get("folder")
    decision = body.get("decision")

    if not path_str or not folder_str or decision not in ("keep", "delete"):
        raise HTTPException(status_code=400, detail="Ontbrekende of ongeldige velden")

    file_path = Path(path_str)

    if not validate_path_in_vault(file_path, config.VAULT_ROOT):
        raise HTTPException(status_code=403, detail="Toegang geweigerd: pad buiten vault")

    trash_path = None

    if decision == "delete":
        if file_path.exists() and file_path.is_file():
            try:
                config.TRASH_FOLDER.mkdir(parents=True, exist_ok=True)
                dest = config.TRASH_FOLDER / file_path.name

                # Naambotsing: voeg tijdstempel toe
                if dest.exists():
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    dest = config.TRASH_FOLDER / f"{file_path.stem}_{ts}{file_path.suffix}"

                # Verplaats met behoud van metadata
                from backend.routes.media import _safe_move_file
                _safe_move_file(file_path, dest)
                trash_path = str(dest)
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Verplaatsen naar prullenmand mislukt: {e}"
                )
        # Als het bestand al weg is: beslissing toch opslaan

    db.record_keurer_decision(path_str, folder_str, decision)

    return {"success": True, "decision": decision, "trash_path": trash_path}


@router.post("/undo")
def undo_decision(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Maak de laatste beslissing ongedaan.
    - Verwijdert het record uit keurer_decisions.
    - Bij 'delete': verplaatst het bestand terug van prullenmand naar origineel pad.
    - Bij 'move': verplaatst het bestand terug naar de originele map.
    - Bij 'keep': alleen het DB-record verwijderen.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuratie niet geïnitialiseerd")

    path_str    = body.get("path")      # origineel pad
    folder_str  = body.get("folder")
    decision    = body.get("decision")
    trash_path  = body.get("trash_path")   # voor delete
    moved_to    = body.get("moved_to")     # voor move

    if not path_str or not folder_str:
        raise HTTPException(status_code=400, detail="Ontbrekende velden")

    from backend.routes.media import _safe_move_file

    restored_path = None

    if decision == "delete" and trash_path:
        trash = Path(trash_path)
        original = Path(path_str)
        if trash.exists():
            try:
                _safe_move_file(trash, original)
                restored_path = str(original)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Terugzetten mislukt: {e}")

    elif decision == "move" and moved_to:
        src = Path(moved_to)
        original = Path(path_str)
        if src.exists():
            try:
                _safe_move_file(src, original)
                restored_path = str(original)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Terugzetten mislukt: {e}")

    # Verwijder beslissing uit database
    db.delete_keurer_decision(path_str, folder_str)

    return {"success": True, "restored_path": restored_path}


@router.get("/stats")
def get_keurer_stats() -> Dict[str, Any]:
    """
    Geeft streak, dagelijks voortgang en totaal terug.
    """
    if not config:
        raise HTTPException(status_code=500, detail="Configuratie niet geïnitialiseerd")

    return db.get_keurer_stats()
