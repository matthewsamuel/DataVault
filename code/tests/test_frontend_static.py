"""
Static checks on the frontend that need no browser:

- every inline handler (onclick="name(...)", also in markup built by JS) calls a
  function that exists, so no button silently throws a ReferenceError (the viewer's
  Delete button and nav zones once called confirmDeleteFile/navigateModal, which did
  not exist);
- every CSS variable that is used is also defined, so no colour, border or font
  silently falls back to nothing (e.g. --primary, --border in the Auto theme).

The browser checks live in tests/ui/smoke_webkit.py (run by hand, see its docstring).
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def _sources():
    files = sorted(FRONTEND.glob("*.js")) + [FRONTEND / "index.html", FRONTEND / "style.css"]
    return {f.name: f.read_text(encoding="utf-8") for f in files}


def test_inline_handlers_call_existing_functions():
    sources = _sources()
    js = "".join(text for name, text in sources.items() if name.endswith(".js"))
    called = set()
    for text in sources.values():
        # on...="fn(" or on...="if (...) fn(" ; object methods like activeShow.close() are skipped
        for m in re.finditer(r'\son[a-z]+="\s*(?:if\s*\([^"]*?\)\s*)?([A-Za-z_$][\w$]*)\(', text):
            called.add(m.group(1))
    assert called, "found no inline handlers at all: the pattern is broken"
    missing = sorted(
        name for name in called
        if not re.search(r"(?:function\s+%s\b|\b%s\s*=[^=]|window\.%s\s*=)" % ((re.escape(name),) * 3), js)
    )
    assert missing == [], f"inline handlers call functions that do not exist: {missing}"


def test_every_used_css_variable_is_defined():
    sources = _sources()
    defined = set(re.findall(r"(--[\w-]+)\s*:", sources["style.css"]))
    for text in sources.values():
        defined |= set(re.findall(r"setProperty\(\s*['\"](--[\w-]+)", text))
    undefined = {}
    for name, text in sources.items():
        for var in re.findall(r"var\(\s*(--[\w-]+)", text):
            if var not in defined:
                undefined.setdefault(var, set()).add(name)
    assert undefined == {}, f"CSS variables used but never defined: {undefined}"


def test_auto_theme_follows_the_system_setting():
    css = _sources()["style.css"]
    assert re.search(r"@media\s*\(prefers-color-scheme:\s*light\)\s*\{\s*:root:not\(\[data-theme\]\)", css)
