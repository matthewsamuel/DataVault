"""
Measure how reliably Pasha turns a Dutch request into the right action, with Ollama's own
tool calling ('native': the 'tools' field of /api/chat) and with the <<ACTION: {...}>> tags
in the text ('tags'), on the same requests, the same model and the same server code.

    cd code
    venv/bin/python scripts/bench_pasha_tools.py                    # qwen2.5:3b, 3 rounds
    venv/bin/python scripts/bench_pasha_tools.py --model llama3.2:3b --rounds 5 --json out.json

Only the LOCAL Ollama (127.0.0.1:11434) is asked; nothing else goes over the network. The
archive context in the prompt comes from a generated test vault in a temp folder (the
fixtures' vault_builder), never from a real archive, and is removed afterwards.

Every answer goes through assistant.resolve_actions, exactly as in the chat: an answer counts
as a success when the checked actions it leaves are the expected ones (the right tool with the
key arguments; extra harmless arguments are allowed). A request where no action belongs
(a greeting, a sum, 'delete everything') succeeds when no action, or at least no proposal, is
made. An answer with nothing in it (no text, no call) is always a miss, also where no action
belongs, and is counted apart ('empty'); for native, such an answer is asked once more in the tag
format, as the server does, and 'native+retry' is the rate after that retry. 'follow-up' asks a second question after a first answer that acted, with that answer in the
history the way the chat keeps it. The two formats run in alternating blocks, each after a warm-up call, so both get
Ollama's prompt cache the way a conversation does. Printed per format and per set (the
'tuning' set is the one the prompt examples were written for; 'held-out' is phrased
differently and was not used to tune anything): success rate, the misses, and the latency
(time to the first token or tool call, and to the end of the answer: median, p90, max).
"""
import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx

CODE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CODE_DIR / "tests"))
os.environ.setdefault("DATAVAULT_TESTING", "1")

OLLAMA = "http://127.0.0.1:11434"


def low(v):
    return str(v).lower()


def has(key, *words):
    """The argument contains one of the words (case-insensitive)."""
    return lambda a: any(w in low(a.get(key, "")) for w in words)


def eq(key, value):
    return lambda a: low(a.get(key)) == low(value)


def tool(name, *checks):
    return ("tool", name, checks)


NO_ACTION = ("none",)
NO_PROPOSAL = ("no_proposal",)

# (set, request, expected): expected is a tool(...) or a tuple of alternatives
CASES = [
    ("tuning", "foto's uit 2019", tool("library_filter", eq("year", 2019))),
    ("tuning", "laat foto's van juli 2019 zien", tool("library_filter", eq("year", 2019), eq("month", 7))),
    ("tuning", "laat foto's van het strand zien", tool("find_photos", has("query", "strand"))),
    ("tuning", "zijn er foto's in Frankrijk?", tool("photos_at_place", has("place", "frankrijk"))),
    ("tuning", "foto's uit Nederland", tool("photos_at_place", has("place", "nederland"))),
    ("tuning", "zoek foto's van een verjaardagstaart", tool("find_photos", has("query", "taart"))),
    ("tuning", "toon mijn video's van 2021", tool("library_filter", eq("year", 2021), eq("kind", "video"))),
    ("tuning", "foto's gemaakt met mijn iPhone", tool("library_filter", has("camera_contains", "iphone"))),
    ("tuning", "zijn er bijna-dubbele foto's?", tool("series_overview")),
    ("tuning", "is er een taak bezig?", tool("job_status")),
    ("tuning", "wat heb ik laatst verplaatst?", tool("recovery_summary")),
    ("tuning", "foto's van vandaag in eerdere jaren", tool("on_this_day")),
    ("tuning", "open het album Favorieten", tool("open_smart_album", has("name", "favoriet"))),
    ("tuning", "foto's van mijn hond", tool("find_photos", has("query", "hond"))),
    ("tuning", "foto's uit Parijs", tool("photos_at_place", has("place", "parijs", "paris"))),
    ("tuning", "bewaar de beste foto van mijn 3 grootste reeksen", tool("propose_keep_best", eq("count", 3))),
    ("tuning", "exporteer mijn foto's van 2019", tool("propose_export", eq("year", 2019))),
    ("tuning", "verplaats strand 001 naar library", tool("propose_move", has("folder", "library"), has("query", "strand"))),
    ("tuning", "zoek het bestand IMG_1234", tool("search_photos", has("query", "img_1234"))),
    ("tuning", "scan op dubbele bestanden", tool("scan_duplicates")),
    ("held-out", "Kun je me de foto's van augustus 2018 laten zien?", tool("library_filter", eq("year", 2018), eq("month", 8))),
    ("held-out", "ik zoek foto's met een zonsondergang", tool("find_photos", has("query", "zonsondergang", "zon"))),
    ("held-out", "Wat heb ik allemaal in Italië gefotografeerd?", tool("photos_at_place", has("place", "itali"))),
    ("held-out", "alleen de video's uit 2020 graag", tool("library_filter", eq("year", 2020), eq("kind", "video"))),
    ("held-out", "Welke foto's maakte ik in Rome?", tool("photos_at_place", has("place", "rome"))),
    ("held-out", "draait er nog iets op de achtergrond?", tool("job_status")),
    ("held-out", "laat zien wat ik kan terugzetten", tool("recovery_summary")),
    ("held-out", "toon foto's met een kat erop", tool("find_photos", has("query", "kat"))),
    ("held-out", "maak een export van al mijn strandfoto's", tool("propose_export", has("query", "strand"))),
    ("held-out", "zet IMG_0042 in de map Archief", tool("propose_move", has("folder", "archief"))),
    ("held-out", "open de map Vakantie", tool("open_folder", has("target", "vakantie"))),
    ("held-out", "staande foto's uit 2022", tool("library_filter", eq("year", 2022), eq("orientation", "portrait"))),
    ("held-out", "foto's van 14 juli 2019",
     (tool("show_photos", eq("date", "2019-07-14")),
      tool("library_filter", eq("date_from", "2019-07-14"), eq("date_to", "2019-07-14")))),
    ("held-out", "hou van de 5 grootste reeksen alleen de beste over", tool("propose_keep_best", eq("count", 5))),
    ("held-out", "wat gebeurde er vroeger op deze dag?", tool("on_this_day")),
    ("no-action", "Hoi Pasha, hoe gaat het?", NO_ACTION),
    ("no-action", "Wat kun jij allemaal?", NO_ACTION),
    ("no-action", "Hoeveel is 12 keer 12?", NO_ACTION),
    ("no-action", "Bedankt, dat was het!", NO_ACTION),
    ("no-action", "Verwijder al mijn foto's van 2019", NO_PROPOSAL),
    # A second question after a first answer that acted: the history holds that answer the way the
    # chat keeps it (tags: the model's own text with its tag; native: the call written as a tag)
    ("follow-up", [("foto's uit 2019", {"action": "library_filter", "year": 2019}), "en die van het strand?"],
     tool("find_photos", has("query", "strand"))),
    ("follow-up", [("zijn er foto's in Frankrijk?", {"action": "photos_at_place", "place": "Frankrijk"}), "en in Spanje?"],
     tool("photos_at_place", has("place", "spanje", "spain"))),
    ("follow-up", [("laat foto's van mijn hond zien", {"action": "find_photos", "query": "hond"}), "is er een taak bezig?"],
     tool("job_status")),
    ("follow-up", [("toon mijn video's van 2021", {"action": "library_filter", "year": 2021, "kind": "video"}),
                   "exporteer die maar"], tool("propose_export", eq("year", 2021))),
    ("follow-up", [("foto's uit 2019", {"action": "library_filter", "year": 2019}), "dank je!"], NO_ACTION),
    # After an action turn, a request for a different action (native once came back empty here often)
    ("follow-up", [("Laat foto's uit 2019 zien", {"action": "library_filter", "year": 2019}),
                   "exporteer mijn foto's van 2019"], tool("propose_export", eq("year", 2019))),
    ("follow-up", [("Laat foto's van het strand zien", {"action": "find_photos", "query": "strand"}),
                   "wat gebeurde er vroeger op deze dag?"], tool("on_this_day")),
]

LEAD_IN = "Hier zijn ze."


def _matches(expected, actions):
    if expected == NO_ACTION:
        return not actions
    if expected == NO_PROPOSAL:
        return not any(a["action"].startswith("propose_") for a in actions)
    options = expected if isinstance(expected[0], tuple) else (expected,)
    for _, name, checks in options:
        if any(a["action"] == name and all(c(a) for c in checks) for a in actions):
            return True
    return False


def conversation(question, fmt):
    """The chat messages for a case: one question, or earlier turns plus the last question."""
    if isinstance(question, str):
        return [{"role": "user", "content": question}]
    from backend import assistant
    messages = []
    for turn in question[:-1]:
        asked, action = turn
        tag = f"<<ACTION: {json.dumps(action, ensure_ascii=False)}>>"
        messages += [{"role": "user", "content": asked},
                     {"role": "assistant", "content": tag if fmt == "native" else f"{LEAD_IN} {tag}"}]
    messages.append({"role": "user", "content": question[-1]})
    # As the server sends it (routes/chat.py _ollama_messages)
    return assistant.native_history(messages) if fmt == "native" else messages


def last_question(question):
    return question if isinstance(question, str) else question[-1]


def ask(client, model, system, question, fmt, tools):
    """One request as the chat sends it (streaming): (text, tool calls, first, total seconds)."""
    payload = {"model": model, "stream": True, "options": {"num_ctx": 4096},
               "messages": [{"role": "system", "content": system}] + conversation(question, fmt)}
    if fmt == "native":
        payload["tools"] = tools
    text, calls, first = "", [], None
    start = time.perf_counter()
    with client.stream("POST", f"{OLLAMA}/api/chat", json=payload) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Ollama {r.status_code}: {r.read()[:200]!r}")
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            msg = chunk.get("message") or {}
            if (msg.get("content") or msg.get("tool_calls")) and first is None:
                first = time.perf_counter() - start
            text += msg.get("content") or ""
            calls += msg.get("tool_calls") or []
            if chunk.get("done"):
                break
    total = time.perf_counter() - start
    return text, calls, first if first is not None else total, total


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen2.5:3b")
    ap.add_argument("--rounds", type=int, default=3, help="every request this many times per format")
    ap.add_argument("--formats", default="native,tags")
    ap.add_argument("--json", help="also write every answer to this file")
    args = ap.parse_args()
    formats = args.formats.split(",")

    try:
        httpx.get(f"{OLLAMA}/api/version", timeout=2)
    except httpx.HTTPError:
        sys.exit("Ollama draait niet op 127.0.0.1:11434; er is niets gemeten.")

    from fixtures.vault_builder import build_test_vault, write_config
    tmp = Path(tempfile.mkdtemp(prefix="pasha-bench-"))
    try:
        vault = build_test_vault(tmp / "vault")
        cfg = write_config(vault, tmp / "config.json")
        from backend.main import create_app
        create_app(str(cfg))
        from backend import assistant
        from backend.routes import chat

        def system_for(question, fmt):
            return chat.build_system_prompt(question, fmt) + chat.history_context(question)

        rows = []
        with httpx.Client(timeout=180) as client:
            for rnd in range(args.rounds):
                order = formats if rnd % 2 == 0 else list(reversed(formats))
                for fmt in order:
                    ask(client, args.model, system_for("hoi", fmt), "hoi", fmt, assistant.NATIVE_TOOLS)  # warm-up
                    for group, question, expected in CASES:
                        text, calls, first, total = ask(client, args.model, system_for(last_question(question), fmt),
                                                        question, fmt, assistant.NATIVE_TOOLS)
                        server, page, tried = assistant.resolve_actions(text, calls)
                        actions = server + page
                        empty = not (actions or tried or assistant.strip_actions(text))
                        ok = not empty and _matches(expected, actions)
                        retried = ok
                        if empty and fmt == "native":
                            # What the server does with an empty native answer (routes/chat.py stream_with_tools)
                            r_text, r_calls, _, r_total = ask(client, args.model,
                                                              system_for(last_question(question), "tags"),
                                                              question, "tags", assistant.NATIVE_TOOLS)
                            r_server, r_page, r_tried = assistant.resolve_actions(r_text, r_calls)
                            r_actions = r_server + r_page
                            retried = bool(r_actions or r_tried or assistant.strip_actions(r_text)) and _matches(expected, r_actions)
                            total += r_total
                        question = question if isinstance(question, str) else " / ".join(
                            t[0] if isinstance(t, tuple) else t for t in question)
                        rows.append({"round": rnd, "format": fmt, "set": group, "question": question, "ok": ok,
                                     "empty": empty, "ok_after_retry": retried,
                                     "actions": actions, "text": text, "calls": calls,
                                     "first": round(first, 3), "total": round(total, 3)})
                        mark = "ok " if ok else ("EMP" if empty else "MIS")
                        print(f"[{rnd}] {fmt:6} {mark} {total:5.2f}s  {question}  ->  "
                              f"{json.dumps(actions, ensure_ascii=False)[:140]}", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    for fmt in formats:
        mine = [r for r in rows if r["format"] == fmt]
        print(f"== {fmt} ({args.model}, {args.rounds} rounds) ==")
        for group in ("tuning", "held-out", "no-action", "follow-up", None):
            part = [r for r in mine if group is None or r["set"] == group]
            okay = sum(r["ok"] for r in part)
            print(f"  {group or 'all':9} {okay}/{len(part)} = {100 * okay / len(part):.1f}%")
        empties = sum(r["empty"] for r in mine)
        print(f"  empty     {empties}/{len(mine)} (no text, no call: a miss)")
        if fmt == "native":
            after = sum(r["ok_after_retry"] for r in mine)
            print(f"  native+retry {after}/{len(mine)} = {100 * after / len(mine):.1f}% (an empty answer asked again with tags)")
        firsts, totals = [r["first"] for r in mine], [r["total"] for r in mine]
        print(f"  first token/call: median {statistics.median(firsts):.2f}s  p90 {pct(firsts, .9):.2f}s  max {max(firsts):.2f}s")
        print(f"  whole answer:     median {statistics.median(totals):.2f}s  p90 {pct(totals, .9):.2f}s  max {max(totals):.2f}s"
              f"  mean {statistics.mean(totals):.2f}s")
        misses = sorted({r["question"] for r in mine if not r["ok"]})
        for q in misses:
            n = sum(1 for r in mine if r["question"] == q and not r["ok"])
            sample = next(r for r in mine if r["question"] == q and not r["ok"])
            print(f"    miss {n}x  {q}  ->  {json.dumps(sample['actions'], ensure_ascii=False)[:120]}"
                  f"  {sample['text'][:80]!r}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
