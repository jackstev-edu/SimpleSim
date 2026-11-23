# First Look Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-feature-at-a-time "first look" screen, time estimates, live progress and a Stop button, so that a beginner gets a first answer within a few minutes and can stop any search.

**Architecture:** The screen logic lives in a new pure module `src/agent/screen.py`, so it can be unit-tested without ANSYS. `Workbench` gains a progress queue, a stop flag and solve timing, plus a `first_look` tool. `optimizer.arsm` gains a `should_stop` callback. `Agent.run` runs each tool call in a helper thread and drains the progress queue into GUI events. `app.py` renders progress and adds a Stop button.

**Tech Stack:** Python 3.12, PyMechanical / PyAnsys Geometry (via the existing `Workbench`), PyOptiSLang 1.7.0, Anthropic SDK 2.3.0, Gradio 6.31.0, pytest (new dev dependency).

**Spec:** `docs/superpowers/specs/2026-10-31-first-look-search-design.md`

## Global Constraints

- All ANSYS calls stay on the single `Workbench._pool` worker thread. The Stop button only sets a flag, checked between designs.
- A running ANSYS solve is never interrupted. After Stop, the solve in progress finishes and no new design starts.
- optiSLang is always disposed. A user stop goes through the same `project.stop(wait_for_finished=True)` and `osl.dispose()` path as the budget stop.
- First look: 1–4 features, **2n + 1 solves** (reference + low + high for each feature). Stored names: `look_ref`, `look_<k>_low`, `look_<k>_high` (k counts from 1).
- Reference value of a feature: its current value (offset → 0, radius → the face's `radius_mm`) if inside [low, high], else (low + high) / 2. A fillet has no current value, so its reference is the middle of the range.
- First-look values are indicative (2 mm mesh + r/4 fillet sizing). No first-look or stopped design is recommended without check_mesh.
- Stopped tools return normally with what they have and `stopped: "user"`. Designs already solved are kept in `Workbench.designs`.
- Time estimate: the measured mean wall time per design in this session (geometry + solve), or 30 s before any design.
- The required safety factor is always stated by the agent (existing rule, unchanged).
- Commits: local commits per task are fine. **Push only when the user asks.**

## Review Focus

1. **Stop pressed while no search runs, or the flag left set from an earlier batch.** The next batch must start normally: clear the flag at the start of every batch tool. Test in Task 2.
2. **A design fails mid-screen** (bad geometry edit, solve error). The screen continues, the row is recorded with `error` and is infeasible, and the summary skips it. Test in Task 1 (summary) and Task 2 (first_look).
3. **Stop pressed during the very first design of an optimize run.** ARSM must return `stopped: "user"` with one evaluation, and dispose optiSLang. Test in the Task 6 smoke run.
4. **A tool raises while running in the helper thread.** The error must reach Claude as an `is_error` tool_result, as today, and must not hang the GUI. Test in Task 3.
5. **A feature range that does not include its current value**, such as a radius range above the existing hole. The reference falls back to the middle of the range, and the report says which value was the reference. Test in Task 1.

---

### Task 1: Pure screen logic (`src/agent/screen.py`) and test setup

**Files:**
- Create: `src/agent/screen.py`, `tests/__init__.py`, `tests/test_screen.py`
- Modify: `requirements.txt` (add `pytest==8.*` under a `# dev` comment), `src/agent/tools.py` (`_optimize` uses `screen.feasible`)

**Interfaces:**
- Produces:
  - `reference_value(current: float | None, low: float, high: float) -> float`
  - `screen_plan(features: list[dict], currents: list[float | None]) -> list[tuple[str, list[float]]]`: (design name, value for each feature) in run order, starting with `look_ref`.
  - `feasible(responses: dict, limits: list[dict]) -> bool`: limit = `{"response", "op": "<=" | ">=", "value"}`. A missing response or an error counts as infeasible.
  - `summarize(features: list[dict], rows: dict[str, dict], objective: str, sense: str, limits: list[dict]) -> dict`: `rows` maps design name to `{"values": [...], "responses": {...}}` or `{"values": [...], "error": str}`. Returns `{"reference": {...}, "features": [{"index", "label", "low", "ref", "high", "responses": {r: {"low","ref","high","change","change_rel"}}, "feasible": {"low": bool, "high": bool}}], "ranking": [feature index, ...], "best_feasible": name | None}`.

- [ ] **Step 1: Install pytest and write the failing tests** (`../.venv/Scripts/python.exe -m pip install "pytest==8.*"`)

```python
from src.agent import screen

def test_reference_value_uses_current_inside_range():
    assert screen.reference_value(0.0, -1.0, 2.0) == 0.0

def test_reference_value_falls_back_to_midpoint():
    assert screen.reference_value(4.0, 5.0, 7.0) == 6.0      # Review Focus 5
    assert screen.reference_value(None, 2.0, 6.0) == 4.0     # fillet: no current value

def test_screen_plan_is_2n_plus_1_in_order():
    f = [{"low": -1, "high": 2}, {"low": 2, "high": 6}]
    plan = screen.screen_plan(f, [0.0, None])
    assert [n for n, _ in plan] == ["look_ref", "look_1_low", "look_1_high", "look_2_low", "look_2_high"]
    assert dict(plan)["look_ref"] == [0.0, 4.0]
    assert dict(plan)["look_2_high"] == [0.0, 6.0]

def test_feasible_handles_both_ops_and_missing():
    lim = [{"response": "safety_factor", "op": ">=", "value": 1.5},
           {"response": "max_def_mm", "op": "<=", "value": 0.5}]
    assert screen.feasible({"safety_factor": 1.6, "max_def_mm": 0.4}, lim)
    assert not screen.feasible({"safety_factor": 1.4, "max_def_mm": 0.4}, lim)
    assert not screen.feasible({"max_def_mm": 0.4}, lim)

def test_summarize_ranks_by_objective_swing_and_skips_errors():
    f = [{"label": "base"}, {"label": "wall"}]
    r = lambda m, sf: {"mass_kg": m, "safety_factor": sf}
    rows = {"look_ref": {"values": [0, 0], "responses": r(0.25, 1.6)},
            "look_1_low": {"values": [-1, 0], "responses": r(0.24, 1.3)},
            "look_1_high": {"values": [1, 0], "responses": r(0.27, 1.9)},
            "look_2_low": {"values": [0, -1], "error": "RuntimeError: edit failed"},   # Review Focus 2
            "look_2_high": {"values": [0, 1], "responses": r(0.251, 1.61)}}
    s = screen.summarize(f, rows, "mass_kg", "min", [{"response": "safety_factor", "op": ">=", "value": 1.5}])
    assert s["ranking"] == [1, 2]
    assert s["features"][1]["feasible"]["low"] is False
    assert s["best_feasible"] == "look_ref"
```

- [ ] **Step 2: Run** `../.venv/Scripts/python.exe -m pytest tests/test_screen.py -v`. Expected: FAIL, `ImportError` for `src.agent.screen`.
- [ ] **Step 3: Implement** the four functions in `src/agent/screen.py`.
  - Feature indices are 1-based (matching the design names).
  - Ranking: sort by |change| of the objective between low and high, divided by the reference objective, largest first. Break ties by the largest relative change of any limited response. A feature with an errored end ranks by its remaining end versus the reference.
  - Replace the inline feasibility expression in `Workbench._optimize` with `screen.feasible(full, limits)`.
- [ ] **Step 4: Run** `../.venv/Scripts/python.exe -m pytest tests -v`. Expected: all PASS. Then `../.venv/Scripts/python.exe -c "import src.agent.tools"` runs without error.
- [ ] **Step 5: Commit** `git add requirements.txt src/agent/screen.py src/agent/tools.py tests && git commit -m "Add first-look screen logic (pure, unit-tested)"`

### Task 2: Workbench: progress, stop, timing, `first_look` tool, ARSM stop

**Files:**
- Modify: `src/agent/tools.py`, `src/sim/optimizer.py`
- Test: `tests/test_workbench.py`

**Interfaces:**
- Consumes: `screen.reference_value`, `screen.screen_plan`, `screen.summarize`, `screen.feasible` (Task 1).
- Produces:
  - `Workbench.stop_event: threading.Event` and `Workbench.request_stop() -> None`.
  - `Workbench.progress: queue.Queue[dict]`. Each event is `{"tool", "k", "n", "design", "responses" | "error", "best_feasible": name | None}`.
  - `Workbench.seconds_per_design() -> float`: mean of the measured wall times, or 30.0.
  - Tool `first_look`. Input: `features` (1–4 items shaped like optimize `variables` but with `low`, `high` and an optional `label`), optional `fixed_edits`, `objective`, optional `sense`, `limits`. Returns `screen.summarize(...)` plus `{"stopped": "done" | "user", "solves": int, "seconds_per_design": float}`.
  - `optimizer.arsm(..., should_stop: Callable[[], bool] | None = None)`: checked before each design is evaluated. When true, it ends with `stopped = "user"`.
  - Every `evaluate_design`, `first_look`, `optimize` and `check_mesh` result, plus `set_load_case`, includes `seconds_per_design`.

- [ ] **Step 1: Write the failing tests.** Use a `Workbench` whose `_evaluate` is replaced by a fake that returns canned responses and records calls, with `wb.part` and `wb.load_case` set, so no ANSYS is needed.
  - `test_first_look_runs_2n_plus_1_and_reports`: 2 features → 5 fake calls, named `look_ref`, `look_1_low`, … in order. The result has `stopped == "done"`, `solves == 5`, and 5 events in `wb.progress` with `k` = 1..5 and `n` = 5.
  - `test_first_look_stops_between_designs`: the fake calls `wb.request_stop()` during call 2. Expect 2 calls, `stopped == "user"`, and both designs stored.
  - `test_stop_flag_cleared_at_batch_start` (Review Focus 1): call `wb.request_stop()`, then `first_look`. All 2n + 1 designs run.
  - `test_first_look_records_failed_design` (Review Focus 2): the fake raises on `look_1_high`. The call returns normally, the summary marks that end infeasible, and a progress event carries `error`.
  - `test_first_look_rejects_more_than_4_features`: raises `ValueError`.
  - `test_reference_uses_face_radius`: a radius feature on a face with `radius_mm` 4 and range 5–7 is run at 6.0 for `look_ref`.
- [ ] **Step 2: Run** `../.venv/Scripts/python.exe -m pytest tests/test_workbench.py -v`. Expected: FAIL (no `first_look`).
- [ ] **Step 3: Implement.**
  - Current values: offset → 0.0; radius → `radius_mm` from `describe_faces` of the original part (read once, on the ANSYS thread); fillet → None.
  - Time each design in `_evaluate` (wall time, including the build) and append it to a list behind `seconds_per_design()`.
  - Put a progress event after every design (first_look, optimize) and after each of the two solves in check_mesh. For check_mesh, split it into two `mechanical.solve` calls inside tools, keeping the existing output shape.
  - `first_look` and `optimize` call `self.stop_event.clear()` at their start. `first_look` checks the flag before each design. `optimize` passes `should_stop=self.stop_event.is_set` to `arsm`. In `arsm`, check `should_stop()` before each design inside the `get_designs` loop, and break both loops to the existing stop path.
  - Add the `first_look` schema to `TOOLS`, after `check_mesh`. Its description says: one feature at a time, 2n + 1 solves, values indicative, show features, ranges and time to the user and wait for their OK first.
- [ ] **Step 4: Run** `../.venv/Scripts/python.exe -m pytest tests -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "Workbench: first_look tool, progress events, stop flag, timing"` (add the test file).

### Task 3: Agent loop runs tools in a helper thread and streams progress

**Files:**
- Modify: `src/agent/agent.py`
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `Workbench.progress`, `Workbench.call` (Task 2).
- Produces: a new GUI event, `{"type": "progress", "tool": str, "text": str}`, where the text is one line such as `look_1_high: 3/5 · mass 0.271 kg · SF 1.82 · best look_ref`. It is yielded between the `tool` and `tool_result` events of the same call.

- [ ] **Step 1: Write the failing tests** with a fake client, whose `messages.create` returns scripted responses (one `tool_use` for `first_look`, then `end_turn` text), and a fake workbench, whose `call` puts 3 events on `progress` with short sleeps and returns `"{}"`.
  - `test_progress_events_stream_before_result`: the event types come out in order `tool`, `progress` ×3, `tool_result`, `text`.
  - `test_tool_exception_becomes_error_result` (Review Focus 4): the fake `call` raises `RuntimeError("boom")`. Expect `tool_result` with `is_error` True and content `"Error: RuntimeError: boom"`, and the generator finishes.
- [ ] **Step 2: Run** `../.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -v`. Expected: FAIL (no progress events).
- [ ] **Step 3: Implement.**
  - Run `self.wb.call` in a `concurrent.futures.ThreadPoolExecutor(max_workers=1)` owned by the `Agent`. While the future is not done, poll `wb.progress.get(timeout=0.2)` and yield progress events. After it finishes, drain the rest.
  - Format the progress text in a small helper `_progress_text(ev: dict) -> str`.
  - Leave the existing error handling unchanged.
- [ ] **Step 4: Run** `../.venv/Scripts/python.exe -m pytest tests -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -am "Agent: stream tool progress events"` (add the test file).

### Task 4: GUI: progress lines and the Stop button

**Files:**
- Modify: `app.py`

**Interfaces:**
- Consumes: the `progress` events (Task 3), `Workbench.request_stop()` (Task 2).

- [ ] **Step 1: Implement.**
  - In `_stream`, a `progress` event appends `- <text>` to the content of the running tool message (`history[-1]`) and keeps its pending status. Yield the table on every progress event, so new designs show up.
  - Add a `gr.Button("Stop search", variant="stop")` under the Send row. Its click calls `wb.request_stop()` and posts nothing to the chat.
  - Register it so that it runs while the chat generator is busy. Try `queue=False`. If Gradio 6.31 rejects that, use `concurrency_id="stop", concurrency_limit=None`.
- [ ] **Step 2: Verify by hand.**
  - Run `$env:ANTHROPIC_API_KEY=[Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY','User'); ..\.venv\Scripts\python.exe app.py`.
  - Upload `examples/l_bracket.scdocx` and ask for a first look.
  - Confirm the progress lines appear one by one and the table fills as designs finish.
  - Press Stop mid-run. The current design finishes, the tool ends with `stopped: "user"`, and the agent reports what it has.
- [ ] **Step 3: Commit** `git commit -am "GUI: live search progress and Stop button"`

### Task 5: Agent prompt follows the first-look flow

**Files:**
- Modify: `src/agent/agent.py` (`SYSTEM` only)

- [ ] **Step 1: Replace step 5 of "How you work"** with this text:
  > 5. Pick the features that may drive the objective and say why. Propose a first look: list each feature with its low and high values, the number of solves (2n + 1) and the estimated time (seconds_per_design x solves), and wait for the user's OK or changes. Then call first_look. Report in plain words which features matter and why, and the best feasible design so far. Say that first-look values are indicative and not mesh-checked, and that a one-at-a-time screen cannot show interactions between features. Then ask whether to stop here or dive deeper. To dive deeper, propose optimize on the features that mattered (bounds, objective, limits, budget, estimated time) and wait for the user's OK. The user can press Stop at any time. After a stop, report what was found and ask how to go on.
- [ ] **Step 2: Replace** "Each simulation takes about 30 s; tell the user before starting a batch." with "Before any batch, state the number of solves and the estimated time from seconds_per_design."
- [ ] **Step 3: Verify** `../.venv/Scripts/python.exe -m pytest tests -v` still passes, then commit: `git commit -am "Prompt: first look, then stop or dive deeper"`

### Task 6: Smoke tests against ANSYS and the notes

**Files:**
- Create: `scripts/smoke_first_look.py`
- Modify: `../CLAUDE.md` (notes only; outside the repo, not committed)

- [ ] **Step 1: Write `scripts/smoke_first_look.py`** (no LLM, same set-up as `smoke_optimize.py`: L-bracket, 500 N down).
  - (a) Run `first_look` with features base offset [-0.5, 1.5] and fillet radius [2, 6] on the inside corner, with **no** `fixed_edits`, because the fillet is a feature here. Objective `mass_kg`, limit `safety_factor >= 1.6667`. Print the summary and check `solves == 5`.
  - Parts (b) and (c) use the fixed r = 4 fillet as `fixed_edits`.
  - (b) Start `optimize` (base offset 0–2, budget 10) in a thread. Call `wb.request_stop()` after the 3rd progress event. Check `stopped == "user"`, 3 or 4 designs stored, and no `optislang` process left (`tasklist`).
  - (c) Start `optimize` again and call `wb.request_stop()` immediately (Review Focus 3). Check `stopped == "user"` and that optiSLang is disposed.
- [ ] **Step 2: Run** `../.venv/Scripts/python.exe -u -m scripts.smoke_first_look` (about 10 min). Expected:
  - (a) Ranking puts the base offset first for mass. `look_ref` (offset 0, r = 4, about 187 MPa from earlier runs) is **infeasible**. `look_1_high` (offset +1.5) is the best feasible design. There are 5 solves in about 2.5 min.
  - (b) and (c) end with `stopped: "user"` and no optiSLang process left.
- [ ] **Step 3: Live agent check (Haiku, a few cents):** run the GUI once as in Task 4, Step 2. Confirm the agent shows the features, ranges and time and waits, reports which features matter, asks stop or deeper, and states the safety factor.
- [ ] **Step 4: Add the results to CLAUDE.md** (local notes), then commit `git add scripts/smoke_first_look.py && git commit -m "Smoke test: first look and user stop"`
