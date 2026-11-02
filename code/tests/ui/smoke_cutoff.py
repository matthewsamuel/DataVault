"""
What a navigation (a reload, a goto) is allowed to explain in the browser smoke (smoke_webkit.py).

A reload cancels the fetches the old page still had on their way. WebKit reports each one as a failed
request, and sometimes also as a console error. That is not a bug in the app, so the smoke asks every
cut-off GET again: an answer below 500 makes it 'transient' (printed, not a problem).

Only a failed request has a real method and address, so only a failed request counts as cut off. A
console or page error during the navigation is explained only when it names the address of such a
request whose retry answered fine (in its text, or, for the browser's own 'Failed to load resource'
line, as the address of the resource it is about). Everything else stays a problem: an error the
app logs itself ('Error loading shows: TypeError: Load failed', located in app.js), and an error
without any address, even when another fetch was cut off in the same navigation.

No Playwright here, so the rules have their own fast tests (tests/test_smoke_cutoff.py).
"""
import re

# How WebKit (and the other engines) report a fetch that a navigation cut off or that lost its connection
ABORTED = re.compile(r"access control checks|network connection was lost|Load failed|cancelled|"
                     r"Load request cancelled|NS_BINDING_ABORTED|ERR_ABORTED|aborted", re.I)
URL_IN_TEXT = re.compile(r"https?://[^\s'\")]+")
# The browser's own line about a resource; its console location is that resource, not a script
BROWSER_RESOURCE_LINE = re.compile(r"^console error: Failed to load resource\b")


def addresses(text: str, location: str | None = None) -> set[str]:
    """The addresses an error names: those in its text, plus its location for the browser's own line."""
    found = {url.rstrip(".,;:") for url in URL_IN_TEXT.findall(text)}
    if location and BROWSER_RESOURCE_LINE.match(text):
        found.add(location)
    return found


def sort_out(cut_off, suspects, retry, origin: str):
    """
    cut_off:  [(method, url, text)] from failed requests during a navigation.
    suspects: [(text, location)] console and page errors during a navigation that read like an abort.
    retry(url) -> an HTTP status (int) or a text saying why the retry failed.
    Returns (problems, transient).
    """
    problems, transient, answered = [], [], {}
    for method, url, text in cut_off:
        if method != "GET" or not url.startswith(origin):
            problems.append(f"{text} (cut off by a navigation; a {method} is not asked again)")
            continue
        if url not in answered:
            answered[url] = retry(url)
        status = answered[url]
        if isinstance(status, int) and status < 500:
            transient.append(f"{text} -> retried, {status}")
        else:
            problems.append(f"{text} (cut off by a navigation; the retry gave {status})")
    fine = {url for url, status in answered.items() if isinstance(status, int) and status < 500}
    for text, location in suspects:
        named = addresses(text, location)
        if named & fine:
            transient.append(f"{text} -> its fetch was cut off and retried above")
        else:
            why = "names no fetch that was cut off and answered" if named else "no address"
            problems.append(f"{text} (during a navigation, but {why})")
    return problems, transient
