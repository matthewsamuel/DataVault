"""
DataGatherer - OAuth Authorization Code with PKCE (RFC 7636, RFC 8252)

For a source whose API needs the user's consent (Spotify): the user clicks Verbinden, the
page opens the source's consent page in their own browser, and the source sends the
browser back to http://127.0.0.1:<port>/api/datagatherer/<id>/callback with a code.

- A fresh code_verifier (43-128 characters) and a random state per attempt; the challenge
  is S256. No client secret: a desktop app cannot keep one.
- The state is single-use, belongs to one source and expires after STATE_TTL seconds; the
  verifier never leaves this process (memory only, never the database).
- The redirect URI uses 127.0.0.1, never 'localhost' (Spotify refuses localhost).
"""
import base64
import hashlib
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

STATE_TTL = 600
MAX_PENDING = 20

_lock = threading.Lock()
_pending: Dict[str, Tuple[str, str, str, float]] = {}   # state -> (source_id, verifier, redirect_uri, created)


def new_verifier() -> str:
    """43-128 characters from the unreserved set (token_urlsafe uses A-Z a-z 0-9 - _)."""
    return secrets.token_urlsafe(72)[:96]


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_uri(port: Optional[int], source_id: str) -> str:
    port_part = f":{port}" if port and port not in (80,) else ""
    return f"http://127.0.0.1{port_part}/api/datagatherer/{source_id}/callback"


def begin(source_id: str, redirect: str) -> Tuple[str, str]:
    """A new attempt: returns (state, code_challenge)."""
    verifier = new_verifier()
    state = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        for key in [k for k, v in _pending.items() if now - v[3] > STATE_TTL]:
            del _pending[key]
        while len(_pending) >= MAX_PENDING:
            del _pending[min(_pending, key=lambda k: _pending[k][3])]
        _pending[state] = (source_id, verifier, redirect, now)
    return state, challenge(verifier)


def finish(source_id: str, state: Optional[str]) -> Optional[Tuple[str, str]]:
    """(verifier, redirect_uri) for a valid, unexpired state of this source; the state is used up."""
    if not state:
        return None
    with _lock:
        entry = _pending.pop(state, None)
    if entry is None:
        return None
    owner, verifier, redirect, created = entry
    if owner != source_id or time.monotonic() - created > STATE_TTL:
        return None
    return verifier, redirect


def forget_all() -> None:
    with _lock:
        _pending.clear()
