"""
DataGatherer - small shared pieces for export connectors (step 3.9c)

    files(raw, wanted)     every wanted file of a raw export: a zip, a single file or a
                           registered folder, also zips INSIDE a folder or a zip (Takeout
                           folders hold takeout-*.zip parts, Apple puts zips in its zip);
                           read in memory, never unpacked to disk
    head(path)             the first bytes of a file (detect() looks at the content)
    PASHA                  the per-source opt-in for the assistant (off by default)
    WATCH_FOLDER           the per-source folder the watcher follows (a synced cloud folder)
    pasha_allowed(db, id)  whether the user switched the assistant on for that source
"""
import io
import zipfile
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple

from backend.datagatherer import raw_store, state
from backend.datagatherer.base import SettingField

WATCH_FOLDER_KEY = "watch_folder"
PASHA_KEY = "pasha"

PASHA = SettingField(key=PASHA_KEY, label="Pasha mag deze gegevens gebruiken", kind="toggle",
                     help="Standaard uit. Aan: de assistent mag bij een vraag deze gegevens lezen, alleen hier op je Mac.")


def watch_setting(help_text: str, placeholder: str = "") -> SettingField:
    return SettingField(key=WATCH_FOLDER_KEY, label="Map om op te volgen", kind="text", help=help_text,
                        placeholder=placeholder)


def pasha_allowed(db, source_id: str) -> bool:
    return state.get_settings(db, source_id).get(PASHA_KEY) is True


def head(path: Path, size: int = 4096) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(size)
    except OSError:
        return b""


def _zip_bytes(blob: bytes, wanted: Callable[[str], bool], prefix: str) -> Iterator[Tuple[str, Callable[[], bytes]]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return
    with zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            base = info.filename.rsplit("/", 1)[-1]
            if info.is_dir() or base.startswith(".") or "__MACOSX/" in info.filename or not wanted(base):
                continue
            yield f"{prefix}/{info.filename}", (lambda i=info, z=zf: raw_store._read_member(z, i))


def files(raw_path: Path, wanted: Callable[[str], bool], nested_zips: bool = True,
          depth: int = 0) -> Iterator[Tuple[str, Callable[[], bytes]]]:
    """(name, read) for every wanted file, descending into zips (one level inside a zip)."""
    raw_path = Path(raw_path)

    def want(base: str) -> bool:
        return wanted(base) or (nested_zips and base.lower().endswith(".zip"))

    for name, read in raw_store.iter_export_files(raw_path, want):
        if name.lower().endswith(".zip") and nested_zips and not wanted(name.rsplit("/", 1)[-1]):
            if raw_path.is_dir():
                yield from files(raw_path / name, wanted, nested_zips=depth < 1, depth=depth + 1)
            elif depth < 1:
                yield from _zip_bytes(read(), wanted, name)
            continue
        yield name, read


def first_line(blob: bytes) -> str:
    return blob.decode("utf-8-sig", "replace").split("\n", 1)[0].strip()


def plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}".replace(",", ".")


def merge_stats(total: dict, part: dict) -> None:
    total["added"] = total.get("added", 0) + part.get("added", 0)
    total["seen"] = total.get("seen", 0) + part.get("seen", 0)
    for key, pick in (("covers_from", min), ("covers_until", max)):
        if part.get(key) is not None:
            total[key] = part[key] if total.get(key) is None else pick(total[key], part[key])


def raw_name(raw: dict) -> str:
    return Path(raw["path"]).name


def optional_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
