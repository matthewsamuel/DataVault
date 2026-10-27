"""
DataVault Dashboard - FastAPI Application
"""
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from typing import Optional
import os

# Ensure ffmpeg is in PATH for video thumbnails
if '/usr/local/bin' not in os.environ.get('PATH', ''):
    os.environ['PATH'] = f"/usr/local/bin:{os.environ.get('PATH', '')}"

import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import Config
from backend.database import Database
from backend.routes import folders, media, duplicates, spotify, shows, chat, stats, keurer, timeline


def create_app(config_path: Optional[str] = None) -> FastAPI:
    """
    Build the application. Config comes from config_path, $DATAVAULT_CONFIG
    or code/config.json (see backend.config.resolve_config_path).
    Run with: uvicorn --factory backend.main:create_app
    """
    # Telemetry is explicitly off: DataVault never sends data anywhere
    app = FastAPI(
        title="DataVault Dashboard",
        telemetry={
            "tracing": False,
            "metrics": False,
            "logs": False,
            "operation_spans": False,
            "auto_configure": False,
        },
    )

    # CORS for local development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    config = None
    try:
        # Load configuration
        config = Config(config_path)

        # Validate paths (but don't fail if T7 not mounted)
        path_status = config.validate_paths()

        if not path_status["vault_root"]:
            print("WARNING: Vault root not found. Is T7 mounted?")
            print(f"Expected path: {config.VAULT_ROOT}")
        else:
            print(f"Vault root found: {config.VAULT_ROOT}")

            # Ensure cache directories exist
            config.ensure_cache_dirs()
            config.ensure_duplicates_folder()

        # Initialize database
        db = Database(config.DB_PATH)
        print(f"Database initialized: {config.DB_PATH}")

        # Initialize routes with config and database
        folders.init_router(config)
        media.init_router(config, db)
        duplicates.init_router(config, db)
        spotify.init_router(config)
        shows.init_router(config, db)
        stats.init_router(config)
        chat.init_router(config)
        keurer.init_router(config, db)
        timeline.init_router(config)

        print("DataVault Dashboard initialized successfully")

    except RuntimeError:
        # Safety guards (e.g. test mode pointing at a real vault) must stop startup
        raise
    except Exception as e:
        print(f"ERROR during initialization: {e}")
        import traceback
        traceback.print_exc()

    app.state.config = config

    # Register routes
    app.include_router(folders.router)
    app.include_router(media.router)
    app.include_router(duplicates.router)
    app.include_router(spotify.router, prefix="/api/spotify", tags=["spotify"])
    app.include_router(shows.router, prefix="/api/shows", tags=["shows"])
    app.include_router(chat.router, tags=["chat"])
    app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
    app.include_router(keurer.router, prefix="/api/keurer", tags=["keurer"])
    app.include_router(timeline.router, prefix="/api/timeline", tags=["timeline"])
    app.include_router(core_router)

    # Serve frontend static files — MOET als allerlaatste staan, anders vangt het API-routes op
    frontend_path = Path(__file__).parent.parent / "frontend"
    if frontend_path.exists():
        app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")

    return app


core_router = APIRouter()


@core_router.post("/api/restart")
async def restart_server():
    """
    Sluit de huidige server af en start hem opnieuw op.
    Spawnt een los achtergrondproces dat na 1s de server herstart.
    """
    import subprocess, sys, os

    # Pad naar de project-root (één niveau boven backend/)
    project_root = str(Path(__file__).parent.parent)
    python_exec  = sys.executable

    # Shell-script dat wacht, alle processen op poort 8000 doodt, en opnieuw opstart
    script = f"""#!/bin/bash
sleep 1
# Dood alle processen op poort 8000 (inclusief reloader parent/child)
PIDS=$(lsof -ti :8000 2>/dev/null)
if [ -n "$PIDS" ]; then
    kill $PIDS 2>/dev/null
    sleep 0.5
    kill -9 $PIDS 2>/dev/null
fi
sleep 1
cd "{project_root}"
export PATH="/usr/local/bin:$PATH"
nohup "{python_exec}" -m uvicorn --factory backend.main:create_app \\
    --host 127.0.0.1 --port 8000 \\
    >> /tmp/datavault_server.log 2>&1 &
"""
    subprocess.Popen(
        ['bash', '-c', script],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return {"status": "restarting"}


@core_router.get("/api/health")
async def health_check(request: Request):
    """Health check endpoint"""
    config = request.app.state.config
    if not config:
        raise HTTPException(status_code=503, detail="Configuration not loaded")

    path_status = config.validate_paths()

    return {
        "status": "running",
        "vault_available": path_status["vault_root"],
        "vault_path": str(config.VAULT_ROOT),
        "paths": path_status
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
