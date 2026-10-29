"""
DataGatherer - the card of a connector, as the DataGatherer tab shows it

Built only from the connector's metadata and its state, so the frontend needs no code per
source. A card says: up to which date the data goes, how many days behind that is, what
the coverage bar shows (hatched where listening time is estimated), the next step in one
sentence, ONE primary action and the other actions, the network label, the schedule and
any notes. The primary action follows the situation:

    reconnect        the consent expired (Spotify: every 6 months)        Opnieuw verbinden
    import           an export is in the archive but not read yet        Inlezen
    sync             the API is connected                                 Bijwerken
    find_downloads   an export was requested: look in Downloads now       In Downloads zoeken
    request_export   no data yet, or only exports (Meta, Takeout, ...)    Export aanvragen
    connect          an API source that is not connected yet             Verbinden
"""
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.datagatherer import keychain, launchd, oauth, raw_store, state
from backend.datagatherer.base import Connector, GatherContext

ACTIONS = {
    "reconnect": ("Opnieuw verbinden", "fa-plug-circle-exclamation"),
    "connect": ("Verbinden", "fa-plug"),
    "sync": ("Bijwerken", "fa-rotate"),
    "import": ("Inlezen", "fa-file-import"),
    "request_export": ("Export aanvragen", "fa-arrow-up-right-from-square"),
    "find_downloads": ("In Downloads zoeken", "fa-magnifying-glass"),
    "upload": ("Bestand kiezen", "fa-file-arrow-up"),
}
STATUS = {
    "opnieuw_verbinden": ("Opnieuw verbinden", "danger"),
    "fout": ("Bijwerken mislukt", "danger"),
    "export_binnen": ("Klaar om in te lezen", "info"),
    "wacht_op_export": ("Wacht op export", "info"),
    "gat": ("Gat in de gegevens", "warning"),
    "actueel": ("Actueel", "success"),
    "achter": ("Achter", "warning"),
    "geregistreerd": ("In het archief", "neutral"),
    "niet_ingesteld": ("Niet ingesteld", "neutral"),
}
KEYCHAIN_NOTE = ("De Sleutelhanger van macOS is niet bereikbaar (vergrendeld, of toegang geweigerd). Ontgrendel hem, "
                "of klik op 'Sta toe' als macOS het vraagt, en laad deze pagina opnieuw.")
DOWNLOADS_NOTICE_DAYS = 14
EXISTING_CACHE_SECONDS = 120     # finding existing exports walks data/: not on every card load

_existing_lock = threading.Lock()
_existing_cache: Dict[str, tuple] = {}


def _days(n: int) -> str:
    return "1 dag" if n == 1 else f"{n:,} dagen".replace(",", ".")


MONTHS = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec")


def _date(ts: Optional[float]) -> Optional[str]:
    """A date as the cards show it (nl-NL): '19 sep 2023'."""
    if not ts:
        return None
    day = datetime.fromtimestamp(ts)
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def _expected(lo: int, hi: int) -> str:
    """'meestal binnen 2 dagen' or 'meestal binnen 1 tot 5 dagen'."""
    return f"meestal binnen {_days(hi)}" if lo <= 0 or lo == hi else f"meestal binnen {lo} tot {_days(hi)}"


def unread_existing(connector: Connector, gctx: GatherContext) -> List[Path]:
    """Export folders already in the vault that are not registered yet (cached briefly: it walks)."""
    if not connector.has("import"):
        return []
    now = time.monotonic()
    with _existing_lock:
        hit = _existing_cache.get(connector.id)
        if hit and now - hit[0] < EXISTING_CACHE_SECONDS:
            found = hit[1]
        else:
            found = None
    if found is None:
        try:
            found = [folder for folder, _ in connector.find_existing(gctx)]
        except OSError:
            found = []
        with _existing_lock:
            _existing_cache[connector.id] = (now, found)
    return [f for f in found if _unread(connector, gctx, f)]


def _unread(connector: Connector, gctx: GatherContext, folder: Path) -> bool:
    """Not registered yet, or registered but its files changed since it was last read."""
    raw = state.raw_by_path(gctx.db, str(folder))
    if raw is None:
        return True
    if not raw["is_folder"]:
        return False
    try:
        fingerprint = raw_store.folder_fingerprint(folder, connector.folder_patterns)[0]
    except OSError:
        return False
    return fingerprint != raw["fingerprint"]


def forget_existing_cache() -> None:
    with _existing_lock:
        _existing_cache.clear()


def card(config, db, connector: Connector, daemon_tick: Optional[float] = None) -> Dict[str, Any]:
    gctx = GatherContext(config, db, connector)
    st = state.get_state(db, connector.id)
    now = time.time()
    cov = connector.coverage(gctx)
    api = connector.has("api_sync")
    keychain_ok = True
    try:
        configured = api and connector.is_configured(gctx)
    except keychain.SecretStoreError:
        # Locked, or the user clicked 'Weigeren' at the Keychain prompt: the tab still works
        configured, keychain_ok = False, False
    pending = state.pending_raw(db, connector.id)
    unread = unread_existing(connector, gctx)
    raws = state.list_raw(db, connector.id, limit=200)
    failed = next((r for r in raws if r["status"] == "failed"), None)
    requests = state.open_requests(db, connector.id)
    request = requests[-1] if requests else None
    gap_days = int((now - cov.last) // 86400) if cov.last else None
    hole = max(cov.holes, key=lambda h: h[1] - h[0]) if cov.holes else None
    hole_days = int((hole[1] - hole[0]) // 86400) if hole else None
    sync_failed = bool(st["last_error"]) and (not st["last_success_at"] or (st["last_sync_at"] or 0) > st["last_success_at"])

    # ---- status ----
    if api and st["needs_reconnect"]:
        status = "opnieuw_verbinden"
    elif sync_failed:
        status = "fout"
    elif pending or unread:
        status = "export_binnen"
    elif request:
        status = "wacht_op_export"
    elif not cov.records and not raws:
        status = "niet_ingesteld"
    elif not cov.records:
        status = "geregistreerd"
    elif hole:
        status = "gat"
    elif gap_days is not None and gap_days <= connector.stale_days:
        status = "actueel"
    else:
        status = "achter"
    status_label, tone = STATUS[status]
    if status == "achter" and gap_days is not None:
        status_label = f"{_days(gap_days)} achter"
    elif status == "gat":
        status_label = f"Gat van {_days(hole_days)}"

    # ---- the one primary action and the sentence ----
    name = connector.name
    request_kind = connector.export_kind(request["kind"]) if request else None
    if api and st["needs_reconnect"]:
        primary = "reconnect"
        step = f"{name} vraagt opnieuw om toestemming. Klik op Opnieuw verbinden en bevestig in je browser."
    elif pending or unread:
        primary = "import"
        what = "een export" if len(pending) + len(unread) == 1 else f"{len(pending) + len(unread)} exports"
        step = f"Er staat {what} in het archief die nog niet is ingelezen. Klik op Inlezen."
    elif configured:
        primary = "sync"
        hole_text = f"tussen {_date(hole[0])} en {_date(hole[1])}" if hole else ""
        if sync_failed:
            # The reason itself is on the card (last_error), right under this sentence
            step = "Bijwerken lukte de laatste keer niet. Probeer het opnieuw, of kijk bij Instellingen of alles klopt."
        elif hole and request:
            step = f"Je export is aangevraagd: die vult het gat {hole_text}. Bijwerken houdt je intussen bij."
        elif hole and connector.has("export_request"):
            primary = "request_export"
            step = (f"Er ontbreken gegevens {hole_text}: Bijwerken haalt alleen je nieuwste op. Vraag een nieuwe "
                    "export aan om het gat te vullen.")
        elif hole:
            step = f"Er ontbreken gegevens {hole_text}. Bijwerken haalt alleen je nieuwste op."
        elif status == "actueel":
            step = "Je gegevens zijn bij. Bijwerken haalt de nieuwste op."
        else:
            step = "Klik op Bijwerken om de nieuwste gegevens op te halen."
        if st["schedule_enabled"]:
            step += f" Het schema doet dat elke {st['schedule_minutes'] or connector.schedule_minutes} minuten."
    elif request and connector.has("export_watch"):
        primary = "find_downloads"
        days = int((now - request["requested_at"]) // 86400)
        lo, hi = request_kind.expected_days if request_kind else (1, 30)
        ago = "vandaag" if days == 0 else f"{_days(days)} geleden"
        step = (f"Export aangevraagd ({ago}); {_expected(lo, hi)} klaar. DataVault kijkt elke 30 seconden "
                f"in Downloads en leest hem dan vanzelf in.")
    elif not cov.records and not raws and connector.has("export_request"):
        primary = "request_export"
        step = f"Nog geen gegevens. Vraag een export aan: {name} maakt hem voor je klaar, DataVault leest hem in."
    elif api and sync_failed:
        primary = "connect"
        step = "Bijwerken lukte de laatste keer niet. Controleer de instellingen en verbind opnieuw."
    elif api:
        primary = "connect"
        if hole:
            step = (f"Er ontbreken gegevens tussen {_date(hole[0])} en {_date(hole[1])}: een nieuwe export vult dat aan. "
                    f"Verbind {name} om daarna bij te blijven.")
        else:
            step = (f"Verbind {name} om bij te blijven tot gisteren." if cov.records
                    else f"Verbind {name} om je gegevens op te halen.")
    elif connector.has("export_request"):
        primary = "request_export"
        last = _date(cov.last) or _date(max((r["received_at"] for r in raws), default=None))
        step = (f"Je laatste gegevens zijn van {last}. Vraag een nieuwe export aan om bij te werken." if last
                else "Vraag een export aan om bij te werken.")
    else:
        primary = "upload"
        step = "Kies een exportbestand om in te lezen."
    if failed and status != "export_binnen" and primary != "import":
        step += f" Een export kon niet gelezen worden: {failed['error']}"

    actions = []
    if api:
        actions.append("sync" if configured else None)
        actions.append("reconnect" if configured or st["needs_reconnect"] else "connect")
    if connector.has("export_request"):
        actions.append("request_export")
    if connector.has("export_watch"):
        actions.append("find_downloads")
    if connector.has("import"):
        actions.append("import")
        actions.append("upload")
    actions = [a for a in dict.fromkeys(actions) if a and a != primary and not (primary == "reconnect" and a == "connect")]

    try:
        notes = list(connector.notes(gctx))
    except keychain.SecretStoreError:
        notes, keychain_ok = [], False
    if not keychain_ok:
        notes.insert(0, {"tone": "warning", "text": KEYCHAIN_NOTE})
    notice = downloads_notice(raws, now)
    if notice:
        notes.append({"tone": "info", "text": f"{notice['name']} staat in het archief. Het origineel in Downloads mag je "
                                              "weggooien: DataVault heeft het niet meer nodig.", "path": notice["path"]})

    last_contact = state.last_contact(db, connector.id) if api else None
    return {
        "id": connector.id,
        "name": name,
        "icon": connector.icon,
        "category": connector.category,
        "description": connector.description,
        "capabilities": sorted(connector.capabilities),
        "status": status,
        "status_label": status_label,
        "tone": tone,
        "data_from": cov.first,
        "data_until": cov.last,
        "gap_days": gap_days,
        "stale_days": connector.stale_days,
        "records": cov.records,
        "record_label": list(connector.record_label),
        "coverage": {
            "first": cov.first, "last": cov.last, "estimated_since": cov.estimated_since,
            "segments": [{"from": a, "until": b, "estimated": e} for a, b, e in cov.segments],
            "holes": [{"from": a, "until": b} for a, b in cov.holes],
        },
        "next_step": step,
        "primary": {"action": primary, "label": ACTIONS[primary][0], "icon": ACTIONS[primary][1]},
        "actions": [{"action": a, "label": ACTIONS[a][0], "icon": ACTIONS[a][1]} for a in actions],
        "exports": [{"id": k.id, "label": k.label} for k in connector.exports],
        "configured": configured,
        "keychain_available": keychain_ok,
        "connected_at": st["connected_at"],
        "needs_reconnect": bool(st["needs_reconnect"]),
        "last_sync_at": st["last_sync_at"],
        "last_success_at": st["last_success_at"],
        "last_error": st["last_error"] if sync_failed else None,
        "network": {
            "label": connector.network_label,
            "local": not api,
            "hosts": list(connector.allowed_hosts),
            "last_contact": last_contact,
        },
        "schedule": {
            "available": api,
            "enabled": bool(st["schedule_enabled"]),
            "minutes": st["schedule_minutes"] or connector.schedule_minutes,
            "launchd": connector.launchd,
        },
        "open_request": _request_view(connector, request) if request else None,
        "raw": {"count": len(raws), "pending": len(pending) + len(unread),
                "failed": failed["error"] if failed else None},
        "notes": notes,
        "job_types": {
            "sync": f"datagatherer.sync.{connector.id}" if api or connector.has("local_import") else None,
            "import": f"datagatherer.import.{connector.id}" if connector.has("import") else None,
        },
    }


def _request_view(connector: Connector, request: Dict[str, Any]) -> Dict[str, Any]:
    kind = connector.export_kind(request["kind"])
    return {"id": request["id"], "kind": request["kind"], "label": kind.label if kind else request["kind"],
            "requested_at": request["requested_at"], "since": request["since"],
            "expected_days": list(kind.expected_days) if kind else None}


def downloads_notice(raws: List[Dict[str, Any]], now: float) -> Optional[Dict[str, str]]:
    """The newest export copied from Downloads whose original is still there (shown for two weeks)."""
    for raw in raws:
        if raw["origin"] != "downloads" or not raw["original_path"]:
            continue
        if now - raw["received_at"] > DOWNLOADS_NOTICE_DAYS * 86400:
            return None
        original = Path(raw["original_path"])
        try:
            if original.is_file() and original.stat().st_size == raw["size"]:
                return {"name": original.name, "path": str(original)}
        except OSError:
            pass
        return None
    return None


def since_for(connector: Connector, gctx: GatherContext) -> Optional[str]:
    """The 'since' date for an incremental export request: the data's last day, minus one day of overlap."""
    cov = connector.coverage(gctx)
    if not cov.last:
        return None
    return (datetime.fromtimestamp(cov.last) - timedelta(days=1)).strftime("%Y-%m-%d")


def detail(config, db, connector: Connector, port: Optional[int]) -> Dict[str, Any]:
    """The card plus what the settings, help and connect windows need."""
    gctx = GatherContext(config, db, connector)
    data = card(config, db, connector)
    redirect = oauth.redirect_uri(port, connector.id)
    st = state.get_state(db, connector.id)
    try:
        secrets = {f.key: gctx.secrets.has(f.key) for f in connector.settings if f.kind == "secret"}
        keychain_ok = True
    except keychain.SecretStoreError:
        secrets, keychain_ok = {}, False
    data.update({
        "help": [{"title": h.title, "effort": h.effort, "steps": list(h.steps), "note": h.note} for h in connector.help],
        "settings": [{
            "key": f.key, "label": f.label, "kind": f.kind, "help": f.help, "required": f.required,
            "placeholder": f.placeholder,
            "value": None if f.kind == "secret" else st["settings"].get(f.key),
            "stored": secrets.get(f.key, False) if f.kind == "secret" else None,
        } for f in connector.settings],
        "keychain_available": keychain_ok and data["keychain_available"],
        "setup": None if connector.setup is None else {
            "intro": connector.setup.intro,
            "steps": [s.replace("{redirect_uri}", redirect) for s in connector.setup.steps],
            "fields": list(connector.setup.fields),
            "note": connector.setup.note,
            "oauth": connector.setup.oauth,
            "redirect_uri": redirect if connector.setup.oauth else None,
            "links": [{"label": label, "url": url} for label, url in connector.setup.links],
        },
        "export_kinds": [{"id": k.id, "label": k.label, "url": k.request_url, "steps": list(k.steps),
                          "expected_days": list(k.expected_days)} for k in connector.exports],
        "raw_imports": [{k: r[k] for k in ("id", "kind", "path", "is_folder", "origin", "received_at", "status",
                                            "imported_at", "records_added", "records_seen", "error")}
                        for r in state.list_raw(db, connector.id, limit=20)],
        "network_log": state.network_log(db, connector.id, limit=20),
        "owner_folder": str(gctx.owner_folder) if gctx.owner_folder else None,
        "launchd": launchd.status() if connector.launchd else None,
    })
    return data
