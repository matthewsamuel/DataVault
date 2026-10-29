"""
DataGatherer - reading a chat database on this Mac (iMessage, WhatsApp for Mac) from a copy

macOS protects ~/Library/Messages and the WhatsApp group container with Full Disk Access
(FDA). The DataVault server does not need it: either

  1. the server can read the live database (the user gave FDA to the app that runs it), or
  2. the small helper (fda_helper.py, started by the user from a Terminal that has FDA)
     copied it into the inbox <cache>/datagatherer-local/<source>/helper-<time>/.

Either way the connector reads a SNAPSHOT: the database plus its -wal and -shm, copied
together into the cache, opened there, and removed from the cache after the import. The
live database is only ever opened for reading (a plain file copy), never written.

Tests and the test server never touch the real home folder: gather_home() is
DATAVAULT_GATHER_HOME, and in test mode without it a folder that does not exist.
"""
import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

INBOX = "datagatherer-local"
READY = "ready.json"
SIDECARS = ("-wal", "-shm")
FDA_SETTINGS_PATH = "Systeeminstellingen > Privacy en beveiliging > Volledige schijftoegang"
FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
HELPER = Path(__file__).with_name("fda_helper.py")

# Where the databases live, relative to the home folder (fda_helper.py has the same list)
LIVE_PATHS = {
    "imessage": "Library/Messages/chat.db",
    "whatsapp": "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite",
}
MEDIA_ROOTS = {
    "imessage": None,
    "whatsapp": "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/Message",
}


class AccessDenied(Exception):
    """macOS refused to open the database (no Full Disk Access for this process)."""


class NotFound(Exception):
    """The database is not on this Mac (the app is not installed or never synced)."""


def gather_home() -> Path:
    home = os.environ.get("DATAVAULT_GATHER_HOME")
    if home:
        return Path(home)
    if os.environ.get("DATAVAULT_TESTING") == "1":
        return Path("/nonexistent/datavault-test-home")      # never the real home in tests
    return Path.home()


def live_path(source: str) -> Path:
    return gather_home() / LIVE_PATHS[source]


def media_root(source: str) -> Optional[str]:
    rel = MEDIA_ROOTS.get(source)
    return str(gather_home() / rel) if rel else None


def inbox(config, source: Optional[str] = None) -> Path:
    base = Path(config.CACHE_FOLDER) / INBOX
    return base / source if source else base


def check_live(source: str) -> str:
    """'ok', 'denied' or 'missing' for the live database (opens it for reading, reads nothing)."""
    path = live_path(source)
    try:
        with open(path, "rb"):
            return "ok"
    except PermissionError:
        return "denied"
    except FileNotFoundError:
        # Without FDA macOS may also hide the folder; a parent we can see but not list is 'denied'
        parent = path.parent
        try:
            if parent.is_dir():
                os.listdir(parent)
        except PermissionError:
            return "denied"
        return "missing"
    except OSError:
        return "denied"


def helper_snapshots(config, source: str) -> List[Path]:
    """The helper's finished copies for source (with ready.json), oldest first."""
    folder = inbox(config, source)
    if not folder.is_dir():
        return []
    found = [p for p in folder.iterdir() if p.is_dir() and p.name.startswith("helper-") and (p / READY).is_file()
             and not p.is_symlink()]
    return sorted(found, key=lambda p: p.name)


def snapshot_live(config, source: str) -> Path:
    """Copy the live database with its -wal and -shm into the cache; returns the copy of the database."""
    src = live_path(source)
    status = check_live(source)
    if status == "denied":
        raise AccessDenied(str(src))
    if status == "missing":
        raise NotFound(str(src))
    folder = inbox(config, source) / f"live-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    folder.mkdir(parents=True, exist_ok=False)
    try:
        for suffix in ("",) + SIDECARS:
            part = Path(str(src) + suffix)
            if suffix and not part.exists():
                continue
            shutil.copyfile(part, folder / (src.name + suffix))
    except PermissionError as e:
        drop(config, folder)
        raise AccessDenied(str(src)) from e
    except OSError:
        drop(config, folder)
        raise
    return folder / src.name


def snapshot_db(folder: Path, source: str) -> Optional[Path]:
    """The database file inside a snapshot folder."""
    name = Path(LIVE_PATHS[source]).name
    path = folder / name
    return path if path.is_file() else None


def snapshot_info(folder: Path) -> Dict:
    try:
        return json.loads((folder / READY).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def drop(config, folder: Path) -> None:
    """Remove a snapshot folder of OUR cache inbox (never anything else)."""
    folder = Path(folder)
    root = inbox(config).resolve()
    try:
        resolved = folder.resolve()
    except OSError:
        return
    if resolved == root or root not in resolved.parents or folder.is_symlink():
        return
    shutil.rmtree(resolved, ignore_errors=True)


def helper_command(config, source: str) -> str:
    """The exact command the card shows: run the helper from a Terminal that has Full Disk Access."""
    return f'/usr/bin/python3 "{HELPER}" --bron {source} --naar "{inbox(config)}"'


def access(config, source: str, app_name: str) -> Optional[Dict]:
    """
    None when the source can be read (live, or a helper copy waits in the inbox); else what
    the card shows under 'Toegang nodig': why, the System Settings path and the helper.
    """
    status = check_live(source)
    if status == "ok" or helper_snapshots(config, source):
        return None
    if status == "missing":
        return {
            "code": "missing",
            "title": f"{app_name} niet gevonden op deze Mac",
            "text": f"DataVault vindt de database van {app_name} niet op {live_path(source)}. Staat de app op deze Mac "
                    "en heb je hem een keer geopend? Kopieerde de hulp hem, klik dan op Opnieuw controleren.",
            "settings_path": None, "settings_url": None, "command": helper_command(config, source),
        }
    return {
        "code": "denied",
        "title": "Toegang nodig",
        "text": f"macOS beschermt de berichten van {app_name}. Geef Volledige schijftoegang aan de app die de hulp "
                "uitvoert (bijvoorbeeld Terminal) en start daar de hulp: die kopieert de database naar DataVault. De "
                "server zelf heeft die toegang niet nodig.",
        "settings_path": FDA_SETTINGS_PATH,
        "settings_url": FDA_SETTINGS_URL,
        "command": helper_command(config, source),
        "steps": [
            f"Open {FDA_SETTINGS_PATH}.",
            "Klik op +, kies Programma's > Hulpprogramma's > Terminal en zet de schakelaar aan.",
            "Open Terminal en plak de opdracht hieronder (Kopiëren). De hulp kopieert alleen de database; hij wist of "
            "wijzigt niets.",
            "Klik daarna hier op Opnieuw controleren: DataVault leest de kopie in en ruimt ze daarna op.",
        ],
    }


def take_snapshot(config, source: str):
    """
    (database copy, its folder, 'live' or 'helper'): the live database copied now when this
    process may read it, else the newest copy the helper left in the inbox. Raises
    AccessDenied or NotFound when there is neither.
    """
    status = check_live(source)
    if status == "ok":
        copy = snapshot_live(config, source)
        return copy, copy.parent, "live"
    snaps = helper_snapshots(config, source)
    if snaps:
        folder = snaps[-1]
        copy = snapshot_db(folder, source)
        if copy is None:
            drop(config, folder)
            raise NotFound(str(folder))
        return copy, folder, "helper"
    if status == "denied":
        raise AccessDenied(str(live_path(source)))
    raise NotFound(str(live_path(source)))


def finish_snapshot(config, source: str, folder: Path, via: str) -> None:
    """After reading: remove this copy and (for the helper) every older copy from the inbox."""
    if via == "helper":
        for older in helper_snapshots(config, source):
            if older.name <= folder.name:
                drop(config, older)
    drop(config, folder)
