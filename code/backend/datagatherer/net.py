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

    def _log(self, request: httpx.Request, outcome: str, status=None, size=None, ms=None, note=None) -> None:
        try:
            state.log_request(self.db, source_id=self.connector.id, trigger=self.trigger, method=request.method,
                              host=(request.url.host or "?")[:255], path=request.url.path[:300], status=status,
                              outcome=outcome, bytes=size, ms=ms, job_id=self.job_id, note=note)
        except Exception:
            pass  # a log that cannot be written (vault just unplugged) never lets a request through

    def _refuse(self, request: httpx.Request, reason: str):
        self._log(request, "refused", note=reason)
        raise NetworkRefused(reason)

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
        start = time.monotonic()
        try:
            response = self.inner.handle_request(request)
            response.read()
        except NetworkRefused as e:
            self._log(request, "refused", note=str(e))
            raise
        except httpx.HTTPError as e:
            self._log(request, "error", ms=int((time.monotonic() - start) * 1000), note=type(e).__name__)
            raise
        self._log(request, "ok" if response.status_code < 400 else "http_error", status=response.status_code,
                  size=len(response.content), ms=int((time.monotonic() - start) * 1000))
        return response

    def close(self) -> None:
        self.inner.close()


def client(db, connector, trigger: str, job_id: Optional[int] = None) -> httpx.Client:
    """An HTTP client for one connector and one reason (trigger); see the module docstring."""
    return httpx.Client(transport=GuardedTransport(db, connector, trigger, job_id), timeout=TIMEOUT,
                        headers={"User-Agent": USER_AGENT}, follow_redirects=False)
