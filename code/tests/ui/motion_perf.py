"""
Motion performance harness: records frame timing in a real WebKit (Safari's engine) during
the key transitions of the motion language (docs/MOTION.md) and fails on a dropped frame.

How it measures: a requestAnimationFrame loop in the page records the time between frames
(the delta). For every transition it starts recording, triggers the transition, waits until
the motion has settled and stops. The frame in which the trigger itself runs is reported
separately as 'work' (that is the app's own synchronous work, e.g. building a page); every
frame after it belongs to the animation and must stay under the budget (32 ms by default:
one dropped frame at 60 Hz is tolerated, two in a row are not).

Every transition runs with Beweging = Volledig, in the dark theme, at 1440x900, after one
warm-up round so data is cached and the measurement is about motion, not about loading.

It never touches a real archive: it builds a throwaway vault in a temp folder (the showcase
vault with --showcase, else the small test vault), starts its own server on a free port with
DATAVAULT_TESTING=1 and removes everything at the end. Or point it at a running test server
with --base http://127.0.0.1:PORT.

    python3 code/tests/ui/motion_perf.py                 # small test vault
    python3 code/tests/ui/motion_perf.py --showcase      # the fuller showcase vault
    python3 code/tests/ui/motion_perf.py --budget 24     # stricter
    python3 code/tests/ui/motion_perf.py --runs 3        # every transition three times

Exit code 0 when every animation frame stayed under the budget, 1 otherwise. Not collected
by pytest (the name does not start with test_).

New transitions: add an entry to TRANSITIONS (a name, the JS that triggers it and how long
the motion takes to settle). Keep the trigger to what a user does (a click, a key, a call the
page itself makes on a click).
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_webkit import CODE_DIR, free_port, launch, server_python  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

RECORDER = """
(() => {
    if (window.__motionPerf) return;
    window.__motionPerf = {
        start() {
            const frames = [];
            let last = null, on = true;
            const tick = (t) => {
                if (last !== null) frames.push({ at: t, delta: t - last });
                last = t;
                if (on) requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
            this.stop = () => { on = false; return frames; };
            this.mark = () => { this.markAt = performance.now(); };
        },
    };
})();
"""

# Bestanden: door de mappen naar beneden tot er foto's in het raster staan
OPEN_PHOTO_FOLDER = """(async () => {
    const PHOTO = '#media-grid .media-card[data-motion-key]:not(.other-card):not(.audio-card) img';
    switchMainView('viewer');
    for (let i = 0; i < 6; i++) {
        await new Promise(r => setTimeout(r, 500));
        if (document.querySelector(PHOTO)) return;
        const folder = document.querySelector("#media-grid .folder-card[aria-label='Map RAW']") || document.querySelector('#media-grid .folder-card');
        if (!folder) return;
        folder.click();
    }
})()"""

# Keurer and Tijdlijn on a folder of the test vault (found from the folder tree, never a real path)
KEURER_FOLDER = """(() => {
    switchMainView('keurer');
    const raw = folderTree.find(f => f.name === 'RAW');
    keurerSelectedFolderForModal = raw.path + '/2022/bursts';
    keurerConfirmFolderSelection();
})()"""
TIMELINE_FOLDER = """(() => {
    localStorage.setItem('timeline_folders', JSON.stringify([folderTree.find(f => f.name === 'RAW').path]));
    switchMainView('timeline');
})()"""
# A user scrolling: twelve steps, one per frame
SCROLL_LOOP = """(async () => {
    const s = document.getElementById('SCROLLER');
    for (let i = 0; i < 12; i++) { s.scrollBy(0, 90); await new Promise(r => requestAnimationFrame(r)); }
})()"""

# (name, setup JS run before recording or None, trigger JS, settle ms)
TRANSITIONS = [
    ("pagina: Home -> Bestanden", "switchMainView('home')", "switchMainView('viewer')", 900),
    ("pagina: Bestanden -> Tijdlijn", "switchMainView('viewer')", "switchMainView('timeline')", 900),
    ("pagina: Tijdlijn -> Albums", "switchMainView('timeline')", "switchMainView('albums')", 900),
    ("pagina: Albums -> Keurer", "switchMainView('albums')", "switchMainView('keurer')", 900),
    ("pagina: Keurer -> Instellingen", "switchMainView('keurer')", "switchMainView('settings')", 900),
    ("pagina: Instellingen -> Home", "switchMainView('settings')", "switchMainView('home')", 900),
    ("venster: sneltoetsen open", "switchMainView('home')", "openShortcutHelp()", 800),
    ("venster: sneltoetsen dicht", None, "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))", 600),
    ("viewer: foto groeit uit miniatuur", OPEN_PHOTO_FOLDER,
     "(() => { const c = document.querySelector('#media-grid .media-card[data-motion-key]:not(.other-card):not(.audio-card)');"
     " c.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); c.click(); })()", 1000),
    ("viewer: terug naar miniatuur", None, "closeModal()", 1000),
    ("melding: binnen", None, "showToast('Klaar', 'Drie foto\\u2019s verplaatst', 'success')", 800),
    ("melding: weg", None, "hideToast(document.querySelector('#toast-container .toast'))", 800),
    ("thema: cirkel naar licht", "document.getElementById('theme-button').focus()", "setTheme('light')", 1000),
    ("thema: cirkel naar donker", "document.getElementById('theme-button').focus()", "setTheme('dark')", 1000),
] + [
    # ---------- Media (media-motion.js, docs/MOTION.md 'Media') ----------
    ("bestanden: map komt binnen (golf)", OPEN_PHOTO_FOLDER, "selectFolder(currentFolder)", 1000),
    ("bestanden: sorteren (FLIP)", "handleSort('name')", "handleSort('size')", 800),
    ("bestanden: filteren tijdens typen", "handleSort('name')",
     "(() => { const q = document.getElementById('global-search'); q.value = 'a'; handleGlobalSearch('a'); })()", 800),
    ("bestanden: raster -> lijst", "(() => { const q = document.getElementById('global-search'); q.value = ''; handleGlobalSearch(''); })()",
     "toggleView('list')", 800),
    ("bestanden: lijst -> raster", None, "toggleView('grid')", 800),
    ("viewer: volgende foto", "(() => { const c = document.querySelector('#media-grid .media-card[data-motion-key]:not(.other-card):not(.audio-card)');"
     " c.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); c.click(); })()", "nextFile()", 700),
    ("viewer: vorige foto", None, "prevFile()", 700),
    ("viewer: dicht", None, "closeModal()", 900),
    ("keurer: houden (kaart vliegt)", KEURER_FOLDER, "keurerDecideKeep()", 800),
    ("keurer: prullenmand (kaart vliegt)", None, "keurerDecideDelete()", 800),
    ("keurer: ongedaan (komt terug)", None, "keurerUndo()", 900),
    ("tijdlijn: eerste scherm (golf)", TIMELINE_FOLDER, "timelineInit()", 1200),
    ("tijdlijn: scrollen (rijen zetten zich neer)", None, SCROLL_LOOP.replace("SCROLLER", "timeline-scroll"), 1000),
    ("albums: ander filter (golf)", "switchMainView('albums')", "albumsShow(null, {kind: ['photo']}, 'taken')", 1100),
    ("albums: scrollen", None, SCROLL_LOOP.replace("SCROLLER", "albums-scroll"), 1000),
    ("reeksen: uitwaaieren op de beste", "switchMainView('duplicates'); dupShowTab('series')",
     "(() => { const c = document.querySelector('#series-list .series-card'); if (c) MediaMotion.seriesSettle(c, {again: true}); })()", 1300),
    ("shows: collecties husselen", "switchMainView('shows')", "shuffleShows()", 900),
    ("shows: dia wisselt (kruisvervagen)", "startShow(_lastShowsData.featured)", "activeShow.next()", 1300),
    ("shows: slideshow dicht", None, "activeShow.close()", 700),
]

# 5000+ photos in one folder (--stress): the first screen cascades, the rest reveals per row
STRESS_TRANSITIONS = [
    ("stress: map met veel foto's openen", "switchMainView('viewer')", "selectFolder(STRESS_PATH)", 1500),
    ("stress: snel scrollen door het raster", None, SCROLL_LOOP.replace("document.getElementById('SCROLLER')", "document.scrollingElement").replace("90", "260"), 1200),
    ("stress: sorteren (FLIP)", "window.scrollTo(0, 0)", "handleSort('size')", 1000),
    ("stress: viewer open en volgende", "(() => { const c = document.querySelector('#media-grid .media-card[data-motion-key]');"
     " c.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); c.click(); })()", "nextFile()", 800),
    ("stress: viewer dicht", None, "closeModal()", 900),
]


def start_server(workdir: Path, showcase: bool, stress: int = 0):
    python = server_python()
    vault = workdir / "vault"
    builder = "showcase_vault.py" if showcase else "vault_builder.py"
    subprocess.run([python, str(CODE_DIR / "tests" / "fixtures" / builder), str(vault)], check=True,
                   cwd=CODE_DIR, stdout=subprocess.DEVNULL)
    if stress:
        make_stress_folder(vault, stress)
    config = Path(f"{vault}_config.json")
    port = free_port()
    env = dict(os.environ, DATAVAULT_TESTING="1", DATAVAULT_CONFIG=str(config),
               DATAVAULT_LOG_FILE=str(workdir / "server.log"), DATAVAULT_GATHER_DAEMON="0",
               DATAVAULT_GATHER_HOME=str(workdir / "home"), DATAVAULT_CHROME_DIR=str(workdir / "Chrome"))
    log = open(workdir / "uvicorn.log", "w")
    proc = subprocess.Popen([python, "-m", "uvicorn", "--factory", "backend.main:create_app",
                             "--host", "127.0.0.1", "--port", str(port)],
                            cwd=CODE_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                if r.status == 200:
                    break
        except OSError:
            time.sleep(0.5)
    else:
        proc.terminate()
        raise RuntimeError(f"server did not start, see {workdir / 'uvicorn.log'}")
    if showcase:
        subprocess.run([python, str(CODE_DIR / "tests" / "fixtures" / "showcase_vault.py"), str(vault),
                        "--populate", base], check=True, cwd=CODE_DIR, stdout=subprocess.DEVNULL)
    return proc, base


def make_stress_folder(vault: Path, count: int) -> Path:
    """count small JPEGs in RAW/Beweging stress (copies of twelve colours; thumbnails come lazily)."""
    from PIL import Image
    folder = vault / "RAW" / "Beweging stress"
    folder.mkdir(parents=True, exist_ok=True)
    templates = []
    for i in range(12):
        path = folder / f"_tpl{i:02d}.jpg"
        Image.new("RGB", (240, 180), ((i * 53) % 256, (i * 97 + 60) % 256, (i * 151 + 120) % 256)).save(path, quality=70)
        templates.append(path.read_bytes())
        path.unlink()
    for i in range(count):
        (folder / f"IMG_{i:05d}.jpg").write_bytes(templates[i % len(templates)])
    return folder


def measure(page, setup, trigger, settle_ms):
    if setup:
        page.evaluate(setup)
        page.wait_for_timeout(700)
    page.evaluate("window.__motionPerf.start()")
    page.wait_for_timeout(120)
    page.evaluate(f"window.__motionPerf.mark(); {trigger};")
    page.wait_for_timeout(settle_ms)
    frames = page.evaluate("window.__motionPerf.stop()")
    mark = page.evaluate("window.__motionPerf.markAt")
    after = [f["delta"] for f in frames if f["at"] > mark]
    if not after:
        return None, []
    # The first frame after the trigger holds the trigger's own synchronous work
    return after[0], after[1:]


def run(args) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="datavault-motionperf-"))
    proc = None
    failed = []
    try:
        base = args.base
        transitions = list(TRANSITIONS)
        if not base:
            proc, base = start_server(workdir, args.showcase, args.stress)
        if args.stress and not args.base:
            stress_path = str((workdir / "vault" / "RAW" / "Beweging stress").resolve())
            transitions += [(n, s.replace("STRESS_PATH", json.dumps(stress_path)) if s else s,
                             t.replace("STRESS_PATH", json.dumps(stress_path)), ms) for n, s, t, ms in STRESS_TRANSITIONS]
        with sync_playwright() as p:
            browser = launch(p, "webkit", args.headed)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_init_script("try { localStorage.setItem('motion', 'volledig');"
                                    " localStorage.setItem('theme', 'dark'); } catch (e) {}")
            context.add_init_script(RECORDER)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base)
            page.wait_for_load_state("networkidle")
            if page.evaluate("Motion.mode()") != "volledig":
                print("Beweging is not Volledig in this browser (prefers-reduced-motion?); nothing to measure")
                return 1
            # Warm-up: every view once, so the measurement is about motion, not about loading
            for view in ("viewer", "timeline", "albums", "keurer", "settings", "home"):
                page.evaluate(f"switchMainView('{view}')")
                page.wait_for_timeout(900)
            print(f"WebKit against {base}, budget {args.budget:.0f} ms per animation frame\n")
            print(f"  {'transition':38} {'work':>7} {'frames':>6} {'median':>7} {'p95':>7} {'max':>7}  result")
            if args.only:
                transitions = [t for t in transitions if args.only in t[0]]
            for name, setup, trigger, settle in transitions:
                worst = []
                for _ in range(args.runs):
                    work, deltas = measure(page, setup, trigger, settle)
                    if not deltas:
                        failed.append(f"{name}: no frames recorded")
                        continue
                    worst.append((max(deltas), work, deltas))
                if not worst:
                    print(f"  {name:38} {'-':>7} {'-':>6} {'-':>7} {'-':>7} {'-':>7}  NO FRAMES")
                    continue
                peak, work, deltas = max(worst, key=lambda w: w[0])
                p95 = sorted(deltas)[max(0, int(len(deltas) * 0.95) - 1)]
                over = [d for d in deltas if d > args.budget]
                result = "ok" if not over else f"FAIL ({len(over)} over budget)"
                if over:
                    failed.append(f"{name}: {len(over)} frame(s) over {args.budget:.0f} ms (max {peak:.1f} ms)")
                print(f"  {name:38} {work:6.1f}  {len(deltas):6d} {statistics.median(deltas):6.1f}  {p95:6.1f}  {peak:6.1f}  {result}")
            if errors:
                failed += [f"page error: {e}" for e in errors]
            browser.close()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(workdir, ignore_errors=True)
    print()
    if failed:
        print("FAILED:\n  " + "\n  ".join(failed))
        return 1
    print("All transitions stayed under the frame budget.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--showcase", action="store_true", help="use the fuller showcase vault")
    parser.add_argument("--base", help="measure against a running test server instead of starting one")
    parser.add_argument("--budget", type=float, default=32.0, help="max ms per animation frame (default 32)")
    parser.add_argument("--runs", type=int, default=2, help="measure every transition this many times (worst counts)")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--stress", type=int, default=0, metavar="N",
                        help="also measure a folder of N photos (e.g. 5000) in Bestanden")
    parser.add_argument("--only", help="only the transitions whose name contains this text")
    sys.exit(run(parser.parse_args()))
