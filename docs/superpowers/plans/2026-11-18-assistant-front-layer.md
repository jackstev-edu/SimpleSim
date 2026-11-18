# Assistant Front Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One GUI-free assistant session that asks for every missing input, runs Jack's PyDiscovery tools (fast path, finalists solved in ANSYS) for the L-bracket family, and hands the chat to the existing ANSYS agent (slow path) for anything else, saying which path and why.

**Architecture:** A pure path rule and study-input guard (`path_rule.py`, `study.py`) decide in code. `FrontAgent` (`front.py`) is an OpenAI Responses loop over Jack's `TOOL_SPECS` plus two own tools. It intercepts setters and `optimize_design`, and emits a `handoff` event instead of deciding paths in the prompt. `AssistantSession` (`session.py`) owns both agents and performs the hand-off. Our dev `app.py` switches to the session.

**Tech Stack:** Python 3.12, OpenAI Responses API through `src/llm.py` (`MODEL`, `counted_client`, `session_budget`), Jack's `src/pydiscovery`, existing `src/agent` (ANSYS agent + `Workbench`), `src/assistant/explain.py` + `decision_log.py`, pytest, Gradio.

**Spec:** `docs/superpowers/specs/2026-11-18-assistant-front-layer-design.md`

Run everything from the `SimpleSim` root with `..\.venv\Scripts\python.exe` (written `python` below).

## Global Constraints

- No edits to `src/pydiscovery/`, `contracts/`, `src/ui/` or `launch_gui.py` (other roles' folders).
- No value reaches a solve unless the user gave or confirmed it. The safety factor is offered as 1.5 but used only after the user confirms it.
- `optimize_design` always receives constraints built by `build_constraints` (never the model's own and never Jack's SF ≥ 2.0 default).
- Fast path engine: `PyDiscoveryTools(backend="ansys")` in production; tests inject `backend="formula"`.
- One embedded Mechanical per process: the fast path's ANSYS backend is closed before the `Workbench` loads a part.
- Mechanical must be disposed on the thread that started it: every `PyDiscoveryTools.call` and the backend's `close()` run on the FrontAgent's single-worker pool.
- `python -m pytest tests -q` passes without ANSYS and without an API key.
- Comments: one short line on non-trivial lines; no em dashes or double-dash separators (SimpleSim CLAUDE.md).
- Log GenAI use in `docs/genai-log.md`. Commit locally; push only with the user's OK.

## Review Focus

1. The user says "use ANSYS" halfway through setup: the switch happens, and the hand-off message lists only what was confirmed so far. Test in Task 5 (`test_user_override_mid_setup`).
2. The model calls `optimize_design` with `constraints=[safety_factor >= 2.0]` after the user said 1.5: the user's 1.5 is what Jack receives. Test in Task 4 (`test_model_constraints_are_replaced`).
3. `optimize_design` before a safety factor, an objective or `confirm_setup`: refused with a message naming what is missing, and Jack's tool is not called. Test in Task 4.
4. The ANSYS engine is missing or fails during optimize (`ok: False` from Jack): relayed as a tool error with no crash and no run folder. Test in Task 4 (`test_failed_optimize_writes_no_run_folder`).
5. A second upload after a hand-off starts again on the front layer with a fresh part and no state carried over from the first. Test in Task 5 (`test_new_upload_resets_to_front`).

---

### Task 1: Merge `origin/main` into `Yunus`

**Files:** merge only (Jack's 5 commits: 53d8841, 73a7aed, 0f7aac9, 02d026d, 6cca515).

- [ ] **Step 1:** `git fetch origin && git merge origin/main`. On conflicts, keep both sides (same rule as 5c7eded). Expected: `src/pydiscovery/tools.py` now has `PyDiscoveryTools(backend, predictor, runs)`, and `_optimize_design` returns `study_spec` and `prediction`.
- [ ] **Step 2:** `python -m pytest tests -q`. Expected: all pass (235 before the merge plus main's new tests), 0 failed.
- [ ] **Step 3:** If merging didn't commit automatically, commit with message `Merge origin/main into Yunus (team GUI PyDiscovery tab, model comparison)`.

### Task 2: Path rule

**Files:**
- Create: `src/assistant/path_rule.py`
- Test: `tests/test_assistant_path_rule.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class PathDecision: path: Literal["fast", "slow"]; reason: str`
  - `part_path(part: src.pydiscovery.session.Part) -> PathDecision | None`: `fast` if `part.family` is set. `slow` with `part.problems[0]` if `part.problems` is not empty. `None` when the file is plain geometry and the user must say which bracket it is.
  - `outside_family(family: Family, *, force_n: float | None = None, direction: Sequence[float] | None = None, material: str | None = None) -> str | None`: a plain-words reason, or `None` when inside.
  - `FAST_MATERIALS = ("structural_steel", "aluminum_6061")` (from `contracts.fakes.MATERIALS`).

- [ ] **Step 1: Write the failing tests** (build parts with `write_demo_part` from `src.pydiscovery.demo_part` and `App("formula").open(...)`):
  - `test_family_file_is_fast`: a demo `.dsco` gives `PathDecision("fast", ...)`.
  - `test_plain_geometry_is_undecided`: `write_demo_part(tmp/"p.scdocx", physics=False)` gives `None`.
  - `test_problems_make_it_slow`: a `.dsco` whose parameters don't match a family gives `path == "slow"`, and the reason equals `part.problems[0]`.
  - `test_force_outside_range`: `outside_family(L_BRACKET_V0, force_n=1500)` contains `"1500"` and `"100"` and `"1000"`. `force_n=500` gives `None`.
  - `test_direction_other_than_minus_y`: `direction=(0, 0, -1)` gives a reason. `(0, -2, 0)` gives `None` (compare unit vectors).
  - `test_material_not_in_fast_table`: `"titanium"` gives a reason naming both fast materials. `"aluminum_6061"` gives `None`.
- [ ] **Step 2:** `python -m pytest tests/test_assistant_path_rule.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement. Read the limits from `family.force_range_n` and `family.load_direction`. Supports need no check: Jack's `set_support` only accepts the family's fixed face.
- [ ] **Step 4:** Run the tests again. Expected: PASS.
- [ ] **Step 5:** Commit `Assistant path rule: fast for the known family, slow with a reason`.

### Task 3: Study inputs, constraints and hand-off text

**Files:**
- Create: `src/assistant/study.py`
- Test: `tests/test_assistant_study.py`

**Interfaces:**
- Produces:
  - `@dataclass class StudyInputs: safety_factor: float | None = None; objective: str | None = None; direction: str | None = None; limits: list[dict] = field(default_factory=list); vary: list[str] | None = None`. Each limit is `{"metric", "op", "value"}`, the same as Jack's `_CONSTRAINT`.
  - `build_constraints(inputs: StudyInputs) -> list[dict]`: `[{"metric": "safety_factor", "op": ">=", "value": inputs.safety_factor}] + inputs.limits`. Any `safety_factor` entry in `limits` is dropped. Raises `ValueError` if `safety_factor` is None.
  - `optimize_blocker(part, inputs: StudyInputs) -> str | None`: names everything missing out of {"the user's confirmation of the setup card" (`part.confirmed`), "a safety factor", "an objective"}, or returns None.
  - `LIBRARY_NAMES = {"structural_steel": "Structural Steel", "aluminum_6061": <exact ANSYS library name, checked in src/sim/materials.py's library list>}`.
  - `handoff_message(filename: str, part: Part | None, inputs: StudyInputs, reason: str) -> str`.

- [ ] **Step 1: Write the failing tests:**
  - `test_constraints_always_carry_user_sf`: SF 1.5 and `limits=[{"metric": "max_disp_mm", "op": "<=", "value": 0.5}]` give exactly those two constraints, with SF first.
  - `test_sf_in_limits_is_replaced`: `limits=[{"metric": "safety_factor", "op": ">=", "value": 2.0}]` with SF 1.5 gives a single SF constraint with value 1.5.
  - `test_no_sf_raises`.
  - `test_blocker_lists_all_missing`: an unconfirmed part with empty inputs gives a message containing all three phrases. A confirmed part with SF and objective gives `None`.
  - `test_handoff_lists_confirmed_values_only`: a part with force 500 N, −Y and `structural_steel`, plus SF 1.5 and objective `mass_kg minimize`, gives a message containing "500 N", "Structural Steel", "1.5", "minimise mass" (wording up to you, values exact), the reason, and "confirm the faces with the user".
  - `test_handoff_with_nothing_confirmed`: `part=None` and empty inputs give a message that contains the filename and "Nothing is confirmed yet". It contains no number besides those in the filename.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. For the hand-off, only values with `part.sources` or `part.confirmed` count. A file value counts as confirmed only once `part.confirmed` is True. Direction goes in words via the axis name ("−Y").
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5:** Commit `Assistant study inputs: explicit SF constraint, optimize guard, hand-off text`.

### Task 4: FrontAgent loop

**Files:**
- Create: `src/assistant/front.py`
- Test: `tests/test_assistant_front.py` (use `ScriptClient`, `call`, `msg`, `resp` from `tests.test_agent_loop`)

**Interfaces:**
- Consumes: Tasks 2–3; `PyDiscoveryTools`, `TOOL_SPECS` (Jack); `explain(result, client, model) -> (text, warnings)`; `DecisionLog`; `MODEL` from `src.llm`.
- Produces:
  - `class FrontAgent:`
    - `__init__(self, tools: PyDiscoveryTools, client, out_root: Path, model: str = MODEL)`
    - attributes `inputs: StudyInputs`, `log: DecisionLog`, `handoff_reason: str | None`, `last_run_dir: Path | None`
    - `call_tool(name: str, args: dict) -> dict`: runs on the single-worker pool
    - `run(user_text: str) -> Iterator[dict]`: events `text`, `tool`, `tool_result`, `error` in the same shapes as `src/agent/agent.py`, plus `{"type": "handoff", "reason": str}`, after which `run` returns
    - `close() -> None`: calls `tools.app.backend.close()` on the pool if the backend has it, then shuts the pool down
  - Own tools, added to Jack's `TOOL_SPECS` as functions:
    - `record_study_inputs(safety_factor?, objective?, direction?, limits?, vary?)`: merges into `inputs` and logs `answer`. Its description says to call it only with values the user stated or confirmed.
    - `switch_to_ansys(reason)`: sets `handoff_reason`.
  - Module constant `PROMPT` (short): ask for every missing input one at a time; offer SF 1.5 and wait for a yes; name the two fast materials, recommend one for the user's goal, and say ANSYS has more; ask "is it the L-bracket?" for plain geometry and never decide that alone; show the setup card and call `confirm_setup` only after the user's yes; announce the path once, ending "say 'use ANSYS' to switch"; call `switch_to_ansys` when the user asks for ANSYS; never compute or invent numbers.

- [ ] **Step 1: Write the failing tests** (each uses `PyDiscoveryTools(backend="formula")` on a demo `.dsco`, opened in the test through `agent.call_tool("open_part", ...)`):
  - `test_set_load_outside_family_hands_off`: the model calls `set_load(force_n=1500)`. A `handoff` event's reason names 1500 N, and Jack's force is not set.
  - `test_material_outside_fast_table_hands_off`: the model calls `set_material` with an unknown key. That gives a `handoff`. (Jack's enum would reject it, so check `outside_family` before forwarding.)
  - `test_switch_to_ansys_tool_hands_off`: gives a `handoff` with the model's reason.
  - `test_optimize_blocked_without_sf`: the model calls `optimize_design`. The `tool_result` has `is_error` True and names "a safety factor", and `tools.part.last_result` stays None.
  - `test_model_constraints_are_replaced`: after `record_study_inputs(safety_factor=1.5, objective="mass_kg", direction="minimize")` and `confirm_setup`, the model calls `optimize_design(constraints=[SF >= 2.0])`. Spy on `PyDiscoveryTools.call`: Jack receives `[SF >= 1.5]`, plus objective and direction from `inputs`.
  - `test_optimize_writes_run_folder`: a successful optimize creates `out_root/<timestamp>/` with `study_spec.json`, `result.json`, `decision_log.jsonl`, `decision_log.md` and `explanation.txt`. Stub `explain` with monkeypatch, so no API call is made.
  - `test_failed_optimize_writes_no_run_folder`: stub Jack's `call` to return `{"ok": False, "message": "engine missing"}`. The `tool_result` is an error, `out_root` is empty, and there is no exception.
  - `test_prompt_rules`: `PROMPT` mentions "1.5", "confirm_setup", "use ANSYS" and "L-bracket".
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. Build the loop on the same structure as `Agent.run` (Responses API; `function_call_output` items; `MAX_TOOL_ROUNDS` guard), without web search. Intercept `set_load`, `set_material`, `switch_to_ansys` and `optimize_design` before forwarding to Jack. After a successful optimize: `explain()`, `log.add_result(result)`, `log.add("explanation", ...)`, then write the run folder. The tool output given to the model is Jack's message plus the explanation.
- [ ] **Step 4:** Run. Expected: PASS. Run the full suite too.
- [ ] **Step 5:** Commit `Assistant front layer: Jack's tools behind an input guard, code-decided hand-off`.

### Task 5: AssistantSession

**Files:**
- Create: `src/assistant/session.py`
- Test: `tests/test_assistant_session.py`

**Interfaces:**
- Consumes: `FrontAgent` (Task 4); `part_path`, `handoff_message`; `src.agent.agent.Agent`; `Workbench`.
- Produces:
  - `class AssistantSession:`
    - `__init__(self, client, out_dir: Path, backend: str = "ansys", make_workbench=Workbench, make_agent=Agent)`
    - `path: Literal["fast", "slow"] | None`
    - `upload(path: str | Path) -> Iterator[dict]`: closes any previous front, workbench and agent. Builds a new `FrontAgent(PyDiscoveryTools(backend), client, out_dir / "assistant")` and calls `open_part`. Then:
      - `part_path` fast: yields `{"type": "path", "path": "fast", "reason": ...}`, then the front's first turn ("I uploaded my part '<name>'. Please look at it and tell me what you see.").
      - slow: does the hand-off.
      - `None`: the front's first turn only. If Jack's `open_part` fails (unsupported file), that counts as slow, with Jack's message as the reason.
    - `send(text: str) -> Iterator[dict]`: routes to the active agent. On a front `handoff` event it runs the hand-off.
    - `stop()`: calls `workbench.request_stop()` if a workbench exists.
    - `close()`
    - `table() -> list[list]`: `workbench.table()` on the slow path, `[]` otherwise.
  - Hand-off sequence (private `_handoff(reason)`):
    1. `front.close()`
    2. yield `path` event (slow, reason)
    3. yield `text` event "Starting ANSYS (about a minute)…"
    4. `wb = make_workbench(out_dir / "session")`; `wb.load_part(path)`
    5. `agent = make_agent(wb, client)`
    6. `yield from agent.run(handoff_message(...))`
    7. `front.log.add("spec", "path: slow", reason=reason)`, saved as `out_dir / "assistant" / "handoff_log"` (`.jsonl` + `.md`)

- [ ] **Step 1: Write the failing tests** (fake workbench and agent classes record calls; `ScriptClient` drives the front):
  - `test_family_upload_is_fast`: the first event is `path` `fast`, and no workbench is created.
  - `test_unknown_part_hands_off_at_once`: a `.dsco` with unmatched parameters gives a `path` `slow` event, the fake workbench's `load_part` is called with the same file, and the fake agent's first `run` text contains the problem reason.
  - `test_backend_closed_before_workbench`: shared call-order list. `close` comes before `load_part`.
  - `test_user_override_mid_setup`: on a fast part, the user has given SF 1.5 but no objective. The model calls `switch_to_ansys`. The hand-off text contains "1.5" and no objective.
  - `test_messages_route_to_ansys_after_handoff`: after a hand-off, `send("hi")` reaches the fake agent and not the front.
  - `test_new_upload_resets_to_front`: a hand-off, then `upload` of a family file. `path` is fast, the old workbench and agent are closed, and `inputs` are empty.
  - `test_unsupported_file_goes_slow`: a `.txt` upload hands off with Jack's "isn't supported" message.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run, plus the full suite. Expected: PASS.
- [ ] **Step 5:** Commit `AssistantSession: one conversation over the fast and ANSYS paths`.

### Task 6: Dev GUI on the session

**Files:**
- Modify: `app.py` (`agent()`, `_events`, `on_upload`, `on_stop`, table calls, header)
- Test: `tests/test_app_stream.py` (update the existing tests that monkeypatch `agent()` so they patch `session()`)

**Interfaces:**
- Consumes: `AssistantSession` (Task 5).
- Produces: `session() -> AssistantSession` in place of `agent()`. `_events` renders `path` events as `"🛣️ **Fast search (team predictor + ANSYS finalists)**: <reason>"` or `"🛣️ **ANSYS search**: <reason>"`, and ignores `handoff`. The header line shows the active path after each turn.

- [ ] **Step 1: Write the failing tests:** `test_path_event_is_shown` (a fake session yields a `path` event, and the chat gets the 🛣️ line with the reason); update the existing tests to the `session()` name.
- [ ] **Step 2:** Run `python -m pytest tests/test_app_stream.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement. `on_upload` no longer calls `wb.load_part` directly. The session decides. The global `wb` goes, and the "Open in ANSYS/Mechanical" buttons use the session's workbench when it exists, otherwise they show the existing "no final design yet" error.
- [ ] **Step 4:** Full suite. Expected: PASS.
- [ ] **Step 5:** Commit `Dev GUI runs the assistant session and shows the chosen path`.

### Task 7: Live check and records

**Files:**
- Create: `scripts/smoke_assistant.py` (needs ANSYS and `OPENAI_API_KEY`; not collected by pytest)
- Modify: `docs/genai-log.md`, `Project2/CLAUDE.md` (Decisions row)

- [ ] **Step 1:** Write the script. It runs two scripted conversations through `AssistantSession` with `counted_client(usage, 0.20)`, prints events, and appends to the ledger (`append_spend(LEDGER, "assistant-smoke", 2, usage)`):
  - (a) `examples/l_bracket.scdocx`: "yes it is the L-bracket", 500 N down, steel, SF 1.5 yes, minimise mass, "yes" to the card. Expect a fast path, an ANSYS-verified result and a run folder.
  - (b) `examples/pyansys/saddle_bracket.scdoc`: expect `path` slow and the ANSYS agent describing the part.
- [ ] **Step 2:** Ask the user to close other programs, then run `PYTHONIOENCODING=utf-8 python -m scripts.smoke_assistant`. Expected: both conversations finish, the fast run folder exists, the finalists' `source` is `ansys_mechanical`, cost is under $0.20, and no leftover SpaceClaim or Mechanical process remains (`tasklist`).
- [ ] **Step 3:** Record the results in `docs/genai-log.md` and the CLAUDE.md Decisions table. Commit `Assistant smoke check and records`. Ask the user before pushing.
