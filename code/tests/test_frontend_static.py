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


def test_icon_only_buttons_in_the_page_have_a_name():
    """A button that shows only an icon or a × must say what it does (aria-label) to screen readers."""
    index = _sources()["index.html"]
    unnamed = []
    for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", index, re.S):
        attrs, inner = m.group(1), m.group(2)
        text = re.sub(r"<[^>]+>", "", inner).replace("&times;", "").replace("×", "").strip()
        if not text and "aria-label=" not in attrs:
            unnamed.append(m.group(0)[:100])
    assert unnamed == [], f"icon-only buttons without aria-label: {unnamed}"


def test_every_main_view_has_a_section_a_menu_entry_and_a_shortcut():
    sources = _sources()
    views = re.search(r"const MAIN_VIEWS = \[([^\]]+)\]", sources["app.js"]).group(1)
    views = re.findall(r"'(\w+)'", views)
    shortcuts = re.findall(r"\{ view: '(\w+)', key: '(\d)', g: '(\w)'", sources["shortcuts.js"])
    assert [v for v, _, _ in shortcuts] == views
    assert [k for _, k, _ in shortcuts] == [str(i) for i in range(1, len(views) + 1)]
    assert len({g for _, _, g in shortcuts}) == len(views), "two views share a g + letter shortcut"
    for view in views:
        assert f'id="section-{view}"' in sources["index.html"], view
        assert f'data-view="{view}"' in sources["index.html"], view


def test_new_scripts_load_after_the_helpers_they_use():
    index = _sources()["index.html"]
    scripts = [s.split("?")[0] for s in re.findall(r'<script src="([^"]+)"', index)]
    assert scripts.index("ui.js") < scripts.index("app.js")
    for late in ("recovery.js", "settings.js", "shortcuts.js"):
        assert scripts.index(late) > scripts.index("jobs.js"), late
