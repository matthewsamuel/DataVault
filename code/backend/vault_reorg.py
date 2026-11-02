"""
DataVault Dashboard - de mappen van het archief herschikken volgens een plan

Een plan is een JSON-bestand met stappen. Paden zijn relatief aan de vault; '../naam' is
een map naast de vault op dezelfde schijf (zoals /Volumes/T7/edit):

    {"op": "move", "from": "library/fam", "to": "library/familie"}
        een map (of bestand) in zijn geheel verplaatsen of hernoemen
    {"op": "move_loose", "from": "library/annick", "to": "library/annick/fotos"}
        de losse bestanden die direct in een map staan naar een (nieuwe) submap
    {"op": "trash_if_empty", "path": "library/trips"}
        een map die leeg is geworden (alleen verborgen bestanden zoals .DS_Store) naar
        TRASH/_reorganisatie-<datum>/; staat er nog iets in, dan blijft hij staan

Zo werkt het, ook op een tweede schijf met dezelfde opbouw en meer data (een back-up):
- Hele mappen verhuizen met één rename: wat er op die schijf extra in staat, gaat mee.
- Nooit overschrijven of samenvoegen: bestaat de bestemming al, dan stopt het plan daar.
- Nooit kopiëren of wissen; een verplaatsing naar een andere schijf wordt geweigerd.
- Een stap waarvan de bron weg is en de bestemming er staat, is al gedaan (overgeslagen),
  dus een onderbroken plan kan gewoon opnieuw.
- Met een database (de schijf van de app): elk pad in de database dat in de verplaatste map
  ligt, verhuist mee in dezelfde transactie (index, ratings, Elo, Keurer, Shows, hashes,
  herstelcentrum, DataGatherer-exports, mapfilters van slimme albums), net als de thumbnails.
  Lukt de database niet, dan gaat de map terug.
- Elke uitgevoerde stap komt in een journaal (JSON-regels, exact) en een logboek (leesbaar);
  het journaal kan alles in omgekeerde volgorde terugdraaien.
"""
import ctypes
import errno
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from backend import file_ops

OPS = ("move", "move_loose", "trash_if_empty")
TRASH_SUBFOLDER = "_reorganisatie-{day}"
# Finder's own files: a folder with only these counts as empty
IGNORED_NAMES = {".DS_Store", ".localized", "Icon\r"}


class ReorgError(Exception):
    pass


@dataclass
class Step:
    index: int
    op: str
    src: str = ""
    dst: str = ""
    note: str = ""

    def describe(self) -> str:
        if self.op == "trash_if_empty":
            return f"{self.src}  ->  prullenbak (als leeg)"
        if self.op == "move_loose":
            return f"{self.src}/* (losse bestanden)  ->  {self.dst}/"
        return f"{self.src}  ->  {self.dst}"


@dataclass
class Check:
    step: Step
    state: str                    # 'todo', 'done', 'missing', 'conflict', 'not_empty', 'refused'
    reason: str = ""
    files: int = 0
    names: List[str] = field(default_factory=list)   # move_loose: the files it moves


# ---------- the plan ----------

def load_plan(path: Path) -> Tuple[Dict[str, Any], List[Step]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ReorgError("Dit plan heeft geen stappen ('steps')")
    steps = []
    for i, item in enumerate(raw, 1):
        op = item.get("op")
        if op not in OPS:
            raise ReorgError(f"Stap {i}: onbekende stap {op!r}")
        src = item.get("path") if op == "trash_if_empty" else item.get("from")
        dst = "" if op == "trash_if_empty" else item.get("to")
        for value in (src, dst) if op != "trash_if_empty" else (src,):
            _check_rel(value, i)
        steps.append(Step(i, op, src, dst or "", item.get("note", "")))
    return data, steps


def _check_rel(value: Any, i: int) -> None:
    if not isinstance(value, str) or not value or value.startswith("/") or value.endswith("/"):
        raise ReorgError(f"Stap {i}: {value!r} is geen relatief pad")
    parts = value.split("/")
    ups = [p for p in parts if p == ".."]
    if any(p in ("", ".") for p in parts) or len(ups) > 1 or (ups and parts[0] != ".."):
        raise ReorgError(f"Stap {i}: {value!r} mag alleen met één '../' beginnen")


class Vault:
    """The vault the plan runs on: its root, TRASH and the folders no step may touch."""

    def __init__(self, root: Path, trash: Optional[Path] = None, protected: Iterable[Path] = ()):
        self.root = Path(os.path.realpath(root))
        if not self.root.is_dir():
            raise ReorgError(f"De vault {root} bestaat niet")
        self.trash = Path(os.path.realpath(trash)) if trash else self.root / "TRASH"
        self.protected = [Path(p) for p in protected]
        self.device = os.stat(self.root).st_dev

    def path(self, rel: str) -> Path:
        if rel.startswith("../"):
            return self.root.parent / rel[3:]
        return self.root / rel

    def refuse(self, path: Path, what: str) -> Optional[str]:
        """Why path may not be a source or destination, else None."""
        if path == self.root or path == self.root.parent:
            return f"{what} is de vault zelf"
        for folder in self.protected:
            if file_ops.is_inside(path, folder) or file_ops.is_inside(folder, path):
                return f"{what} raakt een map van de app ({folder.name})"
        if file_ops.is_inside(path, self.trash) or file_ops.is_inside(self.trash, path):
            return f"{what} raakt de prullenbak"
        parent = path.parent
        while not parent.exists():
            parent = parent.parent
        try:
            if os.stat(parent).st_dev != self.device:
                return f"{what} ligt op een andere schijf"
        except OSError as e:
            return f"{what} is niet bereikbaar ({e})"
        return None


def _visible_files(folder: Path) -> int:
    count = 0
    for _, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        count += sum(1 for f in files if f not in IGNORED_NAMES and not f.startswith("._"))
    return count


def _loose(folder: Path) -> List[str]:
    return sorted(e.name for e in os.scandir(folder)
                  if e.is_file(follow_symlinks=False) and not e.name.startswith("."))


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _same_entry(src: Path, dst: Path) -> bool:
    """dst is src itself under another spelling (a case-only rename on a case-insensitive disk)."""
    try:
        return src.name != dst.name and src.parent.resolve() == dst.parent.resolve() and os.path.samefile(src, dst)
    except OSError:
        return False


def _disk_name(path: Path) -> Optional[str]:
    """The spelling the disk itself has for path's last name (it may differ in case)."""
    try:
        want = path.name.casefold()
        return next((n for n in os.listdir(path.parent) if n.casefold() == want), None)
    except OSError:
        return None


def check(vault: Vault, step: Step, trash_day: str) -> Check:
    """What this step would do on this vault, without changing anything."""
    src = vault.path(step.src)
    if step.op == "trash_if_empty":
        dst = vault.trash / TRASH_SUBFOLDER.format(day=trash_day) / step.src.replace("../", "")
    else:
        dst = vault.path(step.dst)
    for path, what in ((src, "de bron"), (dst, "de bestemming")):
        if step.op == "trash_if_empty" and path is dst:
            continue
        why = vault.refuse(path, what)
        if why:
            return Check(step, "refused", why)
    if step.op == "move_loose":
        if not src.is_dir() or src.is_symlink():
            return Check(step, "missing", "de map staat niet op deze schijf")
        names = _loose(src)
        if not names:
            return Check(step, "done" if dst.is_dir() else "missing", "geen losse bestanden (meer)")
        if _exists(dst) and not dst.is_dir():
            return Check(step, "conflict", "de bestemming is een bestand")
        taken = [n for n in names if _exists(dst / n)]
        if taken:
            return Check(step, "conflict", f"{len(taken)} namen bestaan al in de bestemming, bijv. {taken[0]}")
        return Check(step, "todo", files=len(names), names=names)
    if not _exists(src) or (_same_entry(src, dst) and _disk_name(src) == dst.name):
        if _exists(dst):
            return Check(step, "done", "al gedaan")
        return Check(step, "missing", "staat niet op deze schijf")
    if src.is_symlink():
        return Check(step, "refused", "de bron is een snelkoppeling (symlink)")
    if step.op == "trash_if_empty":
        if not src.is_dir():
            return Check(step, "refused", "geen map")
        n = _visible_files(src)
        if n:
            return Check(step, "not_empty", f"er staan nog {n} bestanden in: blijft staan")
        if _exists(dst):
            return Check(step, "conflict", "staat al in de prullenbak")
        return Check(step, "todo")
    if _exists(dst) and not _same_entry(src, dst):
        return Check(step, "conflict", "de bestemming bestaat al (er wordt nooit samengevoegd)")
    if file_ops.is_inside(dst, src) and not _same_entry(src, dst):
        return Check(step, "refused", "de bestemming ligt in de bron")
    return Check(step, "todo", files=_visible_files(src) if src.is_dir() else 1)


# ---------- moving without overwriting ----------

def _rename_excl(src: Path, dst: Path) -> None:
    """Atomic rename of a file or folder that fails when dst exists (never replaces it)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _same_entry(src, dst):
        os.rename(src, dst)           # only the spelling changes: nothing can be replaced
        return
    renamex = getattr(file_ops, "_renamex_np", None)
    if renamex is not None:
        if renamex(os.fsencode(src), os.fsencode(dst), file_ops._RENAME_EXCL) == 0:
            return
        err = ctypes.get_errno()
        if err not in (errno.ENOTSUP, errno.EINVAL):
            raise OSError(err, os.strerror(err), str(src), None, str(dst))
    # A filesystem without RENAME_EXCL (exFAT): the app is stopped, so check-then-rename
    if _exists(dst):
        raise OSError(errno.EEXIST, "bestaat al", str(dst))
    os.rename(src, dst)


# ---------- the database ----------

def _text_columns(conn: sqlite3.Connection) -> List[Tuple[str, str]]:
    found = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                 "AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE 'CREATE VIRTUAL%'"):
        for row in conn.execute(f'PRAGMA table_info("{table}")'):
            if (row[2] or "").upper() in ("TEXT", "", "JSON"):
                found.append((table, row[1]))
    return found


def path_columns(conn: sqlite3.Connection, root: Path) -> List[Tuple[str, str]]:
    """The (table, column) pairs that hold absolute paths in this vault (found, not listed)."""
    prefix = str(root) + "/"
    cols = []
    for table, col in _text_columns(conn):
        hit = conn.execute(f'SELECT 1 FROM "{table}" WHERE substr("{col}", 1, ?) = ? LIMIT 1',
                           (len(prefix), prefix)).fetchone()
        if hit:
            cols.append((table, col))
    return cols


def _under(col: str) -> str:
    return f'("{col}" = ? OR substr("{col}", 1, ?) = ?)'


def _args(path: str) -> Tuple:
    return (path, len(path) + 1, path + "/")


def db_rows_at(conn, cols, path: str) -> int:
    return sum(conn.execute(f'SELECT count(*) FROM "{t}" WHERE {_under(c)}', _args(path)).fetchone()[0]
               for t, c in cols)


def db_move(conn, cols, old: str, new: str, rel_old: Optional[str], rel_new: Optional[str]) -> int:
    """Every path at or under old points under new; smart album folder filters follow too."""
    changed = 0
    for table, col in cols:
        changed += conn.execute(
            f'UPDATE "{table}" SET "{col}" = ? || substr("{col}", ?) WHERE {_under(col)}',
            (new, len(old) + 1) + _args(old)).rowcount
    if rel_old is not None and rel_new is not None:
        changed += _move_album_folders(conn, rel_old, rel_new)
    return changed


def _move_album_folders(conn, rel_old: str, rel_new: str) -> int:
    try:
        rows = conn.execute("SELECT id, filters FROM smart_albums").fetchall()
    except sqlite3.OperationalError:
        return 0

    def moved(value):
        if isinstance(value, str) and (value == rel_old or value.startswith(rel_old + "/")):
            return rel_new + value[len(rel_old):]
        return value
    changed = 0
    for album_id, text in rows:
        try:
            filters = json.loads(text or "{}")
        except ValueError:
            continue
        if not isinstance(filters, dict):
            continue
        before = json.dumps(filters, sort_keys=True)
        if "folder" in filters:
            filters["folder"] = moved(filters["folder"])
        if isinstance(filters.get("folders"), list):
            filters["folders"] = [moved(v) for v in filters["folders"]]
        if json.dumps(filters, sort_keys=True) != before:
            conn.execute("UPDATE smart_albums SET filters = ? WHERE id = ?", (json.dumps(filters), album_id))
            changed += 1
    return changed


def _media_paths(conn, path: str) -> List[str]:
    try:
        return [r[0] for r in conn.execute(f'SELECT path FROM media WHERE {_under("path")}', _args(path))]
    except sqlite3.OperationalError:
        return []


# ---------- running a plan ----------

class Runner:
    """
    Runs (or with dry_run only checks) the steps on one vault. With db_path the paths in
    that database follow every move; thumbs is the app's thumbnail folder. journal and log
    are appended to after every step that changed something.
    """

    def __init__(self, vault: Vault, steps: List[Step], db_path: Optional[Path] = None,
                 thumbs: Optional[Path] = None, journal: Optional[Path] = None, log: Optional[Path] = None,
                 label: str = "", say: Callable[[str], None] = print, trash_day: Optional[str] = None):
        self.vault = vault
        self.steps = steps
        self.db_path = db_path
        self.thumbs = thumbs
        self.journal = journal
        self.log = log
        self.label = label or str(vault.root)
        self.say = say
        self.trash_day = trash_day or time.strftime("%Y-%m-%d")

    def run(self, stop_on_conflict: bool = True) -> Dict[str, Any]:
        conn = None
        cols: List[Tuple[str, str]] = []
        if self.db_path is not None:
            conn = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
            conn.execute("PRAGMA busy_timeout = 30000")
            cols = path_columns(conn, self.vault.root)
        counts = {"done": 0, "skipped": 0, "stopped": None, "db_rows": 0}
        try:
            for step in self.steps:
                result = check(self.vault, step, self.trash_day)
                if result.state in ("done", "missing", "not_empty"):
                    counts["skipped"] += 1
                    self.say(f"  {step.index:>3}. overgeslagen ({result.reason}): {step.describe()}")
                    self._write_log(step, result.state, result.reason)
                    continue
                if result.state != "todo":
                    self.say(f"  {step.index:>3}. GESTOPT ({result.reason}): {step.describe()}")
                    self._write_log(step, result.state, result.reason)
                    if stop_on_conflict:
                        counts["stopped"] = step.index
                        break
                    continue
                moved, rows = self._do(step, result, conn, cols)
                counts["done"] += 1
                counts["db_rows"] += rows
                self._write_journal(step, moved, result, rows)
                self._write_log(step, "gedaan", f"{result.files} bestanden" + (f", {rows} databaseregels" if conn else ""))
                self.say(f"  {step.index:>3}. gedaan: {step.describe()}  ({result.files} bestanden"
                         + (f", {rows} databaseregels)" if conn else ")"))
        finally:
            if conn is not None:
                conn.close()
        return counts

    def _dest(self, step: Step) -> Path:
        if step.op == "trash_if_empty":
            return self.vault.trash / TRASH_SUBFOLDER.format(day=self.trash_day) / step.src.replace("../", "")
        return self.vault.path(step.dst)

    def _rel(self, path: Path) -> Optional[str]:
        try:
            return str(path.relative_to(self.vault.root))
        except ValueError:
            return None

    def _do(self, step: Step, result: Check, conn, cols) -> Tuple[List[Tuple[str, str]], int]:
        src, dst = self.vault.path(step.src), self._dest(step)
        pairs = ([(str(src / n), str(dst / n)) for n in result.names] if step.op == "move_loose"
                 else [(str(src), str(dst))])
        if conn is not None:
            conn.execute("BEGIN IMMEDIATE")
        done: List[Tuple[str, str]] = []
        try:
            if conn is not None:
                for old, new in pairs:
                    if db_rows_at(conn, cols, new):
                        raise ReorgError(f"de database kent al paden onder {new}")
                media = [p for old, _ in pairs for p in _media_paths(conn, old)]
            for old, new in pairs:
                _rename_excl(Path(old), Path(new))
                done.append((old, new))
            rows = 0
            if conn is not None:
                for old, new in pairs:
                    rows += db_move(conn, cols, old, new, self._rel(Path(old)), self._rel(Path(new)))
                conn.execute("COMMIT")
        except BaseException:
            if conn is not None and conn.in_transaction:
                conn.execute("ROLLBACK")
            for old, new in reversed(done):
                try:
                    _rename_excl(Path(new), Path(old))
                except OSError as e:
                    self.say(f"  !! terugzetten mislukt: {new} -> {old} ({e})")
            raise
        if conn is not None and self.thumbs is not None:
            self._carry_thumbs(media, pairs)
        return done, rows

    def _carry_thumbs(self, media: List[str], pairs: List[Tuple[str, str]]) -> None:
        from backend import thumbnail
        for path in media:
            for old, new in pairs:
                if path == old or path.startswith(old + "/"):
                    thumbnail.carry_over(Path(path), Path(new + path[len(old):]), self.thumbs)
                    break

    def _write_journal(self, step: Step, moved, result: Check, rows: int) -> None:
        if self.journal is None:
            return
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "disk": self.label, "step": step.index,
                 "op": step.op, "from": step.src, "to": step.dst, "files": result.files,
                 "db_rows": rows, "moved": [[self._rel(Path(a)) or a, self._rel(Path(b)) or b] for a, b in moved]}
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _write_log(self, step: Step, state: str, detail: str) -> None:
        if self.log is None:
            return
        self.log.parent.mkdir(parents=True, exist_ok=True)
        new = not self.log.exists()
        with open(self.log, "a", encoding="utf-8") as f:
            if new:
                f.write("# Logboek van de reorganisatie\n\nElke regel is één stap die op een schijf is uitgevoerd "
                        "of overgeslagen, in de volgorde waarin het gebeurde.\n\n"
                        "| Wanneer | Schijf | Stap | Wat | Resultaat |\n|---|---|---|---|---|\n")
            what = step.describe().replace("|", "\\|")
            f.write(f"| {time.strftime('%Y-%m-%d %H:%M')} | {self.label} | {step.index} | `{what}` | "
                    f"{state}: {detail.replace('|', '/')} |\n")


def undo_steps(journal: Path, disk: Optional[str] = None) -> List[Step]:
    """The journal's moves in reverse order, as plain moves back (only those of one disk)."""
    entries = [json.loads(line) for line in Path(journal).read_text(encoding="utf-8").splitlines() if line.strip()]
    if disk is not None:
        entries = [e for e in entries if e.get("disk") == disk]
    steps = []
    for entry in reversed(entries):
        for a, b in reversed(entry["moved"]):
            steps.append(Step(len(steps) + 1, "move", b, a, note=f"terug van stap {entry['step']}"))
    return steps


def untouched_children(vault: Vault, steps: List[Step]) -> Dict[str, List[str]]:
    """Folders next to the ones the plan moves that the plan does not name (extra data on this disk)."""
    named = set()
    parents = set()
    for s in steps:
        for rel in (s.src, s.dst):
            if rel:
                named.add(rel)
                parts = rel.split("/")
                for i in range(1, len(parts)):
                    named.add("/".join(parts[:i]))
                if "/" in rel and not rel.startswith("../"):
                    parents.add(rel.rsplit("/", 1)[0])
    found: Dict[str, List[str]] = {}
    for parent in sorted(parents):
        folder = vault.path(parent)
        if not folder.is_dir():
            continue
        extra = sorted(e.name for e in os.scandir(folder)
                       if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
                       and f"{parent}/{e.name}" not in named)
        if extra:
            found[parent] = extra
    return found


def mirror(vault: Vault, steps: List[Step], target: Path) -> Vault:
    """
    A skeleton of this vault for a dry run: the same folders and file names (empty files),
    plus the folders next to the vault that the plan names ('../edit'). The app's own
    folders and TRASH are left out (only TRASH itself is made). Returns the copy's Vault.
    """
    target = Path(target)
    if _exists(target):
        raise ReorgError(f"{target} bestaat al")
    root = target / vault.root.name
    skip = [vault.trash] + vault.protected
    sources = [vault.root] + sorted({vault.path(s.src.split("/")[0] + "/" + s.src.split("/")[1])
                                     for s in steps if s.src.startswith("../")})
    for source in sources:
        if not source.is_dir():
            continue
        dest_base = root if source == vault.root else target / source.name
        for folder, dirs, files in os.walk(source):
            here = Path(folder)
            dirs[:] = [d for d in dirs if not any(file_ops.is_inside(here / d, s) for s in skip)
                       and not os.path.islink(here / d)]
            out = dest_base / here.relative_to(source)
            out.mkdir(parents=True, exist_ok=True)
            for name in files:
                if not os.path.islink(here / name):
                    (out / name).touch()
    (root / vault.trash.relative_to(vault.root)).mkdir(parents=True, exist_ok=True)
    return Vault(root, root / vault.trash.relative_to(vault.root))
