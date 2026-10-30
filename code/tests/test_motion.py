"""Beweging (motion foundation): the setting in app_settings and the frontend pieces that use it."""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def test_motion_is_unset_until_the_user_chooses(client):
    assert client.get("/api/ui/settings").json() == {"motion": None}
    for mode in ("rustig", "uit", "volledig"):
        assert client.put("/api/ui/settings", json={"motion": mode}).json() == {"motion": mode}
        assert client.get("/api/ui/settings").json() == {"motion": mode}
    # Back to 'follow macOS'
    assert client.put("/api/ui/settings", json={"motion": None}).json() == {"motion": None}


def test_a_wrong_motion_value_is_refused_and_nothing_changes(client):
    client.put("/api/ui/settings", json={"motion": "rustig"})
    for bad in ("snel", 1, True, ""):
        assert client.put("/api/ui/settings", json={"motion": bad}).status_code == 400
    assert client.get("/api/ui/settings").json() == {"motion": "rustig"}
    # Without the key nothing changes either
    assert client.put("/api/ui/settings", json={}).json() == {"motion": "rustig"}


def test_the_motion_setting_lives_with_the_other_app_settings(client, app):
    client.put("/api/ui/settings", json={"motion": "uit"})
    assert app.state.db.get_setting("ui_motion") == "uit"


def _css():
    return (FRONTEND / "style.css").read_text(encoding="utf-8")


def test_motion_tokens_exist_and_springs_use_linear():
    css = _css()
    tokens = css[css.index("/* @tokens:start */"):css.index("/* @tokens:end */")]
    for name in ("--dur-instant", "--dur-1", "--dur-2", "--dur-3", "--dur-4", "--dur-5", "--dur-6",
                 "--ease-standard", "--ease-emphasized", "--ease-exit", "--spring-snappy", "--spring-smooth",
                 "--spring-bouncy", "--stagger-step", "--motion-distance-sm", "--motion-distance-md",
                 "--motion-distance-lg"):
        assert re.search(re.escape(name) + r"\s*:", tokens), name
    assert re.search(r"--spring-smooth\s*:\s*linear\(", tokens)


def test_every_motion_mode_is_styled_and_uit_stops_everything():
    css = _css()
    assert 'html[data-motion="uit"]' in css and 'html[data-motion="rustig"]' in css
    block = re.search(r'html\[data-motion="uit"\] \*,.*?\{(.*?)\}', css, re.S).group(1)
    assert "animation-duration: 0.01ms !important" in block and "transition-duration: 0.01ms !important" in block


def test_motion_js_loads_first_and_knows_the_three_modes():
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    head = index[:index.index("</head>")]
    assert re.search(r'<script src="motion\.js\?v=\d+"></script>', head), "motion.js must load in <head> (no flash)"
    js = (FRONTEND / "motion.js").read_text(encoding="utf-8")
    for mode in ("volledig", "rustig", "uit"):
        assert f"'{mode}'" in js
    assert "prefers-reduced-motion" in js and "startViewTransition" in js
