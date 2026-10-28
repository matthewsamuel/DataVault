"""
The dashboard must work fully offline and never send anything to another host:
no CDN, no web fonts from Google, no album-art lookups. Every icon, font and
script it needs lives in code/frontend (third-party files in frontend/vendor/,
each with its license).

This scans every text file the frontend serves for http(s):// URLs and
protocol-relative ones (//host/...), and fails on any host that is not the local
server itself.
"""
import re
from pathlib import Path
from urllib.parse import urlsplit

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
TEXT_SUFFIXES = {".html", ".js", ".css", ".json", ".svg", ".webmanifest", ".map"}

# Hosts the frontend may load anything from. Only the local server: the app must
# work without internet. To allow another host (for example an explicit opt-in map
# tile server), add it here with a comment that says why, and make sure the
# frontend only contacts it after the user switched that option on.
ALLOWED_HOSTS = {
    "localhost",
    "127.0.0.1",
}

# Identifiers that look like URLs but are never fetched (XML namespaces).
NAMESPACE_URLS = {
    "http://www.w3.org/2000/svg",
}

# Hosts the user can open by clicking a link (a new browser tab). The app itself
# never requests them: no fetch, no <img>/<script>/<link>, no CSS url().
CLICK_ONLY_HOSTS = {
    "open.spotify.com",  # "zoek dit nummer op Spotify" in Spotify Wrapped
}

URL_RE = re.compile(r"""https?://[^\s'"`)<>\\]+""", re.IGNORECASE)
PROTOCOL_RELATIVE_RE = re.compile(
    r"""(?:\b(?:src|href|action)\s*=\s*["']|url\(\s*["']?|@import\s+["'])//[^/\s]""", re.IGNORECASE)
LOAD_CONTEXT_RE = re.compile(r"""fetch\(|\bsrc\s*=|url\(|@import|<link|<script|XMLHttpRequest""", re.IGNORECASE)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _frontend_text_files():
    for path in sorted(FRONTEND.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".css":
        # License headers (/*! Font Awesome ... https://fontawesome.com */) are not requests
        text = CSS_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return text


def _external_urls():
    for path in _frontend_text_files():
        for lineno, line in enumerate(_code(path).splitlines(), 1):
            for match in URL_RE.finditer(line):
                yield path.relative_to(FRONTEND), lineno, line, match.group(0)


def test_frontend_has_files_to_scan():
    names = {p.name for p in _frontend_text_files()}
    assert {"index.html", "app.js", "style.css", "all.min.css"} <= names


def test_frontend_names_no_external_hosts():
    offending = []
    for rel, lineno, line, url in _external_urls():
        host = (urlsplit(url).hostname or "").lower()
        if host in ALLOWED_HOSTS or url in NAMESPACE_URLS:
            continue
        if host in CLICK_ONLY_HOSTS and not LOAD_CONTEXT_RE.search(line):
            continue
        offending.append(f"{rel}:{lineno}: {url}")
    assert not offending, "external URLs in the frontend:\n" + "\n".join(offending)


def test_frontend_has_no_protocol_relative_urls():
    offending = []
    for path in _frontend_text_files():
        for lineno, line in enumerate(_code(path).splitlines(), 1):
            if PROTOCOL_RELATIVE_RE.search(line):
                offending.append(f"{path.relative_to(FRONTEND)}:{lineno}: {line.strip()[:120]}")
    assert not offending, "\n".join(offending)


def test_vendored_assets_are_present_with_licenses():
    vendor = FRONTEND / "vendor"
    fa = vendor / "fontawesome-6.4.0"
    assert (fa / "LICENSE.txt").is_file()
    for name in ["fa-solid-900", "fa-regular-400", "fa-brands-400", "fa-v4compatibility"]:
        assert (fa / "webfonts" / f"{name}.woff2").is_file(), name
    # Every font the stylesheet points to is really there
    for css in [fa / "css" / "all.min.css", vendor / "jetbrains-mono" / "jetbrains-mono.css"]:
        refs = re.findall(r"url\(([^)]+)\)", _code(css))
        assert refs, css
        for ref in refs:
            target = (css.parent / ref.strip("'\"")).resolve()
            assert target.is_file(), f"{css.name} -> {ref}"
    assert (vendor / "jetbrains-mono" / "OFL.txt").is_file()


def test_index_loads_only_local_stylesheets_and_scripts():
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r"""<(?:link|script)\b[^>]*\b(?:href|src)\s*=\s*["']([^"']+)""", index)
    assert refs
    for ref in refs:
        assert "://" not in ref and not ref.startswith("//"), ref
        assert (FRONTEND / ref.split("?")[0]).is_file(), ref


def test_api_docs_pages_load_nothing_from_a_cdn(client):
    """FastAPI's /docs and /redoc pull Swagger UI and ReDoc from cdn.jsdelivr.net; they are off."""
    for url in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        r = client.get(url)
        assert "cdn.jsdelivr.net" not in r.text
        assert "fastapi.tiangolo.com" not in r.text
        assert "swagger-ui" not in r.text.lower()
    # The machine-readable schema stays: plain JSON, nothing external
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "DataVault Dashboard"
