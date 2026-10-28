"""
Browser smoke test: clicks through every tab of the dashboard in a real WebKit (the
engine of Safari) and fails on any JavaScript error, console error or failed API call.

It never touches a real archive: it builds a throwaway test vault in a temp folder,
starts its own server on a free port (DATAVAULT_TESTING=1, DATAVAULT_CONFIG pointing at
that vault) and stops it again at the end.

It also drives the app with the keyboard (tabs, the shortcut overview, the Files grid and the
viewer, focus going back where it was), moves a file and puts it back from the Herstelcentrum,
checks Instellingen, and runs a11y_checks.js on every screen in the light and the dark theme:
all visible text must reach WCAG AA contrast and every control must have a name.

Not collected by pytest (the name does not start with test_). Run it from the repo root
with a Python that has Playwright (`pip install playwright && playwright install webkit`):

    python3 code/tests/ui/smoke_webkit.py              # WebKit
    python3 code/tests/ui/smoke_webkit.py chromium     # or Chromium / Firefox
    python3 code/tests/ui/smoke_webkit.py --headed     # watch it click

If the installed WebKit build does not match Playwright's expected version, point
PLAYWRIGHT_WEBKIT_EXECUTABLE at a pw_run.sh (the script tries the newest one in
~/Library/Caches/ms-playwright by itself).
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright

CODE_DIR = Path(__file__).resolve().parents[2]
VENV_PYTHON = CODE_DIR / "venv" / "bin" / "python"
BIG_FOLDER = "Groot"
BIG_FILES = 1500
MAX_THUMBS_ON_OPEN = 100
VIEWS = ["home", "viewer", "duplicates", "spotify", "shows", "keurer", "timeline", "recovery", "settings"]
A11Y_CHECKS = Path(__file__).with_name("a11y_checks.js")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(workdir: Path):
    vault = workdir / "vault"
    python = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)
    subprocess.run([python, str(CODE_DIR / "tests/fixtures/vault_builder.py"), str(vault)],
                   check=True, stdout=subprocess.DEVNULL)
    # A big folder: opening it may only fetch thumbnails for what is (nearly) in view
    big = vault / "RAW" / BIG_FOLDER
    big.mkdir()
    sample = next(f for f in sorted((vault / "RAW" / "misc").iterdir())
                  if f.suffix == ".jpg" and not f.name.startswith(".")).read_bytes()
    for i in range(BIG_FILES):
        (big / f"foto_{i:04d}.jpg").write_bytes(sample + str(i).encode())   # distinct bytes, still a JPEG
    port = free_port()
    env = dict(os.environ, DATAVAULT_TESTING="1", DATAVAULT_CONFIG=str(workdir / "vault_config.json"),
               DATAVAULT_LOG_FILE=str(workdir / "server.log"))
    log = open(workdir / "uvicorn.log", "w")
    proc = subprocess.Popen(
        [python, "-m", "uvicorn", "--factory", "backend.main:create_app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=CODE_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                if r.status == 200:
                    return proc, base, vault.resolve()
        except OSError:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"server did not start, see {workdir / 'uvicorn.log'}")


def launch(p, engine: str, headed: bool):
    browser_type = getattr(p, engine)
    try:
        return browser_type.launch(headless=not headed)
    except PlaywrightError:
        if engine != "webkit":
            raise
    exe = os.environ.get("PLAYWRIGHT_WEBKIT_EXECUTABLE")
    if not exe:
        found = sorted(glob.glob(os.path.expanduser("~/Library/Caches/ms-playwright/webkit-*/pw_run.sh")))
        if not found:
            raise
        exe = found[-1]
    return browser_type.launch(headless=not headed, executable_path=exe)


def run(engine: str = "webkit", headed: bool = False) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="datavault-smoke-"))
    proc, base, vault = start_server(workdir)
    problems = []
    try:
        with sync_playwright() as p:
            browser = launch(p, engine, headed)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
            page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
            page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}")
                    if "/api/" in r.url and r.status >= 500 else None)

            def step(name, action, wait=1.0):
                before = len(problems)
                action()
                time.sleep(wait)
                status = "ok" if len(problems) == before else "PROBLEM"
                print(f"  {status:7} {name}")

            page.goto(base)
            page.wait_for_load_state("networkidle")
            print(f"{engine} against {base}")

            # Every tab, by clicking it like a user
            for view in VIEWS:
                step(f"tab {view}", lambda: page.click(f".top-nav [data-view='{view}']"), 1.5)
                if not page.is_visible(f"#section-{view}"):
                    problems.append(f"tab {view}: section not visible")

            # Files: a folder with a subfolder, open a file, browse, move dialog, close
            folder = str(vault / "RAW" / "misc")
            step("viewer: open folder", lambda: (page.click(".nav-tab[data-view='viewer']"),
                                                 page.evaluate(f"selectFolder({json.dumps(folder)})")))
            step("viewer: open file", lambda: page.click("#media-grid .media-card:not(.folder-card)"))
            step("viewer: next file", lambda: page.keyboard.press("ArrowRight"), 0.5)
            step("viewer: move dialog", lambda: page.click("#btn-move-file"), 0.5)
            step("viewer: close dialog", lambda: page.keyboard.press("Escape"), 0.4)
            step("viewer: close", lambda: page.keyboard.press("Escape"), 0.4)

            # Files: a folder with 1500 photos renders in chunks with lazy thumbnails
            thumbs = set()
            page.on("request", lambda r: thumbs.add(r.url) if "/api/thumb" in r.url else None)
            big = str(vault / "RAW" / BIG_FOLDER)
            step("viewer: open folder with 1500 files",
                 lambda: page.evaluate(f"selectFolder({json.dumps(big)})"), 3.0)
            cards = page.evaluate("document.querySelectorAll('#media-grid .media-card').length")
            if len(thumbs) >= MAX_THUMBS_ON_OPEN or cards >= BIG_FILES:
                problems.append(f"big folder: {len(thumbs)} thumbnail requests and {cards} cards on open")
            step("viewer: scroll to the end", lambda: page.evaluate(
                "window.scrollTo(0, document.documentElement.scrollHeight)"), 1.0)
            if page.evaluate("document.querySelectorAll('#media-grid .media-card').length") <= cards:
                problems.append("big folder: scrolling down rendered no more cards")

            # Home: chat full screen open and closed, theme switch
            step("home: chat full screen", lambda: (page.click(".nav-tab[data-view='home']"),
                                                    page.click("#section-home .chat-fullscreen-toggle")), 0.5)
            step("home: close full screen", lambda: page.keyboard.press("Escape"), 0.4)

            # Chat actions from the full-screen chat: the file opens above the chat while the chat
            # input keeps focus, Escape closes only the file, and open_folder shows the folder
            def expect(ok, message):
                if not ok:
                    problems.append(message)

            fullscreen_open = "document.getElementById('chat-fullscreen-modal').classList.contains('active')"
            photo = str(next(f for f in sorted((vault / "RAW" / "misc").resolve().iterdir())
                             if f.suffix == ".jpg" and not f.name.startswith(".")))
            step("chat: open_file from full screen", lambda: (
                page.click("#section-home .chat-fullscreen-toggle"),
                page.wait_for_function("document.activeElement.id === 'chat-input-fullscreen'"),
                page.evaluate(f"executeAIAction({{action: 'open_file', path: {json.dumps(photo)}}})")), 1.2)
            expect(page.evaluate("isViewerOpen()"), "chat open_file: viewer did not open")
            step("chat: Escape closes the file", lambda: page.keyboard.press("Escape"), 0.4)
            expect(not page.evaluate("isViewerOpen()") and page.evaluate(fullscreen_open),
                   "chat open_file: Escape did not close only the viewer")
            expect(page.evaluate("document.activeElement.id") == "chat-input-fullscreen",
                   "chat open_file: focus did not go back to the chat input after closing the photo")
            step("chat: open_folder from full screen",
                 lambda: page.evaluate("executeAIAction({action: 'open_folder', target: 'misc'})"), 1.2)
            expect(not page.evaluate(fullscreen_open) and page.is_visible("#section-viewer"),
                   "chat open_folder: the folder stays hidden behind the full-screen chat")
            page.click(".nav-tab[data-view='home']")
            for theme in ("light", "dark", "auto"):
                step(f"theme {theme}", lambda: (page.click(".theme-button"),
                                                 page.click(f".theme-option[data-theme='{theme}']")), 0.3)

            # Keyboard: tabs with digits and g + letter, never while typing, the shortcut overview
            # keeps Tab inside and gives focus back, and the Files grid works with arrows and Enter
            view_now = "currentMainView()"
            active_id = "document.activeElement && (document.activeElement.id || document.activeElement.className)"
            page.click(".nav-tab[data-view='home']")
            step("keys: 7 opens the timeline", lambda: page.keyboard.press("7"), 0.6)
            expect(page.evaluate(view_now) == "timeline", "keys: 7 did not open the timeline")
            step("keys: g b opens Bestanden", lambda: (page.keyboard.press("g"), page.keyboard.press("b")), 0.6)
            expect(page.evaluate(view_now) == "viewer", "keys: g b did not open Bestanden")
            step("keys: / focuses the search", lambda: page.keyboard.press("/"), 0.2)
            expect(page.evaluate("document.activeElement.id") == "global-search", "keys: / did not focus the search")
            step("keys: typing 8 in the search stays", lambda: (page.keyboard.type("8"), page.keyboard.press("Escape")), 0.4)
            expect(page.evaluate(view_now) == "viewer", "keys: a digit typed in the search switched the tab")
            # (not page.fill: that would put the focus back in the search field)
            page.evaluate("document.getElementById('global-search').value = ''; handleGlobalSearch('')")
            step("keys: ? opens the overview", lambda: page.keyboard.press("?"), 0.4)
            expect(page.is_visible("#shortcut-help"), "keys: ? did not open the shortcut overview")
            for _ in range(25):
                page.keyboard.press("Tab")
            expect(page.evaluate("document.getElementById('shortcut-help').contains(document.activeElement)"),
                   "keys: Tab left the shortcut overview")
            step("keys: Escape closes the overview", lambda: page.keyboard.press("Escape"), 0.3)
            expect(not page.is_visible("#shortcut-help"), "keys: Escape did not close the overview")

            step("grid: open a folder", lambda: page.evaluate(f"selectFolder({json.dumps(folder)})"), 1.0)
            page.focus("#media-grid [data-grid-index][tabindex='0']")
            step("grid: arrow right", lambda: page.keyboard.press("ArrowRight"), 0.2)
            expect(page.evaluate("document.activeElement.dataset.gridIndex") == "1", "grid: ArrowRight did not move")
            first_file = page.evaluate("[...document.querySelectorAll('#media-grid [data-grid-index]')]"
                                       ".find(c => !c.classList.contains('folder-card')).dataset.gridIndex")
            page.evaluate(f"focusGridCard({first_file})")
            step("grid: Enter opens the viewer", lambda: page.keyboard.press("Enter"), 0.6)
            expect(page.evaluate("isViewerOpen()"), "grid: Enter did not open the viewer")
            for _ in range(15):
                page.keyboard.press("Tab")
            expect(page.evaluate("document.getElementById('modal-viewer').contains(document.activeElement)"),
                   "viewer: Tab left the viewer")
            step("grid: Escape closes the viewer", lambda: page.keyboard.press("Escape"), 0.4)
            expect(page.evaluate("document.activeElement.dataset.gridIndex") == str(first_file),
                   f"viewer: focus did not go back to the card ({page.evaluate(active_id)})")

            # Herstelcentrum: a move in the viewer is listed and undone from there
            moved_src = page.evaluate("gridFiles()[0].path")
            step("recovery: move a file in the viewer", lambda: (
                page.evaluate("openMediaModal(gridFiles()[0])"), page.keyboard.press("m"),
                page.wait_for_timeout(400),
                page.evaluate(f"selectMoveDestination({json.dumps(str(vault / 'library'))})"),
                page.click("#btn-confirm-move")), 1.2)
            expect(not Path(moved_src).exists(), "recovery: the file was not moved")
            step("recovery: 8 opens the Herstelcentrum", lambda: page.keyboard.press("8"), 1.0)
            expect(page.is_visible(".recovery-entry"), "recovery: no entry for the move")
            page.once("dialog", lambda d: d.accept())
            step("recovery: Terugzetten", lambda: page.click(".recovery-entry .recovery-undo"), 1.2)
            expect(Path(moved_src).exists(), "recovery: the file did not go back")
            expect(page.is_visible("#recovery-list .empty-state"), "recovery: the list did not show its empty state")

            # Instellingen: every built-in part is there and the theme can be chosen there
            step("settings: 9 opens Instellingen", lambda: page.keyboard.press("9"), 1.0)
            for part in ("weergave", "taalmodel", "bibliotheek", "shows", "herstel", "over"):
                expect(page.is_visible(f"#settings-{part}"), f"settings: part {part} missing")
            step("settings: dark theme", lambda: page.check("input[data-theme-choice='dark']"), 0.3)
            expect(page.evaluate("document.documentElement.dataset.theme") == "dark", "settings: theme not set")
            page.check("input[data-theme-choice='auto']")

            # Every screen in both themes: all visible text reaches WCAG AA, every control has a name
            for theme in ("light", "dark"):
                page.evaluate(f"setTheme('{theme}')")
                for view in VIEWS:
                    page.evaluate(f"switchMainView('{view}')")
                    page.wait_for_timeout(700)
                    page.add_script_tag(path=str(A11Y_CHECKS))
                    for fail in page.evaluate("__contrastFailures()"):
                        problems.append(f"contrast {theme} {view}: {fail['ratio']}:1 {fail['key']} ({fail['text']!r})")
                    if theme == "light":
                        for control in page.evaluate("__unnamedControls()"):
                            problems.append(f"unnamed control on {view}: {control}")
            print(f"  ok      contrast and names checked on {len(VIEWS)} screens in 2 themes")
            page.evaluate("setTheme('auto')")
            page.evaluate("switchMainView('home')")

            # Shows: Manage Sources and the Feeling Lucky slideshow
            step("shows: manage sources", lambda: (page.click(".nav-tab[data-view='shows']"),
                                                   page.click("text=Bronnen beheren")), 1.0)
            step("shows: close sources", lambda: page.evaluate("closeSettingsModal()"), 0.3)
            if page.query_selector(".shows-hero"):
                step("shows: slideshow", lambda: page.click(".shows-hero"), 1.5)
                step("shows: next slide", lambda: page.keyboard.press("ArrowRight"), 0.8)
                step("shows: close slideshow", lambda: page.keyboard.press("Escape"), 0.8)

            # Spotify year switch, timeline back again
            step("spotify: year", lambda: (page.click(".nav-tab[data-view='spotify']"),
                                           page.select_option("#spotify-year-select", index=page.evaluate(
                                               "document.getElementById('spotify-year-select').options.length - 1"))), 1.5)
            step("timeline again", lambda: page.click(".nav-tab[data-view='timeline']"), 1.0)

            # Library (media index): the app indexed the vault by itself; the dock shows it,
            # and 'Bibliotheek bijwerken' starts a full pass from the keyboard
            step("library: line after the automatic pass", lambda: page.wait_for_function(
                "/Bibliotheek: \\d/.test((document.getElementById('job-library') || {}).textContent || '')",
                timeout=60000, polling=250), 0.2)
            step("library: start a pass with the keyboard", lambda: (
                page.focus("#btn-library-update"), page.keyboard.press("Enter")), 0.5)
            step("library: pass done", lambda: page.wait_for_function(
                "[...document.querySelectorAll('#toast-container .toast')]"
                ".some(t => t.textContent.includes('Bibliotheek bijwerken: Klaar'))", timeout=60000, polling=250), 0.2)

            # Timeline of the whole vault from the index: only what is in view is drawn
            page.evaluate(f"localStorage.setItem('timeline_folders', JSON.stringify([{json.dumps(str(vault))}]))")
            page.reload()
            page.wait_for_load_state("networkidle")
            step("timeline: whole vault", lambda: (page.click(".nav-tab[data-view='timeline']"),
                                                   page.wait_for_selector(".tl-photo-card")), 1.5)
            drawn = page.evaluate("document.querySelectorAll('.tl-photo-card').length")
            total = page.text_content(".tl-info-count") or ""
            expect(drawn < 300 and str(BIG_FILES)[0] in total,
                   f"timeline: {drawn} cards drawn for {total.strip()!r} (virtualization broken?)")
            step("timeline: scroll down", lambda: page.evaluate(
                "document.getElementById('timeline-scroll').scrollTop = 100000"), 0.8)
            expect(page.evaluate("document.querySelectorAll('.tl-photo-card').length") < 300,
                   "timeline: scrolling drew every card")

            # Paging across a switch of source: a first page from the walk (made up here from
            # the real one, 500 rows) and the rest from the index is the same selection, so the
            # timeline goes on; a first page whose total differs makes it start over (once)
            scans = []
            page.on("request", lambda r: scans.append(r.url) if "/api/timeline/scan" in r.url else None)

            def fake_first_page(change_total):
                faked = []

                def handle(route):
                    url = route.request.url
                    if faked or "offset=0&" not in url:
                        return route.continue_()
                    faked.append(url)
                    response = route.fetch(url=url.replace("limit=3000", "limit=500"))
                    data = response.json()
                    data.update(source="walk", version="w-smoke", total=data["total"] + change_total)
                    route.fulfill(response=response, json=data)
                return handle

            for change_total, requests, label in ((0, 2, "goes on"), (1, 3, "starts over")):
                scans.clear()
                page.route("**/api/timeline/scan*", fake_first_page(change_total))
                # (wait_for_timeout, not sleep: route handlers only run while Playwright waits)
                step(f"timeline: walk then index, {label}", lambda: (
                    page.evaluate("timelineConfirmFolders()"), page.wait_for_timeout(2500)), 0)
                page.unroute("**/api/timeline/scan*")
                expect(len(scans) == requests and page.query_selector(".tl-photo-card"),
                       f"timeline: {len(scans)} scan requests (expected {requests}) when the source switched")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(workdir, ignore_errors=True)

    if problems:
        print("\nFAILED:")
        for problem in problems:
            print("  -", problem)
        return 1
    print("\nSMOKE OK: no JavaScript errors, console errors or failed API calls")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    engine = next((a for a in args if a in ("webkit", "chromium", "firefox")), "webkit")
    sys.exit(run(engine, headed="--headed" in args))
