# Practice Set and Grader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A repeatable practice set that runs the ANSYS design agent against a simulated user on
about 12 bracket problems, grades its setup and feature proposal against reviewed answer keys,
and records a "before" and an "after" score around a prompt update.

**Architecture:** Problems are YAML files under `practice/problems/`. A runner drives the real
`Agent` + `Workbench` with an LLM simulated user. A `RecordingWorkbench` intercepts the first
`first_look`/`optimize` call, so the search never runs. Every event goes to a JSON run log. A pure
grader turns run log + problem key + face list into a per-item checklist and a `summary.md` table.
Answer-key effects come from a one-time ANSYS first-look per problem and are written to a sidecar
file, which a person marks as reviewed.

**Tech Stack:** Python 3.12 (`Project2/.venv`), PyYAML 6, anthropic 2.3, pytest 8, the existing
`src/agent` package (PyAnsys Geometry + PyMechanical, ANSYS Student 2026 R1).

**Spec:** `docs/superpowers/specs/2026-11-11-practice-set-design.md`

## Global Constraints

- Run everything from the SimpleSim repo root with `..\.venv\Scripts\python.exe`. Set `PYTHONIOENCODING=utf-8` for scripts that print agent text.
- Model: the agent uses `SIMPLESIM_MODE` (default `debug` = `claude-haiku-5-5`). The simulated user always uses `claude-haiku-5-5`.
- Never run the search during grading: `first_look`/`optimize` are intercepted.
- Units are mm, N, MPa, kg. Force tolerance ±1 % of the key force magnitude.
- Cap: 8 simulated-user turns per problem.
- Answer-key labels: `controls_limit` if the feature moves a limited response by ≥ 5 % (|change_rel| ≥ 0.05) or ranks first in that response's `limit_ranking`; `moves_objective` if |change_rel| of the objective ≥ 0.05; otherwise `negligible`.
- The grader refuses a problem whose effects sidecar is missing or has `reviewed: false` (status `not_graded`, reason `key not reviewed`).
- SimJEB and NinSample files stay outside the repo. They are referenced as `external:<path relative to Project2>`. A missing file means status `skipped`.
- Do not change files on `main`. Work on branch `Yunus`. Commit locally; push only with the user's OK.
- Unit tests use no ANSYS and no API (fake clients and workbenches, as in `tests/test_agent_loop.py`).

## Review Focus

- **The agent never calls `set_load_case`** (it only asks questions until the turn cap). Expect the setup checks to say `fail` with detail "no set_load_case call", not to crash. Test in Task 3.
- **The proposal comes through `optimize`, not `first_look`.** Its features use `lower`/`upper` instead of `low`/`high`. The range and feature checks must read both forms. Test in Task 3.
- **A split hole is named by the other half face**, or holes are grouped with `also_face_ids`. These must still match the key. Test in Task 2.
- **The agent calls `set_load_case` twice.** Grade the first call and mark `needed_correction`. Test in Task 3.
- **A tool raises during the run** (e.g. `MeshTooLarge` on the baseline). The runner must still save the run log with `end: "error"`, and the grader returns `not_graded`. Test in Task 5.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/eval/__init__.py` | empty |
| `src/eval/problems.py` | `Problem` dataclass, YAML loader, part-path resolution, effects sidecar loader |
| `src/eval/features.py` | canonical feature keys and key-vs-proposal matching (split holes, fillet pairs, wall alternatives) |
| `src/eval/grader.py` | `grade(problem, effects, run_log) -> dict` checklist |
| `src/eval/keys.py` | `label_effects(first_look_summary, objective, limits) -> list[str]` |
| `src/eval/sim_user.py` | `SimUser`: LLM simulated user |
| `src/eval/runner.py` | `RecordingWorkbench`, `run_problem(...) -> dict` run log |
| `src/eval/report.py` | `write_results(dir, results)`, `summary_markdown(results) -> str`, `compare_markdown(before, after) -> str` |
| `scripts/dump_faces.py` | write `practice/parts/<name>.faces.json` from `Workbench.load_part` |
| `scripts/build_key.py` | run the key first-look(s) for one problem, write `<id>.effects.yaml` with `reviewed: false` |
| `scripts/run_practice.py` | run all (or `--only id`) problems with `--label before|after`, write `practice/results/<timestamp>-<label>/` |
| `practice/problems/*.yaml`, `practice/problems/*.effects.yaml`, `practice/parts/*.faces.json` | data |
| `practice/README.md` | how to add a problem, build a key, run, read results |
| `tests/test_eval_*.py` | unit tests |

### Problem YAML (fixed by the spec; the loader validates it)

```yaml
id: lbr_clean_auto            # file name without .yaml, unique
part: examples/l_bracket.scdocx        # or  external:external/simjeb/148.stp
kind: clean_auto   # clean_auto | clean_named | missing_info | units | bad_requirement | complex
request: "I have this L bracket ..."
hidden_facts: |
  The back face of the tall leg is bolted to a wall. 500 N straight down on the end of the short leg.
  Structural steel. Minimise mass, safety factor 1.5. No allowable given beyond that.
user_named_features: []       # list of key feature ids
key:
  setup:
    fixed_face_ids: ["0:12"]
    load_face_ids: ["0:7"]
    force_N: [0, -500, 0]
    material: Structural Steel
    safety_factor: 1.5
  must_ask: []                # subset of: force, fixed_faces, load_faces, material, safety_factor, objective
  must_flag: []               # subset of: safety_factor_below_1, unit_conversion
  objective: mass_kg
  limits: [{response: safety_factor, op: ">=", value: 1.5}]
  features:
    - id: base_thickness
      label: thickness of the horizontal leg
      face_id: "0:3"
      action: offset            # offset | radius | fillet
      other_face_id: null       # fillet only
      alt_face_ids: ["0:4"]     # offset only: other faces whose offset changes the same wall
      key_low: -2.0             # range used by build_key.py
      key_high: 2.0
      min_value: null           # optional range bounds checked by the grader
      max_value: null
```

### Effects sidecar `practice/problems/<id>.effects.yaml`

```yaml
problem: lbr_clean_auto
reviewed: false               # a person sets true after checking
built: 2026-11-12             # ISO date
effects: {base_thickness: controls_limit, hole_radius: negligible}
evidence: {...}               # the raw first_look summaries, for the reviewer
```

### Run log (JSON, one per problem)

```json
{"problem_id": "...", "label": "before", "model": "claude-haiku-5-5", "faces": [...],
 "events": [{"seq": 0, "type": "user", "text": "..."},
            {"seq": 1, "type": "agent_text", "text": "..."},
            {"seq": 2, "type": "tool_call", "name": "set_load_case", "input": {...}},
            {"seq": 3, "type": "tool_result", "name": "set_load_case", "is_error": false, "content": "..."}],
 "proposal": {"tool": "first_look", "input": {...}},
 "end": "proposal", "error": null, "user_turns": 3, "usage": {...}}
```

`end` is one of `proposal`, `turn_cap`, `error`. `proposal` is `null` unless `end == "proposal"`.

### Checklist (grader output)

```json
{"problem_id": "...", "status": "graded", "reason": null,
 "checks": {"setup_fixed_faces": {"result": "pass", "detail": "..."}, ...}}
```

`status`: `graded` | `not_graded` | `skipped`. Each `result`: `pass` | `fail` | `n/a`. Check names
(this order, used as summary columns): `setup_fixed_faces`, `setup_load_faces`, `setup_force`,
`setup_material`, `safety_factor_stated`, `asked_before_acting`, `flagged_before_acting`,
`obedience`, `feature_recall`, `ranges_valid`, `complex_part`, `needed_correction`,
`extra_negligible` (detail = count; `pass` when 0).

---

### Task 1: Problem files, loader and part paths

**Files:**
- Create: `src/eval/__init__.py`, `src/eval/problems.py`, `practice/README.md`
- Test: `tests/test_eval_problems.py`

**Interfaces:**
- Produces:
  - `REPO: Path` and `PROJECT: Path` (`REPO.parent`)
  - `@dataclass Problem(id, part, kind, request, hidden_facts, user_named_features: list[str], key: dict)`
  - `load_problem(path: Path) -> Problem` (raises `ValueError` naming the bad field)
  - `load_problems(folder: Path) -> list[Problem]` (sorted by id; ignores `*.effects.yaml`)
  - `resolve_part(p: Problem) -> Path | None` (`None` when an `external:` file is missing)
  - `load_effects(p: Problem, folder: Path) -> dict | None`

- [ ] **Step 1: Write the failing tests**

```python
def test_load_problem_roundtrip(tmp_path):   # write the YAML example above, load it
    p = load_problem(tmp_path / "lbr_clean_auto.yaml")
    assert p.id == "lbr_clean_auto" and p.key["setup"]["force_N"] == [0, -500, 0]
    assert p.key["features"][0]["alt_face_ids"] == ["0:4"]

def test_rejects_unknown_kind(tmp_path): ...            # kind: foo -> ValueError mentioning "kind"
def test_rejects_named_feature_not_in_key(tmp_path): ...# user_named_features: [nope] -> ValueError
def test_rejects_id_not_matching_filename(tmp_path): ...
def test_external_missing_resolves_none():               # part: external:external/none.stp
    assert resolve_part(prob_with_part("external:external/none.stp")) is None
def test_repo_part_resolves():
    assert resolve_part(prob_with_part("examples/l_bracket.scdocx")) == REPO / "examples/l_bracket.scdocx"
def test_load_problems_skips_effects_files(tmp_path): ...
def test_load_effects_missing_returns_none(tmp_path): ...
```

- [ ] **Step 2:** `..\.venv\Scripts\python.exe -m pytest tests/test_eval_problems.py -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement `src/eval/problems.py`.** Use `yaml.safe_load`. Validate `kind`, `must_ask`, `must_flag` and `action` against the enums above. Every `user_named_features` entry must be a key feature id.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Write `practice/README.md`: the folder layout, the YAML fields (link to this plan's schema), and the add-problem → dump faces → build key → review → run workflow.
- [ ] **Step 6: Commit** `git add src/eval tests/test_eval_problems.py practice/README.md && git commit -m "Practice set: problem schema and loader"`

### Task 2: Feature matching

**Files:**
- Create: `src/eval/features.py`
- Test: `tests/test_eval_features.py`

**Interfaces:**
- Consumes: `src.agent.screen.hole_faces(faces, face_id) -> list[str]`
- Produces:
  - `proposal_features(proposal: dict) -> list[dict]`: normalizes `first_look` (`low`/`high`) and `optimize` (`lower`/`upper`) features to `{face_id, action, other_face_id, also_face_ids, low, high}`.
  - `matches(key_feature: dict, prop_feature: dict, faces: list[dict]) -> bool`
  - `match_all(key_features, prop_features, faces) -> dict[str, list[int]]`: key id → indices of matching proposal features.
  - `unmatched(key_features, prop_features, faces) -> list[int]`: proposal indices that match no key feature.

Matching rules (from the spec):
- `radius`: the key hole's faces (`hole_faces` of the key face) ⊆ the union of `hole_faces` over the proposal's `face_id` and each `also_face_ids` entry.
- `fillet`: `{face_id, other_face_id}` sets are equal.
- `offset`: the proposal's `face_id` ∈ `[key.face_id] + key.alt_face_ids`.
- The actions must be equal.

- [ ] **Step 1: Write the failing tests.** Use a hand-made `faces` list with two half-cylinder faces `"h1a"`, `"h1b"` (same radius and axis) and a second hole `"h2a"`, `"h2b"`.

```python
def test_split_hole_other_half_matches():
    assert matches(KEY_HOLE1, {"face_id": "h1b", "action": "radius"}, FACES)
def test_grouped_holes_match_each_key_hole():
    prop = {"face_id": "h1a", "action": "radius", "also_face_ids": ["h2a"]}
    assert matches(KEY_HOLE1, prop, FACES) and matches(KEY_HOLE2, prop, FACES)
def test_fillet_pair_order_ignored(): ...
def test_offset_alt_face_matches(): ...
def test_action_mismatch_fails(): ...
def test_optimize_lower_upper_normalized():
    f = proposal_features({"tool": "optimize", "input": {"features": [{"face_id": "a", "action": "offset", "lower": -1, "upper": 2, "start": 0}]}})
    assert f[0]["low"] == -1 and f[0]["high"] == 2
def test_unmatched_lists_extra_proposals(): ...
```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `src/eval/features.py`.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `"Practice set: feature matching (split holes, fillet pairs, wall faces)"`

### Task 3: Grader

**Files:**
- Create: `src/eval/grader.py`
- Test: `tests/test_eval_grader.py`

**Interfaces:**
- Consumes: `Problem` (Task 1); `proposal_features`, `match_all`, `unmatched` (Task 2); `screen.hole_faces`.
- Produces: `CHECKS: tuple[str, ...]` (the check names in the order above), and `grade(problem: Problem, effects: dict | None, run_log: dict | None) -> dict` (checklist format above).

Rules (spec "Grading" and "Claude's calls"):
- `run_log is None` → `skipped`. `effects` is None or not reviewed → `not_graded`, reason `key not reviewed`. `end == "error"` → `not_graded`, reason = the run log's `error`. `end == "turn_cap"` → `graded`, and every proposal-dependent check fails with detail `no proposal`.
- **Setup** uses the **first** `set_load_case` tool_call. Face sets are compared after expanding each face with `hole_faces`. Force passes when `|F − F_key| ≤ 0.01·|F_key|`. Material passes when the last `set_material.material` before the proposal equals the key material, or when there is none and the key material is `Structural Steel`.
- `needed_correction`: `fail` when `set_load_case` was called more than once, else `pass`.
- `safety_factor_stated`: some `agent_text` before the proposal matches `/safety factor/i` and contains the key value (formatted `1.5` / `2` etc.).
- `asked_before_acting`: for each `must_ask` item, an `agent_text` containing `?` and that item's keyword appears before the first tool call that uses it. Keywords: force → `force|load|newton|\bN\b`; fixed_faces → `fixed|support|bolt|mount`; load_faces → `load|force`; material → `material`; safety_factor → `safety factor`; objective → `objective|minimi|goal`. Uses: force/fixed_faces/load_faces → `set_load_case`; material/safety_factor → `set_material`; objective → proposal. `n/a` when `must_ask` is empty.
- `flagged_before_acting`: `safety_factor_below_1` → an `agent_text` matching `/safety factor/i` and (`/below 1|less than 1|under 1|< ?1/i` or the requested value) before any `set_material` call with `safety_factor < 1`; it passes if no such call happens and the text exists before the proposal. `unit_conversion` → an `agent_text` containing the converted force (|value − |F_key|| ≤ 1 %), e.g. `490`, before the first `set_load_case`. `n/a` when empty.
- `obedience`: `n/a` when `user_named_features` is empty. Otherwise `pass` iff every named key feature is matched and `unmatched(...)` is empty.
- `feature_recall`: `n/a` when features are named. Otherwise `pass` iff every key feature labelled `controls_limit` is matched. Detail lists the missed ones.
- `extra_negligible`: the count of proposal features matching only `negligible` key features, plus unmatched features. `pass` when 0.
- `ranges_valid`: every proposal feature satisfies offset `low ≤ 0 ≤ high`; radius/fillet `0 < low < high`; and the matched key feature's `min_value`/`max_value` when set.
- `complex_part`: `n/a` unless `kind == "complex"`. `pass` when an `agent_text` before the proposal matches `/complex/i` or asks the user which features to change (`?` and `/which (features|parts|dimensions)/i`), or the proposal has ≤ 4 features.

- [ ] **Step 1: Write the failing tests.** Build run logs by hand with a small helper `log(*events, proposal=None, end="proposal")`. One test per rule, including the Review Focus cases:

```python
def test_turn_cap_fails_proposal_checks_without_crash(): ...      # no set_load_case, end turn_cap
def test_optimize_proposal_graded(): ...                           # lower/upper ranges
def test_second_set_load_case_marks_correction_first_is_graded(): ...
def test_unreviewed_key_not_graded():
    assert grade(P, {"reviewed": False, "effects": {}}, LOG)["status"] == "not_graded"
def test_sf_flag_after_failed_set_material_fails(): ...            # set_material(0.8) error, then text
def test_unit_conversion_flag_passes_with_490(): ...
def test_named_features_extra_unlabelled_fails_obedience(): ...
def test_recall_misses_controls_limit(): ...
def test_offset_range_excluding_zero_invalid(): ...                # the L-bracket absolute-thickness bug
def test_complex_part_says_complex(): ...
def test_split_hole_fixed_faces_pass(): ...                        # agent fixed h1a only, key h1a+h1b
```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `src/eval/grader.py`, keyword patterns as module constants.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `"Practice set: grader checklist"`

### Task 4: Simulated user

**Files:**
- Create: `src/eval/sim_user.py`
- Test: `tests/test_eval_sim_user.py`

**Interfaces:**
- Produces: `SIM_MODEL = "claude-haiku-5-5"`, `class SimUser(problem: Problem, client)` with `reply(agent_text: str) -> str`. It keeps its own message history: the agent's text goes in as user turns, its replies are assistant turns. The `request` is the opening line and is not generated.

System prompt content (spec "How a problem is run" + the plan's calls):
- You are a beginner engineer. Here are the true facts: `hidden_facts`.
- Answer only what is asked, in plain words. Never volunteer a fact the agent did not ask for. Never give face ids. Do not correct the agent's choices.
- When the agent proposes a search or a set of features and asks for an OK: if `user_named_features` is empty, reply `OK, go ahead.`; otherwise reply `OK, go ahead with the features I asked for.`
- When asked about something not in the facts, say you do not know and the agent should choose a sensible default.
- If `kind == "complex"`: when asked which features to change, name the features whose labels are listed under `user_named_features`, or, if that is empty, say you are not sure.
- Keep replies under 60 words.

- [ ] **Step 1: Write the failing tests** with a fake client that records `system` and `messages` and returns fixed text.

```python
def test_system_prompt_holds_hidden_facts_and_rules():
    s = fake.calls[0]["system"]
    assert P.hidden_facts.strip() in s and "Never volunteer" in s and "face ids" in s
def test_named_features_approval_phrase(): ...   # "with the features I asked for" in system prompt
def test_history_alternates_and_grows(): ...     # 2 replies -> 4 messages, roles user/assistant
def test_uses_haiku(): assert fake.calls[0]["model"] == "claude-haiku-5-5"
```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement. `max_tokens=300`.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `"Practice set: simulated user"`

### Task 5: Runner, results and CLI

**Files:**
- Create: `src/eval/runner.py`, `src/eval/report.py`, `scripts/run_practice.py`
- Test: `tests/test_eval_runner.py`, `tests/test_eval_report.py`

**Interfaces:**
- Consumes: `Agent(workbench, client)` and `Agent.run(text) -> Iterator[dict]` (`src/agent/agent.py`); `Workbench` (`src/agent/tools.py`); `SimUser`; `grade`; `CHECKS`.
- Produces:
  - `class RecordingWorkbench(Workbench)`: overrides `call(name, args)`. For `first_look`/`optimize`, on the first call it stores `self.proposal = {"tool": name, "input": args}` and returns `json.dumps({"practice_run": "search intercepted; not run"})`. Every other call goes to `super().call`.
  - `run_problem(problem, part_path, wb, agent, sim_user, label: str, max_user_turns: int = 8) -> dict`. Returns the run log. It calls `wb.load_part(part_path)` and stores `faces = wb.faces`. It loops `agent.run(text)`, turning events into log events (`text` → `agent_text`, `tool` → `tool_call`, `tool_result` → `tool_result`). It stops after the turn in which `wb.proposal` was set (`end: proposal`) or after `max_user_turns` user messages (`end: turn_cap`). Any exception is caught and stored as `end: error` with `error: "<Type>: <msg>"`. The next user text is `sim_user.reply(<all agent_text of the last turn joined>)`.
  - `report.write_results(folder: Path, results: list[tuple[dict, dict]])` writes `<id>.json` (`{"run_log", "checklist"}`) per problem, plus `summary.md`.
  - `report.summary_markdown(checklists) -> str`: problems as rows, `CHECKS` as columns, ✓ / ✗ / – (n/a), a status column, and a final row with the pass count per column.
  - `report.compare_markdown(before: Path, after: Path) -> str`: per check, the pass count before → after, plus the problems whose result changed.
- `scripts/run_practice.py --label before|after [--only ID ...]` makes `practice/results/<YYYYmmdd-HHMM>-<label>/`. For each problem it builds a new `RecordingWorkbench(out_dir/<id>/ws)`, a new `Agent`, and a `SimUser`; skips problems whose part resolves to `None`; grades with `load_effects`; closes the workbench in `finally`; and prints token use and estimated cost with the `PRICES` table copied from `scripts/smoke_agent.py`. `scripts/run_practice.py --compare BEFORE_DIR AFTER_DIR` writes `compare.md` into AFTER_DIR.

- [ ] **Step 1: Write the failing tests.** Use a `FakeWB` subclassing `RecordingWorkbench`, with `load_part` stubbed to set `faces`, and `super().call` replaced by a stub. Use a scripted fake agent client in the style of `tests/test_agent_loop.py`, and a fake `SimUser`.

```python
def test_intercepts_first_look_and_stops(): ...    # end == "proposal", search stub never called
def test_turn_cap_ends_run(): ...                  # agent only asks -> end == "turn_cap", user_turns == 8
def test_tool_exception_saves_log_with_error(): ...# load_part raises MeshTooLarge-like -> end == "error"
def test_events_in_order_with_seq(): ...
def test_summary_markdown_columns_and_totals(): ...
def test_compare_markdown_counts(): ...
```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `runner.py`, `report.py` and `scripts/run_practice.py`.
- [ ] **Step 4:** Run the full suite `..\.venv\Scripts\python.exe -m pytest -q`. Expected: all pass (the 36 existing tests plus the new ones).
- [ ] **Step 5: Commit** `"Practice set: runner, results and summary"`

### Task 6: Face dumps and key builder

**Files:**
- Create: `src/eval/keys.py`, `scripts/dump_faces.py`, `scripts/build_key.py`
- Test: `tests/test_eval_keys.py`

**Interfaces:**
- Consumes: `screen.summarize` output (`features[i].responses[r].change_rel`, `limit_ranking`), `Workbench.call("set_load_case"|"set_material"|"first_look", ...)`, `load_problem`.
- Produces:
  - `label_effects(summary: dict, objective: str, limits: list[dict]) -> list[str]`: one label per summary feature, using the Global Constraints thresholds. A feature with `change_rel None` for every response gets `negligible`, and a warning is added to `evidence`.
  - `scripts/dump_faces.py PART [--name NAME]` writes `practice/parts/<name>.faces.json` (`describe_part` output: faces, inside_corners, volume, min wall).
  - `scripts/build_key.py PROBLEM_YAML` sets the key's material, safety factor and load case, then runs `first_look` on the key features in chunks of ≤ 4 with `low=key_low`, `high=key_high` and the key objective/limits. It writes `<id>.effects.yaml` with `reviewed: false`, the labels and the raw summaries. It refuses to overwrite a sidecar with `reviewed: true` unless `--force` is given.

- [ ] **Step 1: Write the failing tests** for `label_effects` on hand-made summaries.

```python
def test_limit_mover_is_controls_limit(): ...    # safety_factor change_rel 0.12
def test_top_of_limit_ranking_is_controls_limit_even_if_small(): ...
def test_objective_only_mover(): ...             # mass 0.08, sf 0.01 -> moves_objective
def test_small_everything_negligible(): ...      # all < 0.05
```

- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `keys.py`, `dump_faces.py`, `build_key.py`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5 (ANSYS):** Dump faces for the four parts: `python -m scripts.dump_faces examples/l_bracket.scdocx --name l_bracket`, then `saddle_bracket`, `..\external\simjeb\148.stp --name simjeb148` and `..\external\ninsample\NinSample.STEP --name ninsample`. Expected: four JSON files. NinSample's 4 holes show as 8 half faces grouped by `hole_faces`. Commit only the L-bracket and saddle face files: the SimJEB and NinSample face files describe outside-repo geometry, so put them in `practice/parts/external/` and add that folder to `.gitignore`.
- [ ] **Step 6: Commit** `"Practice set: effect labels, face dumps, key builder"`

### Task 7: Write the problems (user checkpoint)

**Files:**
- Create: `practice/problems/*.yaml` (about 12)

The mix, from the spec's problem kinds:

| Part | Problems |
| --- | --- |
| L-bracket | `lbr_clean_auto`, `lbr_named` (base thickness + corner fillet), `lbr_units` ("holds 50 kg"; force 490.5 N; must_flag unit_conversion) |
| Saddle | `sad_clean_auto`, `sad_missing_force` (must_ask force), `sad_named` (left flange only) |
| NinSample | `nin_clean_auto`, `nin_named_holes` (all 4 mounting holes as one variable), `nin_bad_sf` (safety factor 0.8; must_flag safety_factor_below_1) |
| SimJEB 148 | `jeb_complex` (kind complex; hidden facts name 2 features if asked), `jeb_named` |
| any | one more `missing_info` (no fixed faces described) on the L-bracket: `lbr_missing_support` |

- [ ] **Step 1:** Draft each file from the face dumps: setup face ids, candidate features (3–6 per part, with `key_low`/`key_high`), and requests written in beginner wording. Load cases: L-bracket 500 N −Y on the end face, back face fixed (as in `scripts/smoke_agent.py`); saddle as in `scripts/smoke_saddle.py`; SimJEB vertical case from `scripts/smoke_simjeb.py`; NinSample from the user (ask).
- [ ] **Step 2:** `python -c "from src.eval.problems import load_problems; print(len(load_problems(...)))"`. Expected: 12 loaded, no error.
- [ ] **Step 3: User checkpoint.** Show the user a one-line summary per problem plus the NinSample load-case question. Apply their changes.
- [ ] **Step 4: Commit** the problems, without the outside-repo face ids if the user says the NinSample geometry must stay private.

### Task 8: Build and review the answer keys (ANSYS, user checkpoint)

- [ ] **Step 1:** For each problem, run `python -m scripts.build_key practice/problems/<id>.yaml`. Problems on the same part and load case may copy a sidecar (same effects). Expected: one sidecar per problem, `reviewed: false`. About 5 min per part and load case; note any failed designs.
- [ ] **Step 2: User checkpoint.** Show a table per problem: feature, change in safety factor, change in mass, label. The user approves or changes labels. Set `reviewed: true` on the approved ones.
- [ ] **Step 3: Commit** the sidecars.

### Task 9: "Before" run

- [ ] **Step 1:** Check `tasklist` for leftover SpaceClaim, Mechanical or optiSLang processes.
- [ ] **Step 2:** `set PYTHONIOENCODING=utf-8 && ..\.venv\Scripts\python.exe -m scripts.run_practice --label before`. Expected: `practice/results/<ts>-before/summary.md`, every problem `graded` or `skipped`/`not_graded` with a reason, and the cost printed (expected under $0.50 on Haiku).
- [ ] **Step 3:** Read 3 run logs by hand: check that the grade matches what the agent actually did. A wrong grade is a grader bug: fix it in Task 3's code with a test before going on.
- [ ] **Step 4: Commit** the results folder.

### Task 10: Update the agent's instructions

**Files:**
- Modify: `src/agent/agent.py` (`SYSTEM`), `src/agent/tools.py` (the `first_look` and `optimize` descriptions only)
- Test: `tests/test_agent_prompt.py`

Behaviours to add (spec "Agent behaviour being graded"):
- Features the user names are binding: test exactly those. Extra ideas are offered as labelled suggestions ("changing X might also help, because …") and run only after the user agrees to that suggestion.
- If the user names no features, recommend them.
- After `describe_part`, say what you see (holes, walls, inside corners, likely fixed and load faces) and wait for the user to confirm before solving.
- On a really complex part, say it looks complex and ask the user which features to change. Do not say you have never seen such a part.
- A requested safety factor below 1, or a load given as a mass: point it out and ask before using it. Convert kg to N with g = 9.81 and show the conversion.
- Offset ranges are offsets from the current face, in mm, and must include 0 (the earlier absolute-thickness bug).

- [ ] **Step 1: Write the failing test** asserting the key phrases are in `SYSTEM` (`"binding"`, `"suggestion"`, `"looks complex"`, `"9.81"`, `"must include 0"`), and that `"never seen"` appears only in the negated rule.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Edit `SYSTEM` and the two tool descriptions.
- [ ] **Step 4:** Run the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `"Agent prompt: binding user features, labelled suggestions, complex parts, unit and SF flags"`

### Task 11: "After" run and comparison

- [ ] **Step 1:** `python -m scripts.run_practice --label after`.
- [ ] **Step 2:** `python -m scripts.run_practice --compare practice/results/<before> practice/results/<after>`. Expected: `compare.md` with the pass counts before → after.
- [ ] **Step 3:** Write a short note for the user. It covers what improved, what still fails, and the cost of both runs. It also points out that Haiku runs vary, so a single-problem change may be noise.
- [ ] **Step 4: Commit** the results, and update CLAUDE.md (Project2) with the outcome. Ask the user before pushing.
