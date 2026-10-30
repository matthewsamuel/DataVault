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
]

# ---------- Schil (motion-shell.js) ----------
# Een lege staat in een eigen vak boven de pagina, zodat het meten niets van de app verandert
SHELL_EMPTY = ("(() => { let box = document.getElementById('perf-empty'); if (box) box.remove();"
               " box = document.createElement('div'); box.id = 'perf-empty';"
               " box.style.cssText = 'position:fixed;left:40px;bottom:40px;width:420px;z-index:5;background:var(--surface-card)';"
               " document.body.append(box); box.append(emptyState({ icon: 'fa-inbox', title: 'Nog niets ' + Math.random(),"
               " text: 'Een lege staat komt binnen', action: { label: 'Iets doen', onClick() {} } })); })()")
SHELL_BURST = ("(() => { const item = document.createElement('div'); item.className = 'job-item job-done';"
               " item.style.cssText = 'position:fixed;right:40px;bottom:120px;'; item.innerHTML = '<span class=\"job-status\">Klaar</span>';"
               " document.body.append(item); ShellMotion.jobCelebrate(item); setTimeout(() => item.remove(), 900); })()")
TRANSITIONS += [
    ("schil: eerste indruk van Home", "switchMainView('home')", "ShellMotion.homeIntro()", 1200),
    ("schil: Home -> Herstelcentrum (streep)", "switchMainView('home')", "switchMainView('recovery')", 1000),
    ("schil: Herstelcentrum -> Home", None, "switchMainView('home')", 1000),
    ("schil: themamenu open", "closeThemeDropdown()", "toggleThemeDropdown()", 700),
    ("schil: themamenu dicht", None, "closeThemeDropdown()", 500),
    ("schil: zoekvenster open", "switchMainView('home')", "openSearch('')", 900),
    ("schil: zoekvenster dicht", None, "closeSearch()", 600),
    ("schil: segment glijdt", "openSearch('')",
     "document.querySelector('#search-overlay .search-mode[data-mode=\"filename\"]').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));"
     " document.querySelector('#search-overlay .search-mode[data-mode=\"filename\"]').click()", 700),
    ("schil: lege staat komt binnen", "closeSearch()", SHELL_EMPTY, 900),
    ("schil: taak klaar (feestje)", "document.getElementById('perf-empty')?.remove()", SHELL_BURST, 900),
]


def start_server(workdir: Path, showcase: bool):
    python = server_python()
    vault = workdir / "vault"
    builder = "showcase_vault.py" if showcase else "vault_builder.py"
    subprocess.run([python, str(CODE_DIR / "tests" / "fixtures" / builder), str(vault)], check=True,
                   cwd=CODE_DIR, stdout=subprocess.DEVNULL)
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
        if not base:
            proc, base = start_server(workdir, args.showcase)
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
            for name, setup, trigger, settle in TRANSITIONS:
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
    sys.exit(run(parser.parse_args()))
