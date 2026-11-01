"""
The UI findings of the phase-3 verification round, pinned down so they do not come back:

- the idle job dock really is a small icon (no 200 px pill over the page);
- the Kaart rows ('Uit je Tijdlijn') line up with the header and the filters, at every width;
- the DataGatherer Spotify card names how far the export goes that the Spotify tab already
  shows, instead of 'Nog geen gegevens'; WhatsApp's access window names the app once and sets
  the path apart in mono;
- loading and error states keep their page (Shows header, Albums skeletons, Activiteit and
  Keurer without empty bordered slivers), the Spotify tab has a real empty state;
- one choice of words in 'Welke is mooier?', a styled outcome on Pasha's confirmation card,
  AA contrast for the date of the selected conversation, and the smaller polish.

The browser side of these is in tests/ui/smoke_webkit.py; here only what can be checked
without a browser (the CSS and JS sources, and the API of the card).
"""
import json
import re
import time
from pathlib import Path

import pytest

from fixtures.vault_builder import SPOTIFY_FOLDER

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
CSS = (FRONTEND / "style.css").read_text(encoding="utf-8")


def _js(name):
    return (FRONTEND / name).read_text(encoding="utf-8")


def _rule(selector_re):
    """The declarations of the first CSS rule whose selector list matches selector_re."""
    m = re.search(selector_re + r"\s*\{([^}]*)\}", CSS)
    assert m, f"no CSS rule for {selector_re}"
    return m.group(1)


# ---------- job dock ----------

def test_the_idle_dock_is_not_widened_by_the_collapsed_rule():
    # .job-dock.collapsed:not(.compact) has more classes than .job-dock.tiny: its min-width
    # used to win and turned the tiny icon into a 200 px pill
    for m in re.finditer(r"([^{}]*)\{([^}]*)\}", CSS):
        selector, body = m.group(1).strip(), m.group(2)
        if "min-width" in body and ".job-dock.collapsed" in selector and "job-library" not in selector:
            assert ":not(.tiny)" in selector, f"{selector} widens the tiny dock"
    assert "width: max-content" in _rule(r"\.job-dock\.tiny")


# ---------- Kaart ----------

def test_the_map_layer_row_shares_the_width_of_the_header_and_filters():
    body = _rule(r"\.map-header,\s*\.map-filters,\s*\.map-layers")
    assert "max-width" in body and "margin-inline: auto" in body


def test_the_map_page_uses_the_page_gutter_on_small_windows():
    m = re.search(r"@media \(max-width: 1100px\) \{\s*\.map-page \{([^}]*)\}", CSS)
    assert m and "var(--page-gutter)" in m.group(1)


# ---------- DataGatherer ----------

def _spotify_card(client):
    return next(s for s in client.get("/api/datagatherer/sources").json()["sources"] if s["id"] == "spotify")


def test_the_spotify_card_names_the_unread_export_the_spotify_tab_already_shows(client, app, vault):
    from backend.datagatherer import cards
    folder = vault / SPOTIFY_FOLDER
    for f in (vault / "data").rglob("*.json"):
        f.unlink()
    last = 1_700_000_000
    plays = [{"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last - i * 3600)), "ms_played": 200000,
              "master_metadata_track_name": f"T{i}", "master_metadata_album_artist_name": "A", "platform": "ios"}
             for i in range(5)]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Streaming_History_Audio_2023.json").write_text(json.dumps(plays))
    cards.forget_existing_cache()
    card = _spotify_card(client)
    assert card["records"] == 0 and card["data_until"] is None
    assert card["unread_until"] == pytest.approx(last, abs=1)
    assert client.get("/api/spotify/coverage").json()["last"] == pytest.approx(card["unread_until"], abs=1)
    # Once read in, the card has its own coverage and no hint any more
    job = client.post("/api/datagatherer/sources/spotify/import").json()["job"]
    assert app.state.jobs.wait(job["id"], timeout=30)["status"] == "done"
    cards.forget_existing_cache()
    card = _spotify_card(client)
    assert card["records"] == 5 and card["unread_until"] is None and card["data_until"]


def test_a_card_without_unread_data_has_no_hint_and_no_empty_coverage_bar(client):
    card = next(s for s in client.get("/api/datagatherer/sources").json()["sources"] if s["id"] == "lastfm")
    assert card["unread_until"] is None
    js = _js("datagatherer.js")
    assert "src.unread_until" in js and "nog niet ingelezen" in js
    # No grey empty track that looks like a stuck loader
    assert "is-empty" not in js.split("function dgCoverageBar")[1].split("\nfunction ")[0]


def test_the_access_window_names_the_app_once_and_shows_the_path_in_mono(client, app):
    from backend.datagatherer import local_db
    access = local_db.access(app.state.config, "whatsapp", "WhatsApp voor Mac")
    assert access["code"] == "missing" and access["path"] and access["path"] in access["text"]
    js = _js("datagatherer.js")
    assert "access.code === 'missing' || access.title.includes(src.name)" in js
    assert "dgTextWithPath(access.text, access.path)" in js
    assert "overflow-wrap: anywhere" in _rule(r"\.dg-inline-path") and "var(--font-mono)" in _rule(r"\.dg-inline-path")


# ---------- empty, loading and error states ----------

def test_spotify_without_plays_shows_the_shared_empty_state_with_a_way_to_datagatherer():
    js = _js("app.js")
    body = js.split("function showSpotifyEmpty")[1].split("\nfunction ")[0]
    assert "emptyState(" in body and "dgShowSource('spotify')" in body
    assert "!data.total_plays" in js
    assert "function dgShowSource" in _js("datagatherer.js")


def test_albums_show_skeletons_not_a_false_empty_state_while_loading():
    js = _js("library.js")
    draw = js.split("function drawSidebar")[1].split("\n    function ")[0]
    # The skeleton comes before the 'Nog geen albums' note and the restore link
    assert draw.index("sideSkeleton()") < draw.index("Nog geen albums") < draw.index("Standaardalbums terugzetten")
    assert "!state.albumsLoaded && !state.albumsError" in draw
    init = js.split("window.albumsInit")[1].split("function showMode")[0]
    assert "$('albums-loading').hidden = false" in init and "renderFilters();" in init


def test_the_shows_header_stays_while_loading_and_after_an_error():
    js = _js("app.js")
    load = js.split("async function loadShows")[1].split("\nasync function ")[0].split("\nfunction ")[0]
    assert "showsHeader({ busy: true }), showsSkeleton()" in load
    assert "showsHeader({ busy: true }), errorState(" in load
    assert "skeletonCards(8, 'skeleton-grid-shows')" not in load


def test_activity_and_keurer_never_leave_an_empty_bordered_box():
    act = _js("activity.js")
    assert act.count("actShowDays(false)") >= 4      # no sources, no hits, both error paths
    keurer = _js("keurer.js")
    assert "keurerRenderStatsPlaceholder(e)" in keurer and "keurer-stat-skeleton" in keurer
    assert ".keurer-stats-error" in CSS


# ---------- words, cards, contrast, polish ----------

def test_elo_uses_vergeleken_for_duels_and_score_for_the_board():
    js = _js("elo.js")
    assert "foto's beoordeeld" not in js and "} beoordeeld`" not in js
    assert "vergeleken`" in js and "met een score" in js and "in de slideshow" in js


def test_pasha_confirmation_outcome_is_an_inline_message_and_cancel_is_a_button():
    js = _js("pasha-tools.js")
    card = js.split("function chatConfirmCard")[1].split("\nfunction ")[0]
    assert "'btn btn-secondary btn-sm', text: 'Annuleren'" in card
    assert "fa-triangle-exclamation" in card and "classList.toggle('is-error'" in card
    assert "var(--danger-text)" in _rule(r"\.chat-confirm-status\.is-error,\s*\.chat-confirm-status\.is-error > i")


def test_the_date_of_the_selected_conversation_uses_the_secondary_text_colour():
    assert "var(--text-secondary)" in _rule(r"\.msg-item\.active \.msg-item-date")


def test_smaller_polish_actions_footer_and_storage_label():
    assert "'Naar Bestanden'" in _js("library.js")
    msgs = _js("messages.js")
    assert "label: 'Zoekterm wissen', onClick: msgClearSearch" in msgs
    app = _js("app.js")
    modal = app.split("async function openSettingsModal")[1].split("\nfunction ")[0]
    assert 'class="modal-footer"' in modal and 'onclick="closeSettingsModal()">Sluiten' in modal
    assert "'van het totaal'" in app
