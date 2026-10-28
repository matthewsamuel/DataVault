"""
DataVault Dashboard - The background job types

dup_scan    duplicate scan of one folder (scanner.find_duplicates_in_folder); the
            result is stored, so the duplicates screen can show it again after a reload
hashing     fill the hash cache for a folder, so a later duplicate scan only reads
            what changed
thumbnails  make the missing thumbnails of a folder (or the whole vault) ahead of time
index       bring the media index (backend/media_index.py) up to date with the disk
export      copy chosen files out of the archive to another folder (backend/export.py)

None of them moves or changes an archive file: they read the archive and write the
app's own cache (hashes and the media index in the database, thumbnails in the
thumbnail folder). Resume
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
from backend.export import export_type
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


def _describe(config: Config):
    """The folder a job works on, relative to the vault ('hele archief' for the root)."""
    def describe(params: Dict[str, Any]) -> str:
        folder = Path(params.get("folder") or config.VAULT_ROOT)
        try:
            rel = folder.relative_to(config.VAULT_ROOT)
        except ValueError:
            return str(folder)
        return str(rel) if rel.parts else "hele archief"
    return describe


class _JobErrors(scanner._ErrorLog):
    """
    The read errors of one run, and whether the vault was gone when one happened.
    A file that could not be read because the disk was unplugged (even if it is
    back by the end of the run) is not a result: the run is done again.
    """

    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        self.vault_lost = False

    def __call__(self, path, exc) -> None:
        super().__call__(path, exc)
        if not self.vault_lost:
            try:
                self.vault_lost = not self._config.VAULT_ROOT.is_dir()
            except OSError:
                self.vault_lost = True


def _vault_check(config: Config, folder: Path, errors: _JobErrors) -> None:
    """Errors while the vault (or the folder) is gone are not a result: wait or fail."""
    if errors.vault_lost or (errors.count and not config.VAULT_ROOT.is_dir()):
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
        errors = _JobErrors(config)
        try:
            result = scanner.find_duplicates_in_folder(
                folder, config, db, rule=ctx.params["rule"],
                prefer_folder=ctx.params.get("prefer_folder"), reporter=ctx, errors=errors,
            )
        except scanner.ScanAborted:
            if not config.VAULT_ROOT.is_dir():
                raise VaultUnavailable()
            raise ScanFolderGone()
        if errors.vault_lost or (result["errors"] and not config.VAULT_ROOT.is_dir()):
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
                   present=lambda result: present_scan(result), on_done=on_done, results_to_keep=5,
                   describe=_describe(config))


class ScanFolderGone(Exception):
    """The scanned folder disappeared while the vault is still there."""

    def __str__(self) -> str:
        return "De map is tijdens de scan onbereikbaar geworden (verplaatst of hernoemd?)"


def _scan_message(result: Dict[str, Any]) -> str:
    groups, found, errors = len(result["groups"]), result["duplicates_found"], result["errors"]
    text = (f"Klaar: {groups} {'groep' if groups == 1 else 'groepen'}, "
            f"{found} {'duplicaat' if found == 1 else 'duplicaten'}")
    if errors:
        text += f" ({errors} {'leesfout' if errors == 1 else 'leesfouten'}: onvolledig)"
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
        # Hashes an earlier run of this job stored (paused, interrupted, vault unplugged)
        # count in its total: they are 'from the cache' for this run
        before = ctx.checkpoint.get("hashed_before", 0)
        stats: Dict[str, int] = {}
        errors = _JobErrors(config)
        try:
            ctx.progress(0, None, "Bestanden zoeken...")
            fingerprints, _, _ = scanner.collect_media_files(folder, config, ctx, errors)
            _vault_check(config, folder, errors)
            paths = sorted(fingerprints)
            _, newly = scanner.hash_files(paths, fingerprints, folder, db, ctx, errors,
                                          label="Indexeren", stats=stats)
            _vault_check(config, folder, errors)
        except BaseException:
            ctx.save_checkpoint({**ctx.checkpoint, "hashed_before": before + stats.get("stored", 0)})
            raise
        hashed = before + newly
        ctx.save_checkpoint({"files": len(paths), "hashed": hashed})
        ctx.progress(message=f"Klaar: {len(paths)} bestanden, {hashed} nieuw berekend"
                             + (f", {errors.count} leesfout(en)" if errors.count else ""))
        return {
            "folder": str(folder),
            "files": len(paths),
            "hashed": hashed,
            "from_cache": max(0, len(paths) - hashed - errors.count),
            "errors": errors.count,
            "error_samples": errors.samples,
            "complete": errors.count == 0,
        }

    return JobType("hashing", "Controlegetallen berekenen", prepare, run,
                   summarize=lambda r: {k: r[k] for k in ("folder", "files", "hashed", "errors", "complete")},
                   results_to_keep=3, describe=_describe(config))


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

    A photo or video that cannot be read is an error, never 'made': the job does not
    put a placeholder in its place (one would be newer than the photo and stand in
    for it for good). One that reads fine but cannot be decoded (corrupt, empty, an
    unknown format, a video without a frame) is counted apart as 'undecodable': it
    gives the same answer on every run, so it does not make a run incomplete.
    Placeholders a request or an older version left for a photo are made again. If
    the vault goes away during a run, nothing new is started and the run is done
    again once the disk is back.
    """
    from backend.thumbnail import (Undecodable, get_thumbnail_cache_path, have_ffmpeg, is_placeholder,
                                   is_thumbnail_valid, render_photo_thumbnail, render_video_thumbnail)

    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        folder = _folder(params, config, default_root=True)
        return {"folder": str(folder)}

    def make(path: str, ffmpeg: bool) -> str:
        file_path = Path(path)
        video = is_video(file_path)
        cache_path = get_thumbnail_cache_path(file_path, config.THUMB_CACHE_PATH)
        if is_thumbnail_valid(cache_path, file_path) and (video and not ffmpeg or not is_placeholder(cache_path)):
            return "cached"
        if video and not ffmpeg:
            return "no_ffmpeg"  # the viewer shows the video placeholder
        if video:
            render_video_thumbnail(file_path, cache_path)
        else:
            render_photo_thumbnail(file_path, cache_path)
        return "made"

    def run(ctx: JobContext) -> Dict[str, Any]:
        folder = Path(ctx.params["folder"])
        if not folder.is_dir():
            raise VaultUnavailable() if not config.VAULT_ROOT.is_dir() else ScanFolderGone()
        config.ensure_cache_dirs()
        ffmpeg = have_ffmpeg()
        # Thumbnails an earlier run of this job made (paused, interrupted, vault
        # unplugged) count as made: this run finds them 'already there'
        before = ctx.checkpoint.get("made_before", 0)
        errors = _JobErrors(config)
        undecodable = scanner._ErrorLog()
        counts = {"made": 0, "cached": 0, "no_ffmpeg": 0}
        try:
            ctx.progress(0, None, "Bestanden zoeken...")
            paths = _thumb_targets(folder, config, ctx, errors)
            _vault_check(config, folder, errors)
            total = len(paths)
            processed = 0
            last = 0.0
            ctx.progress(0, total, f"Miniaturen... 0/{total}")
            stop = lambda: ctx.stopping or errors.vault_lost  # noqa: E731
            for path, outcome, error in bounded_map(lambda p: make(p, ffmpeg), paths,
                                                    workers=THUMB_WORKERS, stop=stop):
                processed += 1
                if isinstance(error, Undecodable) and not errors.vault_lost and config.VAULT_ROOT.is_dir():
                    undecodable(path, error)
                elif error is not None:
                    errors(path, error)
                else:
                    counts[outcome] += 1
                now = time.monotonic()
                if now - last >= scanner.PROGRESS_SECONDS or processed == total:
                    last = now
                    ctx.progress(processed, total,
                                 f"Miniaturen... {processed}/{total} "
                                 f"({min(before, counts['cached']) + counts['made']} nieuw)")
            ctx.check()
            _vault_check(config, folder, errors)
        except BaseException:
            ctx.save_checkpoint({**ctx.checkpoint, "made_before": before + counts["made"]})
            raise
        # What earlier runs made, this run finds 'already there'; only as many of
        # those as are really there count (one that went missing was made again and
        # is in counts["made"]), so 'nieuw' never adds up to more than the files
        earlier = min(before, counts["cached"])
        made = earlier + counts["made"]
        cached = counts["cached"] - earlier
        ctx.save_checkpoint({"files": total, "made": made, "cached": cached})
        ctx.progress(message=f"Klaar: {made} nieuw, {cached} waren er al"
                             + (f", {counts['no_ffmpeg']} video('s) zonder miniatuur (ffmpeg ontbreekt)"
                                if counts["no_ffmpeg"] else "")
                             + (f", {undecodable.count} onleesbaar (kapot of onbekend formaat)"
                                if undecodable.count else "")
                             + (f", {errors.count} mislukt" if errors.count else ""))
        return {
            "folder": str(folder),
            "files": total,
            "made": made,
            "cached": cached,
            "no_ffmpeg": counts["no_ffmpeg"],
            "undecodable": undecodable.count,
            "undecodable_samples": undecodable.samples,
            "errors": errors.count,
            "error_samples": errors.samples,
            "complete": errors.count == 0,
        }

    return JobType("thumbnails", "Miniaturen maken", prepare, run,
                   summarize=lambda r: {k: r.get(k, 0) for k in ("folder", "files", "made", "undecodable",
                                                                  "errors", "complete")},
                   results_to_keep=3, describe=_describe(config))


# ---------- index ----------

def index_type(config: Config, db) -> JobType:
    """
    One pass of the media index over the whole vault (media_index.run_pass).
    params: full (list every folder and compare every file, the 'Bibliotheek bijwerken'
    button) and auto (queued by the app itself: on start, when the vault returns).
    Resume needs no checkpoint of its own: a folder's row is written after its files,
    so what a stopped pass wrote is unchanged and skipped next time.
    """
    from backend import media_index

    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("full", "auto"):
            if not isinstance(params.get(key, False), bool):
                raise HTTPException(status_code=400, detail=f"{key} moet true of false zijn")
        return {"full": params.get("full", False), "auto": params.get("auto", False)}

    def run(ctx: JobContext) -> Dict[str, Any]:
        if not config.VAULT_ROOT.is_dir():
            raise VaultUnavailable()
        errors, read_errors = _JobErrors(config), _JobErrors(config)

        def vault_check() -> None:
            if errors.vault_lost or read_errors.vault_lost or not config.VAULT_ROOT.is_dir():
                raise VaultUnavailable()

        result = media_index.run_pass(ctx, config, db, ctx.params.get("full", False),
                                      errors, read_errors, vault_check)
        ctx.progress(result["folders"], result["folders"], media_index.pass_message(result))
        return result

    return JobType(media_index.INDEX_JOB, "Bibliotheek bijwerken", prepare, run,
                   summarize=lambda r: {k: r.get(k) for k in ("full", "files", "new", "changed", "moved",
                                                               "removed", "errors", "complete", "seconds")},
                   results_to_keep=3, jobs_to_keep=5, describe=lambda params: "hele archief")


def build_job_types(config: Config, db, on_dup_scan_done=None) -> List[JobType]:
    return [
        dup_scan_type(config, db, on_dup_scan_done),
        hashing_type(config, db),
        thumbnails_type(config),
        index_type(config, db),
        export_type(config, db),
    ]
