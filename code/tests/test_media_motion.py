"""Beweging for the photo screens (media-motion.js): loaded in the right place, built on Motion,
and every tile that opens the viewer can be the shared element."""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def _read(name):
    return (FRONTEND / name).read_text(encoding="utf-8")


def test_media_motion_loads_before_the_screens_that_use_it():
    index = _read("index.html")
    scripts = re.findall(r'<script src="([\w.-]+)\?v=\d+"></script>', index)
    assert "media-motion.js" in scripts
    for user in ("app.js", "keurer.js", "timeline.js", "library.js", "search.js", "series.js", "duplicates.js", "export.js"):
        assert scripts.index("media-motion.js") < scripts.index(user), user
    # motion.js (in <head>) comes first: MediaMotion is built on window.Motion
    assert index.index('src="motion.js') < index.index('src="media-motion.js')


def test_media_motion_only_moves_transform_and_opacity_and_never_loops():
    js = _read("media-motion.js")
    assert "setInterval" not in js
    # Every animation goes through Motion (mode-aware), never element.animate directly
    assert not re.search(r"\.animate\(", js.replace("Motion.animate(", ""))
    for prop in ("width:", "height:", "top:", "left:", "boxShadow"):
        for frames in re.findall(r"Motion\.animate\([^;]*?\]", js, re.S):
            assert f"{{ {prop}" not in frames, prop


def test_every_tile_that_opens_the_viewer_is_a_shared_element():
    assert "card.dataset.motionKey = file.path" in _read("app.js")
    assert "card.dataset.motionKey = file.path" in _read("timeline.js")
    assert "card.dataset.motionKey = file.path" in _read("library.js")
    assert "motionKey: item.path" in _read("search.js")


def test_media_motion_css_uses_tokens_and_stays_still_in_rustig_and_uit():
    css = _read("style.css")
    start = css.index("BEWEGING: MEDIA")
    section = css[start:]
    assert not re.search(r"\b\d+ms\b", section), "durations come from the motion tokens"
    assert "cubic-bezier(" not in section, "curves come from the motion tokens"
    # Scroll-linked rows and the hover light only in Volledig
    assert 'html[data-motion="volledig"] .tl-photo-row' in section
    assert 'html:not([data-motion="volledig"]) .mm-light' in section
    assert 'html:not([data-motion="volledig"]) .cinematic-media.ken-burns-in' in section


def test_the_keurer_ghost_flies_under_the_next_card_and_the_next_photos_are_ready():
    js = (FRONTEND / "media-motion.js").read_text(encoding="utf-8")
    fly = js[js.index("function keurerFly"):js.index("function keurerArrive")]
    assert "layer: keurerLayer(card)" in fly and "flying.length > 1" in fly and "'--ease-out'" in fly
    arrive = js[js.index("function keurerArrive"):js.index("// ---------- 9.")]
    assert "opacity: 0.55" not in arrive   # the new card is opaque from its first frame
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.mm-ghost-layer\.mm-ghost-under \{\s*z-index: auto;", css)
    keurer = (FRONTEND / "keurer.js").read_text(encoding="utf-8")
    assert "keurerPreloadNext();" in keurer and ".decode()" in keurer


def test_the_viewer_lands_exactly_closes_faster_and_never_double_exposes():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    morph = app[app.index("function viewerMorph"):app.index("function gridFiles")]
    # The guessed rect is the content box of the viewer (its padding left out)
    assert "padding${side}" in morph and "radius" in morph
    motion = (FRONTEND / "motion.js").read_text(encoding="utf-8")
    shared = motion[motion.index("function sharedElement"):motion.index("// ---------- Getallen die tellen")]
    assert "spring: opening ? 'gentle' : 'snappy'" in shared
    assert "contentRect(big)" in shared   # the waiting ghost shrinks to the real photo, no snap
    js = (FRONTEND / "media-motion.js").read_text(encoding="utf-8")
    step = js[js.index("function viewerStep"):js.index("function viewerSwipe")]
    assert "Motion.ms('--dur-1', 90) * 0.6" in step and "scale(0.98)" not in step
