"""
DataGatherer - the connector registry

Every module in backend/datagatherer/connectors/ that defines CONNECTOR is a data source.
load() imports them once (sorted by module name, so the order is stable), validates each
one and keeps them by id. A broken connector is refused loudly at start-up rather than
showing a half card: validate() lists everything that is wrong with it.

Extra connectors from $DATAVAULT_GATHER_EXTRA (comma-separated module names) are loaded
only when DATAVAULT_TESTING=1: tests and the browser smoke use them, a real app never does.
"""
import importlib
import os
import pkgutil
import re
import threading
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from backend.datagatherer.base import CAPABILITIES, CATEGORIES, EFFORTS, SETTING_KINDS, Connector

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
ICON_RE = re.compile(r"^fa[bs] fa-[a-z0-9-]+$")
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

_lock = threading.Lock()
_connectors: Optional[Dict[str, Connector]] = None


class InvalidConnector(Exception):
    pass


def validate(connector: Connector) -> List[str]:
    """Everything wrong with a connector's metadata, in plain words (empty when it is fine)."""
    problems = []
    cid = getattr(connector, "id", "")
    if not isinstance(cid, str) or not ID_RE.match(cid):
        problems.append(f"id {cid!r} must match {ID_RE.pattern}")
    for attr in ("name", "description", "raw_folder"):
        value = getattr(connector, attr, "")
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{attr} is empty")
    if getattr(connector, "raw_folder", "") and ("/" in connector.raw_folder or connector.raw_folder.startswith(".")):
        problems.append("raw_folder must be one plain folder name")
    if not ICON_RE.match(getattr(connector, "icon", "") or ""):
        problems.append(f"icon {connector.icon!r} must be 'fab fa-…' or 'fas fa-…'")
    if connector.category not in CATEGORIES:
        problems.append(f"category {connector.category!r} must be one of {CATEGORIES}")
    caps = set(connector.capabilities)
    if not caps:
        problems.append("no capabilities")
    if caps - set(CAPABILITIES):
        problems.append(f"unknown capabilities {sorted(caps - set(CAPABILITIES))}")
    for host in connector.allowed_hosts:
        if not isinstance(host, str) or not HOST_RE.match(host):
            problems.append(f"allowed host {host!r} must be a plain host name (no scheme, port or wildcard)")
    if "api_sync" in caps and not connector.allowed_hosts:
        problems.append("api_sync needs allowed_hosts")
    if "api_sync" not in caps and connector.allowed_hosts:
        problems.append("only an api_sync connector may contact hosts")
    if caps & {"export_request", "export_watch"} and not connector.exports:
        problems.append("export_request/export_watch need exports")
    kind_ids = [k.id for k in connector.exports]
    if len(set(kind_ids)) != len(kind_ids):
        problems.append("two exports share an id")
    for kind in connector.exports:
        parts = urlsplit(kind.request_url)
        if parts.scheme != "https" or not parts.hostname:
            problems.append(f"export {kind.id}: request_url must be https")
        if "export_watch" in caps and not kind.patterns:
            problems.append(f"export {kind.id}: export_watch needs file patterns")
        if not kind.label.strip() or not kind.folder_label.strip() or "/" in kind.folder_label:
            problems.append(f"export {kind.id}: label and folder_label are needed (no '/')")
        lo, hi = kind.expected_days
        if not 0 <= lo <= hi:
            problems.append(f"export {kind.id}: expected_days must be (low, high)")
    if not connector.help:
        problems.append("help is empty: list the ways to get the data, from easy to hard")
    order = [EFFORTS.index(h.effort) if h.effort in EFFORTS else -1 for h in connector.help]
    if -1 in order:
        problems.append(f"help effort must be one of {EFFORTS}")
    elif order != sorted(order):
        problems.append("help must go from easy to hard")
    for option in connector.help:
        if not option.title.strip() or not option.steps:
            problems.append("every help option needs a title and steps")
    keys = [f.key for f in connector.settings]
    if len(set(keys)) != len(keys):
        problems.append("two settings share a key")
    for setting in connector.settings:
        if setting.kind not in SETTING_KINDS or not setting.label.strip():
            problems.append(f"setting {setting.key}: kind must be one of {SETTING_KINDS}, with a label")
    if connector.setup is not None:
        missing = [k for k in connector.setup.fields if k not in keys]
        if missing:
            problems.append(f"setup fields {missing} are not settings")
    if "api_sync" in caps and connector.setup is None:
        problems.append("api_sync needs a setup (the Verbinden wizard)")
    if not 5 <= int(connector.schedule_minutes) <= 24 * 60:
        problems.append("schedule_minutes must be between 5 and 1440")
    return problems


def _discover() -> List[Connector]:
    from backend.datagatherer import connectors as package
    found = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        if hasattr(module, "CONNECTOR"):
            found.append(module.CONNECTOR)
    if os.environ.get("DATAVAULT_TESTING") == "1":
        for name in filter(None, (os.environ.get("DATAVAULT_GATHER_EXTRA") or "").split(",")):
            found.append(importlib.import_module(name.strip()).CONNECTOR)
    return found


def _check(connector: Connector, known: Dict[str, Connector]) -> None:
    problems = validate(connector)
    if connector.id in known:
        problems.append("id is already used by another connector")
    if problems:
        raise InvalidConnector(f"connector {getattr(connector, 'id', '?')}: " + "; ".join(problems))


def load() -> Dict[str, Connector]:
    global _connectors
    with _lock:
        if _connectors is None:
            known: Dict[str, Connector] = {}
            for connector in _discover():
                _check(connector, known)
                known[connector.id] = connector
            _connectors = known
        return _connectors


def connectors() -> List[Connector]:
    """Every connector, grouped by category in CATEGORIES order, then by its order and name."""
    return sorted(load().values(), key=lambda c: (CATEGORIES.index(c.category), c.order, c.name.casefold()))


def get(source_id: str) -> Optional[Connector]:
    return load().get(source_id)


def register(connector: Connector) -> None:
    """Add a connector at run time (tests only; real connectors are modules)."""
    if os.environ.get("DATAVAULT_TESTING") != "1":
        raise RuntimeError("register() is for tests; add a module to backend/datagatherer/connectors/")
    current = load()
    with _lock:
        _check(connector, current)
        current[connector.id] = connector


def unregister(source_id: str) -> None:
    if os.environ.get("DATAVAULT_TESTING") != "1":
        raise RuntimeError("unregister() is for tests")
    with _lock:
        if _connectors is not None:
            _connectors.pop(source_id, None)
