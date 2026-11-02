"""
Ollama's own tool calling for Pasha (docs/PASHA_TOOLS.md): the actions go along as 'tools', the
model's tool_calls pass the same allowlist as the <<ACTION>> tags, a proposal is still only a
card that waits for 'Uitvoeren', and the tags are the fallback (a model without tool support,
Ollama refusing the tools, or the setting). The model is the FakeOllama from conftest.
"""
import json

import pytest

from backend import assistant
from backend.routes import chat
from fixtures.vault_builder import STRAND
from test_assistant import _tool, _tree, indexed  # noqa: F401  (indexed is a fixture)


def _post(client, question="x"):
    r = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": question}]})
    assert r.status_code == 200
    return [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]


def _call(client, fake_ollama, name, args, text="", raw_args=False):
    call = {"function": {"name": name, "arguments": json.dumps(args) if raw_args else args}}
    fake_ollama.chat_lines = [{"message": {"content": text, "tool_calls": [call]}, "done": False}, {"done": True}]
    return _post(client)


def _chat_bodies(fake_ollama):
    return [json.loads(r.content) for r in fake_ollama.requests if r.url.path == "/api/chat"]


def test_the_tools_go_along_and_a_call_runs_like_a_tag(indexed, fake_ollama):
    events = _call(indexed, fake_ollama, "job_status", {})
    body = _chat_bodies(fake_ollama)[-1]
    assert body["tools"] == assistant.NATIVE_TOOLS
    system = body["messages"][0]["content"]
    assert "functies" in system and "<<ACTION" not in system
    assert "title" in _tool(events)["result"]
    # The chat keeps the call in its history as a tag (so the next question sees what Pasha did)
    assert {"raw": '<<ACTION: {"action": "job_status"}>>'} in events
    assert events[-1] == {"done": True}
    # Arguments as a JSON string (some models) work the same
    assert _tool(_call(indexed, fake_ollama, "recovery_summary", {}, raw_args=True))["result"]["link"]["view"] == "recovery"


def test_a_call_to_move_is_only_a_proposal_and_nothing_moves_before_confirm(indexed, fake_ollama, vault):
    before = _tree(vault)
    t = _tool(_call(indexed, fake_ollama, "propose_move", {"query": "strand 001", "folder": "library"}))
    assert t["pending"]["kind"] == "move" and t["pending"]["count"] >= 1
    assert _tree(vault) == before
    done = indexed.post(f"/api/chat/actions/{t['pending']['id']}/confirm")
    assert done.status_code == 200 and not (vault / STRAND).exists()


@pytest.mark.parametrize("name, args", [
    ("delete_files", {"query": "strand"}),                 # no such action
    ("library_filter", {"year": "negentien"}),             # a badly typed argument
    ("propose_move", {"query": "strand"}),                  # the destination missing
    ("open_tab", {"target": "/etc/passwd"}),                # a page action with a value the page would refuse
])
def test_calls_outside_the_allowlist_run_nothing_and_say_so(indexed, fake_ollama, vault, name, args):
    before = _tree(vault)
    events = _call(indexed, fake_ollama, name, args, text="Hier zijn ze.")
    assert _tool(events) == {"tool": "unknown", "error": assistant.NOT_RUN}
    assert {"replace": ""} in events and _tree(vault) == before
    assert not assistant.pending._items


@pytest.mark.parametrize("content, shown", [
    ('{"name": "job_status", "arguments": {}}', ""),                       # the call as plain JSON
    ('Even kijken. <tool_call>{"name": "job_status", "arguments": {}}</tool_call>', "Even kijken."),
    ('job_status()', ""),                                                   # in the style of the prompt's examples
    ('Ik kijk het na. <<ACTION: {"action": "job_status"}>>', "Ik kijk het na."),   # a tag in a native answer
])
def test_a_call_written_as_text_still_counts_and_is_hidden(indexed, fake_ollama, content, shown):
    fake_ollama.chat_lines = [{"message": {"content": content}, "done": False}, {"done": True}]
    events = _post(indexed)
    assert "result" in _tool(events)
    assert {"replace": shown} in events


def test_code_in_a_written_call_is_never_run():
    for text in ('open_tab(target=__import__("os").system("touch /tmp/x"))', "find_photos(query=open('/etc/passwd').read())",
                 "find_photos(*['a'])", "find_photos(query=" + "[" * 200 + "]" * 200 + ")"):
        server, page, tried = assistant.resolve_actions(text)
        assert server == [] and page == []
    assert assistant.resolve_actions('find_photos(query="kat", year=2019)')[0] == [
        {"action": "find_photos", "query": "kat", "year": 2019}]


def test_a_model_without_tool_support_falls_back_to_the_tags(indexed, fake_ollama):
    fake_ollama.refuse_tools = {"qwen2.5:3b"}
    fake_ollama.chat_lines = [{"message": {"content": 'Ik kijk het na. <<ACTION: {"action": "job_status"}>>'}, "done": False},
                              {"done": True}]
    events = _post(indexed)
    assert "result" in _tool(events) and not any("error" in e and "tool" not in e for e in events)
    first, second = _chat_bodies(fake_ollama)[-2:]
    assert "tools" in first and "tools" not in second and "<<ACTION" in second["messages"][0]["content"]
    # Remembered: the next question goes straight to the tags
    fake_ollama.requests.clear()
    _post(indexed)
    assert ["tools" in b for b in _chat_bodies(fake_ollama)] == [False]


def test_ollamas_capabilities_decide_and_the_setting_can_choose_the_tags(indexed, fake_ollama, app):
    fake_ollama.capabilities = {"qwen2.5:3b": ["completion"]}
    chat.forget_ollama_status()
    _post(indexed)
    assert "tools" not in _chat_bodies(fake_ollama)[-1]
    fake_ollama.capabilities = {"qwen2.5:3b": ["completion", "tools"]}
    chat.forget_ollama_status()
    _post(indexed)
    assert "tools" in _chat_bodies(fake_ollama)[-1]
    app.state.db.set_setting(chat.TOOL_FORMAT_SETTING, "tags")
    _post(indexed)
    assert "tools" not in _chat_bodies(fake_ollama)[-1]
    assert "<<ACTION" in _chat_bodies(fake_ollama)[-1]["messages"][0]["content"]


def test_the_native_tools_are_exactly_the_allowlist():
    names = [t["function"]["name"] for t in assistant.NATIVE_TOOLS]
    assert len(names) == len(set(names))
    for tool in assistant.NATIVE_TOOLS:
        fn = tool["function"]
        name, params = fn["name"], fn["parameters"]
        spec = assistant.TOOLS[name][1] if name in assistant.TOOLS else assistant.CLIENT_ACTIONS[name]
        assert set(params["properties"]) <= set(spec), name
        assert set(params["required"]) <= set(params["properties"])
        if name in assistant.TOOLS:
            assert set(assistant.TOOLS[name][2]) <= set(params["required"]), name
        assert fn["description"]
    # Every server action is offered; a single day goes through library_filter (see assistant._NATIVE)
    assert set(assistant.TOOLS) <= set(names) and "show_photos" not in names and "open_file" not in names


def test_earlier_actions_go_back_to_the_model_as_its_own_tool_calls(indexed, fake_ollama):
    history = [{"role": "user", "content": "foto's uit 2019"},
               {"role": "assistant", "content": 'Hier zijn ze. <<ACTION: {"action": "library_filter", "year": 2019}>>'},
               {"role": "user", "content": "hoi"}, {"role": "assistant", "content": "Hallo!"},
               {"role": "user", "content": "en van het strand?"}]
    fake_ollama.chat_lines = [{"message": {"content": "Hallo"}, "done": False}, {"done": True}]
    assert indexed.post("/api/chat/stream", json={"messages": history}).status_code == 200
    sent = _chat_bodies(fake_ollama)[-1]["messages"][1:]
    assert sent[1] == {"role": "assistant", "content": "",
                       "tool_calls": [{"function": {"name": "library_filter", "arguments": {"year": 2019}}}]}
    assert sent[3] == history[3] and sent[4] == history[4]
    # With the tags the history stays as the chat kept it
    indexed.app.state.db.set_setting(chat.TOOL_FORMAT_SETTING, "tags")
    indexed.post("/api/chat/stream", json={"messages": history})
    assert _chat_bodies(fake_ollama)[-1]["messages"][1:] == history


def test_a_bare_action_name_and_blank_arguments_are_understood():
    assert assistant.resolve_actions('<<ACTION: "job_status">>')[0] == [{"action": "job_status"}]
    assert assistant.resolve_actions('<<ACTION: "propose_move">>')[0] == []       # its folder is required
    found = assistant.resolve_actions('<<ACTION: {"action": "propose_export", "date_from": "", "kind": "video", "year": 2021}>>')
    assert found[0] == [{"action": "propose_export", "kind": "video", "year": 2021}]
    assert assistant.resolve_actions('<<ACTION: {"action": "find_photos", "query": ""}>>')[0] == []


EMPTY = [{"message": {"content": ""}, "done": False}, {"done": True}]


def test_an_empty_native_answer_is_asked_again_in_the_tag_format(indexed, fake_ollama):
    # qwen2.5 sometimes writes a call Ollama's parser drops: content "" and no tool_calls
    fake_ollama.chat_queue = [EMPTY, [{"message": {"content": 'Ik kijk het na. <<ACTION: {"action": "job_status"}>>'},
                                       "done": False}, {"done": True}]]
    events = _post(indexed)
    first, second = _chat_bodies(fake_ollama)[-2:]
    assert "tools" in first and "tools" not in second and "<<ACTION" in second["messages"][0]["content"]
    assert "result" in _tool(events)
    assert {"notice": "no_answer"} not in events and events[-1] == {"done": True}
    assert sum(1 for e in events if e.get("done")) == 1
    # Not remembered as a model without tools: the next question is native again
    fake_ollama.requests.clear()
    _post(indexed)
    assert ["tools" in b for b in _chat_bodies(fake_ollama)] == [True]


@pytest.mark.parametrize("native_lines", [EMPTY, [{"message": {"content": "\n "}, "done": False}, {"done": True}]])
def test_empty_twice_gives_a_short_dutch_line_never_an_empty_bubble(indexed, fake_ollama, native_lines):
    fake_ollama.chat_queue = [native_lines, EMPTY]
    events = _post(indexed)
    assert len(_chat_bodies(fake_ollama)) == 2
    text = "".join(e.get("token", "") for e in events if "token" in e)
    assert text.strip().endswith(assistant.NO_ANSWER) or assistant.NO_ANSWER in text
    assert {"notice": "no_answer"} in events and events[-1] == {"done": True}
    assert sum(1 for e in events if e.get("done")) == 1


def test_an_empty_tag_answer_is_not_retried(indexed, fake_ollama, app):
    app.state.db.set_setting(chat.TOOL_FORMAT_SETTING, "tags")
    fake_ollama.chat_lines = EMPTY
    events = _post(indexed)
    assert len(_chat_bodies(fake_ollama)) == 1
    assert {"token": assistant.NO_ANSWER} in events and {"notice": "no_answer"} in events


def test_the_chat_hides_every_tool_name_while_streaming():
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text()
    names = set(re.findall(r"'(\w+)'", re.search(r"const CHAT_TOOL_NAMES = \[(.*?)\];", js, re.S).group(1)))
    assert {t["function"]["name"] for t in assistant.NATIVE_TOOLS} | set(assistant.TOOLS) | set(assistant.CLIENT_ACTIONS) <= names
    assert f"const CHAT_NO_ANSWER = '{assistant.NO_ANSWER}';" in js


def test_the_tool_format_is_chosen_in_instellingen(indexed, fake_ollama, app):
    models = indexed.get("/api/chat/models").json()
    assert models["tool_format"] == "native" and models["tool_format_used"] == "native"
    r = indexed.post("/api/chat/tool-format", json={"format": "tags"})
    assert r.status_code == 200 and r.json() == {"tool_format": "tags", "tool_format_used": "tags"}
    assert app.state.db.get_setting(chat.TOOL_FORMAT_SETTING) == "tags"
    _post(indexed)
    assert "tools" not in _chat_bodies(fake_ollama)[-1]
    for bad in ("json", "", None, 3):
        assert indexed.post("/api/chat/tool-format", json={"format": bad}).status_code == 400
    assert indexed.post("/api/chat/tool-format", json=["tags"]).status_code in (400, 422)
    # Back to native; a model without tool support still gets the tags, and the page can say so
    indexed.post("/api/chat/tool-format", json={"format": "native"})
    fake_ollama.capabilities = {"qwen2.5:3b": ["completion"]}
    chat.forget_ollama_status()
    models = indexed.get("/api/chat/models").json()
    assert models["tool_format"] == "native" and models["tool_format_used"] == "tags"
