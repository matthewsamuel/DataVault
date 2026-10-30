"""
File and folder names, paths, EXIF values, Spotify titles and chat output are
data, never HTML: a file may be called 'xss <img src=x onerror=...>.jpg'. The
frontend therefore only builds HTML through the helpers in frontend/dom.js
(createEl, html`...`, setHTML). This static check fails when code puts anything
else into innerHTML, or interpolates data into an inline event handler.

Allowed right-hand sides for `x.innerHTML = ...`:
- a string literal or a template literal without ${...}   (fixed markup)
- html`...`                                               (every value escaped)
- cond ? 'literal' : 'literal'                            (fixed markup)
Everything else (a variable, a concatenation, a plain template with ${...}) must
use setHTML(el, markup), createEl or textContent instead.
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
SINK_RE = re.compile(r"\.(innerHTML|outerHTML)\s*(\+?=)(?!=)\s*")
BANNED_RE = re.compile(r"insertAdjacentHTML|document\.write|createContextualFragment|\beval\(|new Function\(")
INLINE_HANDLER_WITH_DATA_RE = re.compile(r"""\bon[a-z]+\s*=\s*(["'])(?:(?!\1).)*\$\{""", re.IGNORECASE | re.DOTALL)
LITERAL = r"""(?:'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`[^`$]*`)"""
TERNARY_RE = re.compile(rf"""^[\w.!]+\s*\?\s*{LITERAL}\s*:\s*{LITERAL}\s*[;)\n}}]""")
LITERAL_RE = re.compile(rf"""^{LITERAL}\s*[;)\n}}]""")


def _scripts():
    return [p for p in sorted(FRONTEND.glob("*.js")) if p.name != "dom.js"]


def _template_end(src: str, start: int) -> int:
    """Index just past the template literal that starts at src[start] == '`'."""
    i, depth = start + 1, 0
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if depth == 0 and c == "`":
            return i + 1
        if c == "$" and src[i + 1:i + 2] == "{":
            depth += 1
            i += 2
            continue
        if depth and c == "}":
            depth -= 1
        elif depth and c == "`":
            i = _template_end(src, i)
            continue
        i += 1
    raise AssertionError("unterminated template literal")


def _sink_violations(src: str):
    for m in SINK_RE.finditer(src):
        line = src.count("\n", 0, m.start()) + 1
        rhs = src[m.end():]
        if m.group(1) == "outerHTML" or m.group(2) == "+=":
            yield line, f"{m.group(1)} {m.group(2)}"
        elif rhs.startswith("html`"):
            continue
        elif rhs.startswith("`"):
            literal = rhs[:_template_end(rhs, 0)]
            if "${" in literal:
                yield line, "template literal with ${...} (use html`...`)"
        elif LITERAL_RE.match(rhs) or TERNARY_RE.match(rhs):
            continue
        else:
            yield line, "non-literal value: " + rhs.split("\n", 1)[0][:80] + " (use setHTML / createEl / textContent)"


def test_no_unescaped_html_sinks():
    problems = []
    for path in _scripts():
        src = path.read_text(encoding="utf-8")
        for line, why in _sink_violations(src):
            problems.append(f"{path.name}:{line}: {why}")
        for m in BANNED_RE.finditer(src):
            problems.append(f"{path.name}:{src.count(chr(10), 0, m.start()) + 1}: {m.group(0)}")
    assert not problems, "unsafe HTML sinks:\n" + "\n".join(problems)


def test_no_inline_event_handlers_with_data():
    problems = []
    for path in _scripts():
        src = path.read_text(encoding="utf-8")
        for m in INLINE_HANDLER_WITH_DATA_RE.finditer(src):
            problems.append(f"{path.name}:{src.count(chr(10), 0, m.start()) + 1}: {m.group(0)[:80]}")
    assert not problems, "inline handlers with interpolated data:\n" + "\n".join(problems)


def test_the_checker_catches_the_old_patterns():
    bad = [
        "el.innerHTML = `<b>${file.name}</b>`;",
        "el.innerHTML = '<b>' + name + '</b>';",
        "el.innerHTML = imagesHtml;",
        "el.innerHTML += `<div>x</div>`;",
        "el.innerHTML = escapeHTML(text).replace(/\\n/g, '<br>');",
    ]
    for code in bad:
        assert list(_sink_violations(code)), code
    good = [
        "el.innerHTML = '';",
        "el.innerHTML = '<i class=\"fas fa-check\"></i>';",
        "el.innerHTML = isSel ? '<i class=\"fas fa-check\"></i>' : '';",
        "el.innerHTML = `\n  <div class=\"x\">static</div>\n`;",
        "el.innerHTML = html`<b>${file.name}</b>`;",
    ]
    for code in good:
        assert not list(_sink_violations(code)), code
    assert INLINE_HANDLER_WITH_DATA_RE.search("""`<div onclick="openPhotoOTD('${p.path}')">`""")
    assert not INLINE_HANDLER_WITH_DATA_RE.search("""`<button onclick="loadShows()">Try Again</button>`""")


def test_dom_helpers_load_before_the_app():
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    scripts = [s.split("?")[0] for s in re.findall(r'<script src="([^"]+)"', index)]
    # motion.js runs first, in <head>, only to set html[data-motion] before the first paint. It
    # makes no request of its own: it talks to the server through api.js's fetchJSON, after
    # DOMContentLoaded, so the session token is always added.
    if scripts and scripts[0] == "motion.js":
        motion = (FRONTEND / "motion.js").read_text(encoding="utf-8")
        assert not re.search(r"(?<![\w.])fetch\(|XMLHttpRequest", motion), "motion.js must use window.fetchJSON"
        scripts = scripts[1:]
    assert scripts[:2] == ["api.js", "dom.js"], scripts
