# Template-family benchmark problems: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one fast-path benchmark problem per template family (T01 to T10), a `family` grader check, a CalculiX
reference for templates, and a `--repeat N` run mode with a combined summary.

**Architecture:** Problem files stay one YAML each in `practice/fast/`. The grader gains one study check. The keys module
reads Can's multi-family table for templates. The runner script loops the problem set N times into `r1/ .. rN/`, and a new
`combined_summary` in `src/eval/fast_runner.py` merges the graded repeats.

**Tech Stack:** Python 3.12, pytest, pandas, PyYAML. Run everything with `..\.venv\Scripts\python.exe` from the repo root.

**Spec:** `docs/superpowers/specs/2026-11-27-template-benchmark-design.md`

## Global Constraints

- Scoring key stays the 2,000-design search, seed 0 (`FAST_PATH_CANDIDATES`); pass line `PICK_GAP_PASS = 0.05` unchanged.
- `--repeat` defaults to 1, and a run with 1 writes exactly today's folder layout.
- One budget cap (`--budget`, default $0.20) and one ledger row for the whole run, all repeats included.
- No edits to `src/pydiscovery/`, `contracts/`, `src/ui/`, CI or root `requirements.txt` (team check-in rule).
- Tests that need PyYAML start with `pytest.importorskip("yaml")` (CI has no PyYAML guarantee for these files).
- Comments: one short line, no em dashes or double-dash separators (repo CLAUDE.md).
- No graded run against the template keys before the user has reviewed them (README rule).
- Nothing is pushed without the user's OK; check `gh run list` after any push.

## Review Focus

1. A repeat that never reached a problem (budget stop in r2): the combined summary must count that repeat as not passing, never as a pass or n/a.
2. `--regrade` on an old single-run folder (no `r*/`): output must be identical to today's.
3. Can's table has 11 families including `l_bracket_v0`: the template reference must read only the problem's own family's rows.
4. A hand-off problem in a repeat run: `family` stays `n/a` and does not count in the total.
5. `info` and `n/a` columns when the first repeat lacks the problem: take the first repeat that has it.

---

### Task 1: `family` check in the grader

**Files:**
- Modify: `src/eval/fast_grader.py` (`STUDY_CHECKS`, the no-result loop, the result branch)
- Test: `tests/test_fast_grader.py`

**Interfaces:**
- Produces: check name `"family"` in every graded dict's `checks`; detail `"got X, want Y"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_family_check_passes_on_own_family():
    assert checks(problem(), log())["family"] == "pass"

def test_family_check_fails_on_other_family():
    out = grade_fast(problem(), log(result_spec={**spec(), "bracket_family": "t02_single_slot_v1"}))
    assert out["checks"]["family"]["result"] == "fail"
    assert "t02_single_slot_v1" in out["checks"]["family"]["detail"] and FAM in out["checks"]["family"]["detail"]

def test_family_check_na_on_handoff_and_fail_without_result():  # Review Focus 4
    p = problem(handoff=True)
    assert checks(p, log(events=[{"type": "path", "path": "slow", "reason": "titanium"}], result=False))["family"] == "n/a"
    assert checks(problem(), {**log(result=False), "stopped": "reply cap"})["family"] == "fail"
```

- [ ] **Step 2: Run them, expect FAIL** (`KeyError: 'family'`)

Run: `..\.venv\Scripts\python.exe -m pytest tests/test_fast_grader.py -q -k family`

- [ ] **Step 3: Implement.** Add `"family"` to `STUDY_CHECKS` right after `"vary"`; add `"family"` to the no-result fail loop; in the result branch call `cmp("family", spec.get("bracket_family") == problem.family, spec.get("bracket_family"), problem.family)`.

- [ ] **Step 4: Run the grader tests, expect PASS**

Run: `..\.venv\Scripts\python.exe -m pytest tests/test_fast_grader.py -q`

- [ ] **Step 5: Regrade every saved fast run into a scratch copy** (tracked results are not rewritten here) and confirm `family` passes on every non-hand-off problem: copy each `practice/results/fast-*` folder to the scratchpad, run `python -m scripts.run_fast_practice --regrade <copy>`, and check that no `*.graded.json` has `"family"` with `"result": "fail"`. Record how many folders were checked.

- [ ] **Step 6: Commit**

```bash
git add src/eval/fast_grader.py tests/test_fast_grader.py
git commit -m "Fast grader: family check (study family must be the problem's)"
```

### Task 2: CalculiX reference for template families

**Files:**
- Modify: `src/eval/fast_keys.py` (new constant and helper, `sim_optimum`)
- Test: `tests/test_fast_keys.py`

**Interfaces:**
- Produces: `MULTIFAMILY = {"dataset": REPO/"data"/"processed"/"formal_multifamily_v1"/"model_dataset_1000.csv", "manifest": <same folder>/"design_manifest.csv"}`;
  `_template_table(family: str) -> pd.DataFrame | None` with columns `design_id`, every name in `FAMILIES[family].ranges_mm`, `max_stress_mpa`, `max_disp_mm`, `mass_kg`; None when either file is missing.
- `sim_optimum(study, family)` keeps its signature and return shape.

- [ ] **Step 1: Write the failing tests** with a 4-row fixture written to `tmp_path` and `monkeypatch.setattr(fast_keys, "MULTIFAMILY", {...})` (fixture `fixture_tables`). Rows: `T01_safe` (`t01_double_hole_v1`, stress 150 MPa, 0.25 kg), `T01_unsafe` (same family, 200 MPa, 0.20 kg), `T02_light` (`t02_single_slot_v1`, 100 MPa, 0.10 kg), `V0_light` (`l_bracket_v0`, 100 MPa, 0.10 kg); every `params_json` at that family's baseline.

```python
def test_sim_optimum_template_picks_safe_row_of_own_family(fixture_tables):  # Review Focus 3
    out = sim_optimum(STUDY_500, "t01_double_hole_v1")
    assert out["design_id"] == "T01_safe" and out["max_stress_mpa"] == 150.0
    assert set(out["design"]) == set(FAMILIES["t01_double_hole_v1"].ranges_mm)

def test_sim_optimum_template_missing_table_gives_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(fast_keys, "MULTIFAMILY", {"dataset": tmp_path / "no.csv", "manifest": tmp_path / "no2.csv"})
    out = sim_optimum(STUDY_500, "t01_double_hole_v1")
    assert out["design_id"] is None and "not in this checkout" in out["reason"]
```

- [ ] **Step 2: Run, expect FAIL** (`KeyError: 't01_double_hole_v1'` from `RUNS`)

Run: `..\.venv\Scripts\python.exe -m pytest tests/test_fast_keys.py -q -k template`

- [ ] **Step 3: Implement `_template_table`**: join the two files on `design_id`, keep `bracket_family == family`, expand `params_json` into columns, rename `reference_max_stress_mpa` / `reference_max_disp_mm` / `reference_mass_kg` to `max_stress_mpa` / `max_disp_mm` / `mass_kg`. In `sim_optimum`, read `RUNS[family]` when the family is in `RUNS`, else `_template_table(family)` (None gives the "not in this checkout" reason). The selection rule after loading is unchanged.

- [ ] **Step 4: Run all key tests, expect PASS**, then check the real table: `sim_optimum` at 500 N steel SF 1.5 returns a `design_id` for each of the ten templates.

Run: `..\.venv\Scripts\python.exe -m pytest tests/test_fast_keys.py -q`

- [ ] **Step 5: Commit** (`git commit -m "Fast keys: CalculiX reference for template families from the multi-family table"`)

### Task 3: `--repeat N` and the combined summary

**Files:**
- Modify: `src/eval/fast_runner.py` (add `combined_summary`, `regrade_folder`)
- Modify: `scripts/run_fast_practice.py` (`--repeat`, repeat loop, regrade dispatch)
- Test: `tests/test_fast_runner.py`

**Interfaces:**
- Consumes: graded dicts `{"id", "checks": {name: {"result", "detail"}}, "pick": {...}}` from `grade_fast`.
- Produces:
  - `combined_summary(repeats: list[list[dict]], ids: list[str]) -> str`. `repeats[i]` is repeat i+1's graded list and `ids` the problem order. Per problem and check: if any repeat has the check as pass or fail, the cell is `pass` when all N repeats pass, else `**fail** (k/N)` with k the passing repeats; a repeat without the problem counts as not passing. Otherwise the cell is the result of the first repeat that has the problem. Total line `**P / C combined checks pass**`. Pick table: one scored-gap column per repeat. Failures list names the repeat (`r2 p pick_gap: ...`).
  - `regrade_folder(folder: Path, problems: dict[str, FastProblem]) -> str`. With `r*/` subfolders: regrade each (writing its own `summary.md`), write the combined summary to `folder/summary.md` and return it. Without: regrade, write and return `summary_markdown`, as today.

- [ ] **Step 1: Write the failing tests**

```python
def g(pid, **res):  # graded dict with the given check results
    return {"id": pid, "checks": {k: {"result": v, "detail": k} for k, v in res.items()},
            "pick": {"gap": 0.01, "dense_gap": None, "sim_gap": None}}

def test_combined_all_pass_is_pass():
    md = fast_runner.combined_summary([[g("p", force="pass")]] * 3, ["p"])
    assert "| p | pass |" in md and "1 / 1" in md

def test_combined_counts_failing_repeats():
    md = fast_runner.combined_summary([[g("p", force="pass")], [g("p", force="fail")], [g("p", force="pass")]], ["p"])
    assert "**fail** (2/3)" in md and "0 / 1" in md

def test_combined_missing_repeat_is_not_a_pass():  # Review Focus 1
    md = fast_runner.combined_summary([[g("p", force="pass")], [], [g("p", force="pass")]], ["p"])
    assert "**fail** (2/3)" in md

def test_combined_info_from_first_repeat_with_problem():  # Review Focus 5
    md = fast_runner.combined_summary([[], [g("p", dense_gap="info")], [g("p", dense_gap="n/a")]], ["p"])
    assert "| p | info |" in md

def test_regrade_folder_with_repeats(tmp_path):
    for r in ("r1", "r2"):
        (tmp_path / r).mkdir()
        (tmp_path / r / "p.json").write_text(json.dumps(run(problem(), tmp_path / r)), encoding="utf-8")
    md = fast_runner.regrade_folder(tmp_path, {"p": problem()})
    assert (tmp_path / "r1" / "summary.md").exists()
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == md

def test_regrade_folder_single_run_unchanged(tmp_path):  # Review Focus 2
    (tmp_path / "p.json").write_text(json.dumps(run(problem(), tmp_path)), encoding="utf-8")
    graded = fast_runner.regrade(tmp_path, {"p": problem()})
    assert fast_runner.regrade_folder(tmp_path, {"p": problem()}) == fast_runner.summary_markdown(graded)
```

- [ ] **Step 2: Run, expect FAIL** (`AttributeError: ... combined_summary`)

Run: `..\.venv\Scripts\python.exe -m pytest tests/test_fast_runner.py -q`

- [ ] **Step 3: Implement both functions** in `src/eval/fast_runner.py`.

- [ ] **Step 4: Wire the script.** `--repeat` (int, default 1; with 1 nothing changes). With N > 1, `_run_all` runs into `out / f"r{i}"` for i in 1..N with one shared `client` and `usage`, and after each problem rewrites `out / "summary.md"` with `combined_summary` over the repeats so far. A `BudgetExceeded` ends the whole run; the `finally` writes the one ledger row with `len(problems) * N` problems. `--regrade` calls `regrade_folder` and prints the result.

- [ ] **Step 5: Run the whole suite, expect PASS**

Run: `..\.venv\Scripts\python.exe -m pytest tests -q`

- [ ] **Step 6: Commit** (`git commit -m "Fast benchmark: --repeat N with a combined summary; regrade handles repeat folders"`)

### Task 4: The ten template problems, their keys, docs

**Files:**
- Create: `practice/fast/t01_clean_mass.yaml` ... `practice/fast/t10_clean_mass.yaml`
- Modify: `tests/test_fast_problems.py` (`IDS` gains the ten ids; rename `test_ten_problems_load_with_keys` to `test_problems_load_with_keys`; new test below; module docstring says 20 problems)
- Modify: `practice/fast/README.md`, `docs/genai-log.md`

**Interfaces:**
- Consumes: Task 2's `sim_optimum` through `scripts.build_fast_keys`.

- [ ] **Step 1: Write the failing test**

```python
TEMPLATES = ["t01_double_hole_v1", "t02_single_slot_v1", "t03_twin_slot_v1", "t04_rounded_window_v1",
             "t05_side_notches_v1", "t06_tapered_v1", "t07_stepped_v1", "t08_twin_gusset_v1",
             "t09_perforated_gusset_v1", "t10_offset_planform_v1"]

def test_template_problems_cover_every_template():
    ps = {p.id: p for p in load_fast_problems()}
    for i, fam in enumerate(TEMPLATES, 1):
        p = ps[f"t{i:02d}_clean_mass"]
        assert p.family == fam and p.key["must_ask"] == [] and p.key["states_setup"] is True
        assert p.key["study"]["force_n"] == 500.0 and p.key["study"]["safety_factor"] == 1.5
        assert p.key["sim_optimum"]["design_id"]  # Can's table has safe rows for every template
```

- [ ] **Step 2: Write the ten files** without keys. `request`: "This is a <shape> steel bracket, bolted to a wall by the back face of its upright leg. 500 N acts straight down on the free end of the flange. Safety factor 1.5. Make it as light as possible." Shapes in order: double-hole, single-slot, twin-slot, rounded-window, side-notch, tapered, stepped, twin-gusset, perforated-gusset, offset-planform. `hidden_facts` and `key.study` copied from `v1_clean_mass`; `must_ask: []`, `states_setup: true`, `expect_handoff: false`.

- [ ] **Step 3: Build the keys**

Run: `..\.venv\Scripts\python.exe -m scripts.build_fast_keys --only t01_clean_mass t02_clean_mass t03_clean_mass t04_clean_mass t05_clean_mass t06_clean_mass t07_clean_mass t08_clean_mass t09_clean_mass t10_clean_mass`
Expected: ten lines, each with a model value and a numeric `sim` value (no reason text).

- [ ] **Step 4: Run the whole suite, expect PASS**

Run: `..\.venv\Scripts\python.exe -m pytest tests -q`

- [ ] **Step 5: Docs.** README: twenty problems, the `family` check, `--repeat`, and "template keys built 2026-11-27, awaiting the user's review". genai-log: one 2026-11-27 row for this work.

- [ ] **Step 6: Commit** (`git commit -m "Fast benchmark: ten template-family problems with keys (awaiting review)"`)

- [ ] **Step 7: STOP for the user's key review.** Show each template's model optimum, dense optimum and CalculiX best in one table. After approval, mark the README review status, commit, and ask before the graded run:
  `..\.venv\Scripts\python.exe -m scripts.run_fast_practice --only t01_clean_mass t02_clean_mass t03_clean_mass t04_clean_mass t05_clean_mass t06_clean_mass t07_clean_mass t08_clean_mass t09_clean_mass t10_clean_mass --repeat 3 --label templates` (expected about $0.06; T03 and T08 should fail `family`).
