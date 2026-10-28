"""
DataVault Dashboard - The background job types

dup_scan    duplicate scan of one folder (scanner.find_duplicates_in_folder); the
            result is stored, so the duplicates screen can show it again after a reload
hashing     fill the hash cache for a folder, so a later duplicate scan only reads
            what changed
thumbnails  make the missing thumbnails of a folder (or the whole vault) ahead of time

None of them moves or changes an archive file: they read the archive and write the
app's own cache (hashes in the database, thumbnails in the thumbnail folder). Resume
continues from what is already cached: hashes whose file is unchanged and valid
thumbnails are skipped, and the checkpoint says how far the last run got.
"""
import os
import stat as stat_module
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException

from backend import scanner
from backend.config import Config, is_audio, is_media_file, is_video
from backend.jobs import JobContext, JobType, VaultUnavailable, bounded_map
from backend.paths import resolve_in_vault, walk_archive

THUMB_WORKERS = 2   # Pillow and ffmpeg are CPU-heavy; leave room for the page itself


def _text(params: Dict[str, Any], key: str, required: bool = True):
    value = params.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail=f"{key} moet een pad zijn")
    return value


def _not_protected(folder: Path, config: Config) -> None:
    """RAW_DUPLICATES and TRASH hold what was already moved: never scanned or indexed."""
    from backend.file_ops import is_inside
    for protected in (config.DUPLICATES_FOLDER, config.TRASH_FOLDER):
        if is_inside(folder, protected):
            raise HTTPException(status_code=400, detail="Deze map wordt niet op duplicaten gescand")


def _folder(params: Dict[str, Any], config: Config, key: str = "folder", default_root: bool = False) -> Path:
    raw = _text(params, key, required=not default_root)
    # Inside the vault first, existence second: the same 403 for anything outside
    folder = resolve_in_vault(raw if raw else str(config.VAULT_ROOT), config, folder=True)
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Map niet gevonden")
    return folder


def _vault_check(config: Config, folder: Path, errors) -> None:
    """Errors while the vault (or the folder) is gone are not a result: wait or fail."""
    if errors.count and not config.VAULT_ROOT.is_dir():
        raise VaultUnavailable()
    if errors.count and not folder.is_dir():
        raise scanner.ScanAborted(str(folder))


# ---------- dup_scan ----------

def dup_scan_type(config: Config, db, on_done=None) -> JobType:
    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        folder = _folder(params, config)
        _not_protected(folder, config)
        rule = params.get("rule", scanner.DEFAULT_KEEP_RULE)
        if rule not in scanner.KEEP_RULES:
            raise HTTPException(status_code=400, detail=f"Onbekende regel: kies uit {', '.join(scanner.KEEP_RULES)}")
        prefer = None
        if rule == "folder":
            if not params.get("prefer_folder"):
                raise HTTPException(status_code=400, detail="Kies een voorkeursmap voor deze regel")
            prefer = str(_folder(params, config, "prefer_folder"))
        return {"folder": str(folder), "rule": rule, "prefer_folder": prefer}

    def run(ctx: JobContext) -> Dict[str, Any]:
        folder = Path(ctx.params["folder"])
        if not folder.is_dir():
            if not config.VAULT_ROOT.is_dir():
                raise VaultUnavailable()
            raise scanner.ScanAborted(str(folder))
        ctx.save_checkpoint({"runs": ctx.checkpoint.get("runs", 0) + 1})
        try:
            result = scanner.find_duplicates_in_folder(
                folder, config, db, rule=ctx.params["rule"],
                prefer_folder=ctx.params.get("prefer_folder"), reporter=ctx,
            )
        except scanner.ScanAborted:
            if not config.VAULT_ROOT.is_dir():
                raise VaultUnavailable()
            raise ScanFolderGone()
        if result["errors"] and not config.VAULT_ROOT.is_dir():
            raise VaultUnavailable()  # what it missed is the unplugged disk: never 'done'
        ctx.progress(message=_scan_message(result))
        return {"folder": str(folder), "total_scanned": result["total_files"],
                "prefer_folder": ctx.params.get("prefer_folder"), **result}

    def summarize(result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "folder": result["folder"],
            "groups": len(result["groups"]),
            "duplicates_found": result["duplicates_found"],
            "total_files": result["total_files"],
            "complete": result["complete"],
            "errors": result["errors"],
        }

    return JobType("dup_scan", "Duplicaten zoeken", prepare, run, summarize,
                   present=lambda result: present_scan(result), on_done=on_done, results_to_keep=5)


class ScanFolderGone(Exception):
    """The scanned folder disappeared while the vault is still there."""

    def __str__(self) -> str:
        return "De map is tijdens de scan onbereikbaar geworden (verplaatst of hernoemd?)"


def _scan_message(result: Dict[str, Any]) -> str:
    text = f"Klaar: {len(result['groups'])} groepen, {result['duplicates_found']} duplicaten"
    if result["errors"]:
        text += f" ({result['errors']} leesfout(en): onvolledig)"
    return text


def present_scan(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    A stored scan, as it is now: copies that were moved or changed since (by the
    duplicates screen, the Keurer or Finder) are left out, and a group with fewer
    than two copies left is gone. Which copy stays is chosen again by the scan's rule.
    """
    groups = []
    for group in result.get("groups", []):
        copies = []
        for copy in group["copies"]:
            try:
                st = os.lstat(copy["path"])
            except OSError:
                continue
            if stat_module.S_ISREG(st.st_mode) and st.st_size == group["size"]:
                copies.append(copy)
        if len(copies) < 2:
            continue
        if len(copies) != len(group["copies"]):
            group = {**group, "copies": copies,
                     "keep": scanner.choose_keeper(copies, result.get("rule", scanner.DEFAULT_KEEP_RULE),
                                                   result.get("prefer_folder"))}
        groups.append(group)
    found = sum(len(g["copies"]) - 1 for g in groups)
    return {**result, "groups": groups, "duplicates_found": found,
            "stale_removed": result.get("duplicates_found", found) - found}


# ---------- hashing ----------

def hashing_type(config: Config, db) -> JobType:
    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        folder = _folder(params, config, default_root=True)
        _not_protected(folder, config)
        return {"folder": str(folder)}

    def run(ctx: JobContext) -> Dict[str, Any]:
        folder = Path(ctx.params["folder"])
        if not folder.is_dir():
            raise VaultUnavailable() if not config.VAULT_ROOT.is_dir() else ScanFolderGone()
        errors = scanner._ErrorLog()
        ctx.progress(0, None, "Bestanden zoeken...")
        fingerprints, _, _ = scanner.collect_media_files(folder, config, ctx, errors)
        _vault_check(config, folder, errors)
        paths = sorted(fingerprints)
        _, newly = scanner.hash_files(paths, fingerprints, folder, db, ctx, errors, label="Indexeren")
        _vault_check(config, folder, errors)
        ctx.save_checkpoint({"files": len(paths), "hashed": newly})
        return {
            "folder": str(folder),
            "files": len(paths),
            "hashed": newly,
            "from_cache": len(paths) - newly - errors.count,
            "errors": errors.count,
            "error_samples": errors.samples,
            "complete": errors.count == 0,
        }

    return JobType("hashing", "Controlegetallen berekenen", prepare, run,
                   summarize=lambda r: {k: r[k] for k in ("folder", "files", "hashed", "errors", "complete")},
                   results_to_keep=3)


# ---------- thumbnails ----------

def _thumb_targets(folder: Path, config: Config, ctx: JobContext, errors) -> List[str]:
    """Every photo and video below folder, sorted (audio has a fixed icon)."""
    paths: List[str] = []
    last = time.monotonic()
    for root, dirnames, filenames in walk_archive(folder, config, onerror=errors):
        ctx.check()
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(root) / name
            if is_media_file(path) and not is_audio(path):
                paths.append(str(path))
        if time.monotonic() - last >= scanner.PROGRESS_SECONDS:
            last = time.monotonic()
            ctx.progress(len(paths), None, f"Bestanden zoeken... {len(paths)} gevonden")
    return paths


def thumbnails_type(config: Config) -> JobType:
    """
    Resume needs no list of what was done: a thumbnail that is already there and
    newer than its photo is skipped with two stats, so a resumed (or repeated) run
    only makes what is still missing, including for files added in the meantime.
    """
    from backend.thumbnail import get_or_create_thumbnail, get_thumbnail_cache_path, is_thumbnail_valid

    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        folder = _folder(params, config, default_root=True)
        return {"folder": str(folder)}

    def make(path: str) -> str:
        file_path = Path(path)
        cache_path = get_thumbnail_cache_path(file_path, config.THUMB_CACHE_PATH)
        if is_thumbnail_valid(cache_path, file_path):
            return "cached"
        thumb = get_or_create_thumbnail(file_path, config.THUMB_CACHE_PATH, is_video=is_video(file_path))
        if thumb is None:
            raise OSError(0, "miniatuur kon niet gemaakt worden")
        return "made"

    def run(ctx: JobContext) -> Dict[str, Any]:
        folder = Path(ctx.params["folder"])
        if not folder.is_dir():
            raise VaultUnavailable() if not config.VAULT_ROOT.is_dir() else ScanFolderGone()
        config.ensure_cache_dirs()
        errors = scanner._ErrorLog()
        ctx.progress(0, None, "Bestanden zoeken...")
        paths = _thumb_targets(folder, config, ctx, errors)
        total = len(paths)
        counts = {"made": 0, "cached": 0}
        processed = 0
        last = 0.0
        ctx.progress(0, total, f"Miniaturen... 0/{total}")
        for path, outcome, error in bounded_map(make, paths, workers=THUMB_WORKERS, stop=lambda: ctx.stopping):
            processed += 1
            if error is not None:
                errors(path, error)
            else:
                counts[outcome] += 1
            now = time.monotonic()
            if now - last >= scanner.PROGRESS_SECONDS or processed == total:
                last = now
                ctx.progress(processed, total, f"Miniaturen... {processed}/{total} ({counts['made']} nieuw)")
        ctx.check()
        _vault_check(config, folder, errors)
        ctx.save_checkpoint({"files": total, **counts})
        return {
            "folder": str(folder),
            "files": total,
            "made": counts["made"],
            "cached": counts["cached"],
            "errors": errors.count,
            "error_samples": errors.samples,
            "complete": errors.count == 0,
        }

    return JobType("thumbnails", "Miniaturen maken", prepare, run,
                   summarize=lambda r: {k: r[k] for k in ("folder", "files", "made", "errors", "complete")},
                   results_to_keep=3)


def build_job_types(config: Config, db, on_dup_scan_done=None) -> List[JobType]:
    return [
        dup_scan_type(config, db, on_dup_scan_done),
        hashing_type(config, db),
        thumbnails_type(config),
    ]
