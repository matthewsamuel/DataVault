"""
Browser smoke test: clicks through every tab of the dashboard in a real WebKit (the
engine of Safari) and fails on any JavaScript error, console error or failed API call.

It never touches a real archive: it builds a throwaway test vault in a temp folder,
starts its own server on a free port (DATAVAULT_TESTING=1, DATAVAULT_CONFIG pointing at
that vault) and stops it again at the end.

It also drives the app with the keyboard (tabs, the shortcut overview, the Files grid and the
viewer, Tab staying inside every window, focus going back where it was, Keurer keys that never
act from the theme menu or the search field), moves a file and puts it back from the
Herstelcentrum, compares photos on the Beste uit reeks tab and keeps the best of a series
with the keyboard (and undoes that), checks Instellingen and the storage window, walks through
DataGatherer (help, the menu, the connect window, reading the existing Spotify export, the network log),
makes a backup from Instellingen, compares photos in 'Welke is mooier?' (keys, undo, leaderboard), and runs
a11y_checks.js on every screen in the light and the dark theme:
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
import re
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


def server_python() -> str:
    """
    The Python that runs the test server: $DATAVAULT_PYTHON, else this checkout's venv,
    else the venv of the main checkout when this is a git worktree (worktrees share it),
    else the Python running this script.
    """
    if os.environ.get("DATAVAULT_PYTHON"):
        return os.environ["DATAVAULT_PYTHON"]
    candidates = [CODE_DIR / "venv" / "bin" / "python"]
    try:
        common = subprocess.run(["git", "-C", str(CODE_DIR), "rev-parse", "--path-format=absolute",
                                 "--git-common-dir"], capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            candidates.append(Path(common).parent / "code" / "venv" / "bin" / "python")
    except (OSError, subprocess.SubprocessError):
        pass
    return str(next((c for c in candidates if c.exists()), Path(sys.executable)))
BIG_FOLDER = "Groot"
BIG_FILES = 1500
MAX_THUMBS_ON_OPEN = 100
VIEWS = ["home", "viewer", "duplicates", "spotify", "shows", "keurer", "timeline", "recovery", "settings", "albums", "datagatherer", "map"]
A11Y_CHECKS = Path(__file__).with_name("a11y_checks.js")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(workdir: Path):
    vault = workdir / "vault"
    python = server_python()
    print(f"server python: {python}")
    subprocess.run([python, str(CODE_DIR / "tests/fixtures/vault_builder.py"), str(vault)],
                   check=True, stdout=subprocess.DEVNULL)
    # No search model, whatever is installed in code/models on this machine: the smoke checks the
    # 'not installed' screens of Slim zoeken
    cfg_path = workdir / "vault_config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["models_folder"] = str(workdir / "geen-modellen")
    cfg_path.write_text(json.dumps(cfg))
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
            # Pasha's tools: a result strip with a link, and a confirmation card that does nothing
            # until 'Uitvoeren' (the server is asked to confirm a forged id: a clear message, no move)
            step("chat: tool result and confirmation card", lambda: (
                page.click(".nav-tab[data-view='home']"),
                page.evaluate("""(photo) => {
                    chatRenderToolEvent({tool: 'library_filter', result: {title: '3 bestanden: je selectie', total: 3,
                        items: [{path: photo, name: 'foto.jpg'}], link: {view: 'albums', filters: {year: 2019}, label: 'Alles bekijken'}}});
                    chatRenderToolEvent({tool: 'propose_move', pending: {id: 'nep', kind: 'move', title: '1 bestand verplaatsen naar library',
                        count: 1, expires_in: 600, items: [{name: 'foto.jpg', to: 'library'}], more: 0}});
                }""", photo)), 0.5)
            expect(page.is_visible("#chat-messages .chat-confirm .btn-primary") and page.is_visible("#chat-messages .chat-strip img"),
                   "chat tools: result strip or confirmation card is not visible")
            seen = len(problems)
            page.click("#chat-messages .chat-confirm .btn-primary")
            page.wait_for_timeout(600)
            # (the browser logs the expected 404 of the forged id as a console error)
            problems[seen:] = [p for p in problems[seen:] if "404" not in p]
            expect("verlopen" in page.inner_text("#chat-messages .chat-confirm-status"),
                   "chat tools: a forged id did not give the expired message")
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
            for part in ("weergave", "taalmodel", "bibliotheek", "shows", "herstel", "backups", "over"):
                expect(page.is_visible(f"#settings-{part}"), f"settings: part {part} missing")
            # Back-ups: 'Nu back-uppen' makes one (checked and compressed, next to the vault in this
            # test), the part then shows it, and the focus stays on the button
            # ... in place: the page does not jump while the part is drawn again
            button_top = "document.getElementById('settings-backup-now').getBoundingClientRect().top"
            page.evaluate("document.getElementById('settings-backup-now').scrollIntoView({block: 'center'})")
            top_before = page.evaluate(button_top)
            try:
                step("settings: Nu back-uppen", lambda: (
                    page.focus("#settings-backup-now"), page.keyboard.press("Enter"),
                    page.wait_for_selector("#settings-backups .backup-kind", timeout=15000)), 0.5)
            except PlaywrightError:
                problems.append("settings: 'Nu back-uppen' did not show the new backup")
            top_after = page.evaluate(button_top)
            expect(abs(top_after - top_before) < 4,
                   f"settings: 'Nu back-uppen' moved on screen ({top_before:.0f} -> {top_after:.0f} px)")
            made = sorted(vault.parent.glob("vault_backups/datavault-*-handmatig.db.gz"))
            expect(len(made) == 1, f"settings: expected one backup file, found {[m.name for m in made]}")
            expect("vandaag" in (page.text_content("#settings-backups") or ""), "settings: the last backup time is not shown")
            expect(page.evaluate("document.activeElement.id") == "settings-backup-now",
                   f"settings: focus left 'Nu back-uppen' ({page.evaluate('document.activeElement.id')})")
            expect(page.is_visible("#settings-backups .backup-restore-chevron"),
                   "settings: the restore steps have no chevron that shows they open")
            page.evaluate("document.querySelector('#settings-backups .backup-restore').open = true")
            restore_text = page.text_content("#settings-backups .backup-restore") or ""
            expect("restore_backup.py" in restore_text, "settings: the restore steps do not show the command")
            # The smoke server runs with its own config: the command must name it, never code/config.json
            expect("--config" in restore_text and "vault_config.json" in restore_text,
                   "settings: the restore command does not name the config the app runs with")
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
            # (the fixtures only fall on 27 October; a fixed answer keeps this check date-independent)
            memory = vault / "RAW/2020/Café Ümlaut/koffie é à ü.jpg"
            page.route("**/api/stats/photo-on-this-day*", lambda route: route.fulfill(json={"complete": True, "photos": [{
                "path": str(memory), "name": memory.name, "type": "image", "timestamp": 1603792800.0,
                "year": 2020, "date_source": "exif"}]}))
            step("home: 'op deze dag' photo", lambda: (
                page.evaluate("switchMainView('home'); loadPhotoOtdWidget()"),
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

            # Zoeken (search.js): Enter in the search field opens the search window for the whole
            # library. Without the model: file names work, Slim zoeken explains how to install it.
            step("search: Enter on Home opens the search window", lambda: (
                page.focus("#global-search"), page.keyboard.type("zzz"), page.keyboard.press("Enter"),
                page.wait_for_selector("#search-overlay.active", timeout=5000)), 0.8)
            expect(page.input_value("#search-input") == "zzz" and page.evaluate(view_now) == "home",
                   f"search: the window did not open with the query ({page.input_value('#search-input')!r})")
            expect_focus_trapped("search-overlay", "search: window")
            step("search: by file name", lambda: (
                page.click(".search-mode[data-mode='filename']"), page.fill("#search-input", "burst"),
                page.keyboard.press("Enter"), page.wait_for_selector(".search-result", timeout=5000)), 0.6)
            expect(page.locator(".search-result").count() == 5,
                   f"search: 'burst' found {page.locator('.search-result').count()} files, not 5")
            step("search: arrow down to the results, Enter opens the viewer", lambda: (
                page.focus("#search-input"), page.keyboard.press("ArrowDown"), page.keyboard.press("ArrowRight"),
                page.keyboard.press("Enter")), 0.8)
            expect(page.evaluate("topOverlay() && topOverlay().id") == "modal-viewer"
                   and page.evaluate("viewerFile() && viewerFile().name") == "burst_03.jpg",
                   f"search: Enter did not open the second result ({page.evaluate('viewerFile() && viewerFile().name')})")
            step("search: Escape closes the viewer first", lambda: page.keyboard.press("Escape"), 0.4)
            expect(page.is_visible("#search-overlay") and not page.is_visible("#modal-viewer"),
                   "search: Escape did not go back from the viewer to the results")
            expect(page.evaluate("document.activeElement.dataset.index") == "1",
                   f"search: focus did not go back to the result ({page.evaluate('document.activeElement.className')})")
            step("search: Slim zoeken without the model", lambda: (
                page.click(".search-mode[data-mode='semantic']"),
                page.wait_for_selector("#search-more .empty-state", timeout=5000)), 0.5)
            title = page.evaluate("(document.querySelector('#search-more .empty-state-title') || {}).textContent")
            expect(title == "Slim zoeken is nog niet geïnstalleerd", f"search: without the model it says {title!r}")
            step("search: Naar Instellingen", lambda: page.click("#search-more .empty-state-action"), 0.8)
            expect(page.evaluate(view_now) == "settings" and not page.is_visible("#search-overlay")
                   and page.evaluate("document.activeElement.id") == "settings-search-install",
                   f"search: 'Naar Instellingen' went to {page.evaluate(view_now)}, "
                   f"focus {page.evaluate('document.activeElement.id')}")
            install_text = page.evaluate("(document.getElementById('settings-search-install') || {}).textContent || ''")
            expect("Zoekmodel installeren" in install_text and "MB" in install_text,
                   f"settings: install button says {install_text!r}")
            # The install button asks first, with where it downloads from; 'Annuleren' downloads nothing
            asked = []
            dismiss = lambda d: (asked.append(d.message), d.dismiss())  # noqa: E731
            page.on("dialog", dismiss)
            step("settings: install asks first", lambda: page.click("#settings-search-install"), 0.6)
            page.remove_listener("dialog", dismiss)
            jobs_now = page.evaluate("fetch('/api/jobs?type=model_download').then(r => r.json()).then(d => d.jobs.length)")
            expect(asked and "huggingface.co" in asked[0] and jobs_now == 0,
                   f"settings: install did not ask, or started anyway ({asked}, {jobs_now} jobs)")

            # Closing the search window gives the focus back to the field that opened it
            step("search: open and close with the keyboard", lambda: (
                page.focus("#global-search"), page.keyboard.type("zzz"), page.keyboard.press("Enter"),
                page.wait_for_selector("#search-overlay.active", timeout=5000), page.keyboard.press("Escape"),
                page.wait_for_timeout(300)), 0.3)
            expect(page.evaluate("document.activeElement.id") == "global-search",
                   f"search: Escape left the focus on {page.evaluate('document.activeElement.id || document.activeElement.tagName')}")
            page.evaluate("document.getElementById('global-search').value = ''; document.activeElement.blur()")

            # Slim zoeken with results (the model is not in the smoke vault: the answers are faked
            # with real files, so the results grid, paging hint and similar mode are drawn for real)
            files = page.evaluate("""fetch('/api/library/media?kind=photo&limit=1000').then(r => r.json())
                .then(d => d.items.filter(i => !i.path.includes('/Groot/')).slice(0, 24))""")
            expect(len(files) >= 10, f"search: only {len(files)} photos to fake results with")
            fake_status = {"model": {"id": "x", "label": "SigLIP 2 (meertalig)", "licence": "Apache-2.0", "sources": [],
                                     "size": 971000000, "folder": "/x", "installed": True, "homepage": ""},
                           "ready": True, "library_ready": True, "total": len(files), "embedded": len(files) - 3,
                           "pending": 3, "undecodable": 0, "device": "gpu", "per_second": 70, "job": None, "download": None}

            def fake_answer(route, items):
                route.fulfill(json={"total": len(items) - 4, "ranked": len(items), "offset": 0, "limit": 60,
                                    "next_offset": None, "items": [dict(i, score=0.2) for i in items[:-4]],
                                    "took_ms": 38.0, "library_ready": True, "indexed": len(files) - 3, "pending": 3})

            fakes = {re.compile(r"/api/search/status"): lambda route: route.fulfill(json=fake_status),
                     re.compile(r"/api/search/semantic\?"): lambda route: fake_answer(route, files),
                     re.compile(r"/api/search/similar\?"): lambda route: fake_answer(route, files[1:])}
            for pattern, handler in fakes.items():
                page.route(pattern, handler)
            page.evaluate("switchMainView('home')")
            step("search: s opens the window", lambda: (page.focus("body"), page.keyboard.press("s"),
                                                       page.wait_for_selector("#search-overlay.active", timeout=5000)), 0.5)
            step("search: Slim zoeken shows results", lambda: (
                page.click(".search-mode[data-mode='semantic']"), page.fill("#search-input", "hond op het strand"),
                page.keyboard.press("Enter"), page.wait_for_timeout(800)), 0.3)
            status_text = page.evaluate("document.getElementById('search-status').textContent")
            expect(page.locator(".search-result").count() == len(files) - 4 and "goede resultaten" in status_text
                   and "Nog bezig" in status_text and page.is_visible("#search-show-weak"),
                   f"search: results not drawn as expected ({page.locator('.search-result').count()}, {status_text!r})")
            def open_similar():
                try:
                    page.click(".search-result >> nth=0", timeout=5000)
                    page.wait_for_timeout(600)
                    page.keyboard.press("v")
                    page.wait_for_selector("#search-similar:not([hidden])", timeout=5000)
                except PlaywrightError as e:
                    problems.append(f"search: Vergelijkbaar: {str(e).splitlines()[0]}")
            step("search: open a result, then Vergelijkbaar (v)", open_similar, 0.6)
            expect(not page.is_visible("#modal-viewer") and page.locator(".search-result").count() == len(files) - 5
                   and page.evaluate("document.getElementById('search-title').textContent") == "Vergelijkbare foto’s",
                   "search: Vergelijkbaar did not show the similar photos")
            for theme in ("light", "dark"):
                page.evaluate(f"setTheme('{theme}')")
                page.wait_for_timeout(400)
                if os.environ.get("DATAVAULT_SMOKE_SHOTS"):
                    page.screenshot(path=str(Path(os.environ["DATAVAULT_SMOKE_SHOTS"]) / f"search_{theme}.png"))
                page.add_script_tag(path=str(A11Y_CHECKS))
                for fail in page.evaluate("__contrastFailures()"):
                    problems.append(f"contrast {theme} search: {fail['ratio']}:1 {fail['key']} ({fail['text']!r})")
                for control in page.evaluate("__unnamedControls()"):
                    problems.append(f"unnamed control in search ({theme}): {control}")
            page.evaluate("setTheme('auto')")
            step("search: Escape closes the window", lambda: page.keyboard.press("Escape"), 0.4)
            expect(not page.is_visible("#search-overlay"), "search: Escape left the window open")
            for pattern in fakes:
                page.unroute(pattern)
            page.evaluate("document.getElementById('global-search').value = ''; searchState.status = null;"
                          " document.activeElement.blur()")    # the next steps type shortcuts, not into the field
            print("  ok      search window: contrast and names checked in 2 themes")

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
            # Export (step 3.7): select with space and shift-click, 'alles selecteren', export to a temp folder
            export_dir = Path(tempfile.mkdtemp(prefix="dv-export-smoke-")) / "uit"
            step("export: space selects", lambda: (page.focus("#albums-content .albums-tile[tabindex='0']"),
                                                   page.keyboard.press("Space")), 0.3)
            expect(page.evaluate("document.querySelectorAll('#albums-content .albums-tile.selected').length") == 1
                   and page.is_visible("#export-bar"), "export: space did not select the focused tile")
            step("export: shift-click selects a range", lambda: page.click("#albums-content .albums-tile[data-index='2']",
                                                                           modifiers=["Shift"]), 0.3)
            expect(page.evaluate("DVExport.count") == 3, f"export: shift-click gave {page.evaluate('DVExport.count')} selected, expected 3")
            step("export: select all of the filter", lambda: page.click("#export-select-all"), 1.0)
            expect(page.evaluate("DVExport.count") == 4, "export: 'alles selecteren' did not select all 4 photos")
            step("export: open the window", lambda: (page.click("#export-open"),
                                                     page.wait_for_selector("#export-status.is-ok", timeout=8000)), 0.3)
            expect_focus_trapped("modal-export", "export: window")
            step("export: refuses a destination in the archive", lambda: (
                page.fill("#export-base", str(vault / "RAW")), page.wait_for_selector("#export-status.is-error", timeout=5000)), 0.2)
            expect(not page.is_enabled("#export-start"), "export: a destination inside the archive can be started")
            step("export: start", lambda: (page.fill("#export-base", str(export_dir.parent)), page.fill("#export-name", export_dir.name),
                                           page.wait_for_selector("#export-status.is-ok", timeout=5000),
                                           page.click("#export-start")), 2.0)
            copied = sorted(p.name for p in export_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg")) if export_dir.exists() else []
            expect(len(copied) == 4 and (export_dir / "manifest.csv").exists(), f"export: copied {copied}")
            step("export: best of a period window", lambda: (page.keyboard.press("Escape"), page.click("#albums-best"),
                                                             page.click("#best-preview"),
                                                             page.wait_for_selector("#best-summary", timeout=8000)), 0.3)
            expect_focus_trapped("modal-best", "export: best window")
            step("export: close", lambda: page.keyboard.press("Escape"), 0.3)
            # A finished export stays in the job dock (with 'Toon in Finder')
            expect(page.evaluate("jobDock.jobs.some(j => j.type === 'export' && j.status === 'done' && j.summary.destination)"),
                   "export: the finished job has no destination for 'Toon in Finder'")
            shutil.rmtree(export_dir.parent, ignore_errors=True)

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
            # PageDown/PageUp in op deze dag (as in the grid): never off to the page
            step("albums: PageDown in op deze dag", lambda: (
                page.focus(".albums-otd-year .albums-tile[tabindex='0']"), page.keyboard.press("End"),
                page.keyboard.press("PageUp"), page.wait_for_timeout(200)), 0.2)
            expect(page.evaluate("document.activeElement.classList.contains('albums-tile') && document.activeElement.dataset.index") == "0",
                   f"albums: PageUp in op deze dag left the focus on {page.evaluate('document.activeElement.outerHTML.slice(0, 80)')}")
            # 'Alle N bekijken' opens exactly the N of that year: the page asks for one photo per
            # year here, so the button shows, and the grid must then count the year's total
            page.route("**/api/library/on-this-day*", lambda route: route.continue_(
                url=route.request.url.replace("per_year=60", "per_year=1")))
            try:
                step("albums: 'Alle N bekijken'", lambda: (
                    page.evaluate("albumsOtdShift(0)"),
                    page.wait_for_selector(".albums-otd-year-head .btn", timeout=5000),
                    page.click(".albums-otd-year-head .btn"),
                    page.wait_for_selector("#albums-count .albums-count-number", timeout=5000),
                    page.wait_for_timeout(600)), 0.2)
            except PlaywrightError as e:
                problems.append(f"albums: 'Alle N bekijken' did not show or open: {str(e).splitlines()[0]}")
            page.unroute("**/api/library/on-this-day*")
            page.unroute("**/api/stats/photo-on-this-day*")
            all_n = page.evaluate("""[location.hash, (document.querySelector('#albums-count .albums-count-number') || {}).textContent,
                                      document.activeElement.id]""")
            year_total = sum(n for y, n in otd["api"] if y == 2019)
            expect(all_n[0].startswith("#albums?") and "capture_date=known" in all_n[0] and all_n[1] == str(year_total)
                   and all_n[2] == "albums-title", f"albums: 'Alle {year_total} bekijken' opened {all_n}")

            # Review fixes: bad bookmarks, an inline date check, the iPhone camera, three header
            # buttons on one row at 1024, the short search text, focus after removing a chip
            step("albums: a bookmark with year=abc", lambda: (
                page.evaluate("location.hash = '#albums?kind=photo&year=abc'"), page.wait_for_timeout(900)), 0.2)
            bad = page.evaluate("""[location.hash, !!document.querySelector('#albums-state .empty-state-error'),
                                    [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Adres aangepast'))]""")
            expect(bad == ["#albums?kind=photo", False, True], f"albums: year=abc gave {bad}")
            step("albums: an end date before the start", lambda: (
                page.select_option("#albums-when", "range"),
                page.evaluate("""(() => { const set = (id, v) => { const el = document.getElementById(id); el.value = v;
                                  el.dispatchEvent(new Event('change')); };
                                  set('albums-from', '2020-01-01'); set('albums-to', '2019-01-01'); })()"""),
                page.wait_for_timeout(600)), 0.2)
            rng = page.evaluate("""[(document.getElementById('albums-range-error') || {}).hidden, location.hash,
                                    !!document.querySelector('#albums-state .empty-state-error'),
                                    document.querySelector('#albums-when').getBoundingClientRect().top
                                    === document.querySelector('#albums-sort').getBoundingClientRect().top]""")
            expect(rng == [False, "#albums?kind=photo&date_from=2020-01-01", False, True],
                   f"albums: a reversed date range gave {rng}")
            step("albums: remove a chip with the keyboard", lambda: (
                page.focus("#albums-chips .chip-remove"), page.keyboard.press("Enter"), page.wait_for_timeout(500)), 0.2)
            expect(page.evaluate("document.activeElement !== document.body && !!document.activeElement.closest('#albums-browse')"),
                   f"albums: focus fell to {page.evaluate('document.activeElement.tagName')} after removing a chip")
            iphone = page.evaluate("""(async () => (await fetchJSON(`${API_BASE}/api/library/albums`))
                                          .albums.find(a => a.builtin === 'iphone').id)()""")
            step("albums: the iPhone album", lambda: (
                page.evaluate(f"location.hash = '#albums/album/{iphone}'"), page.wait_for_timeout(800)), 0.2)
            expect(page.evaluate("document.getElementById('albums-camera').selectedOptions[0].textContent") == "Bevat 'iPhone'",
                   "albums: the camera list does not show the iPhone filter")
            side_names = "[...document.querySelectorAll('#albums-side-albums .albums-side-name')].map(e => e.textContent)"
            try:
                step("albums: hide the iPhone album", lambda: (
                    page.click("#albums-edit"), page.click("#album-hidden-row"), page.click("#album-save"),
                    page.wait_for_selector(".toast .toast-action", timeout=5000)), 0.3)
            except PlaywrightError as e:
                problems.append(f"albums: hiding showed no toast with an action: {str(e).splitlines()[0]}")
            expect("iPhone" not in page.evaluate(side_names) and page.evaluate("document.activeElement.id") == "albums-title",
                   f"albums: hiding left {page.evaluate(side_names)} with focus on {page.evaluate('document.activeElement.id')}")
            try:
                step("albums: undo hiding from the toast", lambda: (
                    page.click(".toast .toast-action", timeout=5000), page.wait_for_timeout(800)), 0.2)
            except PlaywrightError:
                problems.append("albums: no 'Ongedaan maken' in a toast after hiding")
            expect("iPhone" in page.evaluate(side_names), f"albums: 'Ongedaan maken' left {page.evaluate(side_names)}")
            page.set_viewport_size({"width": 1024, "height": 768})
            step("albums: a changed album at 1024", lambda: (
                page.click("#albums-kinds [data-kind='photo']"), page.wait_for_timeout(600)), 0.2)
            head = page.evaluate("""[[...document.querySelectorAll('#albums-header-actions button')].map(b => Math.round(b.getBoundingClientRect().top)),
                                     document.getElementById('global-search').placeholder]""")
            expect(len(head[0]) == 3 and len(set(head[0])) == 1 and head[1] == "Zoeken…",
                   f"albums: at 1024 the header buttons sit at {head[0]}, search says {head[1]!r}")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.evaluate("albumsShow(null, {}, '-taken')")

            # Kaart: clusters and pins for the GPS photos, a popup that opens the shared viewer, the
            # opt-in OpenStreetMap tiles that stay off by default (nothing may leave this machine
            # until the user switches them on), and the filter bar
            outside = []
            local = ("http://127.0.0.1:", "data:", "blob:", "about:")
            page.on("request", lambda r: outside.append(r.url) if not r.url.startswith(local) else None)
            pins = "document.querySelectorAll('#map-canvas .map-pin-wrap, #map-canvas .map-cluster-wrap').length"
            step("map: open (g m)", lambda: (page.keyboard.press("g"), page.keyboard.press("m"),
                                             page.wait_for_selector("#map-canvas .map-pin-wrap, #map-canvas .map-cluster-wrap", timeout=10000)), 0.8)
            geo = page.evaluate("fetchJSON(`${API_BASE}/api/map/points`).then(d => [d.total, d.without_gps])")
            expect(geo[0] >= 2 and geo[1] > 0 and f"{geo[1]:,}".replace(",", ".") in page.text_content("#map-nogps"),
                   f"map: {geo} points and the 'zonder locatie' button says {page.text_content('#map-nogps')!r}")
            expect(page.evaluate(pins) >= 1 and page.evaluate("mapView().markers") == geo[0],
                   f"map: {page.evaluate(pins)} markers on screen, {page.evaluate('mapView().markers')} loaded, expected {geo[0]}")
            expect(not page.is_checked("#map-osm-toggle") and page.evaluate("mapView().tiles") is False,
                   "map: the OpenStreetMap tiles are not off by default")
            step("map: a pin opens a popup", lambda: page.locator("#map-canvas .map-pin-wrap").first.click(), 1.0)
            page.wait_for_selector(".map-popup .map-popup-open", timeout=8000)
            expect(page.is_visible(".map-popup .map-popup-thumb img"), "map: the popup shows no thumbnail")
            step("map: the popup opens the viewer", lambda: page.click(".map-popup .map-popup-open"), 0.8)
            expect(page.evaluate("isViewerOpen() && viewer.items.length >= 1"), "map: Openen did not open the viewer")
            page.wait_for_function("!document.getElementById('btn-show-on-map').hidden", timeout=5000)
            step("map: 'Op kaart tonen' goes back to the photo", lambda: page.click("#btn-show-on-map"), 1.5)
            expect(page.evaluate("currentMainView()") == "map" and not page.evaluate("isViewerOpen()"),
                   "map: 'Op kaart tonen' did not return to the map")
            page.wait_for_selector(".map-popup .map-popup-open", timeout=8000)
            page.keyboard.press("Escape")
            zoom, alone = page.evaluate("mapView().zoom"), page.evaluate("document.querySelectorAll('#map-canvas .map-pin-wrap').length")
            # (the test vault's three beach photos share one spot: that cluster fans out instead of zooming)
            step("map: a cluster zooms in or fans out on click", lambda: page.locator("#map-canvas .map-cluster-wrap").first.click(), 1.2)
            expect(page.evaluate("mapView().zoom") > zoom or page.evaluate("document.querySelectorAll('#map-canvas .map-pin-wrap').length") > alone,
                   "map: clicking a cluster neither zoomed in nor fanned it out")
            zoom = page.evaluate("mapView().zoom")
            step("map: keyboard zoom (+)", lambda: (page.focus("#map-canvas"), page.keyboard.press("+")), 0.8)
            expect(page.evaluate("mapView().zoom") > zoom, "map: + on the focused map did not zoom in")
            # The filter bar: a year narrows the points, 'Filters wissen' brings them back
            step("map: a year filter", lambda: page.select_option("#map-year", "2019"), 1.2)
            expect(page.evaluate("mapView().markers") >= 1 and page.is_visible("#map-reset"), "map: the year filter shows nothing")
            step("map: a year without photos", lambda: page.select_option("#map-year", index=page.evaluate(
                "document.getElementById('map-year').options.length") - 1), 1.0)
            expect(page.evaluate("mapView().markers") <= geo[0], "map: the oldest year shows more than everything")
            step("map: clear the filters", lambda: page.click("#map-reset"), 1.0)
            expect(page.evaluate("mapView().markers") == geo[0], "map: clearing the filters did not bring every point back")
            expect(not outside, f"map: requests left this machine while the tiles were off: {outside}")
            # The opt-in: only after the switch does the browser ask OpenStreetMap for tiles (answered here by
            # a blank tile, so nothing really goes out), and the choice is kept by the server
            blank = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360606060000000050001a5f645400000000049454e44ae426082")
            page.route("https://tile.openstreetmap.org/**", lambda route: route.fulfill(status=200, content_type="image/png", body=blank))
            step("map: switch the OpenStreetMap tiles on", lambda: page.click("label.map-osm-toggle .slider"), 1.5)
            expect(page.evaluate("mapView().tiles") is True and any("tile.openstreetmap.org" in u for u in outside)
                   and page.evaluate("fetchJSON(`${API_BASE}/api/map/settings`).then(d => d.osm_tiles)") is True,
                   f"map: tiles on gave {page.evaluate('mapView()')} and requests {outside[:2]}")
            expect(page.is_visible("#map-canvas .leaflet-control-attribution a"), "map: no OpenStreetMap attribution while the tiles are on")
            step("map: switch the tiles off again", lambda: page.click("label.map-osm-toggle .slider"), 1.0)
            expect(page.evaluate("mapView().tiles") is False
                   and page.evaluate("fetchJSON(`${API_BASE}/api/map/settings`).then(d => d.osm_tiles)") is False,
                   "map: tiles did not switch off")
            step("map: the setting in Instellingen", lambda: page.evaluate("switchMainView('settings')"), 0.8)
            expect(page.is_visible("#settings-kaart #settings-map-osm, #settings-kaart .map-osm-input")
                   or page.locator("#settings-kaart").count() == 1, "map: no Kaart section in Instellingen")
            page.evaluate("switchMainView('home')")

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

            # Welke is mooier?: two photos side by side; ← / 2 pick, z takes the last pick back exactly,
            # spatie skips, the pair just compared never comes straight back, Tab stays in the window
            def board():
                return page.evaluate("""(async () => (await fetchJSON(`${API_BASE}/api/shows/leaderboard?limit=200`))
                    .items.map(i => [i.path, i.rating, i.games]).sort())()""")

            def shown_pair():
                return page.evaluate("elo.pair ? elo.pair.map(p => p.path) : []")

            def comparisons():
                return page.evaluate("(async () => (await fetchJSON(`${API_BASE}/api/shows/compare/pair`)).stats.comparisons)()")

            try:
                step("elo: open from Shows", lambda: (
                    page.click(".nav-tab[data-view='shows']"), page.click("#btn-elo"),
                    page.wait_for_selector("#elo-body .elo-choice:not(.is-loading)", timeout=10000)), 0.6)
            except PlaywrightError:
                problems.append("elo: the window showed no pair")
            expect(page.evaluate("document.getElementById('elo-overlay').contains(document.activeElement)"),
                   "elo: the focus is not in the window")
            before_board, first = board(), shown_pair()
            step("elo: ← picks the left photo", lambda: page.keyboard.press("ArrowLeft"), 1.4)
            scores = {path: rating for path, rating, _ in board()}
            expect(len(first) == 2 and scores.get(first[0], 0) > 1000 and scores.get(first[1], 9999) < 1000,
                   f"elo: ← did not make the left photo win ({first}, {scores})")
            expect(set(shown_pair()) != set(first), "elo: the pair just compared came straight back")
            step("elo: z takes it back", lambda: page.keyboard.press("z"), 1.0)
            expect(board() == before_board, "elo: z did not put both scores back exactly")
            expect(shown_pair() == first, "elo: after z the same pair is not shown again")
            server_stats = page.evaluate("(async () => (await fetchJSON(`${API_BASE}/api/shows/compare/pair`)).stats)()")
            expect(page.evaluate("elo.stats && elo.stats.compared") == server_stats["compared"],
                   f"elo: after z the progress line still counts the undone comparison ({page.text_content('#elo-progress')})")
            step("elo: 2 picks the right photo", lambda: page.keyboard.press("2"), 1.4)
            scores = {path: rating for path, rating, _ in board()}
            expect(scores.get(first[1], 0) > 1000, f"elo: 2 did not make the right photo win ({scores})")
            count, current = comparisons(), shown_pair()
            step("elo: spatie skips", lambda: page.keyboard.press(" "), 0.8)
            expect(comparisons() == count and shown_pair() != current, "elo: spatie did not just skip the pair")
            page.focus("#elo-scope")
            page.keyboard.press("1")
            page.wait_for_timeout(400)
            expect(comparisons() == count, "elo: a key in the collection list picked a photo")

            # A second key while the next pair loads (slowed down here), and a held key, pick nothing more
            page.evaluate("""(() => { window.__eloPosts = 0; window.__realFetchJSON = fetchJSON;
                fetchJSON = async (url, options) => {
                    if (url.includes('/api/shows/compare/pair')) await new Promise(r => setTimeout(r, 900));
                    if (url.endsWith('/api/shows/compare') && options && options.method === 'POST') window.__eloPosts++;
                    return window.__realFetchJSON(url, options);
                }; })()""")
            page.focus("#elo-overlay")
            page.keyboard.press("1")
            for _ in range(10):
                page.wait_for_timeout(80)
                page.keyboard.press("1")
            page.keyboard.down("2")
            page.keyboard.down("2")      # a repeat, as when the key is held
            page.keyboard.up("2")
            page.wait_for_timeout(1600)
            expect(page.evaluate("window.__eloPosts") == 1,
                   f"elo: a second key while the next pair loaded made {page.evaluate('window.__eloPosts')} comparisons")
            page.evaluate("() => { fetchJSON = window.__realFetchJSON; }")

            # Tab to a photo, Enter picks it, and the keys still work after that (focus stays on a photo);
            # everything Tab reaches in the window shows a focus ring
            ring = """(() => { const a = document.activeElement; if (!a || a === document.body) return 'none';
                const probe = document.createElement('i'); probe.style.color = 'var(--focus-ring)';
                document.body.appendChild(probe); const colour = getComputedStyle(probe).color; probe.remove();
                const rgb = colour.slice(colour.indexOf('(') + 1, colour.indexOf(')'));
                const el = a.classList.contains('elo-choice') ? a.querySelector('.elo-frame') : a;
                const cs = getComputedStyle(el);
                const ok = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 && cs.outlineColor === colour)
                    || cs.boxShadow.includes(rgb);
                return ok ? 'ok' : (a.id || a.className || a.tagName); })()"""
            page.wait_for_selector("#elo-body .elo-choice:not(.is-loading)", timeout=5000)
            page.focus("#elo-overlay")
            missing_rings = []
            for _ in range(8):
                page.keyboard.press("Tab")
                page.wait_for_timeout(300)      # the ring fades in (--duration-base)
                at = page.evaluate(ring)
                if at != "ok":
                    missing_rings.append(at)
            expect(not missing_rings, f"elo: no visible focus ring on {missing_rings}")
            for _ in range(8):
                if page.evaluate("!!document.activeElement.closest('.elo-choice')"):
                    break
                page.keyboard.press("Tab")
            count = comparisons()
            step("elo: Enter on a photo", lambda: page.keyboard.press("Enter"), 1.4)
            expect(comparisons() == count + 1, "elo: Enter on a photo did not pick it")
            expect(page.evaluate("!!document.activeElement.closest('.elo-choice')"),
                   f"elo: after Enter the focus fell to {page.evaluate('document.activeElement.tagName')}")
            step("elo: 1 after Enter", lambda: page.keyboard.press("1"), 1.4)
            expect(comparisons() == count + 2, "elo: after Tab + Enter the key 1 did nothing")

            # Ranglijst right after a pick (during the short pause before the next pair): the board
            # stays, no dead pair is drawn over it, and Vergelijken then shows a new pair
            page.wait_for_selector("#elo-body .elo-choice:not(.is-loading)", timeout=5000)
            picked = shown_pair()
            page.focus("#elo-overlay")
            page.keyboard.press("1")
            page.click("#elo-tab-board")
            page.wait_for_timeout(1400)
            race = page.evaluate("""({tab: elo.tab, rows: document.querySelectorAll('#elo-body .elo-board-row').length,
                choices: document.querySelectorAll('#elo-body .elo-choice').length})""")
            expect(race["tab"] == "board" and race["rows"] > 0 and race["choices"] == 0,
                   f"elo: Ranglijst right after a pick showed a pair instead of the board ({race})")
            step("elo: back to Vergelijken", lambda: (page.click("#elo-tab-compare"),
                 page.wait_for_selector("#elo-body .elo-choice:not(.is-loading)", timeout=5000)), 0.3)
            expect(len(shown_pair()) == 2 and set(shown_pair()) != set(picked),
                   f"elo: after the board the pair just compared came back ({picked})")

            # z says so in the footer (no toast over the photos); an undo that fails on the way (no
            # connection) keeps the comparison, so z can try again
            page.evaluate("""(() => { window.__realFetchJSON = fetchJSON;
                fetchJSON = async (url, options) => {
                    if (url.endsWith('/api/shows/compare/undo')) throw new TypeError('Load failed');
                    return window.__realFetchJSON(url, options);
                }; })()""")
            undo_before = page.evaluate("elo.undo.length")
            page.focus("#elo-overlay")
            seen = len(problems)
            page.keyboard.press("z")
            page.wait_for_timeout(400)
            # (the console error of that failed undo is expected here)
            problems[seen:] = [p for p in problems[seen:] if "Terugdraaien lukte niet" not in p]
            expect(undo_before > 0 and page.evaluate("elo.undo.length") == undo_before,
                   f"elo: a failed undo dropped the comparison ({undo_before} -> {page.evaluate('elo.undo.length')})")
            page.evaluate("() => { fetchJSON = window.__realFetchJSON; document.querySelectorAll('.toast').forEach(t => t.remove()); }")
            page.focus("#elo-overlay")
            step("elo: z again", lambda: page.keyboard.press("z"), 1.0)
            expect(page.evaluate("elo.undo.length") == undo_before - 1, "elo: z after a failed undo did nothing")
            expect("Teruggedraaid" in (page.text_content("#elo-progress") or "")
                   and not page.query_selector(".toast"), f"elo: the undo is not told in the footer, or a toast covers the photos "
                   f"({page.text_content('#elo-progress')})")

            # A collection with too few photos: Overslaan is off and the round counter starts again
            familie = str((vault / "library" / "Familie").resolve())
            step("elo: Familie", lambda: page.select_option("#elo-scope", familie), 1.0)
            expect(page.is_disabled("#elo-skip"), "elo: Overslaan looks usable without a pair")
            expect("in deze ronde" not in (page.text_content("#elo-progress") or ""),
                   f"elo: the round counter of another collection is shown ({page.text_content('#elo-progress')})")
            step("elo: back to the library", lambda: page.select_option("#elo-scope", ""), 1.0)
            page.focus("#elo-overlay")
            expect_focus_trapped("elo-overlay", "elo: window")
            step("elo: Ranglijst", lambda: page.click("#elo-tab-board"), 1.0)
            ranks = page.evaluate("[...document.querySelectorAll('.elo-board-score')].map(e => Number(e.textContent.replace(/\\./g, '')))")
            expect(len(ranks) >= 2 and ranks == sorted(ranks, reverse=True), f"elo: the leaderboard is not sorted ({ranks})")
            for theme in ("light", "dark"):
                page.evaluate(f"setTheme('{theme}')")
                for tab in ("board", "compare"):
                    page.evaluate(f"eloShowTab('{tab}')")
                    page.wait_for_timeout(900)
                    page.screenshot(path=str(shots / f"elo-{tab}_{theme}.png"))
                    page.add_script_tag(path=str(A11Y_CHECKS))
                    for fail in page.evaluate("__contrastFailures()"):
                        problems.append(f"contrast {theme} elo {tab}: {fail['ratio']}:1 {fail['key']} ({fail['text']!r})")
                    for control in page.evaluate("__unnamedControls()"):
                        problems.append(f"unnamed control in elo {tab}: {control}")
            page.evaluate("setTheme('auto')")
            step("elo: Escape closes", lambda: page.keyboard.press("Escape"), 0.5)
            expect(not page.is_visible("#elo-overlay") and page.evaluate("document.activeElement.id") == "btn-elo",
                   f"elo: Escape did not close the window and return to its button ({page.evaluate('document.activeElement.id')})")
            # Without any show the header (and 'Welke is mooier?') stays above the empty state
            empty_shows = page.evaluate("""(() => { const before = _lastShowsData; renderShows({featured: null, shows: []});
                const r = {elo: !!document.getElementById('btn-elo'), empty: !!document.querySelector('#shows-container .empty-state'),
                           shuffle: !!document.getElementById('btn-shuffle-shows')};
                renderShows(before); return r; })()""")
            expect(empty_shows == {"elo": True, "empty": True, "shuffle": False},
                   f"shows: without shows 'Welke is mooier?' cannot be reached ({empty_shows})")

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
            # The finished export would keep the dock folded: hide it like a user would
            page.evaluate("jobDock.jobs.filter(j => j.type === 'export').forEach(j => jobDock.dismissed.add(j.id)); renderJobDock()")
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

            # Beste uit reeks (second tab of Duplicaten): compare, decide with the keyboard, move one
            # series after the confirmation and put it back from the Herstelcentrum
            page.evaluate("switchMainView('duplicates')")
            page.focus("#dup-tab-exact")
            step("series: ArrowRight opens the tab", lambda: page.keyboard.press("ArrowRight"), 0.5)
            expect(page.is_visible("#dup-panel-series") and not page.is_visible("#dup-panel-exact")
                   and page.evaluate("document.activeElement.id") == "dup-tab-series",
                   "series: ArrowRight on the tabs did not open 'Beste uit reeks'")
            # Before the first comparison there is one primary action, in the empty state
            before = page.evaluate("""[document.getElementById('btn-series-compare').getClientRects().length,
                [...document.querySelectorAll('#dup-panel-series .btn-primary')].filter(b => b.getClientRects().length).map(b => b.textContent.trim())]""")
            expect(before[0] == 0 and before[1] == ["Foto's vergelijken"],
                   f"series: before comparing, expected one primary 'Foto's vergelijken' (in the empty state), got {before}")
            step("series: compare", lambda: page.click("#series-list .empty-state-action"), 0.5)
            running = page.evaluate("[!!document.getElementById('series-empty-progress') || !!document.querySelector('.series-card'),"
                                    " document.querySelectorAll('#series-list .empty-state-action').length]")
            expect(running[0] and running[1] == 0,
                   f"series: while comparing the list still offers to compare, or says nothing ({running})")
            try:
                page.wait_for_function("document.getElementById('series-progress').hidden"
                                       " && !document.getElementById('btn-series-compare').disabled"
                                       " && !!document.querySelector('.series-card')", timeout=120000, polling=250)
            except PlaywrightError:
                problems.append("series: comparing never showed a series")
            # (the big folder's 1500 files are copies of corrupt.jpg: not decodable, so no series)
            cards = page.evaluate("[...document.querySelectorAll('.series-card')].map(c => [...c.querySelectorAll('.series-photo-name')].map(n => n.textContent))")
            expect(len(cards) == 2 and any("burst_00.jpg" in c for c in cards) and any("strand klein.jpg" in c for c in cards),
                   f"series: expected the burst and the beach series, got {cards}")
            for theme in ("light", "dark"):
                page.evaluate(f"setTheme('{theme}')")
                page.wait_for_timeout(300)
                page.screenshot(path=str(shots / f"duplicates-series_{theme}.png"))
                page.add_script_tag(path=str(A11Y_CHECKS))
                for fail in page.evaluate("__contrastFailures()"):
                    problems.append(f"contrast {theme} duplicates-series: {fail['ratio']}:1 {fail['key']} ({fail['text']!r})")
                for control in page.evaluate("__unnamedControls()"):
                    problems.append(f"unnamed control on duplicates-series: {control}")
            page.evaluate("setTheme('auto')")

            def tile_state():
                return page.evaluate("""(() => { const a = document.activeElement;
                    const card = a.closest('.series-card');
                    return { name: a.closest('.series-photo-cell') ? a.closest('.series-photo-cell').querySelector('.series-photo-name').textContent : a.id,
                             pressed: a.getAttribute('aria-pressed'), button: card ? card.querySelector('.series-confirm-btn').textContent : '',
                             cards: document.querySelectorAll('.series-card').length }; })()""")

            page.focus(".series-photo[tabindex='0']")
            # 'g s' goes to Spotify, also from a photo tile (the s is not 'skip' then)
            step("series: g s goes to Spotify", lambda: (page.keyboard.press("g"), page.keyboard.press("s")), 0.5)
            expect(page.evaluate("!document.getElementById('section-spotify').classList.contains('hidden')")
                   and page.evaluate("seriesState.items.length") == 2,
                   "series: 'g s' on a photo tile skipped the series instead of opening Spotify")
            step("series: back to the tab", lambda: (page.evaluate("switchMainView('duplicates')"),
                                                     page.wait_for_selector(".series-card")), 0.8)
            page.focus(".series-photo[tabindex='0']")
            first = tile_state()
            step("series: ArrowRight to the next photo", lambda: page.keyboard.press("ArrowRight"), 0.2)
            second = tile_state()
            expect(second["name"] != first["name"] and second["pressed"] == "false",
                   f"series: ArrowRight did not move to the next photo ({first} -> {second})")
            step("series: space keeps it", lambda: page.keyboard.press(" "), 0.2)
            kept = tile_state()
            expect(kept["pressed"] == "true" and kept["name"] == second["name"],
                   f"series: space did not keep the photo ({kept})")
            step("series: space lets it go again", lambda: page.keyboard.press(" "), 0.2)
            expect(tile_state()["pressed"] == "false", "series: a second space did not let the photo go")
            step("series: s skips the series", lambda: page.keyboard.press("s"), 0.4)
            left = tile_state()
            expect(left["cards"] == 1 and left["pressed"] == "true", f"series: s did not skip to the next series ({left})")

            # v: the photos large, side by side; d switches to 100%, space keeps, Escape goes back
            step("series: v compares large", lambda: page.keyboard.press("v"), 1.0)
            compare = page.evaluate("""[!!document.querySelector('#modal-series-compare.active'),
                document.querySelectorAll('#series-compare-grid .series-compare-pane').length,
                document.activeElement.classList.contains('series-compare-keep'),
                Math.round((document.querySelector('.series-compare-frame') || {getBoundingClientRect: () => ({height: 0})}).getBoundingClientRect().height)]""")
            expect(compare[0] and compare[1] == 2 and compare[2] and compare[3] >= 300,
                   f"series: 'v' did not open the large comparison with the focus on a photo ({compare})")
            for theme in ("light", "dark"):
                page.evaluate(f"setTheme('{theme}')")
                page.wait_for_timeout(300)
                page.screenshot(path=str(shots / f"duplicates-series-compare_{theme}.png"))
                page.add_script_tag(path=str(A11Y_CHECKS))
                for fail in page.evaluate("__contrastFailures()"):
                    problems.append(f"contrast {theme} series compare: {fail['ratio']}:1 {fail['key']} ({fail['text']!r})")
                for control in page.evaluate("__unnamedControls()"):
                    problems.append(f"unnamed control on series compare: {control}")
            page.evaluate("setTheme('auto')")
            if compare[0]:
                expect_focus_trapped("modal-series-compare", "series: large comparison")
                page.focus("#series-compare-grid .series-compare-keep")
                step("series: d shows 100%", lambda: page.keyboard.press("d"), 0.3)
                expect(page.evaluate("document.getElementById('series-compare-grid').classList.contains('is-detail')"
                                     " && document.getElementById('btn-series-detail').getAttribute('aria-pressed') === 'true'"),
                       "series: 'd' did not switch the comparison to 100%")
                page.keyboard.press("d")
                step("series: ArrowRight and space keep the other photo", lambda: (page.keyboard.press("ArrowRight"),
                                                                                  page.keyboard.press(" ")), 0.3)
                both = page.evaluate("[document.activeElement.getAttribute('aria-pressed'),"
                                     " document.querySelectorAll('.series-card .series-photo.selected').length,"
                                     " (document.getElementById('btn-series-compare-go') || {}).textContent]")
                expect(both == ["true", 2, "Alles behouden"], f"series: keeping in the comparison did not reach the list ({both})")
                page.keyboard.press(" ")
            step("series: Escape closes the comparison", lambda: page.keyboard.press("Escape"), 0.4)
            expect(not page.evaluate("!!document.querySelector('#modal-series-compare.active')")
                   and page.evaluate("!!document.activeElement.closest('.series-photo')")
                   and tile_state()["button"] == "Verplaats 1",
                   f"series: Escape did not close the comparison back onto the photo ({tile_state()})")

            step("series: Enter asks first", lambda: page.keyboard.press("Enter"), 0.5)
            dialog = page.evaluate("[document.getElementById('modal-series-confirm').classList.contains('active'),"
                                   " document.getElementById('series-confirm-list').textContent,"
                                   " document.activeElement.id]")
            expect(dialog[0] and "RAW_DUPLICATES/near-duplicates" in dialog[1] and "null" not in dialog[1]
                   and dialog[2] == "btn-series-cancel", f"series: the confirmation is missing or unclear, or does not start on Annuleren ({dialog})")
            expect_focus_trapped("modal-series-confirm", "series: confirmation")
            page.focus("#btn-series-cancel")
            step("series: Escape moves nothing", lambda: page.keyboard.press("Escape"), 0.4)
            klein = vault / "RAW" / "2022" / "bursts" / "strand klein.jpg"
            originals = [vault / "RAW" / "2019" / "zomer vakantie" / "strand 001.jpg", vault / "RAW" / "misc" / "strand 001 kopie.jpg"]
            expect(klein.exists() and all(p.exists() for p in originals) and tile_state()["cards"] == 1
                   and page.evaluate("!!document.activeElement.closest('.series-photo')"),
                   "series: Escape on the confirmation moved something or lost the focus")
            step("series: Enter, Enter cancels", lambda: (page.keyboard.press("Enter"), page.wait_for_timeout(400),
                                                          page.keyboard.press("Enter")), 0.6)
            expect(klein.exists() and tile_state()["cards"] == 1
                   and not page.evaluate("document.getElementById('modal-series-confirm').classList.contains('active')"),
                   "series: a double Enter moved files (the dialog must start on Annuleren)")
            step("series: Enter, Tab, Enter moves", lambda: (page.keyboard.press("Enter"), page.wait_for_timeout(400),
                                                             page.keyboard.press("Tab"), page.keyboard.press("Enter")), 1.0)
            holding = vault / "RAW_DUPLICATES" / "near-duplicates"
            expect(not klein.exists() and (holding / "2022" / "bursts" / "strand klein.jpg").exists()
                   and all(p.exists() for p in originals) and tile_state()["cards"] == 0,
                   "series: confirming did not move the resized photo to RAW_DUPLICATES/near-duplicates")
            # The burst was skipped: the list says so (not 'Geen reeksen gevonden') and can show it again
            empty = page.evaluate("[(document.querySelector('#series-list .empty-state-title') || {}).textContent,"
                                  " (document.querySelector('#series-list .empty-state-action') || {}).textContent]")
            expect(empty == ["Alle reeksen overgeslagen", "Overgeslagen tonen"],
                   f"series: after the last decision the list did not say the rest was skipped ({empty})")
            accept = lambda d: d.accept()
            page.on("dialog", accept)
            step("series: undo in the Herstelcentrum", lambda: (
                page.click(".top-nav [data-view='recovery']"),
                page.wait_for_selector(".recovery-entry"),
                page.click(".recovery-entry:has-text('Beste uit reeks') .recovery-undo")), 1.5)
            page.remove_listener("dialog", accept)
            expect(klein.exists() and not (holding / "2022" / "bursts" / "strand klein.jpg").exists(),
                   "series: the Herstelcentrum did not put the resized photo back")
            step("series: back on the tab", lambda: page.click(".top-nav [data-view='duplicates']"), 1.0)
            back = page.evaluate("[...document.querySelectorAll('.series-photo-name')].map(n => n.textContent)")
            # (the burst was skipped: that holds for this session)
            expect("strand klein.jpg" in back and "burst_00.jpg" not in back,
                   f"series: after the undo the series did not come back on the tab, or the skipped one did ({back})")

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

            # DataGatherer: cards built from the connectors' metadata, g v, the help and the 'Meer'
            # menu with the keyboard, the connect window (Tab stays inside), reading the export that
            # is already in the archive (in place), the Spotify chip, the network log, Instellingen.
            # Nothing here goes online: the test server refuses every request of the network guard.
            page.reload()
            page.wait_for_load_state("networkidle")
            page.click(".nav-tab[data-view='home']")
            step("datagatherer: g v opens it", lambda: (page.keyboard.press("g"), page.keyboard.press("v")), 0.6)
            expect(page.evaluate(view_now) == "datagatherer", "datagatherer: g v did not open the tab")
            spotify_card = ".dg-card[data-source='spotify']"
            try:
                page.wait_for_selector(spotify_card, timeout=10000)
            except PlaywrightError:
                pass   # reported below
            cards = page.evaluate("[...document.querySelectorAll('.dg-card')].map(c => c.dataset.source)")
            expect({"spotify", "lastfm", "google_takeout"} <= set(cards), f"datagatherer: cards {cards}")
            primary = f"{spotify_card} .dg-primary"
            expect(page.evaluate(f"(document.querySelector({json.dumps(primary)}) || {{}}).textContent || ''").strip() == "Inlezen",
                   "datagatherer: the existing Spotify export does not ask to be read (Inlezen)")
            page.focus(f"{spotify_card} button[aria-label^='Hulp']")
            step("datagatherer: Enter opens the help", lambda: page.keyboard.press("Enter"), 0.8)
            expect(page.evaluate(f"document.querySelectorAll({json.dumps(spotify_card + ' .dg-help-option')}).length") >= 3,
                   "datagatherer: the help shows no options")
            expect(page.evaluate("(document.activeElement.getAttribute('aria-label') || '')").startswith("Hulp"),
                   "datagatherer: focus left the help button after it redrew the card")
            page.keyboard.press("Enter")
            # Every card keeps its main button, 'Meer', help and settings on one line
            rows = page.evaluate("""[...document.querySelectorAll('.dg-card')].map(c => {
                const p = c.querySelector('.dg-primary'), t = c.querySelector('.dg-actions-tools');
                return [c.dataset.source, p && t ? Math.abs(p.getBoundingClientRect().top - t.getBoundingClientRect().top) : -1];
            })""")
            expect(all(0 <= gap <= 4 for _, gap in rows), f"datagatherer: an action row wraps {rows}")
            # A category filter chosen with the keyboard keeps the focus on that pill
            page.focus("#dg-filters .pill[data-dg-focus='filter-Muziek']")
            step("datagatherer: Enter on a filter", lambda: page.keyboard.press("Enter"), 0.3)
            expect(page.evaluate("document.activeElement.dataset.dgFocus || document.activeElement.tagName") == "filter-Muziek",
                   "datagatherer: the focus left the filter pill")
            page.evaluate("dgState.filter = 'all'; dgRender()")
            # 'Meer': Enter opens it on the first item, arrows/Home/End move, Escape closes on the button
            more_btn = f"{spotify_card} .dg-more-btn"
            open_menus = "document.querySelectorAll('.dg-more.open').length"
            page.focus(more_btn)
            step("datagatherer: Enter opens 'Meer'", lambda: page.keyboard.press("Enter"), 0.3)
            items = page.evaluate(f"[...document.querySelectorAll({json.dumps(spotify_card + ' .dg-more-menu .menu-item')})].map(i => i.textContent.trim())")
            focus_text = lambda: page.evaluate("document.activeElement.getAttribute('role') === 'menuitem' ? document.activeElement.textContent.trim() : document.activeElement.tagName")
            expect(page.evaluate(open_menus) == 1 and items and focus_text() == items[0],
                   f"datagatherer: 'Meer' did not open on its first item ({focus_text()!r}, {items})")
            page.keyboard.press("ArrowDown")
            expect(len(items) < 2 or focus_text() == items[1], "datagatherer: ArrowDown in 'Meer' did not move")
            page.keyboard.press("End")
            expect(focus_text() == items[-1], "datagatherer: End in 'Meer' did not go to the last item")
            page.keyboard.press("Home")
            expect(focus_text() == items[0], "datagatherer: Home in 'Meer' did not go to the first item")
            step("datagatherer: Escape closes 'Meer'", lambda: page.keyboard.press("Escape"), 0.3)
            expect(page.evaluate(open_menus) == 0
                   and page.evaluate("document.activeElement.classList.contains('dg-more-btn')"),
                   "datagatherer: Escape did not close 'Meer' and keep the focus on it")
            # One menu at a time, and a click next to it closes it
            page.click(more_btn)
            page.click(".dg-card[data-source='google_takeout'] .dg-more-btn")
            expect(page.evaluate(open_menus) == 1, "datagatherer: two 'Meer' menus are open at once")
            step("datagatherer: a click next to 'Meer' closes it", lambda: page.click("#dg-title"), 0.3)
            expect(page.evaluate(open_menus) == 0, "datagatherer: 'Meer' stayed open after a click next to it")
            # A window opened from 'Meer' gives the focus back to 'Meer' when it closes
            page.focus(more_btn)
            page.keyboard.press("ArrowDown")
            for _ in range(len(items)):
                if focus_text() == "Export aanvragen":
                    break
                page.keyboard.press("ArrowDown")
            step("datagatherer: Export aanvragen from 'Meer'", lambda: page.keyboard.press("Enter"), 0.8)
            expect(page.is_visible("#dg-export") and page.evaluate(open_menus) == 0,
                   "datagatherer: 'Export aanvragen' from 'Meer' did not open its window (and close the menu)")
            step("datagatherer: Escape closes the export window", lambda: page.keyboard.press("Escape"), 0.6)
            expect(page.evaluate("document.activeElement.classList.contains('dg-more-btn')"),
                   f"datagatherer: after the window the focus is on {page.evaluate('document.activeElement.tagName')}, not 'Meer'")
            step("datagatherer: the connect window", lambda: page.click(".dg-card[data-source='lastfm'] .dg-primary"), 0.8)
            expect(page.is_visible("#dg-connect"), "datagatherer: Verbinden did not open the connect window")
            expect_focus_trapped("dg-connect", "datagatherer: connect window")
            page.fill("#dg-connect-username", "smoketest")
            page.fill("#dg-connect-api_key", "0123456789abcdef0123456789abcdef")
            step("datagatherer: save Last.fm", lambda: page.click("#dg-connect .modal-footer .btn-primary"), 1.2)
            expect(not page.is_visible("#dg-connect"), "datagatherer: the connect window stayed open after saving")
            expect(page.evaluate("(document.querySelector(\".dg-card[data-source='lastfm'] .dg-primary\") || {}).dataset.action") == "sync",
                   "datagatherer: a connected Last.fm does not offer Bijwerken")
            # Bijwerken is refused by the test server: the card says why, in a red note under the next step
            step("datagatherer: Bijwerken Last.fm (refused here)", lambda: page.click(".dg-card[data-source='lastfm'] .dg-primary"), 0.4)
            try:
                page.wait_for_selector(".dg-card[data-source='lastfm'] .dg-note-danger", timeout=10000)
            except PlaywrightError:
                pass   # reported below
            note = page.evaluate("(document.querySelector(\".dg-card[data-source='lastfm'] .dg-note-danger\") || {}).textContent || ''")
            expect("mislukt" in note and "testmodus" in note, f"datagatherer: a failed sync shows no reason on the card ({note!r})")
            step("datagatherer: read the existing Spotify export", lambda: page.click(primary), 0.4)
            try:
                page.wait_for_function(f"(document.querySelector({json.dumps(primary)}) || {{dataset: {{}}}}).dataset.action !== 'import'",
                                       timeout=20000)
            except PlaywrightError:
                pass   # reported below
            until = page.evaluate(f"(document.querySelector({json.dumps(spotify_card + ' .dg-until-date')}) || {{}}).textContent")
            expect(bool(until), "datagatherer: after Inlezen the card shows no 'Gegevens t/m'")
            expect((vault / "data" / "Matthew" / "Spotify Extended Streaming History").is_dir(),
                   "datagatherer: the existing export folder moved")
            step("datagatherer: the Spotify chip", lambda: page.click(".nav-tab[data-view='spotify']"), 1.2)
            chip = page.evaluate("(document.getElementById('spotify-coverage') || {}).textContent || ''")
            expect(chip.startswith("Bijgewerkt t/m") and "null" not in chip, f"datagatherer: the Spotify chip says {chip!r}")
            page.evaluate("switchMainView('datagatherer')")
            step("datagatherer: the network log", lambda: page.click("#section-datagatherer button:has-text('Netwerklog')"), 0.8)
            expect(page.is_visible("#dg-network"), "datagatherer: the network log did not open")
            expect_focus_trapped("dg-network", "datagatherer: network log")
            step("datagatherer: Escape closes the log", lambda: page.keyboard.press("Escape"), 0.4)
            expect(not page.is_visible("#dg-network"), "datagatherer: Escape did not close the network log")
            step("datagatherer: Instellingen", lambda: page.click(".top-nav [data-view='settings']"), 0.8)
            expect(page.is_visible("#settings-datagatherer"), "datagatherer: no section in Instellingen")

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
