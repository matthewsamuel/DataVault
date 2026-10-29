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


def _m005_keurer_keep_delete_history(conn: sqlite3.Connection):
    """
    Migration 4 still let a new 'delete' on a path replace an earlier 'delete'
    row (e.g. a new file appeared at the same path and was trashed too), and with
    it the only record of where the first file went in TRASH. From now on only
    'keep' rows are unique per path; delete and move rows always stay.
    Only the index changes: no row is touched.
    """
    conn.execute("DROP INDEX IF EXISTS idx_keurer_path_folder")
    conn.execute(
        "CREATE UNIQUE INDEX idx_keurer_keep_path_folder ON keurer_decisions(path, folder) "
        "WHERE decision = 'keep'"
    )


def _m006_hash_cache_algo_and_fingerprint(conn: sqlite3.Connection):
    """
    The hash cache once held SHA-256 and xxHash3 values side by side in one
    column called 'sha256', with nothing saying which was which, so a cached
    SHA-256 made an identical file look different (REPO-HYGIENE-9). The column
    becomes 'hash' and gets an 'algo' tag; existing rows keep algo NULL, which
    never matches a lookup, so they are simply rehashed the next time.
    A cached hash is now only trusted while the file's inode, size, mtime_ns and
    ctime_ns are unchanged (ctime cannot be set back with utime, so an in-place
    edit that restores the mtime is still noticed). The cache holds no user data.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(file_hashes)")}
    if "sha256" in columns and "hash" not in columns:
        conn.execute("ALTER TABLE file_hashes RENAME COLUMN sha256 TO hash")
    for name, kind in (("algo", "TEXT"), ("ino", "INTEGER"), ("mtime_ns", "INTEGER"), ("ctime_ns", "INTEGER")):
        if name not in columns:
            conn.execute(f"ALTER TABLE file_hashes ADD COLUMN {name} {kind}")


def _m007_delete_runs_sequence(conn: sqlite3.Connection):
    """
    Duplicate undo runs were ordered by a local-time text timestamp, so after
    the switch to winter time (or a clock change) 'newest' could be the wrong
    run (GAP-5). Rebuild delete_runs with an AUTOINCREMENT 'seq' that only ever
    goes up. Existing runs get their rowid, which is their insertion order (a new
    rowid is always above every existing one). Also new: created_at (UTC epoch
    seconds, for display; existing rows converted from their local timestamp),
    folder (which folder was being cleaned up) and an index on
    delete_operations(run_id). Every run and operation is kept as it is.
    """
    count_before = conn.execute("SELECT COUNT(*) FROM delete_runs").fetchone()[0]
    conn.execute("""
        CREATE TABLE delete_runs_new (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_files INTEGER DEFAULT 0,
            created_at REAL,
            folder TEXT
        )
    """)
    conn.execute("""
        INSERT INTO delete_runs_new (seq, run_id, timestamp, total_files, created_at)
        SELECT rowid, run_id, timestamp, total_files,
               CAST(strftime('%s', timestamp, 'utc') AS REAL)
        FROM delete_runs ORDER BY rowid
    """)
    count_after = conn.execute("SELECT COUNT(*) FROM delete_runs_new").fetchone()[0]
    if count_after != count_before:
        raise RuntimeError(f"delete_runs copy incomplete: {count_after} of {count_before} rows")
    conn.execute("DROP TABLE delete_runs")
    conn.execute("ALTER TABLE delete_runs_new RENAME TO delete_runs")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_delops_run ON delete_operations(run_id)")


def _m008_background_jobs(conn: sqlite3.Connection):
    """
    Background jobs (backend/jobs.py): duplicate scans, hashing and thumbnails run
    in a worker thread and survive a reload or restart of the page. One row per
    job with its progress and a checkpoint to resume from; the (possibly large)
    result lives in job_results, zlib-compressed JSON, so listing jobs stays cheap.
    Only new tables: no existing row is touched.
    (delete_operations(run_id) already got its index, idx_delops_run, in migration 7.)
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0,
            total INTEGER,
            message TEXT,
            error TEXT,
            checkpoint TEXT,
            summary TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(type, id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_results (
            job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
            encoding TEXT NOT NULL,
            data BLOB NOT NULL
        )
    """)


def _m009_media_dates(conn: sqlite3.Connection):
    """
    The capture date of photos and videos (backend/media_dates.py), so the timeline,
    'on this day' and photos-by-date read EXIF or a video's own date once per file
    instead of on every request. One row per path with the size and mtime_ns it was
    read at: a changed file is read again. taken is the date used (EXIF or video date,
    else the file's birthtime, else its mtime), source says which. Only a new table:
    no existing row is touched.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS media_dates (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            taken REAL NOT NULL,
            source TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_dates_taken ON media_dates(taken)")


def _m010_app_settings(conn: sqlite3.Connection):
    """
    Small app-wide settings chosen in the UI (for now the chat model), as JSON values
    by key. Only a new table: no existing row is touched.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)


def _m011_media_index(conn: sqlite3.Connection):
    """
    The media index (backend/media_index.py): one row per photo, video and audio file
    in the vault with what later features filter and sort on (capture date, size,
    dimensions, camera, GPS, duration), so they query SQLite instead of walking the
    disk. media_folders remembers the mtime of every folder at its last listing: an
    incremental pass only lists folders whose mtime changed.

    id is stable for as long as the file is known (a move, in the app or in Finder,
    keeps the row and its id), so later features (perceptual hashes, CLIP embeddings)
    can key their own tables on media.id. missing = 1 marks a file that was not there
    at the last listing of its folder; such rows are hidden and removed at the end of
    the pass, once a move elsewhere had its chance to claim them.

    media_dates (migration 9) stays as it is: the walks that run while the index is
    not there yet keep using it. Only new tables: no existing row is touched.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            folder TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            ext TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            inode INTEGER,
            birthtime REAL,
            taken REAL,
            taken_source TEXT,
            width INTEGER,
            height INTEGER,
            orientation INTEGER,
            camera_make TEXT,
            camera_model TEXT,
            lens TEXT,
            lat REAL,
            lon REAL,
            altitude REAL,
            duration REAL,
            meta_status TEXT,
            added_at REAL NOT NULL,
            indexed_at REAL NOT NULL,
            seen_at REAL NOT NULL,
            missing INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_taken ON media(taken)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_folder ON media(folder)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_latlon ON media(lat, lon) WHERE lat IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_camera ON media(camera_make, camera_model)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_inode ON media(inode)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_missing ON media(missing) WHERE missing = 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS media_folders (
            path TEXT PRIMARY KEY,
            parent TEXT,
            mtime_ns INTEGER NOT NULL,
            last_scanned REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_folders_parent ON media_folders(parent)")


def _m012_recovery_actions(conn: sqlite3.Connection):
    """
    Undo records for every newer action that moves archive files (backend/recovery.py):
    the move in the viewer now, later near-duplicate 'keep best' runs and assistant moves.
    One action is one user action, however many files it moved; recovery_moves holds
    where each file was (src_path) and where it went (dst_path), with the inode and size
    of the moved file so an undo never takes back a different file with the same name.
    The older Keurer decisions and duplicate runs keep their own tables; the recovery
    center reads those as they are. Only new tables: no existing row is touched.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recovery_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            folder TEXT,
            details TEXT,
            created_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recovery_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id INTEGER NOT NULL REFERENCES recovery_actions(id),
            src_path TEXT NOT NULL,
            dst_path TEXT NOT NULL,
            dst_ino INTEGER,
            dst_size INTEGER,
            moved_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_moves_action ON recovery_moves(action_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_actions_created ON recovery_actions(created_at)")


def _m020_smart_albums(conn: sqlite3.Connection):
    """
    Smart albums (backend/smart_albums.py): a saved filter over the media index, shown
    with its count in Albums. An album is a live query (filters is the JSON of
    backend/library_filters.py): it never copies, moves or changes a file.
    builtin names a default album (smart_albums.DEFAULTS, added once by the app, not
    here; the user can hide or delete them, and 'Standaardalbums terugzetten' adds them
    again). Only a new table: no existing row is touched.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS smart_albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filters TEXT NOT NULL,
            sort TEXT NOT NULL,
            icon TEXT,
            builtin TEXT UNIQUE,
            hidden INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)


def _m030_near_duplicates(conn: sqlite3.Connection):
    """
    Near-duplicates and bursts (backend/near_dups.py), keyed on media.id, which stays the
    same when a file is moved (in the app or in Finder).
    media_phash: per photo its perceptual hashes (dhash, phash: 64 bits, stored signed),
    the mean colours of its four quarters, its contrast, and the quality figures
    (sharpness, clipped shadows and highlights, brightness), with the algorithm and version they were made
    with and the size and mtime_ns of the file at that moment (a changed file is analysed
    again). paired = 0 marks a photo whose pairs still have to be looked up.
    near_dup_pairs: photos that look alike ('near') or were shot as a burst ('burst'),
    with their distances (flat: one of the two is nearly flat, so its colours must match
    closely), so a stricter or looser setting only filters these rows.
    near_dup_reviews: series the user decided on ('alles behouden', or what was kept),
    by the set of photos in it; a series that changes shows up again.
    Only new tables: no existing row is touched.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS media_phash (
            media_id INTEGER PRIMARY KEY,
            algo TEXT NOT NULL,
            version INTEGER NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            status TEXT NOT NULL,
            dhash INTEGER,
            phash INTEGER,
            colors BLOB,
            sharpness REAL,
            clip_low REAL,
            clip_high REAL,
            brightness REAL,
            contrast REAL,
            error TEXT,
            paired INTEGER NOT NULL DEFAULT 0,
            computed_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_phash_unpaired ON media_phash(paired) WHERE paired = 0")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS near_dup_pairs (
            a INTEGER NOT NULL,
            b INTEGER NOT NULL,
            kind TEXT NOT NULL,
            phash_d INTEGER NOT NULL,
            dhash_d INTEGER NOT NULL,
            color_d REAL NOT NULL,
            gap REAL,
            flat INTEGER NOT NULL DEFAULT 0,
            same_bytes INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (a, b, kind)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_near_dup_pairs_b ON near_dup_pairs(b)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS near_dup_reviews (
            key TEXT PRIMARY KEY,
            media_ids TEXT NOT NULL,
            decision TEXT NOT NULL,
            decided_at REAL NOT NULL
        )
    """)


def _m021_keurer_path_index(conn: sqlite3.Connection):
    """
    An index on keurer_decisions(path): the library filter 'Keurer: nog niet beoordeeld'
    asks per file whether any decision names its path. Only an index: no row is touched.
    """
    conn.execute("CREATE INDEX IF NOT EXISTS idx_keurer_path ON keurer_decisions(path)")


def _m031_near_duplicates_content(conn: sqlite3.Connection):
    """
    media_phash.content: the xxh3 hash of a photo that looks exactly like another photo of
    the same size (backend/near_dups.py): the ones with the same hash are byte-for-byte
    copies, shown as one photo. Photos that look exactly alike are now grouped when the
    series are made instead of being paired one by one (a folder of 1,500 copies of one
    shot gave a million pairs), so near_dup_pairs.same_bytes is no longer written.
    Only a new column: no existing row is touched.
    """
    conn.execute("ALTER TABLE media_phash ADD COLUMN content TEXT")


def _m060_datagatherer_core(conn: sqlite3.Connection):
    """
    DataGatherer (backend/datagatherer/): the state of every data source (connector), the
    raw exports it archived or registered, its cursors, export requests and a log of
    every network request it made.

    dg_connectors       one row per connector the user touched: its (non-secret) settings,
                        the opt-in schedule (off by default), when it was connected (OAuth,
                        for the 6-month expiry) and the outcome of the last sync. Tokens and
                        API keys are never here: they live in the macOS Keychain only.
    dg_raw_imports      every raw export: a file copied into data/<owner>/<Bron>/<datum soort>/
                        (sha256, never twice), or an existing folder registered in place
                        (is_folder, fingerprint of its names, sizes and mtimes). status says
                        whether its records are read (pending, importing, imported, failed).
    dg_cursors          how far an api_sync got per stream (JSON).
    dg_requests         an export the user asked for in their browser: while one is open, the
                        Downloads watcher looks for the file.
    dg_network_log      one row per HTTP request (or refused attempt), without query strings.

    Only new tables: no existing row is touched.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dg_connectors (
            source_id TEXT PRIMARY KEY,
            settings TEXT NOT NULL DEFAULT '{}',
            schedule_enabled INTEGER NOT NULL DEFAULT 0,
            schedule_minutes INTEGER,
            connected_at REAL,
            needs_reconnect INTEGER NOT NULL DEFAULT 0,
            status_note TEXT,
            retry_after REAL,
            last_sync_at REAL,
            last_success_at REAL,
            last_error TEXT,
            data_version INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dg_raw_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            is_folder INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT UNIQUE,
            fingerprint TEXT,
            size INTEGER,
            origin TEXT NOT NULL,
            original_path TEXT,
            received_at REAL NOT NULL,
            status TEXT NOT NULL,
            imported_at REAL,
            parser_version INTEGER,
            records_added INTEGER,
            records_seen INTEGER,
            covers_from REAL,
            covers_until REAL,
            stats TEXT,
            error TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dg_raw_source ON dg_raw_imports(source_id, status)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dg_cursors (
            source_id TEXT NOT NULL,
            stream TEXT NOT NULL,
            cursor TEXT,
            updated_at REAL NOT NULL,
            PRIMARY KEY (source_id, stream)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dg_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            requested_at REAL NOT NULL,
            since TEXT,
            status TEXT NOT NULL,
            raw_import_id INTEGER,
            note TEXT,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dg_requests_open ON dg_requests(status, source_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dg_network_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at REAL NOT NULL,
            source_id TEXT NOT NULL,
            trigger TEXT NOT NULL,
            method TEXT NOT NULL,
            host TEXT NOT NULL,
            path TEXT,
            status INTEGER,
            outcome TEXT NOT NULL,
            bytes INTEGER,
            ms INTEGER,
            job_id INTEGER,
            note TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dg_network_source ON dg_network_log(source_id, id)")


def _m061_spotify_plays(conn: sqlite3.Connection):
    """
    Every Spotify play DataGatherer knows, merged from several sources
    (backend/datagatherer/spotify_store.py), each with a stable key so importing the same
    export twice adds nothing:

    spotify_plays       plays from the privacy exports (source export_extended: Extended
                        Streaming History, export_account: Account data) and from Last.fm
                        scrobbles (source lastfm). ts_ms is when the play ended (UTC ms).
    spotify_api_plays   plays from the Web API 'recently played' endpoint, in a table of their
                        own: the developer terms treat API data as provisional; the exports
                        are the lasting copy.

    A play that a richer source also has is marked superseded (with what superseded it),
    never deleted. The view spotify_plays_merged is what the Spotify dashboard reads: every
    play that is not superseded, from both tables. Only new tables and a view.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spotify_plays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            play_key TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            ts_ms INTEGER NOT NULL,
            start_ms INTEGER,
            ms_played INTEGER,
            ms_estimated INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL,
            track TEXT,
            artist TEXT,
            album TEXT,
            track_uri TEXT,
            episode TEXT,
            show TEXT,
            episode_uri TEXT,
            platform TEXT,
            country TEXT,
            reason_start TEXT,
            reason_end TEXT,
            shuffle INTEGER,
            skipped INTEGER,
            offline INTEGER,
            incognito INTEGER,
            raw_import_id INTEGER,
            superseded INTEGER NOT NULL DEFAULT 0,
            superseded_by TEXT,
            first_seen_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spotify_plays_ts ON spotify_plays(ts_ms)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spotify_plays_source ON spotify_plays(source, ts_ms)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS spotify_api_plays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            play_key TEXT NOT NULL UNIQUE,
            ts_ms INTEGER NOT NULL,
            start_ms INTEGER,
            duration_ms INTEGER,
            track TEXT,
            artist TEXT,
            album TEXT,
            track_uri TEXT,
            context_uri TEXT,
            superseded INTEGER NOT NULL DEFAULT 0,
            superseded_by TEXT,
            fetched_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spotify_api_plays_ts ON spotify_api_plays(ts_ms)")
    conn.execute("""
        CREATE VIEW IF NOT EXISTS spotify_plays_merged AS
            SELECT 'p' || id AS row_key, source, ts_ms, start_ms, ms_played, ms_estimated, content,
                   track, artist, album, track_uri, platform
              FROM spotify_plays WHERE superseded = 0
            UNION ALL
            SELECT 'a' || id, 'api', ts_ms, start_ms, duration_ms, 1, 'track',
                   track, artist, album, track_uri, NULL
              FROM spotify_api_plays WHERE superseded = 0
    """)


def _m050_elo_comparisons(conn: sqlite3.Connection):
    """
    'Welke is mooier?' in Shows (backend/elo.py): two photos side by side, the user picks
    one, and both ratings (file_ratings, the same 1000-based scale as the up/down votes)
    move by the standard Elo rule. elo_comparisons keeps every comparison with both paths,
    the winner, both ratings and game counts before and after, so one can be undone
    exactly. How many comparisons a photo took part in (its K-factor drops after 30) is
    counted from these rows, so file_ratings itself does not change. existed_a/b say
    whether the photo had a file_ratings row before (an undo then removes the row the
    comparison made). Paths follow moves like ratings do (media_index._carry_user_data).
    Version 50: numbers 50-59 belong to this feature. Only a new table: no existing row
    is touched.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS elo_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path_a TEXT NOT NULL,
            path_b TEXT NOT NULL,
            winner TEXT NOT NULL CHECK (winner IN ('a', 'b')),
            rating_a_before INTEGER NOT NULL,
            rating_a_after INTEGER NOT NULL,
            rating_b_before INTEGER NOT NULL,
            rating_b_after INTEGER NOT NULL,
            games_a_before INTEGER NOT NULL,
            games_b_before INTEGER NOT NULL,
            existed_a INTEGER NOT NULL,
            existed_b INTEGER NOT NULL,
            k_a INTEGER NOT NULL,
            k_b INTEGER NOT NULL,
            scope TEXT,
            created_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elo_path_a ON elo_comparisons(path_a)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elo_path_b ON elo_comparisons(path_b)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_elo_scope ON elo_comparisons(scope, id)")


def _m040_semantic_embeddings(conn: sqlite3.Connection):
    """
    'Slim zoeken' (backend/semantic.py): one image embedding per photo or video per search
    model, keyed on media.id (stable across moves, so a moved photo keeps its embedding).
    size and mtime_ns are the file's when it was embedded: a file that changed since is
    embedded again. vector is the L2-normalised embedding as float16 (dim values), or
    NULL with status 'undecodable' for a file that could not be read as an image, so it
    is not tried again until it changes. Rows whose media row is gone are ignored by
    every query and removed by the next embedding run. Only a new table: no existing
    row is touched. (Version 40: the numbers below it are left to the steps built
    alongside this one; migrations run in list order.)
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS semantic_embeddings (
            media_id INTEGER NOT NULL,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB,
            status TEXT NOT NULL DEFAULT 'ok',
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            source TEXT,
            created_at REAL NOT NULL,
            PRIMARY KEY (media_id, model)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_semantic_model ON semantic_embeddings(model)")


# (version, name, function) - append only, never renumber or edit an applied migration
MIGRATIONS: List[Tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "baseline", _m001_baseline),
    (2, "keurer_undo_paths", _m002_keurer_undo_paths),
    (3, "keurer_dest_fingerprint", _m003_keurer_dest_fingerprint),
    (4, "keurer_keep_move_history", _m004_keurer_keep_move_history),
    (5, "keurer_keep_delete_history", _m005_keurer_keep_delete_history),
    (6, "hash_cache_algo_and_fingerprint", _m006_hash_cache_algo_and_fingerprint),
    (7, "delete_runs_sequence", _m007_delete_runs_sequence),
    (8, "background_jobs", _m008_background_jobs),
    (9, "media_dates", _m009_media_dates),
    (10, "app_settings", _m010_app_settings),
    (11, "media_index", _m011_media_index),
    (12, "recovery_actions", _m012_recovery_actions),
    (20, "smart_albums", _m020_smart_albums),
    (21, "keurer_path_index", _m021_keurer_path_index),
    (30, "near_duplicates", _m030_near_duplicates),
    (31, "near_duplicates_content", _m031_near_duplicates_content),
    # 40-49: Slim zoeken (step 3.5)
    (40, "semantic_embeddings", _m040_semantic_embeddings),
    # 50-59: Elo comparisons (step 3.9b)
    (50, "elo_comparisons", _m050_elo_comparisons),
    # 60-69: DataGatherer (step 3.9a); gaps in the numbering are fine, the list stays sorted
    (60, "datagatherer_core", _m060_datagatherer_core),
    (61, "spotify_plays", _m061_spotify_plays),
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
