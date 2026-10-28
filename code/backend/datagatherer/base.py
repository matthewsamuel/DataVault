"""
DataGatherer - the connector interface

A connector is one data source. It is a subclass of Connector with class attributes that
describe it (all Dutch where the user sees them) and methods for what it can do:

    capabilities    a subset of CAPABILITIES:
                    api_sync        fetches new data from an official API (needs allowed_hosts)
                    local_import    reads a database on this Mac (no network)
                    export_request  opens the source's own export page in the user's browser
                    export_watch    recognises that export when it lands in Downloads
                    import          reads a raw export (zip, file or registered folder)

The UI (the DataGatherer tab) and the routes are generic: they read this metadata through
card()/describe() and call the methods below. Adding a source is one module in
backend/datagatherer/connectors/ with a CONNECTOR = MyConnector() at the bottom; the
registry validates it (registry.validate) and a test does that for every connector.

A connector never sees an HTTP client that can reach anything else than its
allowed_hosts (net.py), never gets a password, and never moves or deletes a raw export
(raw_store.py copies them in; import_raw only reads).
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

CAPABILITIES = ("api_sync", "local_import", "export_request", "export_watch", "import")
CATEGORIES = ("Muziek", "Berichten", "Sociaal", "Activiteit", "Locatie")
EFFORTS = ("makkelijk", "gemiddeld", "moeilijk")
SETTING_KINDS = ("text", "secret", "number", "toggle")


@dataclass(frozen=True)
class SettingField:
    """One setting of a connector. kind 'secret' goes to the Keychain, the rest to dg_connectors."""
    key: str
    label: str
    kind: str = "text"
    help: str = ""
    required: bool = False
    placeholder: str = ""


@dataclass(frozen=True)
class ExportKind:
    """
    An export the user requests on the source's own website (export_request) and that the
    Downloads watcher recognises (export_watch). patterns are cheap name filters (globs);
    Connector.detect() then looks at the content. folder_label names the folder the copy
    goes into: data/<owner>/<raw_folder>/<YYYY-MM-DD> <folder_label>/.
    """
    id: str
    label: str
    request_url: str
    patterns: Tuple[str, ...]
    folder_label: str
    expected_days: Tuple[int, int] = (1, 30)
    steps: Tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpOption:
    """One way to get the data, for the Help panel; a connector lists them from easy to hard."""
    title: str
    effort: str
    steps: Tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class Setup:
    """
    The 'Verbinden' wizard of an api_sync connector: the steps (with {redirect_uri} filled in
    by the server), the settings the user pastes (keys of Connector.settings), a note and
    links to the pages the steps talk about.
    oauth: after saving, the button opens the source's consent page (Connector.authorize_url).
    """
    intro: str
    steps: Tuple[str, ...]
    fields: Tuple[str, ...]
    note: str = ""
    oauth: bool = False
    links: Tuple[Tuple[str, str], ...] = ()   # (label, https url): opened in the browser on a click


@dataclass
class Coverage:
    """
    What the data covers: from/until (epoch seconds) and the parts that are estimated
    (listening time from the API or Last.fm, not from an export). segments are
    (from, until, estimated) for the coverage bar on the card; holes are (from, until)
    spans between first and last without any data (the card then says so).
    """
    records: int = 0
    first: Optional[float] = None
    last: Optional[float] = None
    estimated_since: Optional[float] = None
    segments: List[Tuple[float, float, bool]] = field(default_factory=list)
    holes: List[Tuple[float, float]] = field(default_factory=list)


class ConnectorError(Exception):
    """A problem the user can act on; the message is Dutch and shown as is."""

    def __init__(self, message: str, reconnect: bool = False, retry_after: Optional[float] = None):
        super().__init__(message)
        self.reconnect = reconnect
        self.retry_after = retry_after


class Connector:
    # ---- metadata (validated by registry.validate) ----
    id: str = ""                      # ^[a-z][a-z0-9_]{1,31}$, also in job type names and URLs
    name: str = ""                    # "Spotify"
    icon: str = ""                    # "fab fa-spotify" (vendored Font Awesome)
    category: str = ""                # one of CATEGORIES
    order: int = 100                  # place within its category (lower first, then by name)
    record_label: Tuple[str, str] = ("item", "items")   # what one record is called (one, many)
    description: str = ""             # one Dutch sentence: what this source brings in
    capabilities: frozenset = frozenset()
    allowed_hosts: Tuple[str, ...] = ()   # every host it may ever contact (exact names, https only)
    exports: Tuple[ExportKind, ...] = ()
    raw_folder: str = ""              # folder under data/<owner>/ for archived exports
    existing_folders: Tuple[str, ...] = ()  # folders under data/<owner>/ registered in place
    folder_patterns: Tuple[str, ...] = ("*",)  # files of such a folder its fingerprint looks at
    help: Tuple[HelpOption, ...] = ()
    settings: Tuple[SettingField, ...] = ()
    setup: Optional[Setup] = None
    stale_days: int = 7               # older data than this is shown as 'achter'
    schedule_minutes: int = 30        # default interval of the opt-in schedule
    launchd: bool = False             # may also run from the optional launchd agent
    parser_version: int = 1

    # ---- derived metadata ----
    @property
    def network_label(self) -> str:
        if "api_sync" in self.capabilities and self.allowed_hosts:
            return f"Verbindt met {_join(self.allowed_hosts)} wanneer jij klikt"
        return "Alleen lokaal: maakt nooit verbinding met internet"

    @property
    def export_url(self) -> Optional[str]:
        return self.exports[0].request_url if self.exports else None

    @property
    def file_patterns(self) -> Tuple[str, ...]:
        seen: List[str] = []
        for kind in self.exports:
            seen += [p for p in kind.patterns if p not in seen]
        return tuple(seen)

    def export_kind(self, kind_id: str) -> Optional[ExportKind]:
        return next((k for k in self.exports if k.id == kind_id), None)

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def secret_keys(self) -> List[str]:
        return [f.key for f in self.settings if f.kind == "secret"]

    # ---- behaviour (override what the capabilities promise) ----
    def detect(self, path: Path, names: Optional[List[str]]) -> Optional[str]:
        """
        The ExportKind id if path is an export of this source, else None. names is the list
        of members when path is a zip (read from its central directory), else None. Look at
        the content, not only the name: Downloads holds many zips.
        """
        return None

    def find_existing(self, ctx: "GatherContext") -> List[Tuple[Path, str]]:
        """Folders of earlier exports already in the vault, as (folder, kind): registered in place."""
        found = []
        owner = ctx.owner_folder
        for name in self.existing_folders:
            folder = owner / name if owner else None
            if folder is not None and folder.is_dir() and not folder.is_symlink():
                found.append((folder, self.exports[0].id if self.exports else "folder"))
        return found

    def import_raw(self, ctx: "GatherContext", raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        Read one raw export (raw['path'], a file or a registered folder) into the database.
        Must be idempotent: importing the same export again adds nothing. Returns stats:
        {added, seen, covers_from, covers_until, ...}. Never writes to the raw export.
        """
        raise ConnectorError(f"{self.name} kan (nog) geen export inlezen")

    def sync(self, ctx: "GatherContext") -> Dict[str, Any]:
        """api_sync / local_import: fetch what is new. Returns stats {added, seen, ...}."""
        raise ConnectorError(f"{self.name} kan niet bijwerken")

    def is_configured(self, ctx: "GatherContext") -> bool:
        """api_sync: are the settings and secrets there to sync (not: is the token still valid)."""
        return all(ctx.secrets.has(key) for key in self.secret_keys()) and all(
            ctx.settings.get(f.key) not in (None, "") for f in self.settings if f.required and f.kind != "secret")

    def coverage(self, ctx: "GatherContext") -> Coverage:
        """What the data of this source covers; shown on the card."""
        return Coverage()

    def notes(self, ctx: "GatherContext") -> List[Dict[str, str]]:
        """Extra lines for the card: [{'tone': 'warning'|'info', 'text': ...}]."""
        return []

    # OAuth (Setup.oauth): the consent page and the code exchange
    def authorize_url(self, ctx: "GatherContext", redirect_uri: str, state: str, challenge: str) -> str:
        raise ConnectorError(f"{self.name} heeft geen koppeling via de browser")

    def finish_authorization(self, ctx: "GatherContext", code: str, redirect_uri: str, verifier: str) -> None:
        raise ConnectorError(f"{self.name} heeft geen koppeling via de browser")

    def forget(self, ctx: "GatherContext") -> None:
        """'Vergeet dit account': remove every secret of this source from the Keychain."""
        for key in self.secret_keys() + list(getattr(self, "extra_secret_keys", ())):
            ctx.secrets.delete(key)


def _join(items: Iterable[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " en " + items[-1]


class GatherContext:
    """
    What a connector works with during one action: the config and database, its own
    Keychain items and settings, a guarded HTTP client, and (inside a job) progress and
    stop checks. trigger says why it runs: 'user' (a click), 'schedule' (an enabled
    schedule) or 'local' (no network allowed: imports and the watcher).
    """

    def __init__(self, config, db, connector: Connector, trigger: str = "local", job=None,
                 sleep: Optional[Callable[[float], None]] = None):
        from backend.datagatherer import keychain, raw_store, state
        self.config = config
        self.db = db
        self.connector = connector
        self.trigger = trigger
        self.job = job
        self.secrets = keychain.Scoped(connector.id)
        self.settings = state.get_settings(db, connector.id)
        self.owner_folder = raw_store.owner_folder(config)
        self._sleep = sleep

    @property
    def job_id(self) -> Optional[int]:
        return getattr(self.job, "job_id", None)

    def http(self):
        """An httpx.Client that only reaches this connector's allowed_hosts (net.NetworkGuard)."""
        from backend.datagatherer import net
        return net.client(self.db, self.connector, self.trigger, job_id=self.job_id)

    def check(self) -> None:
        if self.job is not None:
            self.job.check()

    def progress(self, processed: Optional[int] = None, total: Any = None, message: Optional[str] = None) -> None:
        if self.job is not None:
            from backend.jobs import KEEP
            self.job.progress(processed, KEEP if total is None else total, message)

    def sleep(self, seconds: float) -> None:
        """Wait (for a Retry-After) in small steps, so cancel and pause still work."""
        import time
        if self._sleep is not None:
            self._sleep(seconds)
            return
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            time.sleep(min(0.25, max(0.0, end - time.monotonic())))
