"""Beweging on the data screens (motion-data.js): Spotify, Kaart, DataGatherer, Berichten,
Activiteit and Pasha. Static checks that keep the motion inside the shared language
(docs/MOTION.md): tokens instead of literal timings, guarded hooks, and nothing that loops or
polls behind the user's back."""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
SECTION = "/* ==================== BEWEGING: GEGEVENSSCHERMEN (motion-data.js)"


def _read(name):
    return (FRONTEND / name).read_text(encoding="utf-8")


def _section():
    css = _read("style.css")
    start = css.index(SECTION)
    nxt = css.find("/* ==================== ", start + len(SECTION))
    return css[start:nxt if nxt > 0 else len(css)]


def test_motion_data_loads_after_the_screens_and_helpers_it_uses():
    index = _read("index.html")
    scripts = [s.split("?")[0] for s in re.findall(r'<script src="([^"]+)"', index)]
    assert "motion-data.js" in scripts
    for before in ("motion.js", "app.js", "jobs.js", "map.js", "datagatherer.js", "activity.js", "messages.js"):
        assert scripts.index(before) < scripts.index("motion-data.js"), before


def test_the_css_section_uses_motion_tokens_not_literal_timings():
    css = re.sub(r"/\*.*?\*/", "", _section(), flags=re.S)
    assert "cubic-bezier" not in css
    # Durations and delays come from --dur-*, --spring-*-dur or --stagger-step
    for rule in re.findall(r"(?:animation|transition)[\w-]*\s*:[^;]*;", css):
        assert not re.search(r"(?<![\w.-])(?!0m?s\b)\d*\.?\d+m?s\b", rule), rule


def test_loops_and_moves_only_run_in_volledig():
    css = re.sub(r"/\*.*?\*/", "", _section(), flags=re.S)
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if "infinite" in body:
            assert 'html[data-motion="volledig"]' in selector, selector.strip()


def test_every_screen_calls_its_hook_only_when_the_module_is_there():
    hooks = {
        "app.js": "MotionData.spotifyRendered",
        "map.js": "MotionData.mapRoute",
        "datagatherer.js": "MotionData.dgRendered",
        "messages.js": "MotionData.msgThread",
        "activity.js": "MotionData.actEvents",
    }
    for page, hook in hooks.items():
        js = _read(page)
        calls = [line for line in js.splitlines() if hook in line]
        assert calls, (page, hook)
        assert all("window.MotionData" in line for line in calls), (page, calls)


def test_motion_data_never_polls_or_animates_per_frame_itself():
    js = re.sub(r"/\*.*?\*/|//[^\n]*", "", _read("motion-data.js"), flags=re.S)
    assert "setInterval" not in js
    assert "requestAnimationFrame(tick" not in js
    # Every helper reads the mode through window.Motion (Volledig / Rustig / Uit)
    assert "Motion.full()" in js and "Motion.off()" in js


def test_long_lists_contain_their_paint_and_only_the_active_item_is_a_layer():
    # Without containment the first frame of any animation in a list of 5000 conversations
    # cost up to 200 ms in WebKit (motion_perf.py: "berichten: 5000 gesprekken")
    css = re.sub(r"/\*.*?\*/", "", _section(), flags=re.S)
    assert re.search(r"\.msg-list,\s*\.msg-scroll\s*\{\s*contain:\s*strict;", css)
    # The sliding selection needs a stacking context, but only on the selected item
    assert not re.search(r"(^|\})\s*\.msg-item,\s*\.act-day\s*\{", css)
    assert "\n.act-icon {\n    position" not in css
    # Rows out of view are skipped entirely (content-visibility), so rows that arrive do not
    # repaint 5000 others every frame; the selected row is not, its glide leaves its box
    assert re.search(r"\.msg-list > \.msg-item:not\(\.active\)\s*\{\s*content-visibility:\s*auto;\s*contain-intrinsic-size:\s*auto ", css)


def test_chat_motion_shares_its_keys_with_the_shell_group():
    # Bubbles and the nod use the same keys as motion-shell.js, so after the merge one
    # interrupts the other instead of the two stacking on the same element
    js = _read("motion-data.js")
    assert "key: 'chat-in'" in js and "key: 'nod'" in js
