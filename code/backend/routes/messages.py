"""
DataVault Dashboard - Berichten: the merged conversations of every chat source

GET  /api/messages/sources                                  per message source: name, icon, counts, Pasha toggle
GET  /api/messages/conversations?source=a,b&q=&limit&offset  conversations, newest first
GET  /api/messages/conversations/{id}?before=&around=&limit= a thread (time order, superseded left out)
GET  /api/messages/search?q=&source=a,b&limit=              full-text search (FTS5), newest first
GET  /api/messages/attachments/{message_id}/{index}         a small JPEG preview of an image attachment
                                                            that lives in an archived export

Read-only: imports happen in DataGatherer (backend/datagatherer/). An attachment preview is
read from the archived export itself (a zip member or a file below a registered folder),
never from anywhere else, and only for images; nothing is extracted to disk.
"""
import io
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend.datagatherer import messages_store, raw_store, state

router = APIRouter()
config = None
db = None
PREVIEW_PX = 480
MAX_PREVIEW_SOURCE = 40 * 1024 * 1024


def init_router(cfg, database) -> None:
    global config, db
    config, db = cfg, database


def _sources(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    known = {c.id for c in messages_store.message_sources()}
    wanted = [s for s in value.split(",") if s]
    unknown = [s for s in wanted if s not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Onbekende bron: {unknown[0]}")
    return wanted


@router.get("/sources")
def sources() -> Dict[str, Any]:
    counts = messages_store.counts(db)
    out = []
    for c in messages_store.message_sources():
        n = counts.get(c.id, {"conversations": 0, "messages": 0})
        out.append({"id": c.id, "name": c.name, "icon": c.icon, "conversations": n["conversations"],
                    "messages": n["messages"], "record_label": list(c.record_label),
                    "pasha": state.get_settings(db, c.id).get(messages_store.PASHA_SETTING) is True})
    return {"sources": out}


@router.get("/conversations")
def list_conversations(source: Optional[str] = None, q: Optional[str] = Query(default=None, max_length=200),
                       limit: int = Query(default=200, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> Dict[str, Any]:
    return messages_store.conversations(db, _sources(source), (q or "").strip() or None, limit, offset)


@router.get("/conversations/{conv_id}")
def get_thread(conv_id: int, before: Optional[int] = None, around: Optional[int] = None,
               limit: int = Query(default=200, ge=1, le=1000)) -> Dict[str, Any]:
    data = messages_store.thread(db, conv_id, before=before, around=around, limit=limit)
    if data["conversation"] is None:
        raise HTTPException(status_code=404, detail="Dit gesprek bestaat niet")
    return data


@router.get("/search")
def search(q: str = Query(..., max_length=200), source: Optional[str] = None,
           limit: int = Query(default=50, ge=1, le=200)) -> Dict[str, Any]:
    return {"q": q, "results": messages_store.search(db, q, _sources(source), limit),
            "marks": [messages_store.HIT_START, messages_store.HIT_END]}


def _read_member(raw: Dict[str, Any], member: str) -> Optional[bytes]:
    """The bytes of a member of an archived export (zip or registered folder); None when it is not there."""
    path = Path(raw["path"])
    if raw["is_folder"]:
        target = (path / member)
        try:
            resolved = target.resolve()
            if not resolved.is_relative_to(path.resolve()) or not resolved.is_file() or target.is_symlink():
                return None
            if resolved.stat().st_size > MAX_PREVIEW_SOURCE:
                return None
            return resolved.read_bytes()
        except OSError:
            return None
    try:
        with zipfile.ZipFile(path) as zf:
            infos = {i.filename: i for i in zf.infolist() if not i.is_dir()}
            info = infos.get(member)
            if info is None:   # an export packed inside one more folder
                info = next((i for n, i in infos.items() if n.endswith("/" + member)), None)
            if info is None or info.file_size > MAX_PREVIEW_SOURCE:
                return None
            return raw_store._read_member(zf, info)
    except (zipfile.BadZipFile, OSError, raw_store.RawStoreError):
        return None


@router.get("/attachments/{message_id}/{index}")
def attachment_preview(message_id: int, index: int) -> Response:
    item = messages_store.get_attachment(db, message_id, index)
    if not item or not item.get("member") or not item.get("raw_import_id") or not messages_store._is_image(item):
        raise HTTPException(status_code=404, detail="Geen voorbeeld voor deze bijlage")
    raw = state.get_raw(db, int(item["raw_import_id"]))
    if raw is None:
        raise HTTPException(status_code=404, detail="De export van deze bijlage staat niet meer in het archief")
    data = _read_member(raw, str(item["member"]))
    if data is None:
        raise HTTPException(status_code=404, detail="Deze bijlage zit niet in de export")
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((PREVIEW_PX, PREVIEW_PX))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, "JPEG", quality=82)
    except Exception:   # not an image after all, or one Pillow cannot read (HEIC)
        raise HTTPException(status_code=404, detail="Geen voorbeeld voor deze bijlage")
    return Response(out.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
