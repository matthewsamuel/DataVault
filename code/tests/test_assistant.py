"""
Pasha's tools (step 3.8, backend/assistant.py): allowlisted actions, read-only results,
and pending actions for everything that moves files (nothing moves without the confirm call).
The model is the FakeOllama from conftest; test_semantic's fake encoder stands in for CLIP.
"""
import json
import os
from pathlib import Path

import pytest

from backend import assistant, places
from fixtures.vault_builder import STRAND, BURST
from test_media_index import _index
from test_near_dups import _run as _phash
from test_semantic import fake, _embed  # noqa: F401  (fixture + helper)


def _say(client, fake_ollama, action: dict, text="Even kijken."):
    tag = f"<<ACTION: {json.dumps(action)}>>"
    fake_ollama.chat_lines = [{"message": {"content": f"{text}\n{tag}"}, "done": False}, {"done": True}]
    r = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 200
    return [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]


def _tool(events):
    found = [e for e in events if "tool" in e]
    assert len(found) == 1, events
    return found[0]


def _tree(vault: Path):
    out = {}
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d != ".datavault_cache"]
        for n in files:
            p = Path(root) / n
            out[str(p)] = p.stat().st_size
    return out


@pytest.fixture
def indexed(client, config):
    _index(client)
    return client


# ---------- the allowlist ----------

def test_only_allowlisted_actions_with_valid_arguments_are_kept():
    ok = assistant.parse_actions('<<ACTION: {"action": "find_photos", "query": "strand", "year": "2019"}>>')
    assert ok == [{"action": "find_photos", "query": "strand", "year": 2019}]
    bad = [
        '<<ACTION: {"action": "delete_file", "path": "/x"}>>',
        '<<ACTION: {"action": "find_photos"}>>',                                  # missing query
        '<<ACTION: {"action": "find_photos", "query": 5}>>',
        '<<ACTION: {"action": "library_filter", "year": 99999}>>',
        '<<ACTION: {"action": "propose_move", "folder": "x", "limit": 5000}>>',
        '<<ACTION: not json>>',
    ]
    for text in bad:
        assert assistant.parse_actions(text) == [], text
    # unknown extra arguments (such as a destination path) are not passed on
    kept = assistant.parse_actions('<<ACTION: {"action": "propose_keep_best", "count": 2, "dst": "/etc"}>>')
    assert kept == [{"action": "propose_keep_best", "count": 2}]


def test_the_models_other_spellings_of_an_action_are_understood_and_hidden():
    for text in ('Ja. <|ACTION: {"action": "on_this_day"}|>', 'Ja.\n{"action": "on_this_day"}',
                 'Ja. <<ACTION {"action": "on_this_day"}>>'):
        assert assistant.parse_actions(text) == [{"action": "on_this_day"}], text
        assert assistant.strip_actions(text) == "Ja."
    assert assistant.strip_actions('Even <<ACTION: {"action": "on_th') == "Even"


# ---------- read-only tools ----------

def test_library_filter_returns_a_strip_and_a_link(indexed, fake_ollama):
    t = _tool(_say(indexed, fake_ollama, {"action": "library_filter", "year": 2019}))
    r = t["result"]
    assert r["total"] >= 1 and r["items"] and r["link"]["view"] == "albums"
    assert r["link"]["filters"] == {"year": 2019}
    assert all({"id", "path", "name"} <= set(i) for i in r["items"])


def test_find_photos_needs_the_model_and_says_so(indexed, fake_ollama, monkeypatch):
    from backend import semantic
    monkeypatch.setattr(semantic, "model_ready", lambda c: False)
    t = _tool(_say(indexed, fake_ollama, {"action": "find_photos", "query": "strand"}))
    assert "Slim zoeken" in t["error"]


def test_find_photos_uses_semantic_search(indexed, fake_ollama, fake, config, tmp_path, monkeypatch):
    from backend import semantic
    monkeypatch.setattr(semantic, "model_ready", lambda c: True)
    _embed(indexed)
    t = _tool(_say(indexed, fake_ollama, {"action": "find_photos", "query": "blauw"}))
    assert t["result"]["items"] and t["result"]["link"] == {"view": "search", "query": "blauw",
                                                            "label": "Meer in Slim zoeken"}


def test_places_are_found_offline_in_dutch_and_english():
    assert places.find("Frankrijk").name == "France"
    assert places.find("France").name == "France"
    assert places.find("belgie").name == places.find("Belgium").name == "Belgium"   # Dutch 'België', no accent
    assert places.find("Engeland").name == "United Kingdom"                           # alias, not the Kaart name
    assert places.find("Verenigd Koninkrijk").name == "United Kingdom"
    assert places.find("Verenigde Staten van Amerika").contains(39.0, -77.5)
    assert places.find("Parijs").kind == "city"
    assert places.find("nederland").contains(52.37, 4.89)
    assert not places.find("Nederland").contains(50.85, 4.35)    # Brussels
    assert places.find("Atlantis") is not None and places.find("Nergenshuizen") is None   # Atlantis is a town in South Africa


def test_places_also_know_provinces_and_smaller_towns():
    toscane = places.find("Toscane")
    assert toscane.kind == "region" and toscane.contains(43.77, 11.25) and not toscane.contains(44.49, 11.34)  # Florence, not Bologna
    assert places.find("gelderland").contains(52.21, 5.97)                                              # Apeldoorn
    limburg = places.find("Limburg")            # the Dutch and the Belgian province together
    assert limburg.contains(50.85, 5.69) and limburg.contains(50.93, 5.33) and not limburg.contains(51.44, 5.47)
    assert places.find("Olhao").kind == "city" and places.find("Olhão").contains(37.03, -7.84)
    assert places.find("Groningen").kind == "city"     # a big town wins over the province of the same name


def test_a_name_that_is_a_place_here_and_abroad_means_the_one_here():
    """Population alone does not decide: Middelburg is in Zeeland, not in South Africa; Bali is the Indonesian
    province, not a town in India; Florida in Uruguay is not part of the American state."""
    middelburg = places.find("Middelburg")
    assert middelburg.kind == "city" and middelburg.contains(51.50, 3.61)
    assert places.find("Ede").contains(52.04, 5.66)
    assert places.find("Soest").contains(52.17, 5.30) and places.find("Harlingen").contains(53.17, 5.42)
    bali = places.find("Bali")
    assert bali.kind == "region" and bali.contains(-8.65, 115.22)                      # Denpasar
    assert places.find("Holland").name == "Netherlands"
    florida = places.find("Florida")
    assert florida.contains(28.5, -81.4) and florida.bbox[0] > 20                       # Orlando; not down to Uruguay
    lagos = places.candidates("Lagos")
    assert {p.code for p in lagos} >= {"NGA", "PRT"}
    assert places.find("Limburg").contains(50.85, 5.69) and places.find("Limburg").contains(50.93, 5.33)
    # A city is a circle: a town in the corner of its box is not 'bij Amsterdam'
    amsterdam = places.find("Amsterdam")
    assert amsterdam.contains(52.37, 4.89) and not amsterdam.contains(52.13, 5.07)        # Maarssen, 27 km


def _put_first_photo_at(config, lat, lon):
    import sqlite3
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        conn.execute("UPDATE media SET lat = ?, lon = ? WHERE ROWID = (SELECT MIN(ROWID) FROM media WHERE lat IS NOT NULL)",
                     (lat, lon))


def test_pasha_finds_the_place_where_the_photos_are(indexed, fake_ollama, config):
    _put_first_photo_at(config, 37.10, -8.67)                   # Lagos in the Algarve, not Lagos in Nigeria
    lagos = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Lagos"}))["result"]
    assert lagos["total"] == 1 and lagos["title"] == "1 foto bij Lagos"
    s, w, n, e = lagos["link"]["bounds"]
    assert s < 37.10 < n and w < -8.67 < e
    _put_first_photo_at(config, 51.4988, 3.6136)                # Middelburg, Zeeland
    mid = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Middelburg"}))["result"]
    assert mid["total"] == 1 and mid["lines"] == []
    _put_first_photo_at(config, -8.65, 115.22)                  # Denpasar
    bali = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Bali"}))["result"]
    assert bali["title"] == "1 foto in Bali" and bali["lines"] == ["Denpasar: 1 foto"]


def test_a_city_answer_counts_the_city_itself_first(indexed, fake_ollama, config):
    import sqlite3
    with sqlite3.connect(str(config.DB_PATH)) as conn:
        ids = [r[0] for r in conn.execute("SELECT ROWID FROM media ORDER BY ROWID LIMIT 4")]
        spots = [(52.373, 4.893), (52.36, 4.88), (52.52, 5.07), (52.13, 5.07)]     # Amsterdam twice, Volendam, Maarssen
        for rowid, (lat, lon) in zip(ids, spots):
            conn.execute("UPDATE media SET lat = ?, lon = ?, missing = 0 WHERE ROWID = ?", (lat, lon, rowid))
        conn.execute("UPDATE media SET lat = NULL, lon = NULL WHERE ROWID NOT IN (%s)" % ",".join("?" * len(ids)), ids)
    ams = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Amsterdam"}))["result"]
    assert ams["title"] == "3 foto's bij Amsterdam"                     # Maarssen (27 km) is not
    assert ams["lines"] == ["Amsterdam: 2 foto's", "Volendam: 1 foto"]


def test_place_lines_name_at_most_a_sample(monkeypatch):
    calls = []
    monkeypatch.setattr(places, "describe_parts", lambda lat, lon: calls.append(1) or {
        "town": "Faro", "km": 1.0, "sea": False, "region": "Faro", "country": "Portugal"})
    lines = assistant._place_lines([(i, 37.0, -7.9) for i in range(799)], "Portugal")
    assert len(calls) <= assistant.PLACE_SAMPLE and lines == ["Faro: ~800 foto's"]
    monkeypatch.setattr(places, "describe_parts", lambda lat, lon: {
        "town": None, "km": None, "sea": True, "region": "Zuid-Egeïsche Eilanden", "country": "Griekenland"})
    assert assistant._place_lines([(1, 36.5, 25.4)], "Griekenland") == ["Voor de kust van de Zuid-Egeïsche Eilanden: 1 foto"]


def test_photos_at_a_place_use_the_gps_of_the_index(indexed, fake_ollama):
    nl = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Nederland"}))["result"]
    assert nl["total"] >= 1 and nl["link"]["view"] == "map" and len(nl["link"]["bounds"]) == 4
    assert any(i["path"].endswith("strand 001.jpg") for i in nl["items"])
    fr = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Frankrijk"}))
    assert all("strand 001" not in i["path"] for i in fr.get("result", {}).get("items", []))
    none = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Japan"}))
    assert "Geen foto" in none["error"]
    # Under the answer: where in the country, from the offline place names
    assert nl["lines"] == ["Amsterdam: 1 foto"] if nl["total"] == 1 else nl["lines"][0].startswith("Amsterdam: ")
    holland = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Noord-Holland"}))["result"]
    assert holland["title"].startswith("1 foto's in Noord-Holland") or "in Noord-Holland" in holland["title"]
    assert any(i["path"].endswith("strand 001.jpg") for i in holland["items"])
    paris = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "parijs"}))
    assert paris["error"] == "Geen foto's met een locatie in Parijs."      # the user's word, not 'Paris'
    unknown = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Nergenshuizen"}))
    assert "niet" in unknown["error"]


def test_series_overview_waits_for_the_comparison(indexed, fake_ollama):
    t = _tool(_say(indexed, fake_ollama, {"action": "series_overview"}))
    assert "nog niet berekend" in t["error"]
    _phash(indexed)
    t = _tool(_say(indexed, fake_ollama, {"action": "series_overview"}))
    assert t["result"]["items"] and t["result"]["link"]["view"] == "series"


def test_status_tools_answer(indexed, fake_ollama):
    jobs = _tool(_say(indexed, fake_ollama, {"action": "job_status"}))["result"]
    assert "title" in jobs
    rec = _tool(_say(indexed, fake_ollama, {"action": "recovery_summary"}))["result"]
    assert rec["link"]["view"] == "recovery"
    otd = _tool(_say(indexed, fake_ollama, {"action": "on_this_day"}))
    assert "result" in otd or "error" in otd


def test_tools_say_so_while_the_library_is_not_ready(client, fake_ollama):
    t = _tool(_say(client, fake_ollama, {"action": "library_filter", "year": 2019}))
    assert "ingelezen" in t["error"]


# ---------- proposals and the confirm call ----------

def test_a_move_is_only_proposed_and_nothing_moves_without_confirm(indexed, fake_ollama, vault):
    before = _tree(vault)
    t = _tool(_say(indexed, fake_ollama, {"action": "propose_move", "query": "strand 001", "folder": "library"}))
    p = t["pending"]
    assert p["kind"] == "move" and p["count"] >= 1 and p["items"][0]["to"] == "library"
    assert _tree(vault) == before
    done = indexed.post(f"/api/chat/actions/{p['id']}/confirm")
    assert done.status_code == 200 and done.json()["moved"] == p["count"]
    assert _tree(vault) != before
    assert not (vault / STRAND).exists() and (vault / "library" / "strand 001.jpg").exists()
    # in the recovery center, with an undo that puts it back
    entries = indexed.get("/api/recovery").json()["entries"]
    assert entries
    undo = entries[0]["undo"]
    r = indexed.post(undo["url"], json=undo["body"])
    assert r.status_code == 200, r.text
    assert (vault / STRAND).exists()


def test_confirm_works_once_and_forged_or_expired_ids_fail(indexed, fake_ollama, vault):
    p = _tool(_say(indexed, fake_ollama, {"action": "propose_move", "query": "strand 001", "folder": "library"}))["pending"]
    assert indexed.post("/api/chat/actions/forged-id/confirm").status_code == 404
    assert indexed.post(f"/api/chat/actions/{p['id']}/confirm").status_code == 200
    again = indexed.post(f"/api/chat/actions/{p['id']}/confirm")
    assert again.status_code == 404 and "verlopen" in again.json()["detail"]
    q = _tool(_say(indexed, fake_ollama, {"action": "propose_move", "query": "IMG_0002", "folder": "library"}))["pending"]
    for record in assistant.pending._items.values():
        record["expires"] = 0
    assert indexed.post(f"/api/chat/actions/{q['id']}/confirm").status_code == 404


def test_cancel_forgets_the_proposal(indexed, fake_ollama, vault):
    before = _tree(vault)
    p = _tool(_say(indexed, fake_ollama, {"action": "propose_move", "query": "strand 001", "folder": "library"}))["pending"]
    assert indexed.post(f"/api/chat/actions/{p['id']}/cancel").json() == {"cancelled": True}
    assert indexed.post(f"/api/chat/actions/{p['id']}/confirm").status_code == 404
    assert _tree(vault) == before


@pytest.mark.parametrize("folder", ["/etc", "../../tmp", "/Users", "Bestaat niet"])
def test_a_destination_outside_the_vault_or_missing_is_refused(indexed, fake_ollama, vault, folder):
    before = _tree(vault)
    t = _tool(_say(indexed, fake_ollama, {"action": "propose_move", "query": "strand 001", "folder": folder}))
    assert "error" in t and "pending" not in t
    assert _tree(vault) == before


def test_a_source_path_outside_the_vault_is_refused(indexed, fake_ollama, tmp_path):
    outside = tmp_path / "buiten.jpg"
    outside.write_bytes(b"x")
    t = _tool(_say(indexed, fake_ollama, {"action": "propose_move", "path": str(outside), "folder": "library"}))
    assert "error" in t and outside.exists()


def test_a_file_that_changed_place_is_not_moved_by_a_stale_proposal(indexed, fake_ollama, vault):
    p = _tool(_say(indexed, fake_ollama, {"action": "propose_move", "path": str(vault / STRAND), "folder": "library"}))["pending"]
    (vault / STRAND).rename(vault / STRAND.replace("strand 001", "ergens anders"))
    done = indexed.post(f"/api/chat/actions/{p['id']}/confirm").json()
    assert done["moved"] == 0 and done["failed"]


def test_keep_best_is_proposed_then_confirmed_and_can_be_undone(indexed, fake_ollama, vault):
    _phash(indexed)
    before = _tree(vault)
    p = _tool(_say(indexed, fake_ollama, {"action": "propose_keep_best", "count": 1}))["pending"]
    assert p["kind"] == "keep_best" and p["count"] >= 1 and p["items"]
    assert _tree(vault) == before
    done = indexed.post(f"/api/chat/actions/{p['id']}/confirm").json()
    assert done["moved"] >= 1 and done["actions"]
    assert indexed.post("/api/recovery/undo", json={"action_id": done["actions"][0]}).status_code == 200
    assert _tree(vault) == before


def test_export_is_a_proposal_that_opens_the_export_window(indexed, fake_ollama, vault):
    p = _tool(_say(indexed, fake_ollama, {"action": "propose_export", "year": 2019}))["pending"]
    assert p["kind"] == "export"
    done = indexed.post(f"/api/chat/actions/{p['id']}/confirm").json()
    assert done["open_export"] and all("path" in f for f in done["open_export"])


def test_the_model_cannot_name_a_path_for_keep_best_or_a_delete(indexed, fake_ollama, vault):
    before = _tree(vault)
    events = _say(indexed, fake_ollama, {"action": "delete_file", "path": str(vault / STRAND)})
    # nothing runs; the chat only says it could not do that
    assert [e for e in events if "tool" in e] == [{"tool": "unknown", "error": assistant.NOT_RUN}]
    assert _tree(vault) == before


def test_keep_best_card_shows_the_exact_copies_that_move_along(indexed, fake_ollama, vault):
    import shutil
    copy = vault / "RAW/misc/klein kopie.jpg"
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vault / "RAW/2022/bursts/strand klein.jpg", copy)
    _index(indexed)
    _phash(indexed)
    before = _tree(vault)
    p = _tool(_say(indexed, fake_ollama, {"action": "propose_keep_best", "count": 5}))["pending"]
    listed = p["items"] + [None] * p["more"]
    assert len(listed) == p["count"]
    twins = {"strand klein.jpg", "klein kopie.jpg"}
    photos = {i["name"] for i in p["items"] if "exacte kopie" not in i["to"]}
    copies = {i["name"] for i in p["items"] if "exacte kopie" in i["to"]}
    assert photos & twins, p            # one of the twins goes (the smallest of its series) ...
    assert copies == twins - photos and "1 exacte kopie" in p["title"]   # ... and the card shows the other
    done = indexed.post(f"/api/chat/actions/{p['id']}/confirm").json()
    assert done["moved"] == p["count"], (done, p)
    for action in done["actions"]:
        assert indexed.post("/api/recovery/undo", json={"action_id": action}).status_code == 200
    assert _tree(vault) == before


# ---------- an action Pasha cannot run is never silent ----------

def _raw(client, fake_ollama, content):
    fake_ollama.chat_lines = [{"message": {"content": content}, "done": False}, {"done": True}]
    r = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 200
    return [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]


@pytest.mark.parametrize("content", [
    'Dit vond ik. <<ACTION: {"action": "find_photos", "mood": "blij"}>>',       # missing query, no date
    'Hier zijn ze. <<ACTION: {"action": "delete_everything"}>>',
    'Hier zijn ze. <<ACTION: {"action": "show_photos", "date": "gisteren"}>>',
    'Ik kijk het na. <<ACTION: not json>>',
    'Even kijken. <<ACTION: {"action": "library_filter", "ye',                    # cut off
    'Dit vond ik.',                                                               # a promise without any action
    'Hier zijn ze...',
])
def test_an_action_that_cannot_run_is_reported_not_swallowed(indexed, fake_ollama, content):
    events = _raw(indexed, fake_ollama, content)
    replaced = [e["replace"] for e in events if "replace" in e]
    assert replaced == [""], events                 # the claim 'Hier zijn ze.' is gone
    tool = _tool(events)
    assert tool["error"] == assistant.NOT_RUN
    assert events[-1].get("done")


def test_near_miss_actions_are_mapped_onto_real_ones(indexed, fake_ollama):
    cases = {
        '{"action": "find_photos", "date": "2019-07-14"}': {"action": "library_filter", "date_from": "2019-07-14",
                                                            "date_to": "2019-07-14"},
        '{"action": "find_photos", "year": 2019}': {"action": "library_filter", "year": 2019},
        '{"action": "search_photos", "query": "2019"}': {"action": "library_filter", "year": 2019},
        '{"action": "show_photos", "date": "2019-07"}': {"action": "library_filter", "year": 2019, "month": 7},
        '{"action": "photos_at_place", "place": "Nederland"}}': {"action": "photos_at_place", "place": "Nederland"},
    }
    for raw, want in cases.items():
        assert assistant.parse_actions(f"Hier. <<ACTION: {raw}>>") == [want], raw
    tool = _tool(_raw(indexed, fake_ollama, 'Hier zijn ze. <<ACTION: {"action": "search_photos", "query": "2019"}>>'))
    assert tool["tool"] == "library_filter" and tool["result"]["total"] > 0


def test_a_page_action_stays_for_the_page_in_its_own_spelling(indexed, fake_ollama):
    # valid for the chat screen itself: no error, and the tag reaches the page as <<ACTION: ...>>
    events = _raw(indexed, fake_ollama, 'Ik open hem. <|ACTION: {"action": "open_tab", "target": "keurer"}|>')
    assert not [e for e in events if "tool" in e]
    assert [e["replace"] for e in events if "replace" in e] == \
        ['Ik open hem.\n<<ACTION: {"action": "open_tab", "target": "keurer"}>>']
    # a normal answer without any action is left alone, also when it starts like a promise
    for text in ("Hallo! Hoe gaat het?", "Dit vond ik een mooie reis.", "Alsjeblieft, graag gedaan!"):
        events = _raw(indexed, fake_ollama, text)
        assert not [e for e in events if "tool" in e or "replace" in e], text
