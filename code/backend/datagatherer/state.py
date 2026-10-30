"""
DataGatherer - the dg_* tables (migration 60)

Small functions around the connector state, raw imports, cursors, export requests and the
network log. Everything goes through Database._connect() (one connection per thread,
commit on success), like the rest of the app. Nothing here is a secret: those are in the
Keychain (keychain.py).
"""
import json
import time
from typing import Any, Dict, List, Optional

STATE_FIELDS = ("schedule_enabled", "schedule_minutes", "connected_at", "needs_reconnect", "status_note",
                "retry_after", "last_sync_at", "last_success_at", "last_error")
NETWORK_LOG_KEEP = 20000   # the log is the app's own record, not user data: the newest rows are kept


def _ensure(conn, source_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO dg_connectors (source_id, updated_at) VALUES (?, ?)", (source_id, time.time()))


def get_state(db, source_id: str) -> Dict[str, Any]:
    with db._connect() as conn:
        cur = conn.execute("SELECT * FROM dg_connectors WHERE source_id = ?", (source_id,))
        row = cur.fetchone()
        names = [d[0] for d in cur.description]
    if row is None:
        return {"source_id": source_id, "settings": {}, "schedule_enabled": 0, "schedule_minutes": None,
                "connected_at": None, "needs_reconnect": 0, "status_note": None, "retry_after": None,
                "last_sync_at": None, "last_success_at": None, "last_error": None, "data_version": 0}
    state = dict(zip(names, tuple(row)))
    try:
        state["settings"] = json.loads(state.get("settings") or "{}")
    except ValueError:
        state["settings"] = {}
    return state


def get_settings(db, source_id: str) -> Dict[str, Any]:
    return get_state(db, source_id)["settings"]


def set_settings(db, source_id: str, settings: Dict[str, Any]) -> None:
    with db._connect() as conn:
        _ensure(conn, source_id)
        conn.execute("UPDATE dg_connectors SET settings = ?, updated_at = ? WHERE source_id = ?",
                     (json.dumps(settings, ensure_ascii=False), time.time(), source_id))


def update(db, source_id: str, **fields) -> None:
    unknown = set(fields) - set(STATE_FIELDS)
    if unknown:
        raise ValueError(f"unknown state fields {sorted(unknown)}")
    if not fields:
        return
    with db._connect() as conn:
        _ensure(conn, source_id)
        sets = ", ".join(f"{name} = ?" for name in fields)
        conn.execute(f"UPDATE dg_connectors SET {sets}, updated_at = ? WHERE source_id = ?",
                     (*fields.values(), time.time(), source_id))


def bump_version(conn, source_id: str) -> None:
    """Inside a write transaction: the data of this source changed (cache key for dashboards)."""
    _ensure(conn, source_id)
    conn.execute("UPDATE dg_connectors SET data_version = data_version + 1 WHERE source_id = ?", (source_id,))


def versions(db, source_ids) -> tuple:
    ids = list(source_ids)
    with db._connect() as conn:
        rows = dict(conn.execute(
            f"SELECT source_id, data_version FROM dg_connectors WHERE source_id IN ({','.join('?' * len(ids))})",
            ids).fetchall())
    return tuple(rows.get(i, 0) for i in ids)


def scheduled(db) -> List[Dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute("SELECT source_id, schedule_minutes, last_sync_at, retry_after FROM dg_connectors "
                            "WHERE schedule_enabled = 1").fetchall()
    return [dict(zip(("source_id", "schedule_minutes", "last_sync_at", "retry_after"), r)) for r in rows]


def schedule_enabled(db, source_id: str) -> bool:
    with db._connect() as conn:
        row = conn.execute("SELECT schedule_enabled FROM dg_connectors WHERE source_id = ?", (source_id,)).fetchone()
    return bool(row and row[0])


# ---------- cursors ----------

def get_cursor(db, source_id: str, stream: str) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        row = conn.execute("SELECT cursor FROM dg_cursors WHERE source_id = ? AND stream = ?",
                           (source_id, stream)).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def set_cursor(conn, source_id: str, stream: str, cursor: Dict[str, Any]) -> None:
    """Inside the transaction that stored the records it describes: it never runs ahead of them."""
    conn.execute("INSERT OR REPLACE INTO dg_cursors (source_id, stream, cursor, updated_at) VALUES (?, ?, ?, ?)",
                 (source_id, stream, json.dumps(cursor), time.time()))


# ---------- raw imports ----------

RAW_COLUMNS = ("id", "source_id", "kind", "path", "is_folder", "sha256", "fingerprint", "size", "origin",
               "original_path", "received_at", "status", "imported_at", "parser_version", "records_added",
               "records_seen", "covers_from", "covers_until", "stats", "error")


def _raw(row) -> Dict[str, Any]:
    raw = dict(zip(RAW_COLUMNS, row))
    raw["is_folder"] = bool(raw["is_folder"])
    try:
        raw["stats"] = json.loads(raw["stats"]) if raw["stats"] else None
    except ValueError:
        raw["stats"] = None
    return raw


def _select_raw(conn, where: str, args=()) -> List[Dict[str, Any]]:
    return [_raw(r) for r in conn.execute(f"SELECT {', '.join(RAW_COLUMNS)} FROM dg_raw_imports {where}", args)]


def get_raw(db, raw_id: int) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        rows = _select_raw(conn, "WHERE id = ?", (raw_id,))
    return rows[0] if rows else None


def raw_by_sha(db, sha256: str) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        rows = _select_raw(conn, "WHERE sha256 = ?", (sha256,))
    return rows[0] if rows else None


def raw_by_path(db, path: str) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        rows = _select_raw(conn, "WHERE path = ?", (path,))
    return rows[0] if rows else None


def list_raw(db, source_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    with db._connect() as conn:
        return _select_raw(conn, "WHERE source_id = ? ORDER BY id DESC LIMIT ?", (source_id, limit))


def pending_raw(db, source_id: str) -> List[Dict[str, Any]]:
    """Raw exports whose records are not read yet (or whose reading was interrupted)."""
    with db._connect() as conn:
        return _select_raw(conn, "WHERE source_id = ? AND status IN ('pending', 'importing') ORDER BY id",
                           (source_id,))


def insert_raw(db, **values) -> int:
    values.setdefault("received_at", time.time())
    values.setdefault("status", "pending")
    names = list(values)
    with db._connect() as conn:
        cursor = conn.execute(f"INSERT INTO dg_raw_imports ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                              [values[n] for n in names])
        return cursor.lastrowid


def update_raw(db, raw_id: int, **values) -> None:
    if "stats" in values and not isinstance(values["stats"], (str, type(None))):
        values["stats"] = json.dumps(values["stats"], ensure_ascii=False)
    with db._connect() as conn:
        conn.execute(f"UPDATE dg_raw_imports SET {', '.join(f'{n} = ?' for n in values)} WHERE id = ?",
                     (*values.values(), raw_id))


def claim_raw(db, raw_id: int) -> bool:
    """Mark a raw export as being read; False when another job already took it or it is done."""
    with db._connect() as conn:
        cur = conn.execute("UPDATE dg_raw_imports SET status = 'importing', error = NULL "
                           "WHERE id = ? AND status IN ('pending', 'importing', 'failed')", (raw_id,))
        return cur.rowcount == 1


# ---------- export requests ----------

REQUEST_COLUMNS = ("id", "source_id", "kind", "requested_at", "since", "status", "raw_import_id", "note", "updated_at")


def open_request(db, source_id: str, kind: str, since: Optional[str]) -> int:
    """A new open request (an older open one of the same kind is replaced: it is the same wait)."""
    now = time.time()
    with db._connect() as conn:
        conn.execute("UPDATE dg_requests SET status = 'replaced', updated_at = ? "
                     "WHERE source_id = ? AND kind = ? AND status = 'open'", (now, source_id, kind))
        cur = conn.execute("INSERT INTO dg_requests (source_id, kind, requested_at, since, status, updated_at) "
                           "VALUES (?, ?, ?, ?, 'open', ?)", (source_id, kind, now, since, now))
        return cur.lastrowid


def open_requests(db, source_id: Optional[str] = None) -> List[Dict[str, Any]]:
    where, args = ("WHERE status = 'open'", ()) if source_id is None else \
        ("WHERE status = 'open' AND source_id = ?", (source_id,))
    with db._connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(REQUEST_COLUMNS)} FROM dg_requests {where} ORDER BY id", args).fetchall()
    return [dict(zip(REQUEST_COLUMNS, r)) for r in rows]


def any_open_request(db) -> bool:
    with db._connect() as conn:
        return conn.execute("SELECT 1 FROM dg_requests WHERE status = 'open' LIMIT 1").fetchone() is not None


def get_request(db, request_id: int) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        row = conn.execute(f"SELECT {', '.join(REQUEST_COLUMNS)} FROM dg_requests WHERE id = ?", (request_id,)).fetchone()
    return dict(zip(REQUEST_COLUMNS, row)) if row else None


def close_requests(db, source_id: str, status: str, raw_import_id: Optional[int] = None,
                   request_id: Optional[int] = None, note: Optional[str] = None) -> int:
    """Close the open request(s) of a source: 'arrived' (the file is in), or 'dismissed'."""
    where, args = ("id = ?", [request_id]) if request_id is not None else ("source_id = ?", [source_id])
    with db._connect() as conn:
        cur = conn.execute(f"UPDATE dg_requests SET status = ?, raw_import_id = COALESCE(?, raw_import_id), "
                           f"note = COALESCE(?, note), updated_at = ? WHERE status = 'open' AND {where}",
                           [status, raw_import_id, note, time.time(), *args])
        return cur.rowcount


def last_request(db, source_id: str) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        row = conn.execute(f"SELECT {', '.join(REQUEST_COLUMNS)} FROM dg_requests WHERE source_id = ? "
                           f"AND status != 'replaced' ORDER BY id DESC LIMIT 1", (source_id,)).fetchone()
    return dict(zip(REQUEST_COLUMNS, row)) if row else None


# ---------- network log ----------

# Network log rows that do not come from a connector: the source id and its name in the Netwerklog
SEARCH_MODEL_SOURCE = "slim_zoeken"
OTHER_SOURCES = {SEARCH_MODEL_SOURCE: "Slim zoeken (zoekmodel)"}

LOG_COLUMNS = ("id", "at", "source_id", "trigger", "method", "host", "path", "status", "outcome", "bytes", "ms",
               "job_id", "note")


def log_request(db, **values) -> int:
    """Write one dg_network_log row; returns its id (finish_request fills in a 'pending' row)."""
    values.setdefault("at", time.time())
    names = list(values)
    with db._connect() as conn:
        cur = conn.execute(f"INSERT INTO dg_network_log ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                           [values[n] for n in names])
        if cur.lastrowid and cur.lastrowid % 500 == 0:
            conn.execute("DELETE FROM dg_network_log WHERE id <= ?", (cur.lastrowid - NETWORK_LOG_KEEP,))
        return cur.lastrowid


FINISH_COLUMNS = ("status", "outcome", "bytes", "ms", "note")


def finish_request(db, row_id: int, **values) -> None:
    """The outcome of a request whose 'pending' row log_request wrote before it went out."""
    names = [n for n in values if n in FINISH_COLUMNS]
    if not names:
        return
    with db._connect() as conn:
        conn.execute(f"UPDATE dg_network_log SET {', '.join(f'{n} = ?' for n in names)} WHERE id = ?",
                     [values[n] for n in names] + [row_id])


def network_log(db, source_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    where, args = ("", ()) if source_id is None else ("WHERE source_id = ?", (source_id,))
    with db._connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(LOG_COLUMNS)} FROM dg_network_log {where} ORDER BY id DESC LIMIT ?",
                            (*args, limit)).fetchall()
    return [dict(zip(LOG_COLUMNS, r)) for r in rows]


def last_contact(db, source_id: str) -> Optional[float]:
    with db._connect() as conn:
        row = conn.execute("SELECT MAX(at) FROM dg_network_log WHERE source_id = ? AND outcome = 'ok'",
                           (source_id,)).fetchone()
    return row[0] if row else None
