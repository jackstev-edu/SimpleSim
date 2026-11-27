# The assistant API: chatting with Role 3's assistant from the team GUI

`python launch_gui.py` serves Role 3's `AssistantSession` under `/api/assistant`, next to the PyDiscovery routes under `/api/pydiscovery`. The GUI's chat box talks to it with plain `fetch` calls: one `GET` for the status, raw file bytes for an upload, a small JSON body for a message. The two routes that run the assistant answer as a stream of newline-delimited JSON, one event per line, so the chat box can show each step as it happens. Nothing here needs a JavaScript library.

Everything is bound to 127.0.0.1. The server is `src/pydiscovery/assistant_server.py`; its `handle()` is a pure dispatcher, tested without a network in `tests/test_assistant_api.py`.

## Starting it

```
python launch_gui.py                              # assistant on the fitted engine (no ANSYS needed)
python launch_gui.py --assistant-engine ansys     # finalists solved in ANSYS (Role 3's install)
```

The assistant needs two things on the machine: the `openai` package (in `src/sim/requirements.txt`) and the `OPENAI_API_KEY` environment variable. Without them the launcher still starts, the chat routes still answer, and `GET /api/assistant` says in plain words which one is missing. The key is read by the `openai` package; the server never returns, prints or logs it, only whether it is set.

Every model call goes through Role 3's counted client (`src/llm.py`): tokens are added up per model, and once the cost reaches the session budget (`SIMPLESIM_SESSION_BUDGET_USD`, default $0.20 in debug mode) the next call is refused and the turn ends with an `error` event. The budget covers the whole launcher run, including after a reset. The spend is appended to `practice/spend.csv` when the launcher closes, as the Gradio app does.

## Routes

| Route | Body | Answer |
| --- | --- | --- |
| `GET /api/assistant` | none | JSON status (below) |
| `POST /api/assistant/upload?name=<file name>` | the raw file bytes (`.dsco`, `.scdocx`, `.scdoc`, `.step`, `.stp`, up to 50 MB) | stream of events, then `done` |
| `POST /api/assistant/send` | `{"text": "what the user typed"}` | stream of events, then `done` |
| `POST /api/assistant/stop` | none | JSON status with a `message`; asks a running ANSYS search to stop |
| `POST /api/assistant/reset` | none | JSON status with a `message`; closes the session (SpaceClaim and Mechanical too) so the next upload starts over |

An upload starts a new conversation on that part. The file is saved under `data/raw/uploads/<name>` (the name is reduced to its base name, so paths can't escape the folder) and handed to the session, which opens it, decides the path, and asks the model for its first message. `send` continues the conversation. Only one turn runs at a time: a second `send` or `upload` while a stream is still open gets `409` with a message to wait or stop. A `send` before any upload gets `200` with `{"ok": false, "message": "Upload a part first."}`.

Plain-route errors are JSON with `ok: false` and a `message`: `400` for a bad body, missing name, wrong file type or empty or oversized file; `405` for the wrong method; `404` for an unknown path; `409` while a turn is running; `503` when the assistant can't run on this machine; `500` for anything unexpected (the traceback goes to the launcher's console).

### The status object

```json
{
  "ok": true,
  "available": false,
  "message": "The assistant can't run: OPENAI_API_KEY is not set in the environment. Fix that and restart the launcher.",
  "openai_installed": true,
  "api_key_set": false,
  "engine": "fitted",
  "model": "gpt-6-luna",
  "mode": "debug",
  "budget_usd": 0.2,
  "spent_usd": 0.0135,
  "budget_left_usd": 0.1865,
  "path": "fast",
  "part": "l_bracket.dsco",
  "busy": false
}
```

`available` is what to gate the chat box on; `message` is what to show when it is false. `path` is `null` before an upload, `"fast"` while the team's predictor and engine handle the part, `"slow"` once the ANSYS agent has the chat. `part` is the uploaded file's name. `busy` is true while a turn's stream is still open.

## Reading a stream

The streaming routes answer `200` with `Content-Type: application/x-ndjson`. Each line is one JSON object with a `type`. Lines arrive as the session produces them, so read the body incrementally and split on newlines; the last line is always a `done` event and then the connection closes.

```js
const r = await fetch("/api/assistant/send", {method: "POST", body: JSON.stringify({text})});
if (r.status !== 200) { showError((await r.json()).message); return; }
const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
for (;;) {
  const {value, done} = await reader.read();
  if (done) break;
  buf += dec.decode(value, {stream: true});
  const lines = buf.split("\n"); buf = lines.pop();
  for (const line of lines) if (line) handle(JSON.parse(line));
}
```

For an upload, `fetch("/api/assistant/upload?name=" + encodeURIComponent(file.name), {method: "POST", body: file})` and read the body the same way.

## Events

Every event has a `type`. The first six come from Role 3's session unchanged; `done` is added by the server. A `handoff` event exists inside the session but never reaches the stream: the session turns it into a `path` event with `"slow"`.

**`path`**: which search the part takes, sent once when the session decides. `reason` is a sentence for the user.

```json
{"type": "path", "path": "fast", "reason": "this is the L-bracket v0, which the team's predictor knows"}
{"type": "path", "path": "slow", "reason": "its parameters (Wobble0, Wobble1) don't match a known bracket"}
```

**`text`**: a message from the assistant to the user. Show it as a chat bubble.

```json
{"type": "text", "text": "I see an L-bracket with a 500 N load. What safety factor do you want? 1.5 is a common choice."}
```

A `text` event may carry an optional `options` list: short answers the user can pick with one click. The session does not send one today; the GUI guesses the likely answers from the question itself (a safety factor question offers "Use 1.5" and "Use 2.0", a setup card offers "Yes, the setup is right", and so on) and shows the four follow-ups "Why this design?", "Compare to baseline", "Lighter but less safe" and "Try aluminum" after a result. When the session starts sending `options`, the GUI uses them instead and nothing else changes. The server passes session events through unchanged, so no server change is needed.

```json
{"type": "text", "text": "What safety factor do you want? 1.5 is a common choice.", "options": ["Use 1.5", "Use 2.0"]}
```

**`tool`**: the assistant is calling a tool. `name` is the tool, `input` its arguments. Show it as a collapsed step, pending until its `tool_result`.

```json
{"type": "tool", "name": "set_material", "input": {"material": "aluminum_6061"}}
```

**`progress`**: a line of progress from a long ANSYS tool (the slow path only). Append it to the pending step.

```json
{"type": "progress", "tool": "first_look", "text": "design 3 of 8 solved"}
```

**`tool_result`**: the tool finished. `content` is text (often JSON) meant for the model; `is_error` says whether it failed. Mark the step done or failed. The result of `optimize_design` includes the contract `result.json` and an explanation for the user.

```json
{"type": "tool_result", "name": "set_material", "content": "{\"ok\": true, \"message\": \"...\", \"state\": {...}}", "is_error": false}
```

**`error`**: something the user should see as a warning: a cut-off reply, the tool-round cost guard, ANSYS failing to start on a hand-off, or the budget cap. A budget stop carries `"budget_exceeded": true`; no further model calls will work until the launcher restarts.

```json
{"type": "error", "text": "ANSYS could not start (ModuleNotFoundError: No module named 'ansys'). Upload the part again to retry."}
{"type": "error", "text": "The budget cap reached: $0.20 spent of $0.20. No further model calls this session ($0.20 spent). Restart the launcher to continue, or raise the cap with SIMPLESIM_SESSION_BUDGET_USD.", "budget_exceeded": true}
```

**`done`**: the last line of every stream. `path` and `part` as in the status, the spend so far, and `table`, the rows of evaluated designs from the ANSYS search (empty on the fast path; the column order is the Gradio app's `TABLE_HEADERS`). When the fast path ran `optimize_design` during the turn, `run` carries the three contract files (`study_spec`, `prediction`, `result`) once, so the page can show them in its tables; later `done` events leave `run` out until a new optimize runs.

```json
{"type": "done", "path": "fast", "part": "l_bracket.dsco", "spent_usd": 0.0135, "budget_left_usd": 0.1865, "table": []}
{"type": "done", "path": "fast", "part": "l_bracket.dsco", "spent_usd": 0.0212, "budget_left_usd": 0.1788, "table": [], "run": {"study_spec": {...}, "prediction": {...}, "result": {...}, "verdict": {...}}}
```

`run.verdict` is the judgement call on the result (`src/assistant/verdict.py`): 3 to 5 plain sentences on whether the recommended design is worth adopting, the main trade-off, the binding requirement, how far to trust the numbers (solver checked or a model estimate, inside or outside the training range) and one next step. It is written by the model in one extra call under the budget cap and held to the same number check as the explanation (`src/assistant/numbers.py`): every number must be in `result.json` or be a percent change against the baseline. When the text fails the check twice, or the budget is spent, `text` is a template built from `result.json` and `warnings` says so. The GUI pins it at the top of **Recommendations** and posts it in the chat. The PyDiscovery tab's own `optimize_design` answer carries the template verdict under the same key, since no model runs there. If even the template fails, `verdict` is left out and the run is sent without it; `spent_usd` in the same `done` event already counts the verdict call.

```json
{"text": "Adopt it. pl-0-01069 has a mass of 0.231 kg, 8% lighter than the baseline. ...", "source": "model", "warnings": []}
```

## In the team GUI

`src/ui/index.html` wires all of this into the **Agent** tab: a chat bar with the status line, **Upload part**, a message box, **Send**, **Stop**, **Reset** and a row of quick replies under the box (a click sends that text as an ordinary message). Messages and tool steps stream into the log, and a `run` in the `done` event goes through the page's contract checks and into Variations, Graphs and Recommendations. The bar only appears when `GET /api/assistant` answers, so a page served without the launcher looks as before. `tests/test_ui_assistant_chat.py` drives it in a browser with a fake model when `SIMPLESIM_CHROME` is set.

## What the fitted engine changes

On `--assistant-engine fitted` (the default) the fast path runs entirely in the emulator: the predictor shortlists and the fitted power law stands in for the solver on the finalists, so no ANSYS install is needed and the result says the check was not a real solve. A hand-off to the ANSYS agent (an unknown part, a load or material outside the family, or the user asking for ANSYS) still tries to start ANSYS; on a laptop without it, the stream shows the `path` event, "Starting ANSYS", then an `error` event, and the session resets so the next upload starts over. With Role 3's packages and a licence installed, `--assistant-engine ansys` solves finalists in ANSYS and hand-offs run the full ANSYS agent.
