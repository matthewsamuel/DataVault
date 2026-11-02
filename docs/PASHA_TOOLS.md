# Pasha's actions: Ollama's own tool calling or tags in the text

Pasha (the chat) acts on a request by naming an action: show photos of 2019, find the beach,
propose a move. There are two ways for a local model to name one:

- **Tags** (the format since step 3.8): the prompt explains `<<ACTION: {"action": ..., ...}>>`
  and the model writes it at the end of its answer.
- **Native**: the actions go along as Ollama `tools` (the `tools` field of `/api/chat`, JSON
  schemas with Dutch descriptions in `assistant.NATIVE_TOOLS`). The model answers with
  `message.tool_calls`.

Either way, every action passes the same allowlist (`assistant.clean_action`, `_near_miss`,
`client_action`), a read-only action runs at once, and a move, keep-best or export only makes a
card that waits for **Uitvoeren** (`/api/chat/actions/<id>/confirm`). Nothing about that
confirmation changed.

**Decision: native is the default** (`chat.DEFAULT_TOOL_FORMAT`). It was right in 96.9% of the
requests against 88.9% for the tags, and 92.0% against 77.3% on requests that were never used
for tuning. The tags stay as the fallback (see below).

## How it was measured

`code/scripts/bench_pasha_tools.py` asks the local Ollama (127.0.0.1:11434, `qwen2.5:3b`, the
default model) the same requests in both formats. It uses the server's own prompt
(`chat.build_system_prompt(question, format)`) on a generated test vault, and scores every answer
with `assistant.resolve_actions`, exactly as the chat does. A request counts as right when the
checked actions include the expected tool with its key arguments. The two formats run in
alternating blocks after a warm-up call, five rounds each.

The 45 requests are in four sets:

| Set | Requests | What |
| --- | --- | --- |
| tuning | 20 | The kinds of requests the 3.8 prompt and its examples were written for |
| held-out | 15 | Phrased differently, other places, a single day, portrait photos, "zet ... in de map" |
| no-action | 5 | A greeting, a sum, thanks, "wat kun jij", and "Verwijder al mijn foto's van 2019" (right: no proposal at all) |
| follow-up | 5 | A second question after an answer that acted, with that answer in the history as the chat keeps it |

## Results

Final run (five rounds, 225 answers per format):

| | Native | Tags |
| --- | --- | --- |
| tuning | 100/100 (100%) | 96/100 (96%) |
| held-out | 69/75 (92.0%) | 58/75 (77.3%) |
| no-action | 25/25 (100%) | 21/25 (84%) |
| follow-up | 24/25 (96%) | 25/25 (100%) |
| **all** | **218/225 (96.9%)** | **200/225 (88.9%)** |
| whole answer, median | 0.53 s | 0.56 s |
| whole answer, p90 / max | 1.51 s / 3.99 s | 1.06 s / 3.23 s |
| first visible token or call, median | 0.47 s | 0.06 s |

Where the tags went wrong: "Verwijder al mijn foto's van 2019" became a proposal to move to a
folder "verwijderd" (3 of 5; it would still have needed Uitvoeren and an existing folder),
"zet IMG_0042 in de map Archief" opened the folder instead (5 of 5), "staande foto's" lost the
orientation (5 of 5), a file-name search became `show_photos` with the literal "YYYY-MM-DD", and
a country was sent to the content search. Native missed a single day twice (it took the month),
"zet IMG_0042 in de map Archief" twice (a file-name search instead), and three answers once.

**Latency.** On a warm prompt cache (the normal case in a conversation, because the system prompt
and the earlier turns stay the same) native is a little faster: it writes no lead-in sentence.
With the tags the first words appear sooner, but the action, and so the result strip, only comes
at the end of the answer in both formats. On a cold cache (the first question after the model
loaded, or after another program used Ollama) native is slower, because the tool schemas make the
prompt longer:

| | Native | Tags |
| --- | --- | --- |
| prompt tokens | 2,285 | 1,449 |
| cold cache, median of 7 | 3.43 s | 2.65 s |
| warm cache, median of 7 | 0.51 s | 0.82 s |

The p90 in the table above comes from those cold-cache answers: other work on the same machine
and Ollama evicted the cache now and then, in both formats.

## How the numbers came about (honestly)

The prompt changed between runs, so the earlier runs are not the final numbers:

1. First native prompt: native 92.5%, tags 84.5% (40 requests, no follow-ups).
2. Calls the model writes as text instead of as a call are read too (`{"name": ..., "arguments":
   ...}`, `<tool_call>...</tool_call>`, and `find_photos(query="kat")` in the style of the
   examples, parsed with `ast.literal_eval`, never evaluated). The schemas were trimmed, and an
   iPhone example was added for a tuning miss: native 93.5%, tags 85.0%.
3. `show_photos` was taken out of the native tools, because the model used it for whole months
   ("juli 2019" became 2019-07-01, a tuning miss). The follow-ups were added: native 92.9%, tags
   87.6%, but native follow-ups only 80%. With a bare tag in its history, the model started
   writing tags itself, and wrote them badly.
4. Final: in native mode, earlier actions go back to the model as its own `tool_calls`
   (`assistant.native_history`). A small test of the three ways to send the history gave 22/25
   for tags in the history, 24/25 for tool calls, and 23/25 for tool calls plus a tool message.
   Two changes help both formats: an empty argument (`"date_from": ""`) counts as left out instead
   of rejecting the action, and `<<ACTION: "job_status">>` is understood. One example for a single
   day (3 March 2021) went into **both** prompts after run 3 showed the gap. Since then the
   held-out request "foto's van 14 juli 2019" is no longer blind. Native got it right 1 of 5 times
   in run 3 and 3 of 5 in the final run; the tags got it wrong 5 of 5 and 4 of 5.

The 3.8 measurement of the tags (85 to 90% on its 20 requests) agrees with the tag numbers here.

## Empty answers (added after review)

qwen2.5:3b sometimes answers a native request with nothing at all: `content` is empty and there
are no `tool_calls`, after 23 to 55 generated tokens. Most likely it wrote a call that Ollama's
parser dropped. It happens mostly after an earlier action in the history ("exporteer mijn foto's
van 2019" after a filter turn), and in the tag format never. The earlier runs above counted an
empty answer to a no-action request as right (3 of 225 native answers in the final run were
empty), so their no-action and overall native numbers are slightly too kind.

The bench now scores an empty answer as a miss everywhere and counts it apart. For native, it
also asks an empty answer again with the tag prompt, as the server does, and reports the rate
after that retry. Two follow-ups were added that start after an action turn ("exporteer mijn
foto's van 2019" after "Laat foto's uit 2019 zien", and "wat gebeurde er vroeger op deze dag?"
after a beach search), so there are now 47 requests. Re-run, three rounds (141 answers per format):

| | Native | Tags |
| --- | --- | --- |
| tuning | 60/60 (100%) | 57/60 (95.0%) |
| held-out | 42/45 (93.3%) | 35/45 (77.8%) |
| no-action | 15/15 (100%) | 12/15 (80.0%) |
| follow-up | 19/21 (90.5%) | 20/21 (95.2%) |
| **all** | **136/141 (96.5%)** | **124/141 (87.9%)** |
| empty answers | 2/141 | 0/141 |
| after the tag retry of an empty answer | 137/141 (97.2%) | |

Through the server (a temp vault on a test port, the real local Ollama), the two requests that
came back empty most often in review ("exporteer mijn foto's van 2019" after a filter turn, and
"wat gebeurde er vroeger op deze dag?") were asked 6 times each: no empty answer reached the chat,
12 of 12 had text or a result (11 ran the expected tool, one asked which day was meant).

## Fallback

- The format is a setting (`app_settings.chat_tool_format`: `native` or `tags`, default native),
  chosen in Instellingen > Taalmodel van Pasha > Acties ("Ollama-gereedschap" or "Tekstcode",
  `POST /api/chat/tool-format`). `GET /api/chat/models` reports the choice and what the current
  model really gets (`tool_format_used`), and the page says so when a model without tool support
  falls back to the tags.
- A model whose Ollama `capabilities` do not include `tools` gets the tags.
- When Ollama answers "does not support tools", the same question is asked again in the tag
  format, and that model is remembered until the server restarts.
- An empty native answer (no text, no call, no tried action) is asked once more in the tag format
  (see "Empty answers" below). When that is empty too, the chat gets "Dat lukte me niet, wil je het
  anders vragen?" plus `{"notice": "no_answer"}`, and the page leaves that turn out of the history
  it sends next time. The page never shows an empty bubble, also not from an older server.
- In a native answer, tags and calls written as text still count.
- The chat history keeps every action as a tag, whichever format made it (the server sends it as a
  `raw` event), so switching formats never loses what Pasha did.

## Not done

- **Sending the tool result back to the model for a second answer** (the classic tool loop). Pasha
  shows the result itself as a strip with a link. A second round would double the time per
  question for a sentence nobody needs.
- **Other models.** Measured only with `qwen2.5:3b`, the default. Another model picks its format
  by its capabilities; run the script with `--model` to measure it.

## Reproducing

```bash
cd code
venv/bin/python scripts/bench_pasha_tools.py                      # 3 rounds, both formats (a few minutes)
venv/bin/python scripts/bench_pasha_tools.py --rounds 5 --json out.json
```

It needs a running Ollama with the model. It builds its own test vault in a temp folder and
removes it afterwards, and it only talks to 127.0.0.1:11434.
