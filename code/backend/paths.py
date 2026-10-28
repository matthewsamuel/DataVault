"""
DataVault Dashboard - Path safety

Security-critical. Every route that takes a path or folder from a request runs
it through resolve_in_vault(), so there is one rule for what a request may touch:
- relative input is taken relative to the vault root, absolute input as is;
- the spelling on disk wins, otherwise the NFC (or NFD) form of the name, so a
  name typed or copied in another Unicode form still finds its file;
- after resolving '..' and symlinks the path must lie inside VAULT_ROOT, both
  for the folder it is in and for what it points to (a symlink out of the vault
  is refused);
- the app's own files (cache folder, database, the app folder if it lives in
  the vault) are refused unless a route explicitly handles them itself;
- outside, internal and malformed paths all get the same 403, whether they
  exist or not, so the answer never reveals what exists outside the vault.

For a file, the returned path keeps its last component unresolved (only the
folders above it are resolved), so a route can still see and refuse a symlink
instead of silently acting on its target. For a folder (folder=True) the path is
fully resolved, so listings and stored paths always use one spelling.
"""
import os
import unicodedata
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException

from backend.file_ops import is_inside, same_folder

ACCESS_DENIED = "Toegang geweigerd: pad ligt buiten het archief"

# The app's own folder (repository root). Only treated as internal if it lies inside the vault.
APP_DIR = Path(__file__).resolve().parent.parent.parent


def _on_disk_spelling(path: Path) -> Path:
    """The given spelling if it exists; else the NFC or NFD form that does; else NFC."""
    if os.path.lexists(path):
        return path
    for form in ("NFC", "NFD"):
        variant = Path(unicodedata.normalize(form, str(path)))
        if os.path.lexists(variant):
            return variant
    return Path(unicodedata.normalize("NFC", str(path)))


def locate(user_path: Any, config, folder: bool = False) -> Optional[Path]:
    """
    The absolute path inside the vault that user_path names, or None when it is
    outside the vault (after resolving '..' and symlinks) or not a usable path.
    """
    if isinstance(user_path, Path):
        user_path = str(user_path)
    if not isinstance(user_path, str) or not user_path or "\x00" in user_path:
        return None
    path = Path(user_path)
    if not path.is_absolute():
        path = config.VAULT_ROOT / path
    path = _on_disk_spelling(path)
    try:
        root = config.VAULT_ROOT.resolve()
        if path.name in ("", ".."):
            entry = path.resolve()
        else:
            entry = path.parent.resolve() / path.name
        target = entry.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not entry.is_relative_to(root) or not target.is_relative_to(root):
        return None
    return target if folder else entry


def app_dir_in_vault(config) -> Optional[Path]:
    """The app folder, if it lives inside the vault (and is not the vault itself)."""
    if is_inside(APP_DIR, config.VAULT_ROOT) and not same_folder(APP_DIR, config.VAULT_ROOT):
        return APP_DIR
    return None


def internal_folder_for(path: Path, config) -> Optional[Path]:
    """The app-internal folder/file (cache, database, app folder) that path lies in, else None."""
    for internal in (config.CACHE_FOLDER, config.DB_PATH, app_dir_in_vault(config)):
        if internal is not None and is_inside(path, internal):
            return internal
    return None


def is_in_vault(path: Any, config, allow_internal: bool = False) -> bool:
    """Bool form of resolve_in_vault, for paths that come from the database."""
    located = locate(path, config)
    if located is None:
        return False
    return allow_internal or internal_folder_for(located, config) is None


def resolve_in_vault(user_path: Any, config, allow_internal: bool = False, folder: bool = False) -> Path:
    """
    Validate a path from a request and return it as an absolute path in the vault.
    Raises HTTPException 400 for a non-string, 403 for anything outside the vault or
    inside the app's own folders (the same answer whether the path exists or not).
    """
    if isinstance(user_path, Path):
        user_path = str(user_path)
    if not isinstance(user_path, str):
        raise HTTPException(status_code=400, detail="Ongeldig pad")
    located = locate(user_path, config, folder=folder)
    if located is None:
        raise HTTPException(status_code=403, detail=ACCESS_DENIED)
    if not allow_internal and internal_folder_for(located, config) is not None:
        raise HTTPException(status_code=403, detail=ACCESS_DENIED)
    return located


def vault_folder_or_none(user_path: Any, config) -> Optional[Path]:
    """
    For lists of folders (timeline, on-this-day): the resolved folder, or None for
    anything resolve_in_vault would refuse, so callers can skip it quietly.
    """
    try:
        return resolve_in_vault(user_path, config, folder=True)
    except HTTPException:
        return None
