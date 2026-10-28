"""
DataVault Dashboard - Export (copy a selection, or the best photos of a period, out of the archive)

Nothing in the archive is changed: files are only READ and copied to a folder outside the
vault. The destination is validated first (never inside the vault, so never in RAW, TRASH,
RAW_DUPLICATES or the cache; never a system folder), the total size is computed and the
export is refused when it does not fit with a margin. Nothing is ever overwritten: a name
that is taken gets ' (2)', ' (3)', ... A file is written under a temporary name and moved
into place with an exclusive rename (the same call the archive moves use), so a stopped
export never leaves half a photo under a real name.

Modes: 'originals' (byte-identical copies, mtime and birthtime kept) or 'resized' (photos
as JPEG with a longest edge of max_edge, EXIF kept, HEIC -> JPEG; videos and audio and
photos that cannot be decoded are copied as they are). Structure: 'flat', 'month'
(YYYY-MM by capture date) or 'folders' (the folders of the archive). A manifest (CSV or
JSON) lists every exported file with its source path.

Resume: every finished file is appended to a small log in the destination
(.datavault-export-<job>.jsonl); a resumed job skips what the log lists. The log is
turned into the manifest at the end and then removed (our own temporary file).

best_of() picks the best photos of a period: rating (Shows), Keurer 'keep' and, when step
3.4 is there, the quality score within a near-duplicate series; at most one photo per
burst or series; spread over time. It is deterministic.
"""
import csv
import errno
import json
import math
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from backend import library_filters, media_index
from backend.config import Config, is_audio, is_photo, is_video
from backend.file_ops import _exclusive_rename, is_inside
from backend.jobs import JobContext, JobType, VaultUnavailable
from backend.paths import internal_folder_for, locate

EXPORT_JOB = "export"
DEFAULT_BASE = Path("~/Pictures/DataVault Export")
MODES = ("originals", "resized")
STRUCTURES = ("flat", "month", "folders")
MANIFESTS = ("none", "csv", "json")
MAX_ITEMS = 50_000
MIN_EDGE, MAX_EDGE, DEFAULT_EDGE = 256, 8192, 2048
JPEG_QUALITY = 88
CHUNK = 4 * 1024 * 1024
MARGIN_MIN = 100 * 1024 ** 2        # always keep this much free ...
MARGIN_PCT = 0.05                    # ... or this part of the export, whichever is more
BYTES_PER_PIXEL = 0.6                # a generous estimate of a resized JPEG
UNKNOWN_RESIZED = 1_500_000
LOG_PREFIX = ".datavault-export-"

# Never a destination, itself or anything below it (the real path, symlinks resolved)
SYSTEM_TREES = ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/dev", "/Applications", "/cores",
                "/opt", "/private/etc", "/private/var/db", "/private/var/root")
# Never a destination itself (a folder below them is fine)
SYSTEM_FOLDERS = ("/", "/Users", "/Volumes", "/private", "/private/var", "/home", "/tmp", "/var")
_rename_lock = threading.Lock()


class ExportError(Exception):
    """A request that cannot be honoured; the text is Dutch and for the user."""


# ---------- destination ----------

def default_base() -> Path:
    return DEFAULT_BASE.expanduser()


def _existing_ancestor(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return Path("/")


def check_destination(raw: Any, config: Config) -> Path:
    """The destination as an absolute real path, or ExportError. The folder need not exist yet."""
    if not isinstance(raw, str) or not raw.strip():
        raise ExportError("Kies een map om naartoe te exporteren")
    if "\0" in raw:
        raise ExportError("Ongeldig pad")
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise ExportError("Geef een volledig pad op (beginnend met / of ~)")
    try:
        real = Path(os.path.realpath(path))
    except (OSError, ValueError):
        raise ExportError("Ongeldig pad")
    home = Path.home()
    folders = {Path(p) for p in SYSTEM_FOLDERS} | {home}
    if real in folders or any(real == Path(t) or real.is_relative_to(t) for t in SYSTEM_TREES) \
            or real.is_relative_to(home / "Library"):
        raise ExportError("Dit is een systeemmap; kies een eigen map, bijvoorbeeld in Afbeeldingen")
    protected = {"de bibliotheek (het archief)": config.VAULT_ROOT, "de RAW-map": config.RAW_FOLDER,
                 "RAW_DUPLICATES": config.DUPLICATES_FOLDER, "TRASH": config.TRASH_FOLDER,
                 "de cache van DataVault": config.CACHE_FOLDER}
    for label, folder in protected.items():
        if is_inside(real, folder):
            raise ExportError(f"Exporteren naar {label} kan niet: kies een map buiten het archief")
    if internal_folder_for(real, config) is not None:
        raise ExportError("Exporteren naar een map van DataVault zelf kan niet")
    if real.exists() and not real.is_dir():
        raise ExportError("Op die plek staat een bestand, geen map")
    anchor = _existing_ancestor(real)
    if not anchor.is_dir() or not os.access(anchor, os.W_OK | os.X_OK):
        raise ExportError("In die map mag DataVault niet schrijven")
    return real


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(_existing_ancestor(path)).free


def needed_bytes(total: int) -> int:
    return total + max(MARGIN_MIN, int(total * MARGIN_PCT))


def human_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} TB"


def check_space(destination: Path, total: int) -> Tuple[int, int]:
    """(free, needed); ExportError with the numbers when it does not fit."""
    free, needed = free_bytes(destination), needed_bytes(total)
    if free < needed:
        raise ExportError(f"Te weinig ruimte: de export is ongeveer {human_bytes(total)} en er is "
                          f"{human_bytes(free)} vrij (met marge is {human_bytes(needed)} nodig). "
                          f"Kies een andere map, bijvoorbeeld op de T7 buiten het archief, of kies minder bestanden")
    return free, needed


# ---------- the files ----------

@dataclass
class Item:
    path: str
    name: str
    kind: str
    ext: str
    size: int
    taken: Optional[float]
    width: Optional[int]
    height: Optional[int]
    rel_folder: str

    def estimate(self, mode: str, edge: int) -> int:
        """Bytes this file takes in the export."""
        if mode == "originals" or self.kind != "photo":
            return self.size
        if self.width and self.height:
            scale = min(1.0, edge / max(self.width, self.height))
            return min(self.size, int(self.width * self.height * scale * scale * BYTES_PER_PIXEL) + 20_000)
        return min(self.size, UNKNOWN_RESIZED)


def _chunks(items: List, size: int = 400) -> Iterable[List]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def load_items(db, config: Config, paths: Any) -> Tuple[List[Item], int]:
    """
    The files for these paths, in the given order without doubles, and how many were left
    out (not in the archive, no longer there, not a photo, video or audio file).
    """
    if not isinstance(paths, list) or not paths:
        raise ExportError("Kies eerst bestanden om te exporteren")
    if len(paths) > MAX_ITEMS:
        raise ExportError(f"Te veel bestanden in één export (hoogstens {MAX_ITEMS})")
    located: Dict[str, Path] = {}
    skipped = 0
    for raw in paths:
        file = locate(raw, config) if isinstance(raw, str) else None
        if file is None or internal_folder_for(file, config) is not None or not file.is_file() \
                or not (is_photo(file) or is_video(file) or is_audio(file)):
            skipped += 1
        elif str(file) not in located:
            located[str(file)] = file
    rows: Dict[str, Tuple] = {}
    keys = list(located)
    with db._connect() as conn:
        for chunk in _chunks(keys):
            for row in conn.execute(
                    f"SELECT path, kind, ext, size, taken, width, height FROM media "
                    f"WHERE path IN ({', '.join('?' * len(chunk))})", chunk):
                rows[row[0]] = row
    items = []
    root = config.VAULT_ROOT
    for key in keys:
        file = located[key]
        row = rows.get(key)
        if row:
            _, kind, ext, size, taken, width, height = row
        else:
            try:
                st = file.stat()
            except OSError:
                skipped += 1
                continue
            kind = "photo" if is_photo(file) else "video" if is_video(file) else "audio"
            ext, size, taken, width, height = file.suffix.lower(), st.st_size, st.st_mtime, None, None
        try:
            rel = file.parent.relative_to(root).as_posix()
        except ValueError:
            rel = ""
        items.append(Item(key, file.name, kind, ext, size, taken, width, height, "" if rel == "." else rel))
    return items, skipped


def plan(db, config: Config, paths: Any, mode: str, edge: int, destination: Any = None) -> Dict[str, Any]:
    """What an export of these files would take, for the dialog (the same checks as the job)."""
    mode, edge = _mode(mode), _edge(edge)
    items, skipped = load_items(db, config, paths)
    total = sum(i.size for i in items)
    estimate = sum(i.estimate(mode, edge) for i in items)
    out: Dict[str, Any] = {"count": len(items), "skipped": skipped, "total_bytes": total,
                           "estimated_bytes": estimate, "needed_bytes": needed_bytes(estimate),
                           "destination": None, "destination_error": None, "free_bytes": None, "fits": None}
    if destination is not None:
        try:
            real = check_destination(destination, config)
            out["destination"] = str(real)
            out["free_bytes"] = free_bytes(real)
            out["fits"] = out["free_bytes"] >= out["needed_bytes"]
            if not out["fits"]:
                out["destination_error"] = (f"Te weinig ruimte: er is {human_bytes(out['free_bytes'])} vrij en "
                                            f"{human_bytes(out['needed_bytes'])} nodig (met marge)")
        except ExportError as e:
            out["destination_error"] = str(e)
    return out


def _mode(value: Any) -> str:
    if value not in MODES:
        raise ExportError("Kies originelen of verkleind (JPEG)")
    return value


def _edge(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_EDGE <= value <= MAX_EDGE:
        raise ExportError(f"De langste zijde is een getal tussen {MIN_EDGE} en {MAX_EDGE} pixels")
    return value


# ---------- copying ----------

def _unique(folder: Path, name: str) -> Path:
    candidate = folder / name
    stem, suffix = os.path.splitext(name)
    n = 1
    while os.path.lexists(candidate):
        n += 1
        candidate = folder / f"{stem} ({n}){suffix}"
    return candidate


def _place(tmp: Path, folder: Path, name: str) -> Path:
    """Move tmp to a free name in folder without ever replacing anything."""
    while True:
        with _rename_lock:
            target = _unique(folder, name)
            try:
                _exclusive_rename(tmp, target)
                return target
            except FileExistsError:
                continue
            except OSError as e:
                if e.errno == errno.EEXIST:
                    continue
                if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EINVAL, errno.EMLINK):
                    raise
                # A volume without hard links (exFAT): the name was just checked under the lock
                os.rename(tmp, target)
                return target


def _stamp(src_st: os.stat_result, dst: Path) -> None:
    """mtime and, where the system has it, birthtime like the source (macOS lowers the birthtime with the mtime)."""
    birth = getattr(src_st, "st_birthtime", None)
    if birth is not None and birth < src_st.st_mtime:
        os.utime(dst, (birth, birth))
    os.utime(dst, ns=(src_st.st_atime_ns, src_st.st_mtime_ns))


def _copy(ctx: JobContext, src: Path, tmp: Path) -> None:
    with open(src, "rb") as r, open(tmp, "xb") as w:
        while True:
            ctx.check()
            block = r.read(CHUNK)
            if not block:
                return
            w.write(block)


def _resize(src: Path, tmp: Path, edge: int) -> Tuple[int, int]:
    """src as a JPEG with a longest edge of at most edge, upright, EXIF kept. Returns the size written."""
    from PIL import Image, ImageOps
    from backend import thumbnail  # noqa: F401  registers the HEIC reader
    with Image.open(src) as opened:
        opened.load()
        exif = opened.getexif()
        icc = opened.info.get("icc_profile")
        image = ImageOps.exif_transpose(opened)
        exif = image.getexif() if len(image.getexif()) else exif
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.thumbnail((edge, edge), Image.LANCZOS)
        extra: Dict[str, Any] = {}
        if len(exif):
            exif[0x0112] = 1          # already upright
            extra["exif"] = exif.tobytes()
        if icc:
            extra["icc_profile"] = icc
        with open(tmp, "xb") as w:
            image.save(w, "JPEG", quality=JPEG_QUALITY, optimize=True, **extra)
        return image.size


def _subfolder(item: Item, structure: str) -> str:
    if structure == "month":
        if item.taken:
            try:
                return datetime.fromtimestamp(item.taken).strftime("%Y-%m")
            except (OSError, OverflowError, ValueError):
                pass
        return "Onbekende datum"
    if structure == "folders":
        return item.rel_folder
    return ""


def _safe_parts(rel: str) -> List[str]:
    return [p for p in rel.split("/") if p and p not in (".", "..")]


def _read_log(path: Path) -> Dict[str, Dict[str, Any]]:
    done: Dict[str, Dict[str, Any]] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue            # a line cut off by a crash
                if isinstance(entry, dict) and isinstance(entry.get("source_path"), str):
                    done[entry["source_path"]] = entry
    except OSError:
        pass
    return done


MANIFEST_COLUMNS = ("source_path", "exported_as", "bytes", "taken", "width", "height", "converted")


def _write_manifest(dest: Path, kind: str, entries: List[Dict[str, Any]]) -> Optional[str]:
    if kind == "none" or not entries:
        return None
    ext = "csv" if kind == "csv" else "json"
    tmp = dest / f"{LOG_PREFIX}manifest.tmp"
    if os.path.lexists(tmp):
        tmp = dest / f"{LOG_PREFIX}manifest-{int(time.time())}.tmp"
    with open(tmp, "x", encoding="utf-8", newline="") as f:
        if kind == "csv":
            writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(entries)
        else:
            json.dump([{k: e.get(k) for k in MANIFEST_COLUMNS} for e in entries], f, ensure_ascii=False, indent=1)
    return _place(tmp, dest, f"manifest.{ext}").name


class _Errors:
    def __init__(self):
        self.count, self.samples = 0, []

    def add(self, name: str, error: Any) -> None:
        self.count += 1
        if len(self.samples) < 10:
            self.samples.append(f"{name}: {error}")


def run_export(ctx: JobContext, config: Config, db) -> Dict[str, Any]:
    p = ctx.params
    mode, edge, structure, manifest = p["mode"], p["max_edge"], p["structure"], p["manifest"]
    items, skipped = load_items(db, config, p["paths"])
    dest = check_destination(p["destination"], config)
    log_path = dest / f"{LOG_PREFIX}{ctx.job_id}.jsonl"
    done = _read_log(log_path)
    remaining = sum(i.estimate(mode, edge) for i in items if i.path not in done)
    try:
        check_space(dest, remaining)
    except ExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    dest.mkdir(parents=True, exist_ok=True)
    errors = _Errors()
    entries: List[Dict[str, Any]] = []
    total = len(items)
    copied = bytes_out = converted = fallback = 0
    last = 0.0
    ctx.progress(len(done), total, "Exporteren...")
    with open(log_path, "a", encoding="utf-8") as log:
        for index, item in enumerate(items):
            known = done.get(item.path)
            if known:
                entries.append(known)
                copied += 1
                bytes_out += known.get("bytes") or 0
                converted += 1 if known.get("converted") else 0
                continue
            ctx.check()
            tmp = None
            try:
                src = Path(item.path)
                st = src.stat()
                folder = dest.joinpath(*_safe_parts(_subfolder(item, structure)))
                folder.mkdir(parents=True, exist_ok=True)
                tmp = folder / f"{LOG_PREFIX}{ctx.job_id}-{index}.part"
                if os.path.lexists(tmp):
                    os.unlink(tmp)          # a leftover of this job's own interrupted run
                name, was_converted = item.name, False
                width = height = None
                if mode == "resized" and item.kind == "photo":
                    try:
                        width, height = _resize(src, tmp, edge)
                        name, was_converted = os.path.splitext(item.name)[0] + ".jpg", True
                    except (OSError, ValueError, SyntaxError, Exception) as e:
                        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
                            raise
                        if os.path.lexists(tmp):
                            os.unlink(tmp)
                        fallback += 1
                        _copy(ctx, src, tmp)
                else:
                    _copy(ctx, src, tmp)
                target = _place(tmp, folder, name)
                tmp = None
                _stamp(st, target)
                size = target.stat().st_size
                if not was_converted and size != st.st_size:
                    raise OSError("De kopie heeft een andere grootte dan het origineel")
                entry = {"source_path": item.path, "exported_as": target.relative_to(dest).as_posix(),
                         "bytes": size, "converted": was_converted,
                         "taken": datetime.fromtimestamp(item.taken).isoformat(timespec="seconds") if item.taken else None,
                         "width": width or item.width, "height": height or item.height}
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log.flush()
                entries.append(entry)
                copied += 1
                bytes_out += size
                converted += 1 if was_converted else 0
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    raise HTTPException(status_code=507, detail="De schijf is vol; er is niets overschreven. "
                                                              "Maak ruimte of kies een andere map en hervat")
                if not config.VAULT_ROOT.is_dir():
                    raise VaultUnavailable()
                errors.add(item.name, e.strerror or e)
            finally:
                if tmp is not None and os.path.lexists(tmp):
                    try:
                        os.unlink(tmp)      # our own half-written temporary file
                    except OSError:
                        pass
            now = time.monotonic()
            if now - last >= 0.3 or index == total - 1:
                last = now
                ctx.progress(index + 1, total, f"Exporteren... {index + 1}/{total} ({human_bytes(bytes_out)})")
                ctx.save_checkpoint({"copied": copied, "bytes": bytes_out})
    manifest_name = _write_manifest(dest, manifest, entries)
    try:
        os.unlink(log_path)                 # the progress log of this job, now in the manifest
    except OSError:
        pass
    message = f"Klaar: {copied} bestanden ({human_bytes(bytes_out)}) naar {dest.name}"
    if errors.count:
        message += f", {errors.count} mislukt"
    ctx.progress(total, total, message)
    return {"destination": str(dest), "copied": copied, "bytes": bytes_out, "skipped": skipped,
            "converted": converted, "original_instead": fallback, "errors": errors.count,
            "error_samples": errors.samples, "manifest": manifest_name, "mode": mode,
            "complete": errors.count == 0}


def export_type(config: Config, db) -> JobType:
    def prepare(params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            mode = _mode(params.get("mode", "originals"))
            edge = _edge(params.get("max_edge", DEFAULT_EDGE))
            structure = params.get("structure", "flat")
            manifest = params.get("manifest", "csv")
            if structure not in STRUCTURES:
                raise ExportError("Kies platte map, per maand of originele mappen")
            if manifest not in MANIFESTS:
                raise ExportError("Het overzicht is csv, json of geen")
            items, _ = load_items(db, config, params.get("paths"))
            if not items:
                raise ExportError("Geen van de gekozen bestanden is nog beschikbaar")
            destination = check_destination(params.get("destination"), config)
            check_space(destination, sum(i.estimate(mode, edge) for i in items))
        except ExportError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"paths": [i.path for i in items], "destination": str(destination), "mode": mode,
                "max_edge": edge, "structure": structure, "manifest": manifest}

    def describe(params: Dict[str, Any]) -> str:
        return f"{len(params.get('paths') or [])} bestanden"

    keys = ("destination", "copied", "bytes", "skipped", "converted", "errors", "manifest", "complete")
    return JobType(EXPORT_JOB, "Exporteren", prepare, lambda ctx: run_export(ctx, config, db),
                   summarize=lambda r: {k: r.get(k) for k in keys}, results_to_keep=5, jobs_to_keep=10,
                   describe=describe)


# ---------- the best of a period ----------

RATING_POINTS = (-4.0, 8.0)     # a net vote is a point; from Shows (1000 + 50 per vote)
KEEP_POINTS = 5.0
QUALITY_POINTS = 3.0            # step 3.4: the quality score (0-100) within a series
BURST_GAP = 3.0                 # seconds between shots of one burst (without step 3.4)


def _series_provider(db) -> Tuple[Dict[int, int], Dict[int, float]]:
    """
    ({media id: series number}, {media id: quality 0-100}) from the near-duplicate series of
    step 3.4 when that part of the app is there, else two empty dicts. Never raises.
    """
    try:
        from backend import near_dups
        data = near_dups.all_series(db, "normaal")
        groups: Dict[int, int] = {}
        for number, series in enumerate(data["series"]):
            for media_id in series["ids"]:
                groups[media_id] = number
            for copies in (series.get("copies") or {}).values():
                for media_id in copies:
                    groups[media_id] = number
        quality: Dict[int, float] = {}
        for series in data["series"]:
            members = near_dups._member_rows(db, list(series["ids"]))
            rows = [members[i] for i in series["ids"] if i in members]
            if len(rows) > 1:
                near_dups.score_members(rows)
                quality.update({m["id"]: float(m["score"]) for m in rows})
        return groups, quality
    except Exception:
        return {}, {}


def _score(row: Dict[str, Any], keep: bool, quality: Optional[float]) -> Tuple[float, List[str]]:
    points, why = 0.0, []
    votes = row.get("votes") or 0
    if votes:
        net = max(RATING_POINTS[0], min(RATING_POINTS[1], ((row.get("rating") or 1000) - 1000) / 50.0))
        points += net
        if net > 0:
            why.append("hoog gewaardeerd")
    if keep:
        points += KEEP_POINTS
        why.append("gehouden in de Keurer")
    if quality is not None:
        points += QUALITY_POINTS * quality / 100.0
    return points, why


def _burst_groups(rows: List[Dict[str, Any]]) -> Dict[int, int]:
    """Without step 3.4: shots of one camera in one folder at most BURST_GAP seconds apart are one burst."""
    groups: Dict[int, int] = {}
    ordered = sorted((r for r in rows if r.get("taken") is not None), key=lambda r: (r["folder"], r["taken"], r["id"]))
    number = -1
    prev = None
    for r in ordered:
        camera = (r.get("camera_make"), r.get("camera_model"))
        if prev and prev["folder"] == r["folder"] and (prev.get("camera_make"), prev.get("camera_model")) == camera \
                and r["taken"] - prev["taken"] <= BURST_GAP:
            groups[r["id"]] = groups[prev["id"]]
        else:
            number -= 1                 # negative: never equal to a series number of step 3.4
            groups[r["id"]] = number
        prev = r
    return groups


def select_best(rows: List[Dict[str, Any]], keep_ids: set, count: Optional[int], percent: Optional[float],
                series: Optional[Dict[int, int]] = None, quality: Optional[Dict[int, float]] = None) -> List[Dict[str, Any]]:
    """
    The best photos of rows (media_index rows with rating and votes), as dicts with score and
    reasons, oldest first. Pure and deterministic: ties go to the larger photo, then the path.
    1. score every photo; 2. one photo per burst or series (its best); 3. take count (or percent
    of what is left), the best first but spread over the period: a first round skips photos
    closer in time than half of an even spacing, a second round fills what is left.
    """
    series, quality = series or {}, quality or {}
    bursts = _burst_groups(rows)
    scored = []
    for r in rows:
        points, why = _score(r, r["id"] in keep_ids, quality.get(r["id"]))
        scored.append({**r, "score": round(points, 3), "reasons": why})

    def rank(r):
        return (-r["score"], -((r.get("width") or 0) * (r.get("height") or 0)), r["path"])

    best: Dict[Any, Dict[str, Any]] = {}
    for r in sorted(scored, key=rank):
        key = ("s", series[r["id"]]) if r["id"] in series else ("b", bursts[r["id"]]) if r["id"] in bursts else ("p", r["id"])
        best.setdefault(key, r)
    pool = sorted(best.values(), key=rank)
    if percent is not None:
        count = max(1, math.ceil(len(pool) * percent / 100.0)) if pool else 0
    count = min(count or 0, len(pool))
    stamps = [r["taken"] for r in pool if r.get("taken") is not None]
    span = (max(stamps) - min(stamps)) if stamps else 0
    gap = span / (2.0 * count) if count else 0
    chosen: List[Dict[str, Any]] = []
    for r in pool:
        if len(chosen) >= count:
            break
        if gap and r.get("taken") is not None and any(
                c.get("taken") is not None and abs(c["taken"] - r["taken"]) < gap for c in chosen):
            continue
        chosen.append(r)
    for r in pool:
        if len(chosen) >= count:
            break
        if r not in chosen:
            chosen.append(r)
    return sorted(chosen, key=lambda r: (r.get("taken") or 0, r["path"]))


def best_of(db, config: Config, period: Dict[str, Any], count: Optional[int] = None,
            percent: Optional[float] = None, folder: Optional[str] = None,
            album_id: Optional[int] = None) -> Dict[str, Any]:
    """
    {items, pool, total, count}: the best photos for a period ({date_from, date_to} as
    YYYY-MM-DD, both included), optionally in a folder and/or a smart album's filters.
    Photos only. items carry path, name, size, taken, score, reasons, rating.
    """
    from backend import smart_albums
    if (count is None) == (percent is None):
        raise ExportError("Kies een aantal of een percentage")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 5000):
        raise ExportError("Het aantal is een getal tussen 1 en 5000")
    if percent is not None and (isinstance(percent, bool) or not isinstance(percent, (int, float))
                                or not 0 < percent <= 100):
        raise ExportError("Het percentage ligt tussen 1 en 100")
    query: Dict[str, Any] = {}
    try:
        if album_id is not None:
            album = smart_albums.get_album(db, album_id)
            if not album:
                raise ExportError("Dit album bestaat niet meer")
            query = library_filters.to_query(library_filters.clean(album["filters"]), config)
        for key, field in (("date_from", False), ("date_to", True)):
            value = (period or {}).get(key)
            query.pop(key, None)
            if value:
                try:
                    day = datetime.strptime(value, "%Y-%m-%d")
                except (ValueError, TypeError):
                    raise ExportError("Ongeldige datum, verwacht JJJJ-MM-DD")
                query[key] = (day.timestamp() + 86400) if field else day.timestamp()
        if folder:
            from backend.paths import resolve_in_vault
            query["folder"] = str(resolve_in_vault(folder, config, folder=True))
            query["recursive"] = True
            query.pop("folders", None)
    except library_filters.FilterError as e:
        raise ExportError(str(e))
    query["kind"] = "photo"
    total = media_index.count(db, query)
    rows = media_index.query(db, query, sort="taken", offset=0, limit=media_index.MAX_LIMIT) if total else []
    # query() pages are capped: walk the rest in pages
    while total > len(rows):
        more = media_index.query(db, query, sort="taken", offset=len(rows), limit=media_index.MAX_LIMIT)
        if not more:
            break
        rows += more
    with db._connect() as conn:
        keep = {r[0] for r in conn.execute("SELECT DISTINCT path FROM keurer_decisions WHERE decision = 'keep'")}
    keep_ids = {r["id"] for r in rows if r["path"] in keep}
    groups, quality = _series_provider(db)
    chosen = select_best(rows, keep_ids, count, percent, groups, quality)
    items = [{"id": r["id"], "path": r["path"], "name": r["name"], "size": r["size"], "taken": r.get("taken"),
              "score": r["score"], "reasons": r["reasons"], "rating": r.get("rating")} for r in chosen]
    return {"items": items, "pool": len(rows), "total": total, "count": len(items)}
