"""
DataVault Dashboard - SQLite schema migrations

Every schema change is a numbered migration that runs exactly once, inside a
transaction. Before any pending migration runs on an existing database, a full
copy is written to <cache>/db_backups/, so ratings, streaks and undo data can
always be recovered. Migrations only add; they never drop user data.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Tuple

BACKUP_DIR_NAME = "db_backups"
BACKUPS_TO_KEEP = 10


def _m001_baseline(conn: sqlite3.Connection):
    """Schema as it existed before migrations were introduced (no-op on old DBs)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_hashes (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            sha256 TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS delete_runs (
            run_id TEXT PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_files INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS delete_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            src_path TEXT NOT NULL,
            dst_path TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES delete_runs(run_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_ratings (
            path TEXT PRIMARY KEY,
            rating INTEGER DEFAULT 1000,
            votes INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS show_settings (
            path TEXT PRIMARY KEY,
            enabled BOOLEAN DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS keurer_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            folder TEXT NOT NULL,
            decision TEXT NOT NULL,
            decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(path, folder)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keurer_folder ON keurer_decisions(folder)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keurer_date ON keurer_decisions(decided_at)")


def _m002_keurer_undo_paths(conn: sqlite3.Connection):
    """
    Keurer undo runs server-side: remember where a decision put the file.
    path stays the original location; dest_path is the TRASH or move target
    (NULL for 'keep' and for rows written before this migration).
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(keurer_decisions)")}
    if "dest_path" not in columns:
        conn.execute("ALTER TABLE keurer_decisions ADD COLUMN dest_path TEXT")


def _m003_keurer_dest_fingerprint(conn: sqlite3.Connection):
    """
    Remember which file a Keurer decision moved: inode number and size of the
    file at dest_path. A rename keeps the inode, so undo can tell the moved file
    apart from a different, same-named file that later took its place in TRASH.
    (st_dev is not stored: it changes when an external disk is mounted again.)
    NULL for 'keep' and for rows written before this migration.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(keurer_decisions)")}
    if "dest_ino" not in columns:
        conn.execute("ALTER TABLE keurer_decisions ADD COLUMN dest_ino INTEGER")
    if "dest_size" not in columns:
        conn.execute("ALTER TABLE keurer_decisions ADD COLUMN dest_size INTEGER")


def _m004_keurer_keep_move_history(conn: sqlite3.Connection):
    """
    UNIQUE(path, folder) let a new decision on a path silently replace an
    earlier 'move' row (and with it the only record of where that file went).
    Rebuild keurer_decisions so uniqueness only applies to keep/delete rows.
    Every row is copied with its own id, and the AUTOINCREMENT counter is kept,
    so an id that was already used (e.g. by an undone decision) is never reused.
    """
    row = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'keurer_decisions'"
    ).fetchone()
    old_seq = row[0] if row else 0
    count_before = conn.execute("SELECT COUNT(*) FROM keurer_decisions").fetchone()[0]

    conn.execute("""
        CREATE TABLE keurer_decisions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            folder TEXT NOT NULL,
            decision TEXT NOT NULL,
            decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dest_path TEXT,
            dest_ino INTEGER,
            dest_size INTEGER
        )
    """)
    columns = "id, path, folder, decision, decided_at, dest_path, dest_ino, dest_size"
    conn.execute(f"INSERT INTO keurer_decisions_new ({columns}) SELECT {columns} FROM keurer_decisions")
    count_after = conn.execute("SELECT COUNT(*) FROM keurer_decisions_new").fetchone()[0]
    if count_after != count_before:
        raise RuntimeError(f"keurer_decisions copy incomplete: {count_after} of {count_before} rows")

    conn.execute("DROP TABLE keurer_decisions")
    conn.execute("ALTER TABLE keurer_decisions_new RENAME TO keurer_decisions")
    conn.execute(
        "CREATE UNIQUE INDEX idx_keurer_path_folder ON keurer_decisions(path, folder) "
        "WHERE decision != 'move'"
    )
    conn.execute("CREATE INDEX idx_keurer_folder ON keurer_decisions(folder)")
    conn.execute("CREATE INDEX idx_keurer_date ON keurer_decisions(decided_at)")

    new = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = 'keurer_decisions'"
    ).fetchone()
    if new is None:
        if old_seq:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('keurer_decisions', ?)", (old_seq,))
    elif new[0] < old_seq:
        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'keurer_decisions'", (old_seq,))


# (version, name, function) - append only, never renumber or edit an applied migration
MIGRATIONS: List[Tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "baseline", _m001_baseline),
    (2, "keurer_undo_paths", _m002_keurer_undo_paths),
    (3, "keurer_dest_fingerprint", _m003_keurer_dest_fingerprint),
    (4, "keurer_keep_move_history", _m004_keurer_keep_move_history),
]

LATEST_VERSION = MIGRATIONS[-1][0]


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Highest applied migration, 0 for a database that predates migrations."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def _has_user_tables(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name NOT IN ('schema_version', 'sqlite_sequence')"
    ).fetchone()
    return row[0] > 0


def backup_database(conn: sqlite3.Connection, db_path: Path, from_version: int) -> Path:
    """Consistent copy via the SQLite backup API; keeps the newest BACKUPS_TO_KEEP."""
    backup_dir = db_path.parent / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"{db_path.stem}-v{from_version}-{stamp}.db"
    target = sqlite3.connect(str(backup_path))
    try:
        conn.backup(target)
    finally:
        target.close()

    backups = sorted(backup_dir.glob(f"{db_path.stem}-v*.db"))
    for old in backups[:-BACKUPS_TO_KEEP]:
        old.unlink()  # only our own backup copies, never archive files
    return backup_path


def migrate(db_path: Path) -> List[int]:
    """Bring the database at db_path up to LATEST_VERSION. Returns applied versions."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    applied: List[int] = []
    try:
        current = get_schema_version(conn)
        pending = [m for m in MIGRATIONS if m[0] > current]
        if not pending:
            return applied

        if _has_user_tables(conn):
            backup_database(conn, db_path, current)

        for version, name, fn in pending:
            conn.execute("BEGIN")
            try:
                fn(conn)
                conn.execute(
                    "INSERT INTO schema_version (version, name) VALUES (?, ?)",
                    (version, name),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            applied.append(version)
        return applied
    finally:
        conn.close()
