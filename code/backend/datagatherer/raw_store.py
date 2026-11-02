"""
DataGatherer - the raw-export store

A raw export (my_spotify_data.zip, a Takeout zip, ...) is the user's own, irreplaceable
copy of their data. The rules:

- It is COPIED into the vault, never moved: the file in ~/Downloads stays where it is and
  the card says it may be removed from Downloads (DataVault never deletes there).
- It goes to data/<owner>/<Bron>/<YYYY-MM-DD> <soort>/<original name>, with a small
  datavault-import.json next to it (sha256, size, where it came from, when). <owner> is
  config 'data_owner', else the first folder under data/ (for the user: 'Matthew').
- Nothing is ever overwritten (file_ops.copy_no_overwrite), and the same bytes are never
  stored twice: dg_raw_imports.sha256 is unique, a second copy is 'al ingelezen'.
- Exports that are already in the vault (for example 'data/Matthew/Spotify Extended
  Streaming History' or 'data/Matthew/RAW_google') are registered IN PLACE as a folder,
  with a fingerprint of their names, sizes and mtimes; nothing in them is moved or changed.
- Zips stay zipped: importers read them through read_zip_names/open_member, never unpack
  them into the vault. A .tgz/.tar.gz (Takeout offers it next to .zip) is read the same way:
  as a stream, member by member, never extracted to disk (iter_export_files, tar_head_names).
"""
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tarfile
import threading
import time
import zipfile
from zlib import error as zlib_error
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Tuple

from backend import file_ops
from backend.datagatherer import state

FALLBACK_OWNER = "Mijn gegevens"
MANIFEST = "datavault-import.json"
HASH_CHUNK = 4 * 1024 * 1024
FREE_SPACE_MARGIN = 200 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024 * 1024     # one JSON file in an export; more is not an export
MAX_RATIO = 200                            # uncompressed / compressed: more looks like a zip bomb
MAX_STREAM_BYTES = 32 * 1024 * 1024 * 1024  # a file a parser reads as it goes (ExportFile.open)
TAR_SUFFIXES = (".tgz", ".tar.gz", ".tar")
TAR_HEAD_MEMBERS = 200                      # detect() looks at the first members of a tar only
TAR_HEAD_BYTES = 512 * 1024 * 1024          # ... and stops after this much decompressed data
BOMB_FLOOR = 50 * 1024 * 1024               # below this a high compression ratio is no bomb


# One export at a time from 'is it new?' to its row: two uploads of the same bytes (or an upload
# and the watcher) would otherwise both copy it and leave an orphan copy nobody may delete
_archive_lock = threading.Lock()


class RawStoreError(Exception):
    """A raw export that cannot be stored; the message is Dutch."""


def data_folder(config) -> Path:
    return config.VAULT_ROOT / "data"


def owner_folder(config) -> Optional[Path]:
    """data/<owner>: config 'data_owner', else the first (sorted) folder under data/."""
    data = data_folder(config)
    owner = getattr(config, "DATA_OWNER", None)
    if owner:
        return data / owner
    try:
        names = sorted(e.name for e in os.scandir(data)
                       if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "_")))
    except OSError:
        return data / FALLBACK_OWNER if config.VAULT_ROOT.is_dir() else None
    return data / (names[0] if names else FALLBACK_OWNER)


def sha256_file(path: Path, check: Optional[Callable[[int], None]] = None) -> Tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            if check is not None:
                check(size)
    return digest.hexdigest(), size


def folder_fingerprint(folder: Path, patterns: Tuple[str, ...] = ("*",)) -> Tuple[str, int, int]:
    """(fingerprint, files, bytes) of the matching files below folder: names, sizes and mtimes."""
    lines = []
    total = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or not any(fnmatch.fnmatch(name, p) for p in patterns):
                continue
            path = Path(root) / name
            try:
                st = path.lstat()
            except OSError:
                continue
            if not os.path.isfile(path) or path.is_symlink():
                continue
            total += st.st_size
            lines.append(f"{path.relative_to(folder)}\t{st.st_size}\t{st.st_mtime_ns}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), len(lines), total


STALE_PARTIAL_SECONDS = 10 * 60     # a copy in progress writes its partial file every few MB


def sweep_partials(config, connectors, now: Optional[float] = None) -> List[str]:
    """
    Remove the hidden partial copies (file_ops.copy_no_overwrite's temporary
    '.<name>.datavault-partial-<hex>') that a crash, power loss or unplug left in
    data/<owner>/<raw_folder>/<date folder>/. They are always DataVault's own unfinished
    copies, never an export: only that function makes them (exclusive create) and it never
    keeps that name. Taken under the archive lock, so no copy of this process is busy, and
    only when untouched for STALE_PARTIAL_SECONDS (another process writing would update it).
    Regular files only, never a symlink or a folder. Returns the removed paths.
    """
    owner = owner_folder(config)
    if owner is None:
        return []
    now = time.time() if now is None else now
    removed: List[str] = []
    with _archive_lock:
        for connector in connectors:
            raw = getattr(connector, "raw_folder", "")
            if not raw:
                continue
            try:
                days = [e.path for e in os.scandir(owner / raw) if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for day in days:
                try:
                    entries = [e for e in os.scandir(day) if file_ops.PARTIAL_MARK in e.name
                               and file_ops.PARTIAL_NAME.match(e.name)]
                except OSError:
                    continue
                for e in entries:
                    try:
                        st = e.stat(follow_symlinks=False)
                        if not stat.S_ISREG(st.st_mode) or now - st.st_mtime < STALE_PARTIAL_SECONDS:
                            continue
                        os.unlink(e.path)
                        removed.append(e.path)
                    except OSError:
                        continue
    for path in removed:
        print(f"DataGatherer: removed an unfinished copy: {path}")
    return removed


def target_folder(config, connector, kind_label: str, when: Optional[float] = None) -> Path:
    owner = owner_folder(config)
    if owner is None:
        raise RawStoreError("Het archief is niet bereikbaar")
    day = datetime.fromtimestamp(when or time.time()).strftime("%Y-%m-%d")
    return owner / connector.raw_folder / f"{day} {kind_label}"


def archive_file(config, db, connector, kind: str, src: Path, origin: str,
                 check: Optional[Callable[[int], None]] = None, original_name: Optional[str] = None) -> Tuple[dict, bool]:
    """
    Copy src into the vault as a raw export of connector. Returns (raw row, created):
    created is False when these exact bytes were stored before (nothing is copied then).
    origin: 'downloads' (the watcher), 'upload' (picked in the browser) or 'manual'.
    """
    export = connector.export_kind(kind)
    label = export.folder_label if export else kind
    sweep_partials(config, [connector])        # what an earlier, interrupted copy left behind
    sha, size = sha256_file(src, check)
    with _archive_lock:
        existing = state.raw_by_sha(db, sha)
        if existing is not None:
            return existing, False
        folder = target_folder(config, connector, label)
        _make_folder(config, folder)
        free = shutil.disk_usage(folder).free
        if free < size + FREE_SPACE_MARGIN:
            raise RawStoreError(f"Te weinig vrije ruimte in het archief voor deze export ({size // (1024 * 1024)} MB nodig)")
        name = original_name or src.name
        final, copied_sha, copied_size = file_ops.copy_no_overwrite(src, folder / name, expected_sha256=sha, check=check)
        original = str(src) if origin != "upload" else None
        raw_id = state.insert_raw(db, source_id=connector.id, kind=kind, path=str(final), is_folder=0,
                                  sha256=copied_sha, size=copied_size, origin=origin, original_path=original,
                                  parser_version=connector.parser_version)
        # The manifest only once the row exists: it never describes a copy DataVault does not know
        _write_manifest(folder, {
            "file": final.name, "sha256": copied_sha, "size": copied_size, "source": connector.id, "kind": kind,
            "origin": origin, "original_path": original, "original_name": name,
            "received_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "parser_version": connector.parser_version,
        })
    return state.get_raw(db, raw_id), True


def original_name(raw: dict) -> str:
    """
    The file name an export had before DataVault copied it: a second export with the same name
    on the same day is stored as 'name 2.zip'. From the original path (the Downloads watcher),
    else from the manifest next to the copy (a browser upload), else the copy's own name
    without a ' 2' that only the copy has.
    """
    path = Path(raw["path"])
    if raw.get("original_path"):
        return Path(raw["original_path"]).name
    try:
        manifests = sorted(path.parent.glob("datavault-import*.json"))
    except OSError:
        manifests = []
    for manifest in manifests:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("file") == path.name and isinstance(data.get("original_name"), str):
            name = data["original_name"].replace("\\", "/").rsplit("/", 1)[-1].strip()
            if name:
                return name
    m = re.match(r"^(.+) \d+(\.[^.]+)$", path.name)
    if m and (path.parent / (m.group(1) + m.group(2))).exists():
        return m.group(1) + m.group(2)
    return path.name


def register_folder(config, db, connector, kind: str, folder: Path, patterns: Tuple[str, ...] = ("*",)) -> Tuple[dict, bool]:
    """
    Register an export folder that is already in the vault, in place. Returns (raw row,
    changed): changed when it is new, or its files changed since it was last read (then it
    is read again; the record keys make that add only what is new).
    """
    fingerprint, files, total = folder_fingerprint(folder, patterns)
    existing = state.raw_by_path(db, str(folder))
    if existing is None:
        raw_id = state.insert_raw(db, source_id=connector.id, kind=kind, path=str(folder), is_folder=1,
                                  fingerprint=fingerprint, size=total, origin="existing",
                                  parser_version=connector.parser_version)
        return state.get_raw(db, raw_id), True
    if existing["fingerprint"] != fingerprint or existing["parser_version"] != connector.parser_version:
        state.update_raw(db, existing["id"], fingerprint=fingerprint, size=total, status="pending",
                         parser_version=connector.parser_version)
        return state.get_raw(db, existing["id"]), True
    return existing, False


def _make_folder(config, folder: Path) -> None:
    """Make the folder below the vault root, one level at a time (never the vault root itself)."""
    try:
        config._make_dir(folder)
    except FileNotFoundError as e:
        raise RawStoreError("Het archief is niet bereikbaar") from e
    except OSError as e:
        raise RawStoreError(f"De map voor de export kon niet gemaakt worden: {e.strerror or e}") from e


def _write_manifest(folder: Path, data: dict) -> None:
    """datavault-import.json next to the export: written once, never overwritten (a second
    export in the same folder on the same day gets 'datavault-import 2.json')."""
    text = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    for n in range(1, 100):
        path = folder / (MANIFEST if n == 1 else f"datavault-import {n}.json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(text)
        return


# ---------- reading exports without unpacking them ----------

def read_zip_names(path: Path) -> Optional[List[str]]:
    """The member names of a zip (its central directory only), None when it is no zip."""
    try:
        with zipfile.ZipFile(path) as zf:
            return [info.filename for info in zf.infolist() if not info.is_dir()]
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        return None


def is_tar_name(name: str) -> bool:
    return name.lower().endswith(TAR_SUFFIXES)


class _EndInfo(tarfile.TarInfo):
    """A TarInfo that notes why a stream ended: only a block of zeros is a real end of archive."""

    @classmethod
    def fromtarfile(cls, tar):
        try:
            return super().fromtarfile(tar)
        except tarfile.HeaderError as e:
            tar.dv_end = e
            raise


class _TarGuard:
    """
    The raw file under a tar stream. Every read of compressed bytes checks how far the archive
    has been inflated, also while tarfile skips a member nobody reads (its own seek), so a
    gzip bomb stops after BOMB_FLOOR instead of being inflated in full first.
    """

    def __init__(self, raw, name: str):
        self.raw, self.name = raw, name
        self.stream = None            # tarfile._Stream, once the tar is open

    def read(self, size: int = -1) -> bytes:
        data = self.raw.read(size)
        self.check()
        return data

    def check(self) -> None:
        if self.stream is None:
            return
        done = getattr(self.stream, "pos", 0)   # decompressed bytes so far
        packed = max(1, self.raw.tell())        # compressed bytes read so far
        if done > BOMB_FLOOR and done / packed > MAX_RATIO:
            raise RawStoreError(f"{self.name} is verdacht sterk ingepakt; niet verder ingelezen")


def _open_tar(path: Path):
    """
    (raw file, TarFile) of a .tgz/.tar.gz/.tar, read as a stream (mode 'r|*'): never seeks
    back. The TarFile reads through a _TarGuard (tar.dv_guard).
    """
    raw = open(path, "rb")
    try:
        guard = _TarGuard(raw, Path(path).name)
        tar = tarfile.open(fileobj=guard, mode="r|*", tarinfo=_EndInfo)
        guard.stream = tar.fileobj
        tar.dv_guard = guard
        return raw, tar
    except BaseException:
        raw.close()
        raise


def _check_complete(tar: "tarfile.TarFile", name: str) -> None:
    """
    After the last member: was this the real end of the archive? A tar stream that is cut
    off between members or inside a header just stops (tarfile returns no next member), so
    a truncated download would otherwise look complete. The end must be a block of zeros,
    and a compressed stream must reach its own end.
    """
    damaged = RawStoreError(f"{name} is beschadigd of onvolledig")
    if not isinstance(getattr(tar, "dv_end", None), tarfile.EOFHeaderError):
        raise damaged
    stream = tar.fileobj
    try:
        while stream.read(stream.bufsize):    # the rest: padding after the end blocks
            pass
    except (tarfile.TarError, EOFError, zlib_error) as e:
        raise RawStoreError(f"{name} is beschadigd of onvolledig") from e
    cmp = getattr(stream, "cmp", None)
    if cmp is not None and not getattr(cmp, "eof", True):
        raise damaged


class _TarSource:
    """
    The current member of a tar stream. A stream cannot go back, but parsers peek at the start
    and then open again (location_store.parse_export_file): the first REPLAY bytes are
    kept, so a second open() replays them and continues where the stream is.
    """
    REPLAY = 64 * 1024

    def __init__(self, tar: "tarfile.TarFile", info: "tarfile.TarInfo", guard: _TarGuard):
        self.tar, self.info, self.guard = tar, info, guard
        self.stream = None
        self.head = bytearray()   # the first bytes of the member, while pos <= REPLAY
        self.pos = 0              # bytes taken from the stream

    def open(self) -> "_TarMember":
        if self.stream is None:
            if self.tar.members[-1] is not self.info:
                raise RawStoreError(f"{self.info.name}: het archief is al verder gelezen")
            self.stream = self.tar.extractfile(self.info)
        elif self.pos > len(self.head):
            raise RawStoreError(f"{self.info.name}: het archief is al verder gelezen")
        return _TarMember(self)

    def pull(self, size: int) -> bytes:
        try:
            data = self.stream.read(size)
        except (tarfile.TarError, EOFError, zlib_error) as e:
            raise RawStoreError(f"{self.guard.name} is beschadigd of onvolledig") from e
        if len(self.head) == self.pos and self.pos + len(data) <= self.REPLAY:
            self.head += data
        self.pos += len(data)
        return data


class _TarMember(io.RawIOBase):
    """One open() of a tar member: a binary stream (replays the kept start, then reads on)."""

    def __init__(self, source: _TarSource):
        super().__init__()
        self._src, self._at = source, 0

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def read(self, size: int = -1) -> bytes:
        src = self._src
        out = b""
        if self._at < len(src.head):
            end = len(src.head) if size is None or size < 0 else min(len(src.head), self._at + size)
            out = bytes(src.head[self._at:end])
            self._at = end
            if size is not None and 0 <= size <= len(out):
                return out
        if self._at != src.pos:
            raise RawStoreError(f"{src.info.name}: het archief is al verder gelezen")
        rest = src.pull(-1 if size is None or size < 0 else size - len(out))
        self._at += len(rest)
        return out + rest

    def readable(self) -> bool:
        return True


def tar_head_names(path: Path, members: int = TAR_HEAD_MEMBERS, max_bytes: int = TAR_HEAD_BYTES) -> Optional[List[str]]:
    """
    The names of the first files of a .tgz/.tar.gz/.tar (None when it is none): enough for
    detect(). A tar has no central directory, so listing all of a 50 GB Takeout part would
    mean decompressing all of it; the first members say what it is.
    """
    if not is_tar_name(Path(path).name):
        return None
    names: List[str] = []
    try:
        raw, tar = _open_tar(Path(path))
    except (tarfile.TarError, OSError, EOFError, zlib_error, RawStoreError):
        return None
    try:
        with raw, tar:
            for info in tar:
                if info.isfile():
                    names.append(info.name[2:] if info.name.startswith("./") else info.name)
                if len(names) >= members or tar.offset > max_bytes:
                    break
    except (tarfile.TarError, OSError, EOFError, zlib_error, RawStoreError):
        if not names:
            return None
    return names


def archive_names(path: Path, tar: bool = False) -> Optional[List[str]]:
    """read_zip_names, and for a connector that reads tars (tar=True) the first tar members."""
    names = read_zip_names(path)
    if names is None and tar:
        names = tar_head_names(path)
    return names


def _iter_tar(path: Path, wanted: Callable[[str], bool]) -> Iterator[Tuple[str, "ExportFile"]]:
    """
    The wanted members of a tar, as a stream. A raw that is missing raises OSError (as for a
    zip: 'Niet leesbaar'); one that is no tar, damaged or cut off raises RawStoreError, so
    it is never marked as read with nothing in it.
    """
    try:
        raw, tar = _open_tar(path)       # OSError (missing, unreadable) goes up as it is
    except (tarfile.TarError, EOFError, zlib_error) as e:
        raise RawStoreError(f"{path.name} is geen leesbaar .tgz- of .tar-archief") from e
    with raw, tar:
        guard = tar.dv_guard
        while True:
            try:
                info = tar.next()
            except (tarfile.TarError, EOFError, zlib_error) as e:
                raise RawStoreError(f"{path.name} is beschadigd of onvolledig") from e
            if info is None:
                _check_complete(tar, path.name)
                return
            tar.members = [info]       # a stream keeps every header otherwise (100.000 photos)
            name = info.name[2:] if info.name.startswith("./") else info.name
            base = name.rsplit("/", 1)[-1]
            if not info.isfile() or base.startswith(".") or "__MACOSX/" in name or not wanted(base):
                continue
            yield name, ExportFile.of_tar_member(tar, info, guard)


def iter_export_files(path: Path, wanted: Callable[[str], bool]) -> Iterator[Tuple[str, Callable[[], bytes]]]:
    """
    (name, read) for every wanted file in a raw export: the members of a zip, the files
    below a registered folder, the members of a .tgz/.tar.gz/.tar (in archive order, as a
    stream), or the file itself. read() returns the bytes; a member that is bigger than
    MAX_MEMBER_BYTES or compressed suspiciously well is refused (zip bomb). Names are data:
    nothing is ever written to disk from here. A tar member is read while it is the current
    one: call read() (or open()) before asking for the next name.
    """
    path = Path(path)
    if path.is_dir():
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                full = Path(root) / name
                if name.startswith(".") or full.is_symlink() or not wanted(name):
                    continue
                rel = str(full.relative_to(path))
                yield rel, ExportFile.of_file(full)
        return
    if is_tar_name(path.name):
        yield from _iter_tar(path, wanted)
        return
    names = read_zip_names(path)
    if names is None:
        if wanted(path.name):
            yield path.name, ExportFile.of_file(path)
        return
    with zipfile.ZipFile(path) as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            base = info.filename.rsplit("/", 1)[-1]
            if info.is_dir() or base.startswith(".") or "__MACOSX/" in info.filename or not wanted(base):
                continue
            yield info.filename, ExportFile.of_member(zf, info)


class ExportFile:
    """
    One file of an export, as iter_export_files hands it out. Calling it returns the bytes
    (refused above MAX_MEMBER_BYTES). open() gives a binary stream instead, for a parser
    that reads as it goes (a multi-GB Records.json): up to MAX_STREAM_BYTES, and a zip member
    that is compressed suspiciously well is refused here too. size is the uncompressed size.
    """

    def __init__(self, read: Callable[[], bytes], opener: Callable[[], Any], size: int):
        self._read, self._open, self.size = read, opener, size

    def __call__(self) -> bytes:
        return self._read()

    def open(self):
        return self._open()

    @classmethod
    def of_file(cls, path: Path) -> "ExportFile":
        def opener():
            if path.stat().st_size > MAX_STREAM_BYTES:
                raise RawStoreError(f"{path.name} is te groot om in te lezen")
            return open(path, "rb")
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return cls(lambda: _read_file(path), opener, size)

    @classmethod
    def of_tar_member(cls, tar: "tarfile.TarFile", info: "tarfile.TarInfo", guard: _TarGuard) -> "ExportFile":
        # Valid only while info is the current member of the stream (iter_export_files)
        source = _TarSource(tar, info, guard)

        def opener():
            if info.size > MAX_STREAM_BYTES:
                raise RawStoreError(f"{info.name} in het archief is te groot om in te lezen")
            return source.open()

        def read() -> bytes:
            if info.size > MAX_MEMBER_BYTES:
                raise RawStoreError(f"{info.name} in het archief is te groot om in te lezen")
            with opener() as member:
                return member.read(MAX_MEMBER_BYTES + 1)
        return cls(read, opener, info.size)

    @classmethod
    def of_member(cls, zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> "ExportFile":
        def opener():
            if info.file_size > MAX_STREAM_BYTES or _bomb(info):
                raise RawStoreError(f"{info.filename} in de zip is verdacht groot; niet ingelezen")
            return zf.open(info)
        return cls(lambda: _read_member(zf, info), opener, info.file_size)


def _bomb(info: zipfile.ZipInfo) -> bool:
    return bool(info.compress_size and info.file_size / info.compress_size > MAX_RATIO
                and info.file_size > 50 * 1024 * 1024)


def _read_file(path: Path) -> bytes:
    if path.stat().st_size > MAX_MEMBER_BYTES:
        raise RawStoreError(f"{path.name} is te groot om in te lezen")
    return path.read_bytes()


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_MEMBER_BYTES or _bomb(info):
        raise RawStoreError(f"{info.filename} in de zip is verdacht groot; niet ingelezen")
    with zf.open(info) as member:
        data = member.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:
        raise RawStoreError(f"{info.filename} in de zip is te groot om in te lezen")
    return data
