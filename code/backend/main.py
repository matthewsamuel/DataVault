"""
DataVault Dashboard - FastAPI Application
"""
from contextlib import asynccontextmanager
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pathlib import Path
from typing import Optional
import os
import shutil
import threading
import uuid

# Ensure ffmpeg is in PATH for video thumbnails (Homebrew on Apple silicon and on Intel)
for _bin in ('/usr/local/bin', '/opt/homebrew/bin'):
    if _bin not in os.environ.get('PATH', '').split(':'):
        os.environ['PATH'] = f"{os.environ.get('PATH', '')}:{_bin}".lstrip(':')

import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import Config
from backend.database import Database
from backend import errors, file_ops, media_index, restart, security, thumbnail
from backend.jobs import JobManager
from backend.job_types import build_job_types
from backend.routes import (
    folders, media, duplicates, spotify, shows, chat, stats, keurer, timeline, jobs, library, recovery, albums,
    series,
)


def _say(message: str) -> None:
    """A startup message; a closed terminal (revoked tty) must never stop the server from starting."""
    try:
        print(message, flush=True)
    except OSError:
        pass


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Automatic index passes (on start, when the vault returns) only while the server runs
    auto_index = getattr(app.state, "auto_index", None)
    if auto_index is not None:
        auto_index.start()
    yield
    if auto_index is not None:
        await run_in_threadpool(auto_index.stop)
    # Stop background jobs at their next check (they stay resumable as 'interrupted'),
    # then close the database connections
    job_manager = getattr(app.state, "jobs", None)
    if job_manager is not None:
        await run_in_threadpool(job_manager.shutdown)
    database = getattr(app.state, "db", None)
    if database is not None:
        database.close_all()


def create_app(config_path: Optional[str] = None) -> FastAPI:
    """
    Build the application. Config comes from config_path, $DATAVAULT_CONFIG
    or code/config.json (see backend.config.resolve_config_path).
    Run with: uvicorn --factory backend.main:create_app
    """
    # Telemetry is explicitly off: DataVault never sends data anywhere
    app = FastAPI(
        title="DataVault Dashboard",
        lifespan=_lifespan,
        # No Swagger UI or ReDoc: both load their scripts from a CDN, and DataVault must work
        # fully offline. /openapi.json (plain JSON, nothing external) stays available.
        docs_url=None,
        redoc_url=None,
        telemetry={
            "tracing": False,
            "metrics": False,
            "logs": False,
            "operation_spans": False,
            "auto_configure": False,
        },
    )

    # No CORS: the frontend is served same-origin, so no other website may read the API.
    # Every state-changing request needs this process's session token (see backend/security.py).
    app.state.session_token = security.new_session_token()
    app.state.instance_id = uuid.uuid4().hex
    app.add_middleware(security.SessionTokenMiddleware, token=app.state.session_token)
    # Added last = outermost: a foreign Host header (DNS rebinding) is refused first
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=security.allowed_hosts())

    config = None
    app.state.jobs = None
    app.state.db = None
    app.state.auto_index = None
    try:
        # Load configuration
        config = Config(config_path)

        # No file move may ever take something out of (or into) the app's own cache/database
        file_ops.protect_paths([config.CACHE_FOLDER, config.DB_PATH])
        # A moved file keeps its cached thumbnail (renamed to the new path's key), and its
        # index row, rating and Keurer decisions follow it (see media_index.record_move)
        file_ops.on_moved(_after_move(config, lambda: app.state.db))

        # Validate paths (but don't fail if T7 not mounted)
        path_status = config.validate_paths()
        if not path_status["vault_root"]:
            _say("WARNING: Vault root not found. Is T7 mounted?")
            _say(f"Expected path: {config.VAULT_ROOT}")
            _say("The app starts anyway and opens the archive as soon as the disk is there.")
        else:
            _say(f"Vault root found: {config.VAULT_ROOT}")

        # The database lives in the vault's cache folder: it opens now when the vault is
        # there, otherwise on the first request after the disk comes back (no restart)
        db = Database(config.DB_PATH, prepare=_vault_preparer(config))
        app.state.db = db
        if db.ready:
            _say(f"Database initialized: {config.DB_PATH}")

        # Background jobs (scans, hashing, thumbnails); what a previous run left
        # running can only be resumed by hand
        job_manager = JobManager(db, config, build_job_types(config, db, on_dup_scan_done=duplicates.remember_scan))
        db.when_ready(lambda: _recover_jobs(job_manager))
        # Old-style thumbnails (made before EXIF rotation, path-only key) and stored
        # placeholders are made again on demand; clear them out in the background
        db.when_ready(lambda: threading.Thread(
            target=thumbnail.prune_thumbnail_cache, args=(config.THUMB_CACHE_PATH,),
            name="thumb-prune", daemon=True).start())
        app.state.jobs = job_manager
        jobs.init_router(job_manager)

        # The media index is brought up to date on start, when the vault returns and when
        # the timeline finds it behind the disk (DATAVAULT_AUTO_INDEX=0 turns this off: tests)
        if os.environ.get("DATAVAULT_AUTO_INDEX", "1") != "0":
            app.state.auto_index = media_index.AutoIndexer(job_manager)
            media_index.set_updater(app.state.auto_index.request)
        else:
            media_index.set_updater(None)

        # Initialize routes with config and database: always, also without the vault
        folders.init_router(config)
        media.init_router(config, db)
        duplicates.init_router(config, db)
        spotify.init_router(config)
        shows.init_router(config, db)
        stats.init_router(config, db)
        chat.init_router(config, db)
        keurer.init_router(config, db)
        timeline.init_router(config, db)
        library.init_router(config, db, job_manager)
        albums.init_router(config, db)
        recovery.init_router(config, db)
        series.init_router(config, db, job_manager)

        _say("DataVault Dashboard initialized successfully")

    except RuntimeError:
        # Safety guards (e.g. test mode pointing at a real vault) must stop startup
        raise
    except Exception as e:
        _say(f"ERROR during initialization: {e}")
        import traceback
        _say(traceback.format_exc())

    app.state.config = config
    errors.install(app)

    # Register routes; every route that reads the archive answers 503 while it is unplugged
    vault = [Depends(errors.require_vault)]
    app.include_router(folders.router, dependencies=vault)
    app.include_router(media.router, dependencies=vault)
    app.include_router(duplicates.router, dependencies=vault)
    app.include_router(jobs.router, tags=["jobs"])
    app.include_router(spotify.router, prefix="/api/spotify", tags=["spotify"], dependencies=vault)
    app.include_router(shows.router, prefix="/api/shows", tags=["shows"], dependencies=vault)
    app.include_router(chat.router, tags=["chat"])
    app.include_router(stats.router, prefix="/api/stats", tags=["stats"], dependencies=vault)
    app.include_router(keurer.router, prefix="/api/keurer", tags=["keurer"], dependencies=vault)
    app.include_router(timeline.router, prefix="/api/timeline", tags=["timeline"], dependencies=vault)
    app.include_router(library.router, prefix="/api/library", tags=["library"], dependencies=vault)
    app.include_router(albums.router, prefix="/api/library", tags=["library"], dependencies=vault)
    app.include_router(recovery.router, prefix="/api/recovery", tags=["recovery"], dependencies=vault)
    app.include_router(series.router, prefix="/api/series", tags=["series"], dependencies=vault)
    app.include_router(security.router)
    app.include_router(core_router)

    # Serve frontend static files — MOET als allerlaatste staan, anders vangt het API-routes op
    frontend_path = Path(__file__).parent.parent / "frontend"
    if frontend_path.exists():
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")

    return app


# Kept here as well: routes import it from backend.main
VAULT_UNREACHABLE = errors.VAULT_UNREACHABLE


def _vault_preparer(config):
    """What must be true before the database may be opened: the vault is there and the cache folder exists."""
    def prepare():
        if not config.VAULT_ROOT.is_dir():
            raise FileNotFoundError(f"vault not found: {config.VAULT_ROOT}")
        # _make_dir never creates the vault root itself (nothing under /Volumes)
        config._make_dir(config.CACHE_FOLDER)
        config._make_dir(config.THUMB_CACHE_PATH)
        config.ensure_duplicates_folder()
    return prepare


def _after_move(config, get_db):
    """What follows a file the app moved: its thumbnail, then its index row, rating and Keurer decisions."""
    def after(src, dst):
        try:
            thumbnail.carry_over(src, dst, config.THUMB_CACHE_PATH)
        except Exception:
            pass
        try:
            media_index.record_move(get_db(), config, src, dst)
        except Exception as e:
            _say(f"Index not updated after a move ({e}); the next pass picks it up")
    return after


def _recover_jobs(job_manager) -> None:
    interrupted = job_manager.recover()
    if interrupted:
        _say(f"{interrupted} background job(s) were interrupted; resume them in the job dock")


core_router = APIRouter()


@core_router.post("/api/restart")
def restart_server():
    """
    Herstart alleen dit serverproces (nooit iets anders op poort 8000 of elders).
    Een hulpproces wacht tot dit proces gestopt is en start de server dan opnieuw
    met dezelfde Python, argumenten, werkmap en omgeving (zie backend/restart.py).
    Vereist het sessietoken, zoals elk POST-verzoek.
    """
    try:
        return restart.schedule_restart()
    except (restart.RestartNotPossible, restart.RestartInProgress) as e:
        raise HTTPException(status_code=409, detail=str(e))


@core_router.get("/api/health")
def health_check(request: Request):
    """
    Health check: is the vault there, and what can this Mac do (capabilities). 'notices'
    lists in Dutch what does not work and how to fix it; the page shows them quietly.
    """
    config = request.app.state.config
    if not config:
        raise HTTPException(status_code=503, detail="Configuration not loaded")

    path_status = config.validate_paths()
    db = request.app.state.db
    caps = capabilities()

    return {
        "status": "running",
        # Changes on every (re)start, so the page can tell the new process from the old one
        "instance": request.app.state.instance_id,
        "pid": os.getpid(),
        "vault_available": path_status["vault_root"],
        "vault_path": str(config.VAULT_ROOT),
        "vault_message": None if path_status["vault_root"] else VAULT_UNREACHABLE,
        "database_ready": bool(db is not None and db.ready),
        "paths": path_status,
        "capabilities": caps,
        "notices": capability_notices(caps),
    }


def capabilities() -> dict:
    """Optional tools the app uses (all local): checked on every call, the chat check is cached."""
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
        "pillow_heif": thumbnail.HEIF_SUPPORT,
        "sips": shutil.which("sips") is not None,
        "chat": chat.chat_capability(),
    }


def capability_notices(caps: dict) -> list:
    notices = []
    if not caps["ffmpeg"]:
        notices.append({"code": "ffmpeg", "title": "ffmpeg ontbreekt",
                        "message": "Video's krijgen geen miniatuur. Installeer het met: brew install ffmpeg"})
    if not caps["pillow_heif"]:
        notices.append({"code": "pillow_heif", "title": "HEIC-ondersteuning ontbreekt",
                        "message": "iPhone-foto's (HEIC) krijgen geen miniatuur. Installeer het met: "
                                   "venv/bin/pip install -r requirements.txt"})
    chat_caps = caps["chat"]
    if chat_caps["message"]:
        notices.append({"code": "chat_model" if chat_caps["reachable"] else "ollama",
                        "title": "Chat werkt niet" if not chat_caps["reachable"] else "Chatmodel ontbreekt",
                        "message": chat_caps["message"]})
    return notices

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
