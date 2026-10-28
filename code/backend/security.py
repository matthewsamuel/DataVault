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
"""
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
                response = JSONResponse(
                    {"detail": "Ongeldige of ontbrekende sessiesleutel; herlaad de pagina"},
                    status_code=403,
                    headers={AUTH_FAILED_HEADER: "token"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


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
