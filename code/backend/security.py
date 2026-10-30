"""
DataVault Dashboard - HTTP security

The dashboard is served by its own server on 127.0.0.1, so the browser page and
the API share one origin. That lets us lock the HTTP layer down:
- No CORS headers at all: other websites cannot read any API response.
- Trusted hosts only (127.0.0.1, localhost): a DNS-rebinding page whose Host
  header is its own domain gets 400 before reaching any route.
- A per-process session token: every request that can change something (any
  method other than GET/HEAD/OPTIONS) must carry it in X-DataVault-Token. Only
  same-origin JavaScript can read it (GET /api/session), so a cross-site form or
  fetch can never move a file, even as a "simple request" without preflight.
- A Content-Security-Policy on every response: the page may load and connect to its
  own server only. The single exception is the opt-in OpenStreetMap tile server (images
  only, used after the user switched it on); the browser enforces this, so a stray
  external URL in the frontend cannot reach the internet.
- No query strings in the access log: an OAuth callback (?code=...&state=...) would
  otherwise write a credential to the server log.
"""
import logging
import os
import secrets
from typing import List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

TOKEN_HEADER = "X-DataVault-Token"
# Set on a refused request, so the frontend knows to fetch a fresh token (after a restart)
AUTH_FAILED_HEADER = "X-DataVault-Auth"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def allowed_hosts() -> List[str]:
    """Host headers the server answers to. TestClient sends 'testserver' (test mode only)."""
    hosts = ["127.0.0.1", "localhost"]
    if os.environ.get("DATAVAULT_TESTING") == "1":
        hosts.append("testserver")
    return hosts


class SessionTokenMiddleware:
    """
    Refuses (403) every request with a state-changing method that does not carry
    the session token. Pure ASGI, so streaming responses (chat) pass untouched.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            sent = b""
            for name, value in scope["headers"]:
                if name == TOKEN_HEADER.lower().encode():
                    sent = value
                    break
            if not secrets.compare_digest(sent, self.token):
                message = "Ongeldige of ontbrekende sessiesleutel; herlaad de pagina"
                response = JSONResponse(
                    {"error": {"code": "session_token", "message": message}, "detail": message},
                    status_code=403,
                    headers={AUTH_FAILED_HEADER: "token"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# 'unsafe-inline' for scripts and styles: index.html uses onclick attributes and the views set
# inline styles. What matters for privacy is where the page may load from and connect to: 'self'.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https://tile.openstreetmap.org",
    "media-src 'self' blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
CSP_HEADER = b"content-security-policy"


class ContentSecurityPolicyMiddleware:
    """Adds CONTENT_SECURITY_POLICY to every HTTP response (pure ASGI: streaming passes untouched)."""

    def __init__(self, app, policy: str = CONTENT_SECURITY_POLICY):
        self.app = app
        self.policy = policy.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_policy(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(name.lower() == CSP_HEADER for name, _ in headers):
                    headers.append((CSP_HEADER, self.policy))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_policy)


class _NoQueryStringInAccessLog(logging.Filter):
    """uvicorn logs (client, method, path with query, http version, status): drop the query."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def strip_query_strings_from_access_log() -> None:
    """Install the filter on uvicorn's access logger (once per process)."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _NoQueryStringInAccessLog) for f in logger.filters):
        logger.addFilter(_NoQueryStringInAccessLog())


router = APIRouter()


@router.get("/api/session")
def get_session(request: Request):
    """
    The session token for this server process (same-origin pages only: there are no
    CORS headers, and browsers that send Sec-Fetch-Site get refused when cross-site).
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="Alleen voor de eigen pagina")
    return JSONResponse(
        {"token": request.app.state.session_token},
        headers={"Cache-Control": "no-store"},
    )
