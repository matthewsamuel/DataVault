"""
Browser smoke test: clicks through every tab of the dashboard in a real WebKit (the
engine of Safari) and fails on any JavaScript error, console error or failed API call.

It never touches a real archive: it builds a throwaway test vault in a temp folder,
starts its own server on a free port (DATAVAULT_TESTING=1, DATAVAULT_CONFIG pointing at
that vault) and stops it again at the end.

It also drives the app with the keyboard (tabs, the shortcut overview, the Files grid and the
viewer, Tab staying inside every window, focus going back where it was, Keurer keys that never
act from the theme menu or the search field), moves a file and puts it back from the
Herstelcentrum, checks Instellingen and the storage window, and runs a11y_checks.js on every screen in the light and the dark theme:
all visible text must reach WCAG AA contrast and every control must have a name. On the way
it saves a screenshot of every screen in both themes (for a look at the design), in a temp
folder it prints at the end, or in $DATAVAULT_SMOKE_SHOTS when that is set.

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
VIEWS = ["home", "viewer", "duplicates", "spotify", "shows", "keurer", "timeline", "recovery", "settings", "albums"]
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

            # Files starts at the top folders of the archive (never a blank page); walking down
            # through the grid keeps the folder tree in step, and 'Archief' in the breadcrumb goes back
            def grid_folders():
                return page.evaluate("[...document.querySelectorAll('#media-grid .folder-card-name')].map(e => e.textContent)")

            step("files: the archive root", lambda: page.click(".top-nav [data-view='viewer']"), 0.6)
            if "RAW" not in grid_folders() or page.text_content("#breadcrumb").strip() != "Archief":
                problems.append(f"files: the first page shows {grid_folders()} instead of the archive folders")
            step("files: RAW and misc from the grid", lambda: (
                page.click("#media-grid .folder-card[aria-label='Map RAW']"), page.wait_for_timeout(600),
                page.click("#media-grid .folder-card[aria-label='Map misc']")), 0.6)
            selected = page.evaluate("(() => { const s = document.querySelector('#folder-tree .folder-item.selected');"
                                     " return s ? s.dataset.path : ''; })()")
            if not selected.endswith("/RAW/misc"):
                problems.append(f"files: the folder tree marks {selected!r}, not the folder opened from the grid")
            step("files: 'Archief' in the breadcrumb", lambda: page.click("#breadcrumb .breadcrumb-root"), 0.6)
            if "RAW" not in grid_folders() or page.evaluate("currentFolder") != "":
                problems.append("files: 'Archief' in the breadcrumb did not go back to the archive folders")
            # 'Archief' while a folder is still loading: the breadcrumb and the tree follow the
            # last choice (the archive root), not the folder that was still on its way
            step("files: 'Archief' while a folder still loads", lambda: page.evaluate(
                f"selectFolder({json.dumps(str(vault / 'RAW' / '2019' / 'zomer vakantie'))}); showArchiveRoot()"), 0.8)
            superseded = page.evaluate("[document.getElementById('breadcrumb').textContent.trim(), currentFolder,"
                                       " document.querySelectorAll('#folder-tree .folder-item.selected').length]")
            if superseded != ["Archief", "", 0] or "RAW" not in grid_folders():
                problems.append(f"files: a folder that finished loading after 'Archief' took the breadcrumb or tree back ({superseded})")

            # One way to show a path everywhere (Keurer, Herstelcentrum): from the archive, long
            # paths shortened in the middle
            labels = page.evaluate(f"""[displayPath({json.dumps(str(vault))}), displayPath({json.dumps(str(vault / 'RAW' / '2019'))}),
                displayPath({json.dumps(str(vault / 'RAW' / 'a' / 'b' / 'c' / 'een heel lange mapnaam' / 'nog een lange map'))}, 40)]""")
            if labels[:2] != ["Archief", "RAW/2019"] or not (labels[2].startswith("RAW/…/") and len(labels[2]) <= 40):
                problems.append(f"files: displayPath gives {labels}")

            # Files: a folder with a subfolder, open a file, browse, move dialog, close
            folder = str(vault / "RAW" / "misc")
            step("viewer: open folder", lambda: (page.click(".nav-tab[data-view='viewer']"),
                                                 page.evaluate(f"selectFolder({json.dumps(folder)})")))
            # A photo tile shows its name on hover (burst shots look alike); list rows line up
            step("files: hover a photo", lambda: page.hover("#media-grid .media-card:not(.folder-card):not(.other-card)"), 0.4)
            hovered = page.evaluate("(() => { const s = getComputedStyle(document.querySelector('#media-grid .media-card:hover .media-card-name'));"
                                    " return s.display + ' ' + s.opacity; })()")
            if hovered != "block 1":
                problems.append(f"files: a photo tile shows no name on hover ({hovered})")
            page.evaluate("toggleView('list')")
            page.wait_for_timeout(300)
            columns = page.evaluate("""[...document.querySelectorAll('#media-grid .media-card')].map(c => {
                const icon = c.querySelector('.folder-card-icon, .media-thumbnail, .other-card-icon');
                const name = c.querySelector('.folder-card-name, .media-card-name');
                const text = name.getBoundingClientRect().left + parseFloat(getComputedStyle(name).paddingLeft);
                return Math.round(icon.getBoundingClientRect().left) + '/' + Math.round(text); })""")
            if len(set(columns)) != 1:
                problems.append(f"files: list rows do not line up (icon/name x: {sorted(set(columns))})")
            page.evaluate("toggleView('grid')")
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
            # The messages fill the window, the input sits at the bottom, the example questions come along
            chat = page.evaluate("""(() => {
                const w = document.querySelector('.chat-fullscreen-wrapper').getBoundingClientRect();
                const i = document.querySelector('#chat-fullscreen-modal .chat-input-container').getBoundingClientRect();
                const s = document.querySelector('#chat-fullscreen-modal #chat-suggestions');
                return {gap: Math.round(w.bottom - i.bottom), suggestions: !!(s && s.getClientRects().length)};
            })()""")
            if chat["gap"] > 40 or not chat["suggestions"]:
                problems.append(f"home: full-screen chat layout {chat} (input not at the bottom, or no example questions)")
            step("home: close full screen", lambda: page.keyboard.press("Escape"), 0.4)
            if not page.evaluate("!!document.querySelector('#chat-home-slot #chat-suggestions')"):
                problems.append("home: the example questions did not come back to the chat on Home")

            # Chat actions from the full-screen chat: the file opens above the chat while the chat
            # input keeps focus, Escape closes only the file, and open_folder shows the folder
            def expect(ok, message):
                if not ok:
                    problems.append(message)

            def expect_focus_trapped(overlay_id, name, presses=8):
                """Shift+Tab and Tab, one at a time: focus must stay on something inside the window.
                (Safari skips buttons on a native Tab, so a trap that leaves Tab to the browser leaks.)"""
                where = ("(() => { const a = document.activeElement; const o = document.getElementById("
                         + json.dumps(overlay_id) + "); return a && a !== document.body && o.contains(a) ? "
                         "'in' : 'OUT ' + (a ? a.tagName + '#' + a.id + '.' + a.className : 'none'); })()")
                for key in ["Shift+Tab"] * presses + ["Tab"] * presses:
                    page.keyboard.press(key)
                    at = page.evaluate(where)
                    if at != "in":
                        problems.append(f"{name}: {key} moved the focus out of the window ({at})")
                        return

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
            # A key combination reads as one shortcut: never broken over two lines (also at 1024 px)
            for width in (1400, 1024):
                page.set_viewport_size({"width": width, "height": 900})
                wrapped = page.evaluate("[...document.querySelectorAll('#shortcut-help dt')]"
                                        ".filter(dt => new Set([...dt.children].map(k => k.offsetTop)).size > 1)"
                                        ".map(dt => dt.textContent)")
                expect(not wrapped, f"keys: shortcut keys wrap at {width}px: {wrapped}")
            # In a low window the groups scroll and the footnote stays whole inside the panel
            page.set_viewport_size({"width": 1024, "height": 600})
            note = page.evaluate("(() => { const n = document.querySelector('#shortcut-help .shortcut-note').getBoundingClientRect();"
                                 " const p = document.querySelector('#shortcut-help .dv-overlay-panel').getBoundingClientRect();"
                                 " return [Math.round(n.bottom), Math.round(p.bottom)]; })()")
            expect(note[0] <= note[1], f"keys: the overview's footnote is cut off in a low window ({note})")
            page.set_viewport_size({"width": 1400, "height": 900})
            expect_focus_trapped("shortcut-help", "keys: shortcut overview")
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
            expect_focus_trapped("modal-viewer", "viewer")
            step("grid: Escape closes the viewer", lambda: page.keyboard.press("Escape"), 0.4)
            expect(page.evaluate("document.activeElement.dataset.gridIndex") == str(first_file),
                   f"viewer: focus did not go back to the card ({page.evaluate(active_id)})")

            # Move window: with a folder highlighted, Enter on Annuleren or × closes it and moves nothing
            moved_src, second_src = page.evaluate("[gridFiles()[0].path, gridFiles()[1].path]")
            move_open = "document.getElementById('modal-move-file').classList.contains('active')"
            # The folder the arrows are on is visible (ring) and is what the button would move to
            step("move: arrows highlight a folder", lambda: (
                page.evaluate("openMediaModal(gridFiles()[0])"), page.keyboard.press("m"),
                page.wait_for_timeout(400), page.keyboard.press("ArrowDown")), 0.4)
            marked = page.evaluate("""(() => {
                const node = document.querySelector('#move-folder-tree .move-folder-node.keyboard-focus');
                return node && { ring: getComputedStyle(node).outlineStyle, path: node.dataset.path,
                                 chosen: selectedMoveFolder, ready: !document.getElementById('btn-confirm-move').disabled };
            })()""")
            expect(marked and marked["ring"] != "none" and marked["path"] == marked["chosen"] and marked["ready"],
                   f"move: the highlighted folder is not visible or not the one the button moves to ({marked})")
            page.keyboard.press("Escape")
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            for name, button in (("Annuleren", "#modal-move-file .move-modal-footer .btn-secondary"),
                                 ("×", "#modal-move-file .modal-close")):
                step(f"move: Enter on {name}", lambda button=button: (
                    page.evaluate("openMediaModal(gridFiles()[0])"), page.keyboard.press("m"),
                    page.wait_for_timeout(400), page.keyboard.press("ArrowDown"),
                    page.focus(button), page.keyboard.press("Enter")), 0.8)
                expect(Path(moved_src).exists() and not page.evaluate(move_open),
                       f"move: Enter on {name} did not just close the window (moved: {not Path(moved_src).exists()})")
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)

            # Herstelcentrum: two moves in the viewer are listed and undone from there with the
            # keyboard; after each undo the focus stays in the list
            for src in (moved_src, second_src):
                step("recovery: move a file in the viewer", lambda src=src: (
                    page.evaluate(f"openMediaModal(gridFiles().find(f => f.path === {json.dumps(src)}))"),
                    page.keyboard.press("m"), page.wait_for_timeout(400),
                    page.evaluate(f"selectMoveDestination({json.dumps(str(vault / 'library'))})"),
                    page.click("#btn-confirm-move")), 1.2)
                expect(not Path(src).exists(), "recovery: the file was not moved")
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
            step("recovery: 8 opens the Herstelcentrum", lambda: page.keyboard.press("8"), 1.0)
            expect(page.locator(".recovery-entry").count() == 2, "recovery: no entry for each move")
            focused = "(document.activeElement.closest('.recovery-entry') || {dataset: {}}).dataset.key || document.activeElement.id"
            accept = lambda d: d.accept()
            page.on("dialog", accept)
            first_key = page.evaluate("document.querySelector('.recovery-entry').dataset.key")
            step("recovery: Enter on Terugzetten", lambda: (
                page.focus(".recovery-entry .recovery-undo"), page.keyboard.press("Enter")), 1.2)
            expect(Path(second_src).exists(), "recovery: the newest move did not go back")
            left_key = page.evaluate("document.querySelector('.recovery-entry').dataset.key")
            expect(left_key != first_key and page.evaluate(focused) == left_key,
                   f"recovery: focus not on the next entry after the undo ({page.evaluate(focused)})")
            step("recovery: Enter on the last Terugzetten", lambda: page.keyboard.press("Enter"), 1.2)
            expect(Path(moved_src).exists(), "recovery: the file did not go back")
            expect(page.is_visible("#recovery-list .empty-state"), "recovery: the list did not show its empty state")
            expect(page.evaluate("document.activeElement.id") == "recovery-title",
                   f"recovery: focus not on the title once the list is empty ({page.evaluate(active_id)})")
            page.remove_listener("dialog", accept)

            # Instellingen: every built-in part is there and the theme can be chosen there
            step("settings: 9 opens Instellingen", lambda: page.keyboard.press("9"), 1.0)
            for part in ("weergave", "taalmodel", "bibliotheek", "shows", "herstel", "over"):
                expect(page.is_visible(f"#settings-{part}"), f"settings: part {part} missing")
            expect(page.evaluate("getComputedStyle(document.querySelector('.nav-tab-settings.active'), '::after').content") != "none",
                   "settings: the gear has no active indicator")
            step("settings: dark theme", lambda: page.check("input[data-theme-choice='dark']"), 0.3)
            expect(page.evaluate("document.documentElement.dataset.theme") == "dark", "settings: theme not set")
            page.check("input[data-theme-choice='auto']")

            # Keurer: arrows in the theme menu move through the menu, not a file; typing a space
            # in the search does not open the preview; on the page itself the arrows still decide
            keurer_state = "[keurerIndex, keurerHistory.length, document.getElementById('modal-keurer-move')" \
                           ".classList.contains('active'), keurerPreviewOpen]"
            step("keurer: open a folder", lambda: (
                page.keyboard.press("6"),
                page.evaluate(f"keurerSelectedFolderForModal = {json.dumps(folder)}; keurerConfirmFolderSelection()"),
                page.wait_for_selector("#keurer-card-area .keurer-card", timeout=10000)), 0.8)
            before = page.evaluate(keurer_state)
            keurer_file = page.evaluate("keurerFileQueue[keurerIndex].path")
            step("keurer: theme menu with the keyboard", lambda: (
                page.focus("#theme-button"), page.keyboard.press("Enter")), 0.3)
            expect(page.evaluate("document.activeElement.classList.contains('theme-option')"),
                   "keurer: the theme menu did not take the focus")
            step("keurer: arrows in the theme menu", lambda: [
                page.keyboard.press(key) for key in ("ArrowRight", "ArrowDown", "ArrowUp", "ArrowLeft", "End", "Home")], 1.0)
            expect(page.evaluate(keurer_state) == before and Path(keurer_file).exists(),
                   f"keurer: arrows in the theme menu acted on the Keurer ({page.evaluate(keurer_state)} != {before})")
            step("keurer: Escape closes the theme menu", lambda: page.keyboard.press("Escape"), 0.3)
            expect(page.evaluate("document.activeElement.id") == "theme-button", "keurer: focus not back on the theme button")
            # Opened with the mouse, then arrows: the menu marks the focused option itself (Safari
            # shows no focus ring after a click)
            step("theme menu: click, then an arrow", lambda: (page.click("#theme-button"), page.keyboard.press("ArrowDown")), 0.2)
            expect(page.evaluate("document.getElementById('theme-dropdown').classList.contains('keyboard')"
                                 " && document.activeElement.classList.contains('theme-option')"),
                   "theme menu: arrows after a click do not mark the focused option")
            step("theme menu: close", lambda: page.keyboard.press("Escape"), 0.3)
            step("keurer: arrow on the theme button", lambda: page.keyboard.press("ArrowRight"), 0.8)
            expect(page.evaluate(keurer_state) == before and Path(keurer_file).exists(),
                   "keurer: an arrow on the theme button decided on a file")
            step("keurer: / and typing a space", lambda: (
                page.keyboard.press("/"), page.keyboard.type("a b")), 0.5)
            expect(page.input_value("#global-search") == "a b" and page.evaluate(keurer_state) == before,
                   f"keurer: typing in the search was taken as Keurer keys ({page.input_value('#global-search')!r})")
            expect(page.evaluate("getComputedStyle(document.querySelector('.search-enter')).display") != "none",
                   "search: no Enter hint while typing outside Bestanden")
            page.keyboard.press("Escape")
            page.evaluate("document.getElementById('global-search').value = ''; handleGlobalSearch('')")
            step("keurer: ArrowLeft on the page keeps", lambda: page.keyboard.press("ArrowLeft"), 1.0)
            after_keep = page.evaluate(keurer_state)
            expect(after_keep[1] == before[1] + 1, f"keurer: ArrowLeft on the page made no decision ({after_keep})")
            step("keurer: ArrowDown undoes it", lambda: page.keyboard.press("ArrowDown"), 1.0)
            undone = page.evaluate(keurer_state)
            expect(undone[1] == before[1] and Path(keurer_file).exists(),
                   f"keurer: ArrowDown did not undo the decision ({undone} vs {before})")
            # Only the Keurer tab passes arrows on: on another tab an arrow decides nothing.
            # (Undoing a keep leaves keurerIndex where it is, so compare with the state just before.)
            base = page.evaluate(keurer_state)
            current = page.evaluate("keurerFileQueue[keurerIndex].path")
            step("keurer: arrow on the Tijdlijn tab", lambda: (
                page.focus(".nav-tab[data-view='timeline']"), page.keyboard.press("ArrowRight")), 0.8)
            expect(page.evaluate(keurer_state) == base and Path(current).exists(),
                   f"keurer: an arrow on another tab decided on a file ({page.evaluate(keurer_state)} vs {base})")
            # Space on a Keurer button presses that button, it does not open the preview
            step("keurer: Space on Houden", lambda: (
                page.focus(".keurer-btn-keep"), page.keyboard.press("Space")), 1.0)
            pressed = page.evaluate(keurer_state)
            expect(pressed[1] == base[1] + 1 and not pressed[3],
                   f"keurer: Space on Houden did not keep ({pressed} vs {base})")
            step("keurer: undo the keep", lambda: page.keyboard.press("ArrowDown"), 1.0)
            expect(page.evaluate(keurer_state)[1] == base[1], "keurer: ArrowDown did not undo the keep")
            # Move window: a highlighted folder and Enter on Annuleren: closed, nothing moved
            base = page.evaluate(keurer_state)
            current = page.evaluate("keurerFileQueue[keurerIndex].path")
            step("keurer: Enter on Annuleren in the move window", lambda: (
                page.evaluate("document.activeElement.blur()"), page.keyboard.press("ArrowUp"),
                page.wait_for_selector("#keurer-move-folder-tree .folder-item", timeout=5000),
                page.keyboard.press("ArrowDown"),
                page.focus("#modal-keurer-move .modal-footer .btn-secondary"), page.keyboard.press("Enter")), 0.8)
            expect(page.evaluate(keurer_state) == base and Path(current).exists(),
                   f"keurer: Enter on Annuleren moved the file or left the window open ({page.evaluate(keurer_state)} vs {base})")

            # A folder with only subfolders: a calm 'nothing here' state, never 'fully reviewed'
            step("keurer: a folder without photos", lambda: (
                page.evaluate(f"keurerSelectedFolderForModal = {json.dumps(str(vault / 'RAW'))}; keurerConfirmFolderSelection()"),
                page.wait_for_selector("#keurer-card-area .keurer-state-card h3", timeout=10000)), 0.6)
            title = page.text_content("#keurer-card-area .keurer-state-card h3")
            expect(title == "Geen foto's of video's in deze map", f"keurer: a folder without photos says {title!r}")
            chip = page.text_content("#keurer-folder-display")
            expect(chip == "RAW", f"keurer: the folder chip shows {chip!r}, not the path from the archive")

            # Home: the 'op deze dag' photo opens with the keyboard
            step("home: 'op deze dag' photo", lambda: (
                page.evaluate("switchMainView('home')"),
                page.wait_for_selector(".otd-large-image.active", timeout=10000)), 0.3)
            step("home: Enter on the photo", lambda: (page.focus(".otd-large-image.active"), page.keyboard.press("Enter")), 0.8)
            expect(page.evaluate("isViewerOpen()"), "home: Enter on the 'op deze dag' photo did not open it")
            step("home: close the photo", lambda: page.keyboard.press("Escape"), 0.4)

            # Storage per folder: the list starts at the top (nothing cut off) and fills the width
            step("storage: open the window", lambda: (
                page.evaluate("switchMainView('home'); openStorageModal()"),
                page.wait_for_selector("#storage-breakdown-content", state="visible", timeout=10000)), 0.8)
            storage = page.evaluate("""(() => {
                const body = document.querySelector('#modal-storage-details .modal-body');
                const first = [...body.children].find(c => c.getClientRects().length);
                return { top: first.getBoundingClientRect().top - body.getBoundingClientRect().top,
                         width: first.getBoundingClientRect().width, room: body.clientWidth };
            })()""")
            expect(storage["top"] >= 0 and storage["width"] > storage["room"] * 0.8,
                   f"storage: the list is cut off or narrow ({storage})")
            step("storage: close", lambda: page.keyboard.press("Escape"), 0.3)

            # The search field searches the folder open in Bestanden: elsewhere Enter goes there
            step("search: Enter on Home opens Bestanden", lambda: (
                page.focus("#global-search"), page.keyboard.type("zzz"), page.keyboard.press("Enter")), 0.8)
            expect(page.evaluate(view_now) == "viewer" and page.input_value("#global-search") == "zzz"
                   and page.is_visible("#media-grid .empty-state .empty-state-action"),
                   f"search: Enter on Home did not search in Bestanden ({page.evaluate(view_now)})")
            step("search: Zoekopdracht wissen", lambda: page.click("#media-grid .empty-state-action"), 0.5)
            expect(page.input_value("#global-search") == "", "search: 'Zoekopdracht wissen' left the search")
            page.keyboard.press("Escape")

            # Albums: the library with filters (a virtualized grid that loads per page), smart albums
            # saved from the filters, an address that can be bookmarked, and 'op deze dag' per year
            # (the server indexes the vault by itself; each poll asks once more whether it is done)
            page.wait_for_function("fetchJSON(`${API_BASE}/api/library/status`).then(s => { window.__libReady = s.ready; })"
                                   " && window.__libReady === true", timeout=30000, polling=500)
            tiles = "document.querySelectorAll('#albums-content .albums-tile').length"
            step("albums: open", lambda: (page.keyboard.press("g"), page.keyboard.press("a"),
                                          page.wait_for_selector("#albums-content .albums-tile img", timeout=10000)), 0.6)
            total = page.evaluate("fetchJSON(`${API_BASE}/api/library/browse?limit=1`).then(d => d.total)")
            count = page.evaluate("document.querySelector('#albums-count .albums-count-number').textContent")
            expect(total > BIG_FILES and count == f"{total:,}".replace(",", "."),
                   f"albums: {count!r} shown, the library has {total}")
            expect(page.evaluate(tiles) < 300, f"albums: {page.evaluate(tiles)} tiles drawn on open")
            step("albums: scroll to the end", lambda: page.evaluate(
                "document.getElementById('albums-scroll').scrollTop = 1e7"), 1.2)
            end = page.evaluate("(() => { const t = [...document.querySelectorAll('#albums-content .albums-tile:not(.is-placeholder)')];"
                                " return [t.length, Math.max(...t.map(x => +x.dataset.index))]; })()")
            expect(end[0] < 300 and end[1] == total - 1, f"albums: at the end {end} (of {total})")
            page.evaluate("document.getElementById('albums-scroll').scrollTop = 0")
            page.wait_for_timeout(400)
            # Keyboard: arrows and Enter open the viewer; at the end of a page it loads the next one
            step("albums: arrows and Enter", lambda: (
                page.focus("#albums-content .albums-tile[tabindex='0']"), page.keyboard.press("ArrowRight"),
                page.keyboard.press("Enter")), 0.6)
            expect(page.evaluate("isViewerOpen() && viewer.index") == 1, "albums: Enter did not open the second tile")
            step("albums: viewer past the first page", lambda: page.evaluate(
                "viewer.index = viewer.items.length - 1; renderViewerFile(); nextFile()"), 1.2)
            expect(page.evaluate("viewer.index") == 200 and page.evaluate("viewer.items.length") >= 400,
                   f"albums: the viewer did not go on into the next page ({page.evaluate('[viewer.index, viewer.items.length]')})")
            step("albums: close the viewer", lambda: page.keyboard.press("Escape"), 0.4)
            expect(page.evaluate("document.activeElement.classList.contains('albums-tile')"),
                   "albums: focus did not go back to the grid after the viewer")
            # Filters: a kind pill and a year narrow the grid and end up in the address
            step("albums: only videos", lambda: page.click("#albums-kinds [data-kind='video']"), 0.8)
            videos = page.evaluate("fetchJSON(`${API_BASE}/api/library/browse?kind=video&limit=1`).then(d => d.total)")
            expect(page.evaluate("location.hash") == "#albums?kind=video" and page.evaluate(tiles) == videos,
                   f"albums: kind filter gave {page.evaluate('location.hash')} with {page.evaluate(tiles)} tiles (expected {videos})")
            step("albums: photos of 2019", lambda: (
                page.click("#albums-kinds [data-kind='video']"), page.click("#albums-kinds [data-kind='photo']"),
                page.select_option("#albums-when", "y:2019")), 0.8)
            expect(page.evaluate("location.hash") == "#albums?kind=photo&year=2019" and page.evaluate(tiles) == 4,
                   f"albums: photos of 2019 gave {page.evaluate('location.hash')} and {page.evaluate(tiles)} tiles")
            # Save as a smart album (Enter in the name field), then change it and delete it again
            step("albums: save as album", lambda: (page.click("#albums-save-new"),
                                                   page.wait_for_function("document.activeElement.id === 'album-name'")), 0.3)
            expect_focus_trapped("modal-album", "albums: save window")
            page.focus("#album-name")
            step("albums: Enter saves", lambda: (page.keyboard.press("Meta+A"), page.keyboard.type("Zomer smoke"),
                                                 page.keyboard.press("Enter")), 1.0)
            saved = page.evaluate("[location.hash, document.getElementById('albums-title-text').textContent,"
                                  " [...document.querySelectorAll('#albums-side-albums .albums-side-name')].map(e => e.textContent)]")
            expect(saved[0].startswith("#albums/album/") and saved[1] == "Zomer smoke" and "Zomer smoke" in saved[2],
                   f"albums: saving the album gave {saved}")
            step("albums: bookmark reload", lambda: (page.reload(), page.wait_for_load_state("networkidle"),
                                                     page.wait_for_selector("#albums-content .albums-tile img", timeout=10000)), 0.5)
            expect(page.evaluate("[currentMainView(), location.hash, document.getElementById('albums-title-text').textContent]")
                   == ["albums", saved[0], "Zomer smoke"], "albums: a bookmarked album did not open again")
            step("albums: change the album", lambda: page.click("#albums-kinds [data-kind='video']"), 0.8)
            expect(page.is_visible("#albums-save-changes") and "?" in page.evaluate("location.hash"),
                   "albums: a changed album offers no 'Wijzigingen bewaren'")
            step("albums: undo the change", lambda: page.click("#albums-header-actions .btn-ghost"), 0.8)
            step("albums: delete the album", lambda: (page.click("#albums-edit"), page.click("#album-delete"),
                                                      page.click("#album-delete")), 1.0)
            expect("Zomer smoke" not in page.evaluate("[...document.querySelectorAll('#albums-side-albums .albums-side-name')]"
                                                      ".map(e => e.textContent)"), "albums: the album was not deleted")
            step("albums: folder window", lambda: (page.click("#albums-folder-btn"),
                                                   page.wait_for_selector("#albums-folder-tree .folder-item", timeout=5000)), 0.3)
            expect_focus_trapped("modal-albums-folder", "albums: folder window")
            step("albums: close the folder window", lambda: page.keyboard.press("Escape"), 0.3)
            # Op deze dag: from Home, and for 14 July (the beach photos of 2019), per year
            step("albums: 'Alles bekijken' on Home", lambda: (page.evaluate("switchMainView('home')"),
                                                             page.click(".otd-see-all")), 0.8)
            expect(page.evaluate("[currentMainView(), location.hash]") == ["albums", "#albums/op-deze-dag"],
                   f"albums: 'Alles bekijken' opened {page.evaluate('location.hash')}")
            step("albums: 14 July", lambda: (page.evaluate("location.hash = '#albums/op-deze-dag/07-14'"),
                                             page.wait_for_selector(".albums-otd-year", timeout=5000)), 0.6)
            otd = page.evaluate("""(async () => {
                const d = await fetchJSON(`${API_BASE}/api/library/on-this-day?date=07-14`);
                return {api: d.years.map(g => [g.year, g.items.length]),
                        shown: [...document.querySelectorAll('.albums-otd-year')].map(s =>
                            [+s.querySelector('.albums-otd-year-number').textContent, s.querySelectorAll('.albums-tile').length]),
                        title: document.querySelector('.albums-otd-year-title span').textContent};
            })()""")
            expect(otd["shown"] == otd["api"] and otd["api"] and otd["api"][0][0] == 2019
                   and otd["title"].endswith("jaar geleden"), f"albums: op deze dag {otd}")
            step("albums: next day with the button", lambda: page.click("[aria-label='Volgende dag']"), 0.6)
            expect(page.evaluate("location.hash") == "#albums/op-deze-dag/07-15", "albums: the next day did not open")
            step("albums: op deze dag in the viewer", lambda: (
                page.evaluate("location.hash = '#albums/op-deze-dag/07-14'"),
                page.wait_for_selector(".albums-otd-year .albums-tile", timeout=5000),
                page.focus(".albums-otd-year .albums-tile[tabindex='0']"), page.keyboard.press("ArrowRight"),
                page.keyboard.press("Enter")), 0.6)
            expect(page.evaluate("isViewerOpen() && viewer.index === 1 && viewer.items.length") == sum(n for _, n in otd["api"]),
                   "albums: a photo of op deze dag did not open in the viewer with all its photos")
            step("albums: close the photo", lambda: page.keyboard.press("Escape"), 0.4)
            page.evaluate("albumsShow(null, {}, '-taken')")

            # Every screen in both themes: all visible text reaches WCAG AA, every control has a name,
            # and a screenshot to look at
            shots = Path(os.environ.get("DATAVAULT_SMOKE_SHOTS") or tempfile.mkdtemp(prefix="datavault-shots-"))
            shots.mkdir(parents=True, exist_ok=True)
            for theme in ("light", "dark"):
                page.evaluate(f"setTheme('{theme}')")
                for view in VIEWS:
                    page.evaluate(f"switchMainView('{view}')")
                    page.wait_for_timeout(700)
                    page.screenshot(path=str(shots / f"{view}_{theme}.png"))
                    page.add_script_tag(path=str(A11Y_CHECKS))
                    for fail in page.evaluate("__contrastFailures()"):
                        problems.append(f"contrast {theme} {view}: {fail['ratio']}:1 {fail['key']} ({fail['text']!r})")
                    if theme == "light":
                        for control in page.evaluate("__unnamedControls()"):
                            problems.append(f"unnamed control on {view}: {control}")
                # The Spotify windows (DNA and an explanation), 'op deze dag' and the album window too
                for name, opener, closer in (("spotify-dna", "switchMainView('spotify'); showDnaExplanation()", "closeDnaModal()"),
                                             ("spotify-uitleg", "switchMainView('spotify'); showInsightExplanation('record_day')",
                                              "closeInsightModal()"),
                                             ("albums-otd", "albumsOpenOnThisDay(7, 14)", "albumsShow(null, {})"),
                                             ("albums-album", "albumsShow(null, {kind: ['photo']}); "
                                              "setTimeout(() => document.getElementById('albums-save-new').click(), 300)",
                                              "albumsCloseDialog()")):
                    page.evaluate(opener)
                    page.wait_for_timeout(500)
                    page.screenshot(path=str(shots / f"{name}_{theme}.png"))
                    page.add_script_tag(path=str(A11Y_CHECKS))
                    for fail in page.evaluate("__contrastFailures()"):
                        problems.append(f"contrast {theme} {name}: {fail['ratio']}:1 {fail['key']} ({fail['text']!r})")
                    page.evaluate(closer)
            print(f"  ok      contrast and names checked on {len(VIEWS)} screens in 2 themes")
            print(f"          screenshots: {shots}")
            page.evaluate("setTheme('auto')")
            page.evaluate("switchMainView('home')")

            # Shows: Manage Sources and the Feeling Lucky slideshow
            step("shows: manage sources", lambda: (page.click(".nav-tab[data-view='shows']"),
                                                   page.click("text=Bronnen beheren")), 1.0)
            expect_focus_trapped("modal-show-settings", "shows: sources window")
            step("shows: close sources", lambda: page.evaluate("closeSettingsModal()"), 0.3)
            if page.query_selector(".shows-hero"):
                step("shows: slideshow", lambda: page.click(".shows-hero"), 1.5)
                # Title, details and progress bar have their own rows: the photo's row ends above them
                for height in (768, 900):
                    page.set_viewport_size({"width": 1024 if height == 768 else 1400, "height": height})
                    page.wait_for_timeout(300)
                    rows = page.evaluate("""(() => { const box = id => document.querySelector(id).getBoundingClientRect();
                        return [box('.player-controls-top').bottom, box('#slide-container').top,
                                box('#slide-container').bottom, box('.player-controls-bottom').top]; })()""")
                    expect(rows[0] <= rows[1] + 1 and rows[2] <= rows[3] + 1,
                           f"shows: slideshow chrome overlaps the photo at height {height} ({rows})")
                page.set_viewport_size({"width": 1400, "height": 900})
                expect((page.get_attribute(".elo-rating-badge", "aria-label", timeout=2000) or "").startswith("Score "),
                       "shows: the score badge has no name")
                step("shows: next slide", lambda: page.keyboard.press("ArrowRight"), 0.8)
                step("shows: close slideshow", lambda: page.keyboard.press("Escape"), 0.8)

            # Spotify: both top lists show everything the export has (never cut to the shorter one),
            # the shorter one ends with a quiet closing line, and the stat labels in a row line up
            step("spotify: top lists", lambda: (page.click(".nav-tab[data-view='spotify']"), page.wait_for_timeout(1200)), 0)
            lists = page.evaluate("""(async () => {
                const year = document.getElementById('spotify-year-select').value;
                const d = await fetchJSON(`${API_BASE}/api/spotify/stats?year=${encodeURIComponent(year)}`);
                const count = id => document.querySelectorAll(`#${id} .spotify-list-item`).length;
                const labels = [...document.querySelectorAll('.spotify-stats-grid')].map(grid =>
                    new Set([...grid.querySelectorAll('.stat-label')].map(l => Math.round(l.getBoundingClientRect().bottom))).size);
                return {api: [d.top_artists.length, d.top_tracks.length], shown: [count('top-artists-list'), count('top-tracks-list')],
                        ends: document.querySelectorAll('.spotify-list-end').length, labelRows: labels};
            })()""")
            expect(lists["api"] == lists["shown"] and lists["ends"] == (lists["api"][0] != lists["api"][1])
                   and all(n == 1 for n in lists["labelRows"]), f"spotify: top lists or stat labels ({lists})")

            # Spotify year switch, timeline back again
            step("spotify: year", lambda: (page.click(".nav-tab[data-view='spotify']"),
                                           page.select_option("#spotify-year-select", index=page.evaluate(
                                               "document.getElementById('spotify-year-select').options.length - 1"))), 1.5)
            step("timeline again", lambda: page.click(".nav-tab[data-view='timeline']"), 1.0)

            # The job dock floats over the page: the page keeps that much room free at the bottom
            dock = page.evaluate("""(() => { const d = document.getElementById('job-dock');
                return [d.hidden ? 0 : Math.round(d.getBoundingClientRect().height),
                        parseFloat(getComputedStyle(document.querySelector('.main-content')).paddingBottom)]; })()""")
            expect(dock[1] >= dock[0], f"job dock: {dock[0]} px high, but only {dock[1]} px kept free under the page")
            # ... except on pages that are exactly as high as the window: they keep it free inside
            for view in ("keurer", "duplicates"):
                page.evaluate(f"switchMainView('{view}')")
                page.wait_for_timeout(300)
                extra = page.evaluate("document.documentElement.scrollHeight - innerHeight")
                expect(extra <= 1, f"job dock: the {view} page scrolls the window by {extra} px")

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

            # A run that finished with read errors stays in view: the dock does not fold away, the
            # run is not put under 'eerder klaar', and an incomplete library pass is named too.
            # (Made-up jobs, drawn and read back in one go before the next poll redraws the dock.)
            dock = page.evaluate("""(() => {
                const saved = { jobs: jobDock.jobs, library: jobDock.library };
                const now = Date.now() / 1000;
                const job = (id, summary, ago) => ({ id, type: 'dup_scan', label: 'Duplicaten zoeken', status: 'done',
                    target: 'RAW', processed: 3, total: 3, params: {}, message: 'Klaar', summary,
                    finished_at: now - ago, updated_at: now - ago });
                jobDock.jobs = [job(-1, { complete: true, errors: 0 }, 1), job(-2, { complete: false, errors: 1 }, 5),
                                job(-3, { complete: true, errors: 0 }, 9)];
                jobDock.library = Object.assign({}, saved.library, { ready: true, last_pass_at: now, last_complete_at: now - 60 });
                renderJobDock();
                const el = document.getElementById('job-dock');
                const folded = el.querySelector('.job-folded');
                const result = {
                    collapsed: el.classList.contains('collapsed'),
                    incomplete: [...el.querySelectorAll('.job-item.job-incomplete')].map(i => i.dataset.id),
                    title: el.querySelector('.job-dock-title').textContent,
                    folded: folded ? folded.textContent : '',
                    library: (el.querySelector('.job-library-warning') || {}).textContent || '',
                };
                jobDock.jobs = saved.jobs; jobDock.library = saved.library;
                renderJobDock();
                return result;
            })()""")
            expect(not dock["collapsed"] and dock["incomplete"] == ["-2"] and "1 onvolledig" in dock["title"]
                   and "2 eerder" not in dock["folded"] and "onvolledig" in dock["library"],
                   f"job dock: an incomplete run is folded away or not marked: {dock}")

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

            # A narrow window (Split View): the menu scrolls instead of running under the search
            # field, and timeline tiles stay square and in line with the month headers
            page.evaluate("document.getElementById('timeline-scroll').scrollTop = 0")
            for width in (700, 820):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(600)
                narrow = page.evaluate("""(() => {
                    const card = document.querySelector('.tl-photo-card').getBoundingClientRect();
                    const header = document.querySelector('.tl-group-header');
                    const headerX = header.getBoundingClientRect().left + parseFloat(getComputedStyle(header).paddingLeft);
                    // Where the menu items end on screen: the box when it clips (scrolls), else its content
                    const tabs = document.querySelector('.nav-tabs');
                    const box = tabs.getBoundingClientRect();
                    const clips = getComputedStyle(tabs).overflowX !== 'visible';
                    return {tile: [Math.round(card.width), Math.round(card.height)], cardX: Math.round(card.left),
                            headerX: Math.round(headerX),
                            navRight: Math.round(clips ? box.right : box.left + tabs.scrollWidth),
                            searchLeft: Math.round(document.querySelector('.nav-search').getBoundingClientRect().left),
                            scrollsSideways: document.documentElement.scrollWidth > innerWidth};
                })()""")
                expect(abs(narrow["tile"][0] - narrow["tile"][1]) <= 1 and narrow["cardX"] == narrow["headerX"]
                       and narrow["navRight"] <= narrow["searchLeft"] and not narrow["scrollsSideways"],
                       f"narrow window {width}px: {narrow}")
            # From 1024 px up the whole menu fits, with at least 24 px before the search field
            for width in (1024, 1150, 1240, 1400):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(200)
                gap = page.evaluate("""(() => { const tabs = [...document.querySelectorAll('.top-nav .nav-tab')];
                    const t = document.querySelector('.nav-tabs');
                    return [Math.round(document.querySelector('.nav-search').getBoundingClientRect().left
                                       - tabs[tabs.length - 1].getBoundingClientRect().right), t.scrollWidth > t.clientWidth + 1]; })()""")
                expect(gap[0] >= 24 and not gap[1], f"menu at {width}px: {gap[0]} px before the search field, scrolls: {gap[1]}")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.wait_for_timeout(400)

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

            # Home with the archive unplugged: the 'op deze dag' cards show a calm state (never text
            # inside the spinning circle) and the storage tile shows '–', not a sentence
            unplugged = vault.with_name("vault-unplugged")
            vault.rename(unplugged)
            try:
                seen = len(problems)
                page.reload()
                page.wait_for_load_state("networkidle")
                page.evaluate("switchMainView('home')")
                page.wait_for_selector("#spotify-otd-content .empty-state", timeout=10000)
                page.wait_for_selector("#photo-otd-content .empty-state", timeout=10000)
                page.wait_for_timeout(500)
                home = page.evaluate("""(() => ({
                    spinners: [...document.querySelectorAll('#section-home .loading-spinner')]
                        .filter(s => s.getClientRects().length || s.textContent.trim()).length,
                    storage: document.getElementById('storage-used-text').textContent,
                }))()""")
                expect(home == {"spinners": 0, "storage": "–"}, f"home unplugged: {home}")
                # The three library tiles say the same thing, not one tile apart
                metas = page.evaluate("['home-stat-folders', 'home-stat-videos-meta', 'home-stat-audio-meta']"
                                      ".map(id => document.getElementById(id).textContent)")
                expect(len(set(metas)) == 1 and "niet bereikbaar" in metas[0], f"home unplugged: tile lines {metas}")
                page.screenshot(path=str(shots / "home_unplugged.png"))
                # Bestanden: not a blank page and an empty folder column, but say what is going on
                page.evaluate("switchMainView('viewer')")
                try:
                    page.wait_for_selector("#media-grid .empty-state", timeout=10000)
                except PlaywrightError:
                    pass   # reported below
                files = page.evaluate("[(document.querySelector('#media-grid .empty-state-title') || {}).textContent || '',"
                                      " document.getElementById('folder-tree').textContent.trim()]")
                expect(files == ["Archief niet bereikbaar", "Archief niet bereikbaar"], f"files unplugged: {files}")
                page.screenshot(path=str(shots / "files_unplugged.png"))
                # (the 503s of an unplugged archive are expected here)
                problems[seen:] = [p for p in problems[seen:] if "503" not in p and "Failed to load resource" not in p]
                print(f"  {'ok' if len(problems) == seen else 'PROBLEM':7} home: archive unplugged")
            finally:
                unplugged.rename(vault)

            # An archive without any folder: 'Het archief is leeg', not 'Kies een map'
            page.route("**/api/folders", lambda route: route.fulfill(json={"folders": []}))
            page.reload()
            page.wait_for_load_state("networkidle")
            page.evaluate("switchMainView('viewer')")
            try:
                page.wait_for_selector("#media-grid .empty-state", timeout=10000)
            except PlaywrightError:
                pass   # reported below
            empty = page.evaluate("(document.querySelector('#media-grid .empty-state-title') || {}).textContent")
            expect(empty == "Het archief is leeg", f"files: an empty archive says {empty!r}")
            page.unroute("**/api/folders")
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
