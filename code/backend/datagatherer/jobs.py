"""
DataGatherer - background job types (backend/jobs.py)

Per connector, so different sources run side by side (the JobManager runs one job of a
type at a time):

    datagatherer.sync.<id>     api_sync / local_import: fetch what is new.
                               params {trigger: 'user' | 'schedule'}
    datagatherer.import.<id>   read raw exports: {} registers the existing export folders in
                               place and reads every pending one; {raw_import_id} reads one.
    datagatherer.watch         copy one export into the vault, then queue its import: one found
                               in a watch folder (Downloads), params {source, path}, or one
                               picked in the browser, params {source, upload, name}

Every job is idempotent (the record keys), so resume, a second click or an interrupted run
never adds anything twice. An import that is stopped halfway stays 'importing' and is read
again (from the start) by the next run. A raw export that cannot be read is marked 'failed'
with the reason; the others still go on, and the job then reports itself incomplete.
"""
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

import httpx
from fastapi import HTTPException

from backend.datagatherer import keychain, net, raw_store, registry, state
from backend.datagatherer.base import ConnectorError, GatherContext
from backend.jobs import JobContext, JobStopped, JobType, VaultUnavailable

SYNC_PREFIX = "datagatherer.sync."
IMPORT_PREFIX = "datagatherer.import."
WATCH_TYPE = "datagatherer.watch"


class GatherFailed(Exception):
    """A sync or import the user can act on; the message is Dutch and goes to the job dock."""
    quiet = True    # no stack trace in the server log (backend/jobs.py)


def sync_type_name(source_id: str) -> str:
    return SYNC_PREFIX + source_id


def import_type_name(source_id: str) -> str:
    return IMPORT_PREFIX + source_id


def source_of(job_type: str):
    for prefix in (SYNC_PREFIX, IMPORT_PREFIX):
        if job_type.startswith(prefix):
            return job_type[len(prefix):]
    return None


def _vault(config) -> None:
    if not config.VAULT_ROOT.is_dir():
        raise VaultUnavailable()


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}".replace(",", ".")


# ---------- sync ----------

def run_sync(config, db, connector, trigger: str, job=None, sleep=None) -> Dict[str, Any]:
    """One sync of a connector (a job, or the launchd agent through cli.py). Records the outcome."""
    gctx = GatherContext(config, db, connector, trigger=trigger, job=job, sleep=sleep)
    state.update(db, connector.id, last_sync_at=time.time())
    try:
        if not connector.is_configured(gctx):
            if not connector.has("api_sync"):
                raise ConnectorError(f"{connector.name} is niet gevonden op deze Mac")
            raise ConnectorError(f"{connector.name} is nog niet verbonden: klik Verbinden")
        stats = connector.sync(gctx)
    except ConnectorError as e:
        fields = {"last_error": str(e)}
        if e.reconnect:
            fields["needs_reconnect"] = 1
        if e.retry_after:
            fields["retry_after"] = e.retry_after
        state.update(db, connector.id, **fields)
        raise GatherFailed(str(e)) from e
    except keychain.SecretStoreError as e:
        state.update(db, connector.id, last_error=str(e))
        raise GatherFailed(str(e)) from e
    except net.NetworkRefused as e:
        state.update(db, connector.id, last_error=str(e))
        raise GatherFailed(f"Geweigerd door DataVault: {e}") from e
    except httpx.HTTPError as e:
        if not config.VAULT_ROOT.is_dir():
            raise VaultUnavailable() from e
        message = f"Geen verbinding met {connector.name} ({type(e).__name__}); probeer het later opnieuw"
        state.update(db, connector.id, last_error=message)
        raise GatherFailed(message) from e
    state.update(db, connector.id, last_success_at=time.time(), last_error=None, retry_after=None)
    return {"source": connector.id, **stats}


def sync_type(config, db, connector) -> JobType:
    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        trigger = params.get("trigger", "user")
        if trigger not in net.NETWORK_TRIGGERS:
            raise HTTPException(status_code=400, detail="trigger moet 'user' of 'schedule' zijn")
        return {"trigger": trigger}

    def run(ctx: JobContext) -> Dict[str, Any]:
        _vault(config)
        result = run_sync(config, db, connector, ctx.params["trigger"], job=ctx)
        ctx.progress(message=sync_message(connector, result))
        return result

    return JobType(sync_type_name(connector.id), f"{connector.name} bijwerken", prepare, run,
                   summarize=lambda r: {k: r.get(k) for k in ("source", "added", "seen", "complete", "errors", "gap_possible")},
                   results_to_keep=3, jobs_to_keep=10,
                   describe=lambda p: "volgens schema" if p.get("trigger") == "schedule" else "nieuwe gegevens ophalen")


def sync_message(connector, result: Dict[str, Any]) -> str:
    added = result.get("added", 0)
    text = f"Klaar: {_plural(added, *connector.record_label)} erbij" if added else "Klaar: niets nieuws"
    if result.get("gap_possible"):
        text += " (mogelijk een gat: meer nieuwe nummers dan Spotify teruggeeft)"
    return text


# ---------- import ----------

def run_import(config, db, connector, raw_ids: List[int], ctx: JobContext = None) -> Dict[str, Any]:
    gctx = GatherContext(config, db, connector, trigger="local", job=ctx)
    done = list((ctx.checkpoint if ctx else {}).get("done", []))
    totals = {"source": connector.id, "imports": 0, "added": 0, "seen": 0, "errors": 0, "error_samples": []}
    for raw_id in raw_ids:
        if raw_id in done:
            continue
        gctx.check()
        raw = state.get_raw(db, raw_id)
        if raw is None or raw["source_id"] != connector.id or not state.claim_raw(db, raw_id):
            continue
        gctx.progress(totals["imports"], len(raw_ids), f"Inlezen: {Path(raw['path']).name}")
        try:
            stats = connector.import_raw(gctx, raw)
        except (ConnectorError, raw_store.RawStoreError, ValueError, OSError) as e:
            if isinstance(e, OSError) and not config.VAULT_ROOT.is_dir():
                raise VaultUnavailable() from e
            message = str(e) if not isinstance(e, OSError) else f"Niet leesbaar: {e.strerror or e}"
            _import_failed(db, raw, message, totals)
            continue
        except (JobStopped, VaultUnavailable):
            raise
        except Exception as e:  # a bug or a very odd file: this export fails, the others still go on
            import traceback
            traceback.print_exc()
            _import_failed(db, raw, f"Onverwachte fout bij het inlezen ({type(e).__name__}); "
                                    "de andere exports zijn wel ingelezen", totals)
            continue
        state.update_raw(db, raw_id, status="imported", imported_at=time.time(), error=None,
                         records_added=stats.get("added", 0), records_seen=stats.get("seen", 0),
                         covers_from=stats.get("covers_from"), covers_until=stats.get("covers_until"),
                         parser_version=connector.parser_version, stats=stats)
        state.close_requests(db, connector.id, "arrived", raw_import_id=raw_id)
        totals["imports"] += 1
        totals["added"] += stats.get("added", 0)
        totals["seen"] += stats.get("seen", 0)
        done.append(raw_id)
        if ctx is not None:
            ctx.save_checkpoint({"done": done})
    after = getattr(connector, "after_import", None)
    if after is not None and (totals["imports"] or raw_ids):
        after(gctx)
    totals["complete"] = totals["errors"] == 0
    return totals


def _import_failed(db, raw: Dict[str, Any], message: str, totals: Dict[str, Any]) -> None:
    state.update_raw(db, raw["id"], status="failed", error=message)
    totals["errors"] += 1
    if len(totals["error_samples"]) < 5:
        totals["error_samples"].append({"path": raw["path"], "error": message})


def register_existing(config, db, connector, gctx) -> int:
    """Register the source's export folders that are already in the vault; returns how many are new or changed."""
    changed = 0
    for folder, kind in connector.find_existing(gctx):
        _, is_new = raw_store.register_folder(config, db, connector, kind, folder,
                                              getattr(connector, "folder_patterns", ("*",)))
        changed += int(is_new)
    return changed


def import_type(config, db, connector) -> JobType:
    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        raw_id = params.get("raw_import_id")
        if raw_id is None:
            return {}
        if not isinstance(raw_id, int) or isinstance(raw_id, bool) or raw_id <= 0:
            raise HTTPException(status_code=400, detail="raw_import_id moet een nummer zijn")
        return {"raw_import_id": raw_id}

    def run(ctx: JobContext) -> Dict[str, Any]:
        _vault(config)
        if "raw_import_id" in ctx.params:
            raw_ids = [ctx.params["raw_import_id"]]
        else:
            ctx.progress(0, None, "Exports in het archief zoeken…")
            register_existing(config, db, connector, GatherContext(config, db, connector, job=ctx))
            raw_ids = [r["id"] for r in state.pending_raw(db, connector.id)]
        result = run_import(config, db, connector, raw_ids, ctx)
        ctx.progress(result["imports"], max(1, len(raw_ids)), import_message(connector, result))
        return result

    return JobType(import_type_name(connector.id), f"{connector.name} inlezen", prepare, run,
                   summarize=lambda r: {k: r.get(k) for k in ("source", "imports", "added", "seen", "errors", "complete")},
                   results_to_keep=3, jobs_to_keep=10,
                   describe=lambda p: "één export" if p.get("raw_import_id") else "exports in het archief")


def import_message(connector, result: Dict[str, Any]) -> str:
    if not result["imports"] and not result["errors"]:
        return "Klaar: geen nieuwe exports"
    text = (f"Klaar: {_plural(result['added'], *connector.record_label)} erbij uit "
            f"{_plural(result['imports'], 'export', 'exports')}")
    if result["errors"]:
        text += f" ({_plural(result['errors'], 'export', 'exports')} niet leesbaar)"
    return text


# ---------- watch: an export from Downloads into the vault ----------

UPLOAD_STAGING = "datagatherer-upload"


def upload_staging(config) -> Path:
    """Where an export picked in the browser waits for its copy into the vault: the app's own cache folder."""
    return config.CACHE_FOLDER / UPLOAD_STAGING


def drop_upload(config, token: str) -> None:
    """Remove one staged upload (the app's own temporary copy in its cache folder, never an archive file)."""
    folder = upload_staging(config) / token
    try:
        for entry in os.scandir(folder):
            os.unlink(entry.path)
        os.rmdir(folder)
    except OSError:
        pass


def _staged_file(config, token: Any, name: Any) -> Path:
    """The staged upload of params {upload, name}; only a plain folder and file name in the staging folder."""
    if not isinstance(token, str) or not isinstance(name, str) or not token or not name:
        raise HTTPException(status_code=400, detail="Onbekende upload")
    for part in (token, name):
        if "/" in part or part.startswith(".") or any(ord(c) < 32 or ord(c) == 127 for c in part):
            raise HTTPException(status_code=400, detail="Onbekende upload")
    path = upload_staging(config) / token / name
    if path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=400, detail="Deze upload is er niet (meer); kies het bestand opnieuw")
    return path


def watch_type(config, db, get_jobs: Callable[[], Any]) -> JobType:
    """
    Copy one export into the vault, then queue its import. Two ways in: a file in a watch
    folder (params {source, path}: the Downloads watcher or 'In Downloads zoeken') or a file
    picked in the browser (params {source, upload, name}: staged by the upload route in the
    app's cache folder, removed once it is in the vault or the job was cancelled).
    """
    from backend.datagatherer import daemon

    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        connector = registry.get(params.get("source") or "")
        if connector is None:
            raise HTTPException(status_code=400, detail="Onbekende bron")
        if "upload" in params:
            if not connector.has("import"):
                raise HTTPException(status_code=400, detail=f"{connector.name} kan geen bestand inlezen")
            _staged_file(config, params.get("upload"), params.get("name"))
            return {"source": connector.id, "upload": params["upload"], "name": params["name"]}
        if not connector.has("export_watch"):
            raise HTTPException(status_code=400, detail="Onbekende bron")
        path = params.get("path")
        if not isinstance(path, str) or not daemon.in_watch_folder(config, Path(path),
                                                                   daemon.own_watch_folder(config, db, connector)):
            raise HTTPException(status_code=400, detail="Dit bestand staat niet in een map die DataVault bekijkt")
        return {"source": connector.id, "path": path}

    def run(ctx: JobContext) -> Dict[str, Any]:
        _vault(config)
        upload = ctx.params.get("upload")
        keep_staged = False
        try:
            return _copy(ctx, upload)
        except JobStopped as e:
            keep_staged = e.reason != "cancel"       # paused or shutting down: resumed later
            raise
        except VaultUnavailable:
            keep_staged = True
            raise
        finally:
            if upload and not keep_staged and config.VAULT_ROOT.is_dir():
                drop_upload(config, upload)

    def _copy(ctx: JobContext, upload) -> Dict[str, Any]:
        connector = registry.get(ctx.params["source"])
        if upload:
            try:
                src = _staged_file(config, upload, ctx.params.get("name"))
            except HTTPException as e:
                raise GatherFailed(e.detail)
            origin = "upload"
        else:
            src = Path(ctx.params["path"])
            origin = "downloads"
            if not src.is_file():
                raise GatherFailed("Het bestand staat niet meer in Downloads")
        names = raw_store.read_zip_names(src)
        kind = connector.detect(src, names)
        if kind is None:
            raise GatherFailed(f"{src.name} is geen export van {connector.name}")
        size = src.stat().st_size

        def check(done: int) -> None:
            ctx.check()
            ctx.progress(done, size * 2, f"Kopiëren naar het archief: {src.name}")

        raw, created = raw_store.archive_file(config, db, connector, kind, src, origin, check=check,
                                              original_name=src.name)
        state.close_requests(db, connector.id, "arrived", raw_import_id=raw["id"])
        import_job = None
        if raw["status"] != "imported":
            job, _ = get_jobs().start(import_type_name(connector.id), {"raw_import_id": raw["id"]})
            import_job = job["id"]
        ctx.progress(size * 2, size * 2, f"Gekopieerd naar het archief: {Path(raw['path']).name}" if created
                     else "Deze export stond al in het archief")
        return {"source": connector.id, "raw_import_id": raw["id"], "created": created, "path": raw["path"],
                "original": None if upload else str(src), "import_job": import_job, "complete": True, "errors": 0}

    return JobType(WATCH_TYPE, "Export naar het archief kopiëren", prepare, run,
                   summarize=lambda r: {k: r.get(k) for k in ("source", "raw_import_id", "created", "import_job",
                                                              "complete", "errors")},
                   results_to_keep=5, jobs_to_keep=10,
                   on_cancel=lambda p: drop_upload(config, p["upload"]) if isinstance(p.get("upload"), str) else None,
                   describe=lambda p: p.get("name") or Path(p.get("path") or "").name)


# ---------- geotag: estimated positions for photos without GPS ----------

GEOTAG_TYPE = "datagatherer.geotag"


def geotag_type(config, db) -> JobType:
    """Estimate positions again for every photo without GPS (params {full: bool}); see geotag.py."""
    from backend.datagatherer import geotag

    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        full = params.get("full", True)
        if not isinstance(full, bool):
            raise HTTPException(status_code=400, detail="full moet waar of onwaar zijn")
        return {"full": full}

    def run(ctx: JobContext) -> Dict[str, Any]:
        _vault(config)
        ctx.progress(0, None, "Foto's zonder GPS zoeken…")
        stats = geotag.run(db, check=ctx.check, progress=ctx.progress, full=ctx.params["full"])
        ctx.progress(stats["checked"], max(1, stats["checked"]),
                     f"Klaar: {_plural(stats['estimated'], 'foto', 'foto’s')} met een geschatte locatie")
        return {**stats, "complete": True, "errors": 0}

    return JobType(GEOTAG_TYPE, "Locaties schatten uit de Tijdlijn", prepare, run,
                   summarize=lambda r: {k: r.get(k) for k in ("checked", "estimated", "skipped", "complete")},
                   results_to_keep=3, jobs_to_keep=10,
                   describe=lambda p: "alle foto's zonder GPS" if p.get("full") else "nieuwe foto's")


def build(config, db, get_jobs: Callable[[], Any]) -> List[JobType]:
    types = [geotag_type(config, db)]
    for connector in registry.connectors():
        if connector.has("api_sync") or connector.has("local_import"):
            types.append(sync_type(config, db, connector))
        if connector.has("import"):
            types.append(import_type(config, db, connector))
    types.append(watch_type(config, db, get_jobs))
    return types
