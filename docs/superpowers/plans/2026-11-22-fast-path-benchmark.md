# Fast-Path Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 10 fast-path problems with model-optimum keys, a grader and a run script that score whether the assistant picks the right design.

**Architecture:** `src/eval/fast_keys.py` loads problems and computes keys with Jack's `PowerLawPredictor` (same pessimistic-band rule as the shortlist). `src/eval/fast_grader.py` grades a recorded run log. `scripts/run_fast_practice.py` drives `AssistantSession(backend="fitted")` against a simulated student and writes run logs + a summary. Nothing in `contracts/`, `src/pydiscovery/`, `src/models/` or the existing practice set changes.

**Tech Stack:** Python 3.12, numpy, PyYAML, pydantic contracts, OpenAI Responses API via `src/llm.py` (`gpt-6-luna`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-22-fast-path-benchmark-design.md`

## Global Constraints

- Truth for scoring = model optimum with limits judged at the **pessimistic end of the band** (`build_prediction` rule); median optimum recorded for information; simulation optimum report-only, only for 500 N, -90°, steel.
- Pass line `pick_gap` ≤ **5 %** (provisional, module constant `PICK_GAP_PASS = 0.05`).
- Families/materials: `l_bracket_v0`, `l_bracket_gusset_v1`; `structural_steel`, `aluminum_6061`. Fast-path force range 100 to 1000 N.
- Engine `fitted`; no ANSYS or CalculiX; the hand-off problem uses stub `make_workbench`/`make_agent`.
- Spend: `--budget` default **0.20** USD hard cap, counted client, ledger `practice/spend.csv`.
- Simulated student: answers only from hidden facts; missing values follow the practice-set convention (accepts the assistant's offer); 12 student replies max per problem.
- Tests must pass without an API key and without ANSYS (`python -m pytest tests -q`).
- Repo conventions: units in field names, no em dashes, one short comment on non-trivial lines, GenAI log row.
- Commit to `main` (minor, Role 3 files only); push only with the user's OK.

## Review Focus

- Student says "yes" to a setup card before the SF is settled: `confirmed` passes only for the card actually agreed; `asked` for SF needs its own answer. (Task 3 `test_sf_asked_needs_own_answer`.)
- A run that ends with no `optimize_design` result (reply cap, budget, error): every pick check fails with a reason, never a crash. (Task 3 `test_no_result_fails_cleanly`.)
- `vary` given as display labels ("Gusset length") instead of contract names: grader normalises through the family labels. (Task 3 `test_vary_accepts_labels`.)
- Recommended design `None` (nothing feasible): `feasible` and `pick_gap` fail with "no recommendation". (Task 3 `test_no_recommendation`.)
- Key missing in a problem file: grader and runner refuse that problem with a clear message. (Task 1 `test_missing_key_refused`.)

---

### Task 1: Problem loading and key computation

**Files:**
- Create: `src/eval/fast_keys.py`, `tests/test_fast_keys.py`

**Interfaces:**
- Produces:
  - `FAST_DIR = <repo>/practice/fast`
  - `@dataclass FastProblem(id: str, family: str, request: str, hidden_facts: str, key: dict, path: Path)`
  - `load_fast_problem(path: Path, require_key: bool = True) -> FastProblem` (raises `ValueError` naming the file and field; with `require_key`, `key.model_optimum` must exist unless `key.expect_handoff`)
  - `load_fast_problems(folder: Path = FAST_DIR, require_key: bool = True) -> list[FastProblem]`
  - `model_eval(family: str, material_key: str, force_n: float, design: dict[str, float]) -> dict` with keys `mass_kg, max_stress_mpa_med, max_stress_mpa_worst, max_disp_mm_med, max_disp_mm_worst, safety_factor_med, safety_factor_worst`
  - `meets(ev: dict, safety_factor: float, limits: list[dict], at: str = "worst") -> bool`
  - `model_optimum(study: dict, family: str, n: int = 400_000, seed: int = 0) -> dict` with `design, objective_value, safety_factor_worst, method, median: {design, objective_value}`
  - `sim_optimum(study: dict, family: str) -> dict` with `design_id, design, objective_value, safety_factor` (all `None` plus `reason` when not applicable)

- [ ] **Step 1: Write failing tests**

```python
def test_model_eval_worst_is_more_pessimistic():
    ev = model_eval("l_bracket_v0", "structural_steel", 500.0, BASELINE_V0)
    assert ev["max_stress_mpa_worst"] > ev["max_stress_mpa_med"] > 0
    assert ev["safety_factor_worst"] < ev["safety_factor_med"]

def test_model_optimum_feasible_and_median_not_heavier():
    opt = model_optimum(STUDY_500, "l_bracket_v0", n=20_000)
    ev = model_eval("l_bracket_v0", "structural_steel", 500.0, opt["design"])
    assert meets(ev, 1.5, [], at="worst")
    assert opt["median"]["objective_value"] <= opt["objective_value"]

def test_model_optimum_holds_unvaried_dims_at_baseline():
    opt = model_optimum({**STUDY_500, "vary": ["thickness_mm"]}, "l_bracket_v0", n=5_000)
    assert all(opt["design"][k] == BASELINE_V0[k] for k in BASELINE_V0 if k != "thickness_mm")

def test_sim_optimum_only_for_500n_steel():
    assert sim_optimum({**STUDY_500, "force_n": 800.0}, "l_bracket_v0")["design_id"] is None

def test_missing_key_refused(tmp_path):
    (tmp_path / "x.yaml").write_text("id: x\nfamily: l_bracket_v0\nrequest: r\nhidden_facts: f\nkey: {study: {}, expect_handoff: false}\n")
    with pytest.raises(ValueError, match="model_optimum"):
        load_fast_problem(tmp_path / "x.yaml")
```
(`STUDY_500` = 500 N, -90°, structural_steel, SF 1.5, minimise mass, no limits, vary all; `BASELINE_V0 = FAMILIES["l_bracket_v0"].baseline_mm`; sim test skips when the CSV is absent.)

- [ ] **Step 2: Run** `python -m pytest tests/test_fast_keys.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `src/eval/fast_keys.py`.
  - `model_eval`: build `PowerLawPredictor` for the material and family as `Part._predictor_for` does (Poisson 0.3), evaluate one row with `fit.predict_arrays`, mass from `FAMILY_VOLUME[family]`, worst = med × band.
  - `model_optimum`: build a `StudySpec` (constraint `safety_factor >= sf` plus limits; non-varied dims as low == high at baseline; `n_candidates=n`), call `predict(spec, n=n, seed=seed)`; the first shortlisted candidate with `feasible_p95` is the pessimistic optimum. Median optimum: the same sample with constraints judged on the median. `method = "power_law predict, n=<n>, seed=<seed>"`.
  - `sim_optimum`: not applicable unless 500 N, -90°, steel; else filter `runs.csv` by SF = 250 / `max_stress_mpa` ≥ sf and limits, and non-varied dims within 1 % of baseline; best objective row.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -m "Fast-path benchmark: problem loading and model-optimum keys"`

### Task 2: The 10 problems and their keys

**Files:**
- Create: `practice/fast/*.yaml` (10), `scripts/build_fast_keys.py`, `practice/fast/README.md`
- Test: `tests/test_fast_problems.py`

**Interfaces:**
- Consumes: Task 1 `load_fast_problems`, `model_optimum`, `sim_optimum`.
- Produces: problem files with `key.study`, `key.must_ask`, `key.expect_handoff`, `key.model_optimum`, `key.sim_optimum`.

- [ ] **Step 1: Write failing tests**

```python
def test_ten_problems_load_with_keys():
    assert sorted(p.id for p in load_fast_problems()) == sorted(["v0_clean_mass", "v0_missing_sf", "v0_missing_force",
        "v0_alu_vague", "v0_kn_units", "v0_stiff", "v1_clean_mass", "v1_named_dims", "v1_sf2_lbf", "fast_refuse_titanium"])

def test_unit_conversions():
    k = {p.id: p.key["study"] for p in load_fast_problems()}
    assert k["v0_kn_units"]["force_n"] == 800.0
    assert abs(k["v1_sf2_lbf"]["force_n"] - 667.233) < 0.01 and k["v1_sf2_lbf"]["safety_factor"] == 2.0

def test_forces_inside_fast_range_except_handoff():
    for p in load_fast_problems():
        if not p.key["expect_handoff"]:
            assert 100.0 <= p.key["study"]["force_n"] <= 1000.0

def test_sim_optimum_only_where_load_matches():
    for p in load_fast_problems():
        if p.key["expect_handoff"]:
            continue
        s = p.key["study"]
        match = s["force_n"] == 500.0 and s["angle_deg"] == -90.0 and s["material_key"] == "structural_steel"
        assert (p.key["sim_optimum"]["design_id"] is not None) == match
```

- [ ] **Step 2: Run** → FAIL (no problems).
- [ ] **Step 3: Write the 10 YAMLs** per the spec table: requests in a student's plain words; `must_ask` includes `support` and `load_face` unless the request states them; `v0_stiff` minimises `max_disp_mm` with `mass_kg <= 0.25`; `v1_named_dims` names thickness, gusset thickness, gusset length; `fast_refuse_titanium` has `expect_handoff: true`. Then `scripts/build_fast_keys.py [--only ID]` writes `model_optimum` and `sim_optimum` (n 400,000, seed 0), skipping hand-off problems.
- [ ] **Step 4: Run** `python -m scripts.build_fast_keys`, then `python -m pytest tests/test_fast_problems.py -q` → PASS.
- [ ] **Step 5: Commit** `git commit -m "Fast-path benchmark: 10 problems and keys (pending user review)"`.
- [ ] **Step 6: STOP for the user's review** of requests, hidden facts and keys (table: id, study, must_ask, model optimum, median optimum, sim optimum). Record approval in `practice/fast/README.md`.

### Task 3: Grader

**Files:**
- Create: `src/eval/fast_grader.py`, `tests/test_fast_grader.py`

**Interfaces:**
- Consumes: Task 1 `FastProblem`, `model_eval`, `meets`; `src.pydiscovery.families.FAMILIES` (labels).
- Run log (written by Task 4): `{"id": str, "events": [{"t": float, "type": "student"|"text"|"tool"|"tool_result"|"path"|"handoff"|"error", ...}], "result": dict | None, "stopped": str | None}`. A `tool_result` for `optimize_design` carries the JSON content string with `result` (contract `Result`).
- Produces: `PICK_GAP_PASS = 0.05`; `grade_fast(problem: FastProblem, log: dict) -> dict` = `{"id", "checks": {name: {"result": "pass"|"fail"|"n/a"|"info", "detail": str}}, "pick": {"gap": float | None, "sim_gap": float | None}}`. Check names: `force, direction, material, safety_factor, objective, limits, vary, asked, confirmed, no_silent_default, feasible, pick_gap, sim_gap, handoff`.

- [ ] **Step 1: Write failing tests** (helper `make_log(spec, recommended, events)`):
  - `test_clean_run_passes_all` (spec = key, recommended = key model optimum: every check pass or info, `pick["gap"] == 0`)
  - `test_wrong_force_fails_force_only`
  - `test_vary_accepts_labels`
  - `test_sf_asked_needs_own_answer`
  - `test_confirm_before_yes_fails`
  - `test_silent_sf_default` (spec SF 2.0, key 1.5 → `no_silent_default` fail)
  - `test_pick_gap_threshold` (4 % pass, 6 % fail)
  - `test_no_recommendation`, `test_no_result_fails_cleanly`
  - `test_handoff_problem` (`path: slow` and no `optimize_design` → `handoff` pass, others `n/a`)
  - `test_sim_gap_is_info_only`
- [ ] **Step 2: Run** `python -m pytest tests/test_fast_grader.py -q` → FAIL.
- [ ] **Step 3: Implement** `grade_fast`. Spec = `result.spec`: force = `load.force_n` (within 0.5 %), direction = `load.angle_deg` (within 1°), SF = the `safety_factor >=` constraint, limits = the other constraints, vary = parameters with low < high. "Asked" reuses the practice grader's timing rule (`src/eval/grader.py` `_asked`) with patterns: support /support|fixed|bolted|wall|back face/i, load_face /free end|end face|load(ed)? face/i, safety_factor /safety factor|\bSF\b/i, force /force|newton|\bN\b|load/i, material /material|steel|alumin/i. Feasible and gap use `model_eval` at `worst`; gap = (pick − opt) / opt for minimise, (opt − pick) / opt for maximise.
- [ ] **Step 4: Run** → PASS; `python -m pytest tests -q` → all pass.
- [ ] **Step 5: Commit** `git commit -m "Fast-path benchmark: grader"`.

### Task 4: Runner, simulated student, report, first run

**Files:**
- Create: `src/eval/fast_runner.py`, `scripts/run_fast_practice.py`, `tests/test_fast_runner.py`
- Modify: `docs/genai-log.md` (one row)

**Interfaces:**
- Consumes: Task 1 loaders, Task 3 `grade_fast`, `AssistantSession`, `write_demo_part(path, family=..., physics=False)`, `src.eval.sim_user` rules text, `src.llm` (`counted_client`, `cost`, `append_spend`, `BudgetExceeded`).
- Produces:
  - `class FastStudent(problem: FastProblem, client)` with `reply(assistant_text: str) -> str`
  - `run_fast_problem(problem, client, out_dir: Path, max_replies: int = 12, session_factory=AssistantSession) -> dict` (run log; stub workbench/agent when `expect_handoff`)
  - `summary_markdown(graded: list[dict]) -> str` (checks table + "pick" table with `gap` and `sim_gap`)
  - CLI `python -m scripts.run_fast_practice [--only ID ...] [--label L] [--budget 0.20] [--regrade DIR]` → `practice/results/fast-<YYYYMMDD-HHMM>[-L]/` with `<id>.json`, `<id>.graded.json`, `summary.md`; spend appended to the ledger.

- [ ] **Step 1: Write failing tests** with a fake session factory and fake student (no network):
  - `test_runner_stops_on_result`, `test_runner_stops_at_reply_cap` (`stopped == "reply cap"`, 12 student events)
  - `test_handoff_uses_stubs` (stub make_workbench/make_agent passed; no ANSYS import)
  - `test_budget_stop` (`BudgetExceeded` → `stopped == "budget"`)
  - `test_regrade_reads_saved_logs`
- [ ] **Step 2: Run** `python -m pytest tests/test_fast_runner.py -q` → FAIL.
- [ ] **Step 3: Implement.** `session.upload(part)`, then `session.send(request)`; after each assistant turn with no result and no hand-off, the student replies to that turn's text. Result = `result` of the last ok `optimize_design` `tool_result`.
- [ ] **Step 4: Run** → PASS; full suite → PASS.
- [ ] **Step 5: Live cost check** (needs `OPENAI_API_KEY`, keys approved): `PYTHONIOENCODING=utf-8 python -m scripts.run_fast_practice --only v0_clean_mass --label costcheck`. Expected: one graded problem, cost under $0.02.
- [ ] **Step 6: Full live run** `... --label first`. Expected: 10 graded problems, total under $0.20; read `summary.md`.
- [ ] **Step 7: Commit** code, `practice/results/fast-*-first/`, GenAI log row: `git commit -m "Fast-path benchmark: runner and first run"`.
