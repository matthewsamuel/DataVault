"""
Back-ups: how long ago each source was last saved, and the user's own list of sources.

Two kinds of rows, one overview (Home and DataGatherer > Back-ups):
- a DataGatherer source with data or an open request: saved at the last successful update
  or the last export that came in (dg_raw_imports.received_at), whichever is later;
- an own source (dg_own_sources): something DataGatherer cannot reach, such as an old iPad,
  a phone in a drawer or a USB stick. The user writes down where the back-up is, when it was
  made and how far it is: 'te_doen', 'bezig' or 'veilig'.

health: 'goed' (saved within GOOD_DAYS, or an old device that is fully in a back-up), 'let_op'
(within OLD_DAYS, or a back-up that is not finished), 'oud' (longer ago), 'nooit' (no back-up
yet). A list row is the user's own note, not an archive file: removing one removes the note.
"""
import time
from typing import Any, Dict, List, Optional

from backend.datagatherer import cards, registry

GOOD_DAYS = 31
OLD_DAYS = 183
DAY = 86400.0

KINDS = {
    "toestel": ("Toestel", "fa-mobile-screen"),
    "computer": ("Computer", "fa-laptop"),
    "schijf": ("Schijf of stick", "fa-hard-drive"),
    "account": ("Account", "fa-user"),
    "anders": ("Anders", "fa-box-archive"),
}
STATUSES = {"te_doen": "Nog te doen", "bezig": "Bezig", "veilig": "Veiliggesteld"}
COLUMNS = ("id", "name", "kind", "location", "note", "status", "in_use", "backup_at", "position",
           "created_at", "updated_at")
MAX_TEXT = {"name": 80, "location": 300, "note": 1000}


class OwnSourceError(ValueError):
    pass


def _age_health(at: Optional[float], now: float) -> str:
    if not at:
        return "nooit"
    days = (now - at) / DAY
    return "goed" if days <= GOOD_DAYS else "let_op" if days <= OLD_DAYS else "oud"


def own_health(row: Dict[str, Any], now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    if row["status"] == "te_doen":
        return "nooit"
    if row["in_use"]:
        health = _age_health(row["backup_at"], now)
        return "let_op" if row["status"] == "bezig" and health == "goed" else health
    return "goed" if row["status"] == "veilig" else "let_op"


# ---------- the own list ----------

def _row(r) -> Dict[str, Any]:
    row = dict(zip(COLUMNS, r))
    row["in_use"] = bool(row["in_use"])
    label, icon = KINDS.get(row["kind"], KINDS["anders"])
    row.update(kind_label=label, icon=f"fas {icon}", status_label=STATUSES.get(row["status"], row["status"]),
               health=own_health(row))
    return row


def list_own(db) -> List[Dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM dg_own_sources ORDER BY position, id").fetchall()
    return [_row(r) for r in rows]


def get_own(db, source_id: int) -> Optional[Dict[str, Any]]:
    with db._connect() as conn:
        r = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM dg_own_sources WHERE id = ?", (source_id,)).fetchone()
    return _row(r) if r else None


def _clean(values: Dict[str, Any], partial: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("name", "location", "note"):
        if key not in values:
            continue
        value = values[key]
        if value is None and key != "name":
            out[key] = None
            continue
        if not isinstance(value, str) or len(value.strip()) > MAX_TEXT[key]:
            raise OwnSourceError(f"{key} moet tekst zijn (hoogstens {MAX_TEXT[key]} tekens)")
        out[key] = value.strip() or None
    if "name" in out and not out["name"]:
        raise OwnSourceError("Geef de bron een naam")
    if not partial and "name" not in out:
        raise OwnSourceError("Geef de bron een naam")
    if "kind" in values:
        if values["kind"] not in KINDS:
            raise OwnSourceError("Onbekende soort")
        out["kind"] = values["kind"]
    if "status" in values:
        if values["status"] not in STATUSES:
            raise OwnSourceError("Onbekende stand")
        out["status"] = values["status"]
    if "in_use" in values:
        if not isinstance(values["in_use"], bool):
            raise OwnSourceError("in_use moet waar of onwaar zijn")
        out["in_use"] = int(values["in_use"])
    if "backup_at" in values:
        at = values["backup_at"]
        if at is not None and (isinstance(at, bool) or not isinstance(at, (int, float))
                               or not 0 < at < time.time() + DAY):
            raise OwnSourceError("De datum van de back-up klopt niet")
        out["backup_at"] = float(at) if at is not None else None
    return out


def add_own(db, values: Dict[str, Any]) -> Dict[str, Any]:
    clean = _clean(values, partial=False)
    clean.setdefault("kind", "toestel")
    clean.setdefault("status", "veilig" if clean.get("backup_at") else "te_doen")
    now = time.time()
    with db._connect() as conn:
        position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM dg_own_sources").fetchone()[0]
        cur = conn.execute(
            "INSERT INTO dg_own_sources (name, kind, location, note, status, in_use, backup_at, position, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (clean["name"], clean["kind"], clean.get("location"), clean.get("note"), clean["status"],
             clean.get("in_use", 0), clean.get("backup_at"), position, now, now))
        new_id = cur.lastrowid
    return get_own(db, new_id)


def update_own(db, source_id: int, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    clean = _clean(values, partial=True)
    if clean:
        sets = ", ".join(f"{k} = ?" for k in clean)
        with db._connect() as conn:
            conn.execute(f"UPDATE dg_own_sources SET {sets}, updated_at = ? WHERE id = ?",
                         [*clean.values(), time.time(), source_id])
    return get_own(db, source_id)


def remove_own(db, source_id: int) -> Optional[Dict[str, Any]]:
    row = get_own(db, source_id)
    if row is not None:
        with db._connect() as conn:
            conn.execute("DELETE FROM dg_own_sources WHERE id = ?", (source_id,))
    return row


def restore_own(db, row: Dict[str, Any]) -> Dict[str, Any]:
    """Put a removed row back (the undo of remove_own), with its own id when that is free."""
    with db._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO dg_own_sources (id, name, kind, location, note, status, in_use, backup_at, "
            "position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(int(row[c]) if c == "in_use" else row[c] for c in COLUMNS))
    return get_own(db, row["id"])


# ---------- the overview ----------

def _last_received(db) -> Dict[str, float]:
    with db._connect() as conn:
        rows = conn.execute("SELECT source_id, MAX(received_at) FROM dg_raw_imports "
                            "WHERE status != 'failed' GROUP BY source_id").fetchall()
    return {source: at for source, at in rows if at}


def overview(config, db, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    received = _last_received(db)
    items: List[Dict[str, Any]] = []
    for connector in registry.connectors():
        card = cards.card(config, db, connector)
        request = card.get("open_request")
        last = max([t for t in (card.get("last_success_at"), received.get(connector.id)) if t], default=None)
        if not (card.get("records") or last or request):
            continue
        items.append({
            "key": f"bron:{connector.id}", "origin": "bron", "id": connector.id, "name": card["name"],
            "icon": card["icon"], "last_at": last, "requested_at": request["requested_at"] if request else None,
            "health": _age_health(last, now),
        })
    for row in list_own(db):
        items.append({
            "key": f"eigen:{row['id']}", "origin": "eigen", "id": row["id"], "name": row["name"],
            "icon": row["icon"], "last_at": row["backup_at"], "requested_at": None,
            "health": own_health(row, now), "status": row["status"], "in_use": row["in_use"],
        })
    order = {"oud": 0, "nooit": 1, "let_op": 2, "goed": 3}
    items.sort(key=lambda i: (order[i["health"]], i["last_at"] or 0, i["name"].lower()))
    own = [i for i in items if i["origin"] == "eigen"]
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "good": sum(i["health"] == "goed" for i in items),
            "requested": sum(bool(i["requested_at"]) for i in items),
            "own_total": len(own),
            "own_safe": sum(i["status"] == "veilig" for i in own),
            "own_busy": sum(i["status"] == "bezig" for i in own),
        },
        "good_days": GOOD_DAYS,
        "old_days": OLD_DAYS,
    }
