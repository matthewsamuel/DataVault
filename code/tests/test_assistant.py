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


# ---------- read-only tools ----------

def test_library_filter_returns_a_strip_and_a_link(indexed, fake_ollama):
    t = _tool(_say(indexed, fake_ollama, {"action": "library_filter", "year": 2019}))
    r = t["result"]
    assert r["total"] >= 1 and r["items"] and r["link"]["view"] == "albums"
    assert r["link"]["filters"] == {"year": 2019}
    assert all({"id", "path", "name"} <= set(i) for i in r["items"])


def test_find_photos_needs_the_model_and_says_so(indexed, fake_ollama):
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
    assert places.find("Parijs").kind == "city"
    assert places.find("nederland").contains(52.37, 4.89)
    assert not places.find("Nederland").contains(50.85, 4.35)    # Brussels
    assert places.find("Atlantis") is None


def test_photos_at_a_place_use_the_gps_of_the_index(indexed, fake_ollama):
    nl = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Nederland"}))["result"]
    assert nl["total"] >= 1 and nl["link"]["view"] == "map" and len(nl["link"]["bounds"]) == 4
    assert any(i["path"].endswith("strand 001.jpg") for i in nl["items"])
    fr = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Frankrijk"}))
    assert all("strand 001" not in i["path"] for i in fr.get("result", {}).get("items", []))
    none = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Japan"}))
    assert "Geen foto" in none["error"]
    unknown = _tool(_say(indexed, fake_ollama, {"action": "photos_at_place", "place": "Atlantis"}))
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
    assert not [e for e in events if "tool" in e]
    assert _tree(vault) == before
