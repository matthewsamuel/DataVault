"""
DataVault Dashboard - DataGatherer routes (backend/datagatherer/)

GET    /api/datagatherer/sources                       every card, grouped by category
GET    /api/datagatherer/sources/{id}                  card + help, settings, setup, raw imports, log
PUT    /api/datagatherer/sources/{id}/settings         {settings?, schedule_enabled?, schedule_minutes?}
PUT    /api/datagatherer/sources/{id}/secrets          {key: value}: into the Keychain, never echoed
DELETE /api/datagatherer/sources/{id}/secrets/{key}
POST   /api/datagatherer/sources/{id}/forget           'Vergeet dit account': every Keychain item of it
POST   /api/datagatherer/sources/{id}/connect          {authorize_url}: OAuth + PKCE consent page
GET    /api/datagatherer/{id}/callback?code&state      where the consent page sends the browser back
POST   /api/datagatherer/sources/{id}/sync             start 'datagatherer.sync.<id>' (a click: trigger user)
POST   /api/datagatherer/sources/{id}/import           start 'datagatherer.import.<id>'
POST   /api/datagatherer/sources/{id}/request-export   {kind}: remember the request; {url, steps, since}
POST   /api/datagatherer/requests/{rid}/dismiss
GET    /api/datagatherer/sources/{id}/candidates       exports of this source in the watch folders
POST   /api/datagatherer/sources/{id}/candidates/import  {path}: copy that one into the vault and read it
POST   /api/datagatherer/sources/{id}/upload?name=     the request body is an export file (picked in
                                                       the browser): copied into the vault, then read
GET    /api/datagatherer/network-log?source&limit      every request DataGatherer made (or refused)
GET    /api/datagatherer/launchd                       the optional launchd agent
POST   /api/datagatherer/launchd/install | /uninstall  only on a click in Instellingen

The app never opens a web page itself: request-export and connect return the URL, and the
page opens it in the user's browser after their click.
"""
import os
import tempfile
from html import escape
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.datagatherer import cards, daemon, keychain, launchd, oauth, raw_store, registry, state
from backend.datagatherer.base import CATEGORIES, ConnectorError, GatherContext
from backend.datagatherer.jobs import WATCH_TYPE, import_type_name, sync_type_name
from backend.jobs import JobError

router = APIRouter()
config = None
db = None
jobs = None
MAX_UPLOAD = 20 * 1024 * 1024 * 1024
MAX_SECRET = 4096


def init_router(cfg, database, job_manager) -> None:
    global config, db, jobs
    config, db, jobs = cfg, database, job_manager


def _connector(source_id: str):
    connector = registry.get(source_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Onbekende bron")
    return connector


def _start(job_type: str, params: Dict[str, Any]) -> JSONResponse:
    if jobs is None:
        raise HTTPException(status_code=503, detail="Achtergrondtaken zijn niet beschikbaar")
    try:
        job, created = jobs.start(job_type, params)
    except JobError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return JSONResponse(status_code=201 if created else 200, content={"job": job, "created": created})


@router.get("/sources")
def list_sources() -> Dict[str, Any]:
    items = [cards.card(config, db, c) for c in registry.connectors()]
    owner = raw_store.owner_folder(config)
    return {
        "sources": items,
        "categories": [c for c in CATEGORIES if any(i["category"] == c for i in items)],
        "owner_folder": str(owner) if owner else None,
        "watch_folders": [str(f) for f in daemon.watch_folders(config)],
        "summary": {
            "total": len(items),
            "current": sum(i["status"] == "actueel" for i in items),
            "behind": sum(i["status"] in ("achter", "fout", "opnieuw_verbinden") for i in items),
            "waiting": sum(i["status"] in ("wacht_op_export", "export_binnen") for i in items),
        },
    }


@router.get("/sources/{source_id}")
def source_detail(source_id: str, request: Request) -> Dict[str, Any]:
    return cards.detail(config, db, _connector(source_id), request.url.port)


@router.put("/sources/{source_id}/settings")
def save_settings(source_id: str, body: Any = Body(...)) -> Dict[str, Any]:
    connector = _connector(source_id)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Verwacht een object")
    if "settings" in body:
        values = body["settings"]
        if not isinstance(values, dict):
            raise HTTPException(status_code=400, detail="settings moet een object zijn")
        fields = {f.key: f for f in connector.settings if f.kind != "secret"}
        current = state.get_settings(db, connector.id)
        for key, value in values.items():
            field = fields.get(key)
            if field is None:
                raise HTTPException(status_code=400, detail=f"Onbekende instelling: {key}")
            if field.kind == "toggle" and not isinstance(value, bool):
                raise HTTPException(status_code=400, detail=f"{field.label} moet aan of uit zijn")
            if field.kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                raise HTTPException(status_code=400, detail=f"{field.label} moet een getal zijn")
            if field.kind == "text":
                if not isinstance(value, str) or len(value) > 500:
                    raise HTTPException(status_code=400, detail=f"{field.label} moet tekst zijn")
                value = value.strip()
            current[key] = value
        state.set_settings(db, connector.id, current)
    fields = {}
    if "schedule_enabled" in body:
        if not isinstance(body["schedule_enabled"], bool):
            raise HTTPException(status_code=400, detail="schedule_enabled moet true of false zijn")
        if body["schedule_enabled"] and not connector.has("api_sync"):
            raise HTTPException(status_code=400, detail=f"{connector.name} heeft geen schema")
        fields["schedule_enabled"] = int(body["schedule_enabled"])
    if "schedule_minutes" in body:
        minutes = body["schedule_minutes"]
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 15 <= minutes <= 24 * 60:
            raise HTTPException(status_code=400, detail="Kies een interval tussen 15 minuten en 24 uur")
        fields["schedule_minutes"] = minutes
    if fields:
        state.update(db, connector.id, **fields)
    return cards.card(config, db, connector)


@router.put("/sources/{source_id}/secrets")
def save_secrets(source_id: str, body: Any = Body(...)) -> Dict[str, Any]:
    connector = _connector(source_id)
    allowed = set(connector.secret_keys())
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail="Verwacht {sleutel: waarde}")
    for key, value in body.items():
        if key not in allowed:
            raise HTTPException(status_code=400, detail=f"Onbekend geheim: {key}")
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_SECRET or "\n" in value.strip():
            raise HTTPException(status_code=400, detail="Plak de waarde zonder spaties of regels ervoor of erna")
    try:
        for key, value in body.items():
            keychain.set(connector.id, key, value.strip())
    except keychain.SecretStoreError as e:
        raise HTTPException(status_code=503, detail=str(e))
    fields: Dict[str, Any] = {"last_error": None}
    if "client_id" in body:
        # A new app means a new consent: a token belongs to the app it was given to
        keychain.delete(connector.id, "token")
        fields.update(connected_at=None, needs_reconnect=0)
    elif not (connector.setup and connector.setup.oauth):
        fields["needs_reconnect"] = 0   # a new API key: worth trying again
    state.update(db, connector.id, **fields)
    return {"stored": sorted(body), "card": cards.card(config, db, connector)}


@router.delete("/sources/{source_id}/secrets/{key}")
def delete_secret(source_id: str, key: str) -> Dict[str, Any]:
    connector = _connector(source_id)
    if key not in connector.secret_keys():
        raise HTTPException(status_code=404, detail="Onbekend geheim")
    try:
        keychain.delete(connector.id, key)
    except keychain.SecretStoreError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return cards.card(config, db, connector)


@router.post("/sources/{source_id}/forget")
def forget(source_id: str) -> Dict[str, Any]:
    """Remove every Keychain item of this source; its data and exports stay in the archive."""
    connector = _connector(source_id)
    try:
        connector.forget(GatherContext(config, db, connector))
    except keychain.SecretStoreError as e:
        raise HTTPException(status_code=503, detail=str(e))
    state.update(db, connector.id, connected_at=None, needs_reconnect=0, schedule_enabled=0, last_error=None,
                 status_note=None)
    return cards.card(config, db, connector)


@router.post("/sources/{source_id}/connect")
def connect(source_id: str, request: Request) -> Dict[str, Any]:
    connector = _connector(source_id)
    if connector.setup is None or not connector.setup.oauth:
        raise HTTPException(status_code=400, detail=f"{connector.name} verbindt zonder browser: sla de instellingen op")
    redirect = oauth.redirect_uri(request.url.port, connector.id)
    state_value, challenge = oauth.begin(connector.id, redirect)
    try:
        url = connector.authorize_url(GatherContext(config, db, connector), redirect, state_value, challenge)
    except ConnectorError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"authorize_url": url, "redirect_uri": redirect}


def _page(title: str, text: str, ok: bool) -> HTMLResponse:
    icon = "fa-circle-check" if ok else "fa-circle-exclamation"
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="nl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DataVault: {escape(title)}</title>
<link rel="stylesheet" href="/vendor/fontawesome-6.4.0/css/all.min.css"><link rel="stylesheet" href="/style.css">
</head><body class="dg-callback-body"><main class="dg-callback card">
<i class="fas {icon} dg-callback-icon{'' if ok else ' is-error'}" aria-hidden="true"></i>
<h1 class="page-title">{escape(title)}</h1><p class="page-subtitle">{escape(text)}</p>
<a class="btn btn-primary" href="/#datagatherer">Terug naar DataGatherer</a>
</main></body></html>""", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/{source_id}/callback")
def oauth_callback(source_id: str, request: Request):
    """
    The consent page sends the browser here (a GET without the session token: the single-use
    state, bound to this source and to this process, is what proves the request is ours).
    """
    connector = registry.get(source_id)
    params = request.query_params
    state_value = params.get("state")
    if connector is None or connector.setup is None or not connector.setup.oauth:
        return _page("Onbekende bron", "Deze koppeling hoort niet bij DataVault.", False)
    found = oauth.finish(connector.id, state_value)
    if found is None:
        return _page("Koppeling verlopen", f"Begin opnieuw met Verbinden bij {connector.name} in DataVault.", False)
    if params.get("error"):
        return _page("Niet verbonden", f"{connector.name} gaf geen toestemming ({params.get('error')}).", False)
    verifier, redirect = found
    gctx = GatherContext(config, db, connector, trigger="user")
    try:
        connector.finish_authorization(gctx, params.get("code") or "", redirect, verifier)
    except ConnectorError as e:
        return _page("Niet verbonden", str(e), False)
    except Exception as e:  # network refused or unreachable: say so, never a stack trace
        return _page("Niet verbonden", f"{connector.name} was niet bereikbaar ({type(e).__name__}).", False)
    try:
        jobs.start(sync_type_name(connector.id), {"trigger": "user"})
    except Exception:
        pass
    return _page(f"{connector.name} is verbonden",
                 "Je kunt dit tabblad sluiten. DataVault haalt nu je laatste nummers op.", True)


@router.post("/sources/{source_id}/sync")
def start_sync(source_id: str):
    connector = _connector(source_id)
    if not (connector.has("api_sync") or connector.has("local_import")):
        raise HTTPException(status_code=400, detail=f"{connector.name} kan niet bijwerken")
    gctx = GatherContext(config, db, connector)
    if not connector.is_configured(gctx):
        raise HTTPException(status_code=409, detail=f"{connector.name} is nog niet verbonden: klik Verbinden")
    return _start(sync_type_name(connector.id), {"trigger": "user"})


@router.post("/sources/{source_id}/import")
def start_import(source_id: str):
    connector = _connector(source_id)
    if not connector.has("import"):
        raise HTTPException(status_code=400, detail=f"{connector.name} heeft niets om in te lezen")
    cards.forget_existing_cache()
    return _start(import_type_name(connector.id), {})


@router.post("/sources/{source_id}/request-export")
def request_export(source_id: str, body: Any = Body(default=None)) -> Dict[str, Any]:
    connector = _connector(source_id)
    if not connector.has("export_request"):
        raise HTTPException(status_code=400, detail=f"{connector.name} heeft geen exportpagina")
    kind_id = (body or {}).get("kind") if isinstance(body, dict) else None
    kind = connector.export_kind(kind_id) if kind_id else connector.exports[0]
    if kind is None:
        raise HTTPException(status_code=400, detail="Onbekende soort export")
    since = cards.since_for(connector, GatherContext(config, db, connector))
    request_id = state.open_request(db, connector.id, kind.id, since)
    return {"request_id": request_id, "url": kind.request_url, "label": kind.label, "steps": list(kind.steps),
            "since": since, "expected_days": list(kind.expected_days),
            "watching": [str(f) for f in daemon.watch_folders(config)] if connector.has("export_watch") else [],
            "card": cards.card(config, db, connector)}


@router.post("/requests/{request_id}/dismiss")
def dismiss_request(request_id: int) -> Dict[str, Any]:
    row = state.get_request(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Deze aanvraag bestaat niet")
    state.close_requests(db, row["source_id"], "dismissed", request_id=request_id)
    connector = registry.get(row["source_id"])
    return cards.card(config, db, connector) if connector else {}


@router.get("/sources/{source_id}/candidates")
def list_candidates(source_id: str) -> Dict[str, Any]:
    connector = _connector(source_id)
    if not connector.has("export_watch"):
        raise HTTPException(status_code=400, detail=f"{connector.name} heeft geen exports om te zoeken")
    found = daemon.candidates(config, db, connector)
    return {"folders": [str(f) for f in daemon.watch_folders(config)], "candidates": found}


@router.post("/sources/{source_id}/candidates/import")
def import_candidate(source_id: str, body: Any = Body(...)):
    connector = _connector(source_id)
    path = body.get("path") if isinstance(body, dict) else None
    if not isinstance(path, str):
        raise HTTPException(status_code=400, detail="Geef het pad van de export")
    # Only a file the watcher itself reports for this source (never an arbitrary path)
    if not any(c["path"] == path for c in daemon.candidates(config, db, connector)):
        raise HTTPException(status_code=404, detail="Dit bestand is (nu) geen export van deze bron")
    return _start(WATCH_TYPE, {"source": connector.id, "path": path})


@router.post("/sources/{source_id}/upload")
async def upload(source_id: str, request: Request, name: str = ""):
    """
    An export picked in the browser: the body is the file. It is streamed into a temporary file
    in the app's cache folder in the vault (never the nearly full internal disk), recognised,
    then archived like a download (copied into its dated folder, so the raw store has one way
    in) and read. Async only to receive the stream: every disk step runs in the thread pool.
    """
    connector = _connector(source_id)
    if not connector.has("import"):
        raise HTTPException(status_code=400, detail=f"{connector.name} kan geen bestand inlezen")
    base = os.path.basename(name or "")
    if not base or base.startswith(".") or len(base) > 200 or not base.lower().endswith(daemon.EXPORT_SUFFIXES):
        raise HTTPException(status_code=400, detail="Kies een exportbestand (.zip of .json)")
    temp = await run_in_threadpool(_upload_temp, base)
    try:
        out = await run_in_threadpool(open, temp, "wb")
        size, buffer = 0, bytearray()
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="Dit bestand is te groot")
                buffer += chunk
                if len(buffer) >= UPLOAD_BUFFER:
                    await run_in_threadpool(out.write, bytes(buffer))
                    buffer.clear()
            await run_in_threadpool(out.write, bytes(buffer))
        finally:
            await run_in_threadpool(out.close)
        if size == 0:
            raise HTTPException(status_code=400, detail="Het bestand is leeg")
        return await run_in_threadpool(_archive_upload, connector, temp, base)
    finally:
        await run_in_threadpool(_drop_temp, temp)


UPLOAD_BUFFER = 4 * 1024 * 1024


def _upload_temp(base: str) -> Path:
    staging = config.CACHE_FOLDER / "datagatherer-upload"
    staging.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=staging, suffix="-" + base)
    os.close(fd)
    return Path(name)


def _drop_temp(temp: Path) -> None:
    """The temporary upload in the app's own cache folder (never an archive file)."""
    try:
        os.unlink(temp)
    except OSError:
        pass


def _archive_upload(connector, temp: Path, base: str) -> Dict[str, Any]:
    kind = connector.detect(temp, raw_store.read_zip_names(temp))
    if kind is None:
        raise HTTPException(status_code=400, detail=f"{base} is geen export van {connector.name}")
    try:
        raw, created = raw_store.archive_file(config, db, connector, kind, temp, "upload", original_name=base)
    except raw_store.RawStoreError as e:
        raise HTTPException(status_code=507, detail=str(e))
    state.close_requests(db, connector.id, "arrived", raw_import_id=raw["id"])
    job = None
    if raw["status"] != "imported":
        job, _ = jobs.start(import_type_name(connector.id), {"raw_import_id": raw["id"]})
    return {"raw_import_id": raw["id"], "created": created, "path": raw["path"], "job": job,
            "message": None if created else "Deze export stond al in het archief"}


@router.get("/network-log")
def get_network_log(source: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    if source is not None:
        _connector(source)
    limit = max(1, min(int(limit), 500))
    names = {c.id: c.name for c in registry.connectors()}
    entries = state.network_log(db, source, limit)
    for entry in entries:
        entry["source_name"] = names.get(entry["source_id"], entry["source_id"])
    return {"entries": entries}


@router.get("/launchd")
def launchd_status() -> Dict[str, Any]:
    return {**launchd.status(), "sources": [c.id for c in registry.connectors() if c.launchd]}


@router.post("/launchd/install")
def launchd_install() -> Dict[str, Any]:
    try:
        return launchd.install(config.config_path)
    except (launchd.LaunchdError, OSError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/launchd/uninstall")
def launchd_uninstall() -> Dict[str, Any]:
    try:
        return launchd.uninstall()
    except (launchd.LaunchdError, OSError) as e:
        raise HTTPException(status_code=409, detail=str(e))
