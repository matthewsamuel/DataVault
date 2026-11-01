"""Beweging van de schil (motion-shell.js): it loads last, moves only through Motion and the
motion tokens, and every caller in the app works without it."""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def _read(name):
    return (FRONTEND / name).read_text(encoding="utf-8")


def _shell_css():
    css = _read("style.css")
    start = css.index("/* ==================== BEWEGING: SCHIL (motion-shell.js)")
    end = css.find("/* ====================", start + 10)
    return css[start:end if end != -1 else len(css)]


def test_the_shell_motion_loads_last_after_the_screens_it_calls():
    index = _read("index.html")
    scripts = re.findall(r'<script src="([^"?]+)(?:\?v=\d+)?"></script>', index)
    assert scripts[-1] == "motion-shell.js", "motion-shell.js must be the last script (it calls into app.js and jobs.js)"
    assert scripts.index("motion.js") < scripts.index("app.js")


def test_the_shell_moves_only_through_motion():
    js = _read("motion-shell.js")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", js, flags=re.S)
    # Never a raw Web Animation, media query or fetch: Motion knows the mode (Volledig, Rustig, Uit)
    assert ".animate(" not in code.replace("M.animate(", "")
    assert "matchMedia" not in code and "fetch(" not in code
    # No literal durations: timings come from the tokens (M.ms) or a spring
    assert not re.search(r"duration:\s*\d", code)
    assert not re.search(r"delay:\s*\d", code)


def test_the_shell_css_uses_the_motion_tokens():
    css = re.sub(r"/\*.*?\*/", "", _shell_css(), flags=re.S)
    for rule in re.findall(r"(?:animation|transition)\s*:[^;]+;", css):
        assert "cubic-bezier" not in rule, rule
        # Every time in an animation or transition is a token (var(--...)), never '200ms'
        assert not re.search(r"(?<![\w-])\d+(?:\.\d+)?m?s\b", rule), rule
    # Rustig gets a calm variant of every loop that moves
    assert 'html[data-motion="rustig"] .job-running .job-bar::after' in css
    assert 'html[data-motion="rustig"] .skeleton' in css


def test_every_caller_works_without_the_shell_motion():
    # app.js, jobs.js and recovery.js only call ShellMotion behind a guard, so a page without
    # motion-shell.js (the style guide, a test page) keeps working
    for name in ("app.js", "jobs.js", "recovery.js"):
        lines = _read(name).splitlines()
        for i, line in enumerate(lines):
            if "ShellMotion." not in line:
                continue
            guard = "\n".join(lines[max(0, i - 3):i + 1])
            assert re.search(r"window\.ShellMotion|if \((before|play)\)", guard), \
                f"{name}: unguarded ShellMotion call: {line.strip()}"


def test_the_theme_circle_has_a_narrow_feather():
    # A wide feather (14vmax) read as a grey smear and cost a dropped frame at the start
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    assert "--shell-reveal-feather: 32px;" in css
