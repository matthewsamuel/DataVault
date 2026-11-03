"""
DataVault Dashboard - One error shape for the whole API

Every error answer is JSON of the form
    {"error": {"code": "<short code>", "message": "<Dutch text for the user>"}, "detail": ...}
(MEDIA-THUMBS-TIMELINE-17, LIVE-PROBE-18). 'detail' keeps what FastAPI used to send (the
same text, or the list of validation errors), so older code that reads it still works.
The frontend reads error.message (api.js fetchJSON / apiErrorMessage) and recognises
'vault_unavailable' to show the "archive not reachable" banner instead of a toast.

The vault (the T7) can be unplugged at any moment. Every route that needs it depends on
require_vault (a 503 before any work), and an OSError or database error that turns out
to be caused by a missing vault becomes the same 503, also halfway through a request.
"""
import errno
import sqlite3
import traceback

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

VAULT_UNREACHABLE = "Archief niet bereikbaar (schijf losgekoppeld?)"

CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "too_large",
    422: "invalid_request",
    500: "internal_error",
    502: "upstream_error",
    503: "unavailable",
    504: "timeout",
}

MESSAGES = {
    404: "Niet gevonden",
    405: "Deze actie is hier niet toegestaan",
    422: "Ongeldig verzoek",
    500: "Er ging iets mis in de server",
}


def error_body(status: int, message: str, code: str = None, detail=None) -> dict:
    if code is None:
        code = "vault_unavailable" if message == VAULT_UNREACHABLE else CODES.get(status, f"http_{status}")
    return {"error": {"code": code, "message": message}, "detail": message if detail is None else detail}


def error_response(status: int, message: str, code: str = None, detail=None, headers=None) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_body(status, message, code, detail), headers=headers)


def vault_missing(request: Request) -> bool:
    config = getattr(request.app.state, "config", None)
    return config is None or not config.VAULT_ROOT.is_dir()


def require_vault(request: Request) -> None:
    """Dependency for every route that reads the archive: 503 while the disk is not there."""
    if vault_missing(request):
        raise HTTPException(status_code=503, detail=VAULT_UNREACHABLE)


def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, str) and detail:
        message = detail
    else:
        message = MESSAGES.get(exc.status_code, f"Fout {exc.status_code}")
    return error_response(exc.status_code, message, detail=detail, headers=getattr(exc, "headers", None))


def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = []
    for err in exc.errors()[:3]:
        where = ".".join(str(part) for part in err.get("loc", ())[1:]) or "verzoek"
        problems.append(f"{where}: {err.get('msg', 'ongeldig')}")
    message = "Ongeldig verzoek" + (f" ({'; '.join(problems)})" if problems else "")
    return error_response(422, message, detail=jsonable_encoder(exc.errors()))


def _database_error(request: Request, exc: sqlite3.OperationalError) -> JSONResponse:
    """
    The database lives in the vault's cache folder, so an unplugged disk makes every
    database query fail. Answer that with a clear 503 instead of a bare 500.
    """
    config = request.app.state.config
    if config is None or not config.VAULT_ROOT.is_dir() or not config.DB_PATH.parent.is_dir():
        return error_response(503, VAULT_UNREACHABLE)
    from backend.main import _say
    _say("".join(traceback.format_exception(exc)))
    return error_response(500, f"Databasefout: {exc}", code="database_error")


def _os_error(request: Request, exc: OSError) -> JSONResponse:
    """
    A read or write error no route caught. With the vault gone (unplugged mid-request)
    that is the vault's 503; otherwise a short Dutch message with a fitting status.
    """
    if vault_missing(request):
        return error_response(503, VAULT_UNREACHABLE)
    if isinstance(exc, FileNotFoundError):
        return error_response(404, "Bestand of map niet (meer) gevonden")
    if isinstance(exc, PermissionError):
        return error_response(403, "Geen toegang tot dit bestand of deze map")
    if exc.errno in (errno.EIO, errno.ENXIO, errno.ENODEV):
        return error_response(503, "Leesfout op de schijf; is de schijf nog goed aangesloten?", code="io_error")
    from backend.main import _say
    _say("".join(traceback.format_exception(exc)))
    return error_response(500, f"Lees- of schrijffout: {exc.strerror or exc}", code="io_error")


def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    # Starlette still logs the traceback (it re-raises after sending this answer)
    return error_response(500, MESSAGES[500])


def install(app) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(sqlite3.OperationalError, _database_error)
    app.add_exception_handler(OSError, _os_error)
    app.add_exception_handler(Exception, _unexpected)
