"""
DataGatherer - the network guard

DataVault is local-first: it goes online ONLY for a connector the user acts on (a click:
trigger 'user') or one whose schedule the user switched on (trigger 'schedule', checked
against dg_connectors at the moment of the request). Then it may reach that connector's
allowed_hosts over https and nothing else. Every request, and every refused attempt, is a
row in dg_network_log (method, host and path, never the query string, which can hold an API
key), shown in the DataGatherer tab.

The guard is the transport of every client a connector gets (GatherContext.http), so a
redirect to another host is checked too. Tests (DATAVAULT_TESTING=1) never reach the real
network: unless a test installs a fake (set_transport_factory with an httpx.MockTransport),
every request is refused.

Every allowed request first gets a 'pending' row, written before it goes out; when that row
cannot be written the request is refused. The row gets its outcome once the answer is in.
"""
import os
import time
from typing import Callable, Optional

import httpx

from backend.datagatherer import state

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
USER_AGENT = "DataVault-DataGatherer/1 (lokaal, persoonlijk archief)"
NETWORK_TRIGGERS = ("user", "schedule")

_transport_factory: Optional[Callable[[], httpx.BaseTransport]] = None


PENDING = "pending"   # the row of a request that is on its way (written before it goes out)
LOG_UNAVAILABLE = "Het netwerklog kan nu niet worden bijgewerkt; de verbinding is niet gemaakt. Probeer het zo opnieuw."


class NetworkRefused(Exception):
    """A request the guard did not let through; the message is Dutch."""


def set_transport_factory(factory: Optional[Callable[[], httpx.BaseTransport]]) -> None:
    """Tests: where requests go instead of the internet (an httpx.MockTransport factory)."""
    global _transport_factory
    _transport_factory = factory


class _Offline(httpx.BaseTransport):
    def handle_request(self, request):
        raise NetworkRefused("Geen echte netwerkverbinding in testmodus")


def _inner() -> httpx.BaseTransport:
    if _transport_factory is not None:
        return _transport_factory()
    if os.environ.get("DATAVAULT_TESTING") == "1":
        return _Offline()
    return httpx.HTTPTransport(retries=0)


class GuardedTransport(httpx.BaseTransport):
    def __init__(self, db, connector, trigger: str, job_id: Optional[int] = None):
        self.db = db
        self.connector = connector
        self.trigger = trigger
        self.job_id = job_id
        self.inner = _inner()

    def _log(self, request: httpx.Request, outcome: str, status=None, size=None, ms=None, note=None) -> Optional[int]:
        return state.log_request(self.db, source_id=self.connector.id, trigger=self.trigger, method=request.method,
                                 host=(request.url.host or "?")[:255], path=request.url.path[:300], status=status,
                                 outcome=outcome, bytes=size, ms=ms, job_id=self.job_id, note=note)

    def _refuse(self, request: httpx.Request, reason: str):
        try:
            self._log(request, "refused", note=reason)
        except Exception:
            pass  # refused either way
        raise NetworkRefused(reason)

    def _finish(self, row_id: int, outcome: str, **values) -> None:
        try:
            state.finish_request(self.db, row_id, outcome=outcome, **values)
        except Exception:
            pass  # the request is in the log already (as 'pending'), only its outcome is missing

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").lower()
        if self.trigger not in NETWORK_TRIGGERS:
            self._refuse(request, "Geen netwerk zonder klik of schema")
        if self.trigger == "schedule" and not state.schedule_enabled(self.db, self.connector.id):
            self._refuse(request, "Het schema van deze bron staat uit")
        if request.url.scheme != "https":
            self._refuse(request, f"Alleen https: {request.url.scheme}://{host}")
        if host not in self.connector.allowed_hosts:
            self._refuse(request, f"{host} staat niet in de lijst van {self.connector.name}")
        # The row is written BEFORE the request goes out: a log that cannot be written (vault
        # just unplugged, database locked) never lets a request through unrecorded
        try:
            row_id = self._log(request, PENDING)
        except Exception:
            raise NetworkRefused(LOG_UNAVAILABLE)
        start = time.monotonic()
        try:
            response = self.inner.handle_request(request)
            response.read()
        except NetworkRefused as e:
            self._finish(row_id, "refused", note=str(e))
            raise
        except Exception as e:
            self._finish(row_id, "error", ms=int((time.monotonic() - start) * 1000), note=type(e).__name__)
            raise
        self._finish(row_id, "ok" if response.status_code < 400 else "http_error", status=response.status_code,
                     bytes=len(response.content), ms=int((time.monotonic() - start) * 1000))
        return response

    def close(self) -> None:
        self.inner.close()


def client(db, connector, trigger: str, job_id: Optional[int] = None) -> httpx.Client:
    """An HTTP client for one connector and one reason (trigger); see the module docstring."""
    return httpx.Client(transport=GuardedTransport(db, connector, trigger, job_id), timeout=TIMEOUT,
                        headers={"User-Agent": USER_AGENT}, follow_redirects=False)
