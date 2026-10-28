"""
Browser smoke test: clicks through every tab of the dashboard in a real WebKit (the
engine of Safari) and fails on any JavaScript error, console error or failed API call.

It never touches a real archive: it builds a throwaway test vault in a temp folder,
starts its own server on a free port (DATAVAULT_TESTING=1, DATAVAULT_CONFIG pointing at
that vault) and stops it again at the end.

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
VIEWS = ["home", "viewer", "duplicates", "spotify", "shows", "keurer", "timeline"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(workdir: Path):
    vault = workdir / "vault"
    python = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)
    subprocess.run([python, str(CODE_DIR / "tests/fixtures/vault_builder.py"), str(vault)],
                   check=True, stdout=subprocess.DEVNULL)
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
                step(f"tab {view}", lambda: page.click(f".nav-tab[data-view='{view}']"), 1.5)
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

            # Home: chat full screen open and closed, theme switch
            step("home: chat full screen", lambda: (page.click(".nav-tab[data-view='home']"),
                                                    page.click("#section-home .chat-fullscreen-toggle")), 0.5)
            step("home: close full screen", lambda: page.keyboard.press("Escape"), 0.4)
            for theme in ("light", "dark", "auto"):
                step(f"theme {theme}", lambda: (page.click(".theme-button"),
                                                 page.click(f".theme-option[data-theme='{theme}']")), 0.3)

            # Shows: Manage Sources and the Feeling Lucky slideshow
            step("shows: manage sources", lambda: (page.click(".nav-tab[data-view='shows']"),
                                                   page.click("text=Manage Sources")), 1.0)
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
