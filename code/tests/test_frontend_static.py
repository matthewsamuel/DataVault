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
    shortcuts = re.findall(r"\{ view: '(\w+)', key: (?:'(\d)'|null), g: '(\w)'", sources["shortcuts.js"])
    assert [v for v, _, _ in shortcuts] == views
    # The first nine have a digit, in order; a view after those only has g + letter
    digits = [k for _, k, _ in shortcuts]
    assert digits[:9] == [str(i) for i in range(1, min(len(views), 9) + 1)]
    assert all(k == "" for k in digits[9:]), "only nine views can have a digit"
    assert len({g for _, _, g in shortcuts}) == len(views), "two views share a g + letter shortcut"
    for view in views:
        assert f'id="section-{view}"' in sources["index.html"], view
        # A menu entry of its own, or (no room in the menu) under another one with data-also: Berichten
        # opens from DataGatherer, whose icon stays active while it is open
        assert f'data-view="{view}"' in sources["index.html"] or re.search(
            rf'data-also="[^"]*\b{view}\b', sources["index.html"]), view


def test_new_scripts_load_after_the_helpers_they_use():
    index = _sources()["index.html"]
    scripts = [s.split("?")[0] for s in re.findall(r'<script src="([^"]+)"', index)]
    assert scripts.index("ui.js") < scripts.index("app.js")
    for late in ("recovery.js", "settings.js", "shortcuts.js", "library.js", "datagatherer.js", "elo.js", "backup-settings.js", "messages.js"):
        assert scripts.index(late) > scripts.index("jobs.js"), late
    # DataGatherer registers its Instellingen section, so it loads after settings.js
    assert scripts.index("datagatherer.js") > scripts.index("settings.js")
    # elo.js adds a shortcut group and backup-settings.js a settings section
    assert scripts.index("elo.js") > scripts.index("shortcuts.js")
    assert scripts.index("backup-settings.js") > scripts.index("settings.js")


def test_the_datagatherer_page_has_no_code_per_source():
    """A new source is one Python file: the page knows no connector by name (it reads their metadata)."""
    from backend.datagatherer import registry
    for page in ("datagatherer.js", "activity.js"):
        js = re.sub(r"/\*.*?\*/|//[^\n]*", "", _sources()[page], flags=re.S).lower()
        for connector in registry.connectors():
            for word in {connector.id, connector.name.lower()}:
                assert word not in js, (page, word)


# Colours come from the design tokens (style.css, between @tokens:start and @tokens:end),
# never from a literal somewhere in a rule: one place to tune light and dark, and WCAG AA
# checked once. A colour that is truly not a token goes here, with the reason.
COLOUR_ALLOWLIST = set()
COLOUR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(", re.IGNORECASE)


def _colours(text):
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for m in COLOUR_RE.finditer(line):
            if m.group(0).lower() not in COLOUR_ALLOWLIST:
                found.append(f"{number}: {line.strip()[:90]}")
    return found


def test_no_hard_coded_colours_outside_the_design_tokens():
    css = _sources()["style.css"]
    start, end = css.index("/* @tokens:start */"), css.index("/* @tokens:end */")
    assert start < end
    # Line numbers stay those of style.css: comments and the token block become empty lines
    blank = lambda m: "\n" * m.group(0).count("\n")
    rules = css[:start] + re.sub(r"[^\n]", "", css[start:end]) + css[end:]
    rules = re.sub(r"/\*.*?\*/", blank, rules, flags=re.S)
    assert _colours(rules) == [], "hard-coded colours in style.css: use (or add) a token"

    guide = (FRONTEND / "styleguide.css").read_text(encoding="utf-8")
    assert _colours(re.sub(r"/\*.*?\*/", blank, guide, flags=re.S)) == [], "styleguide.css uses literal colours"


def test_no_hard_coded_colours_in_inline_styles():
    """style="..." in the page and in markup built by JS uses tokens too (var(--...))."""
    offenders = []
    for name, text in _sources().items():
        if name == "style.css":
            continue
        for m in re.finditer(r"""style(?:\.cssText\s*=\s*|\s*[:=]\s*)(["'`])(.*?)\1""", text, re.S):
            offenders += [f"{name}: {c}" for c in _colours(m.group(2))]
    assert offenders == [], f"literal colours in inline styles: {offenders}"


def test_both_themes_define_the_same_colour_tokens():
    css = _sources()["style.css"]
    block = lambda selector: re.search(re.escape(selector) + r"[^{]*\{(.*?)\n\s*\}", css, re.S).group(1)
    dark = set(re.findall(r"(--[\w-]+)\s*:", block(":root,\n.theme-scope[data-theme=\"dark\"]")))
    light = set(re.findall(r"(--[\w-]+)\s*:", block(":root[data-theme=\"light\"]")))
    auto = set(re.findall(r"(--[\w-]+)\s*:", block(":root:not([data-theme])")))
    assert dark and dark == light == auto, (dark ^ light, light ^ auto)


def test_screen_bound_toasts_and_audio_tiles():
    app = (FRONTEND / "app.js").read_text()
    assert "toast.dataset.view = activeMainView" in app and "t.dataset.view !== view" in app
    assert "audioThumbUrl(file.name)" not in app and "fa-music" in app


def test_the_idle_job_dock_is_a_small_icon_not_a_wide_pill():
    css = _sources()["style.css"]
    # the 200 px minimum of a collapsed dock must not apply to the tiny 'n klaar' icon
    assert ".job-dock.collapsed:not(.compact):not(.tiny)" in css
    assert not re.search(r"\.job-dock\.collapsed:not\(\.compact\)\s*\{", css)
    # on a small screen the dock starts collapsed and stays low when opened
    assert "JOB_DOCK_SMALL" in _sources()["jobs.js"]
