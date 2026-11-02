"""
The rules the browser smoke uses for what a navigation may explain (tests/ui/smoke_cutoff.py): only a
failed request counts as cut off, it is retried at its own address, and a console or page error is let
go only when it names such a request. Real errors stay problems.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("smoke_cutoff", Path(__file__).parent / "ui" / "smoke_cutoff.py")
smoke_cutoff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_cutoff)

ORIGIN = "http://127.0.0.1:9999"
APP_JS = f"{ORIGIN}/app.js?v=83"


def run(cut_off, suspects, statuses=None):
    asked = []

    def retry(url):
        asked.append(url)
        return (statuses or {}).get(url, 200)

    problems, transient = smoke_cutoff.sort_out(cut_off, suspects, retry, ORIGIN)
    return problems, transient, asked


def test_a_cut_off_get_is_retried_at_its_own_address_and_is_transient():
    url = f"{ORIGIN}/api/shows"
    problems, transient, asked = run([("GET", url, "GET /api/shows cancelled")], [])
    assert asked == [url]
    assert problems == [] and transient == ["GET /api/shows cancelled -> retried, 200"]


def test_a_failed_retry_or_a_post_stays_a_problem():
    get, post = f"{ORIGIN}/api/shows", f"{ORIGIN}/api/move"
    problems, transient, asked = run([("GET", get, "GET /api/shows cancelled"),
                                      ("POST", post, "POST /api/move cancelled")], [], {get: 500})
    assert asked == [get]                      # a POST is never sent again
    assert transient == [] and len(problems) == 2


def test_an_error_the_app_logs_itself_stays_a_problem_even_with_a_get_retried():
    # app.js:415 logs this when /api/shows really fails; its location is app.js, not the fetch
    text = "console error: Error loading shows: TypeError: Load failed"
    problems, transient, asked = run([("GET", f"{ORIGIN}/api/health?cut-off", "GET /api/health?cut-off cancelled")],
                                     [(text, APP_JS)])
    assert asked == [f"{ORIGIN}/api/health?cut-off"]     # app.js itself is never 'retried'
    assert [p for p in problems if p.startswith(text)]
    assert not [t for t in transient if t.startswith(text)]


def test_a_page_error_without_an_address_stays_a_problem():
    text = "page error: Error: real bug: upload aborted by a broken handler"
    problems, transient, _ = run([("GET", f"{ORIGIN}/api/jobs", "GET /api/jobs cancelled")], [(text, None)])
    assert [p for p in problems if p.startswith(text) and "no address" in p]
    assert len(transient) == 1


def test_an_error_naming_a_cut_off_fetch_is_transient():
    url = f"{ORIGIN}/api/folders"
    named = f"console error: Fetch API cannot load {url}. due to access control checks."
    browser_line = "console error: Failed to load resource: The network connection was lost."
    problems, transient, asked = run([("GET", url, "GET /api/folders cancelled")],
                                     [(named, APP_JS), (browser_line, url)])
    assert problems == [] and len(transient) == 3 and asked == [url]


def test_an_error_naming_a_fetch_that_was_not_cut_off_stays_a_problem():
    text = f"console error: Fetch API cannot load {ORIGIN}/api/other due to access control checks"
    problems, _, asked = run([("GET", f"{ORIGIN}/api/folders", "GET /api/folders cancelled")], [(text, None)])
    assert asked == [f"{ORIGIN}/api/folders"] and [p for p in problems if p.startswith(text)]


def test_an_error_naming_a_fetch_whose_retry_failed_stays_a_problem():
    url = f"{ORIGIN}/api/folders"
    text = "console error: Failed to load resource: The network connection was lost."
    problems, transient, _ = run([("GET", url, "GET /api/folders cancelled")], [(text, url)], {url: 503})
    assert transient == [] and len(problems) == 2


def test_only_the_browsers_own_resource_line_may_use_its_location():
    url = f"{ORIGIN}/api/folders"
    assert smoke_cutoff.addresses("console error: Failed to load resource: cancelled", url) == {url}
    assert smoke_cutoff.addresses("console error: Error loading folders: TypeError: Load failed", url) == set()
    assert smoke_cutoff.addresses(f"console error: cannot load {url}. due to access control checks") == {url}
