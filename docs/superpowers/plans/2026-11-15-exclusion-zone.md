# Exclusion Zones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Judge the safety factor and stress limits on the peak outside growing zones around the fixed and loaded faces, report the excluded peak as not physical, and rebuild the SimJEB answer key with it.

**Architecture:** A pure function in `src/sim/zones.py` takes node coordinates, node stresses, the node indices of the support, load and guard faces, and a step length. It returns the zone result, and it is unit-tested without ANSYS. `mechanical.solve` gathers those arrays from the solved model (the stress PlotData it already reads, plus MeshData node coordinates and the named-selection nodes) and merges the zone result into its dict. `Workbench` tags the guard faces, uses the new stress for the safety factor and limits, and the prompt tells the agent how to report it.

**Tech Stack:** Python 3.12, numpy, scipy (`scipy.spatial.cKDTree`), PyMechanical embedding (ANSYS 2026 R1 Student), pytest.

**Spec:** `docs/superpowers/specs/2026-11-15-exclusion-zone-design.md`

## Global Constraints

- Steps: ½ × `min_wall_mm`, up to 3 steps (½, 1, 1½ × the wall). Settled = the outside peak changes by less than 5 % between steps.
- No extra solves. Only the results of the existing solve are used.
- `max_stress_MPa` keeps its meaning (the raw peak). New fields: `stress_for_limit_MPa`, `stress_for_limit_at_mm`, `excluded_peak_MPa`, `excluded_peak_at_mm`, `excluded_at` (`"support"` | `"load"` | `None`), `zone_mm`, `zone_settled`.
- `safety_factor` and every stress limit use `stress_for_limit_MPa`.
- A zone never contains a node of a guard face: the fillet faces (`NS_FILLETS`) and the faces edited in the design, minus any face that is itself fixed or loaded.
- Anti-goal: never hide a real stress. When in doubt, use the smaller zone (the higher, safer stress) and say the zone did not settle.
- Repo rules (SimpleSim CLAUDE.md): units in field names, one short comment line on non-trivial lines, no em dashes, log GenAI use in `docs/genai-log.md`, `tests/test_contracts.py` must pass.
- Do not run ANSYS or the OpenAI API in Tasks 1-4. Task 5 runs ANSYS only (no API).

## Review Focus

1. The peak is away from every support and load (the L-bracket's fillet). Expected: nothing excluded, `stress_for_limit_MPa == max_stress_MPa`, settled. Pinned in Task 1.
2. A fixed or loaded face is also a varied feature (the SimJEB bolt holes, the L-bracket arm length on load face `0:36`). Expected: that face is not a guard, so the zone can still grow. Pinned in Task 3.
3. A guard face sits within one step of a support (the fillet about 6 mm from the L-bracket wall face). Expected: the zone stops before it and, with fewer than 2 usable steps, `zone_settled` is false with the safe (higher) stress. Pinned in Task 1.
4. The stress PlotData cannot be read (the intermittent "PlotData is not supported" failure). Expected: the zone fields are None, `stress_for_limit_MPa` falls back to `max_stress_MPa`, and a message says so. Pinned in Task 2.
5. `min_wall_mm` is None (no opposite planar faces, e.g. an all-curved part). Expected: the step becomes 1 × the element size, and the result is still produced. Pinned in Task 1.

---

### Task 1: Zone function

**Files:**
- Create: `src/sim/zones.py`
- Test: `tests/test_zones.py`
- Modify: `requirements.txt` (add `scipy==1.18.1`, the version in `.venv`)

**Interfaces:**
- Produces: `exclusion_zone(coords_mm: np.ndarray, stress_mpa: np.ndarray, support_idx: np.ndarray, load_idx: np.ndarray, guard_idx: np.ndarray, step_mm: float, n_steps: int = 3, tol: float = 0.05, zone_mm: float | None = None) -> dict` with the 7 new fields from Global Constraints. `coords_mm` has shape (N, 3); the index arrays are integer row indices into it. If `zone_mm` is given, that single zone size is used (no search, `zone_settled` is copied as None); this is for the mesh check.
- Produces: `step_for(min_wall_mm: float | None, element_size_mm: float) -> float`, which returns `min_wall_mm / 2`, or `element_size_mm` when the wall is None.

Algorithm (not determined by the tests alone):
- d_s and d_l are the distances to the nearest support node and the nearest load node (one `cKDTree` each).
- d = min(d_s, d_l), and `kind` is the nearer of the two.
- d_guard = min over guard nodes of d (∞ if there are none). The usable sizes are `step_mm * k` for k = 1..n_steps with size < d_guard.
- p(s) = the max stress over nodes with d > s.
- Settled at the first k ≥ 2 with |p(s_k) − p(s_{k−1})| / p(s_{k−1}) < tol. The result is zone s_{k−1} and stress p(s_{k−1}): the smaller zone and the higher value.
- Not settled after the usable sizes run out, or fewer than 2 usable sizes: zone = the smallest usable size (0 if none), stress = p(that size), or the raw peak if none, and `zone_settled: False`.
- If the raw peak node already has d > s_1: nothing is excluded, so `excluded_*` is None, stress = raw, zone = s_1, `zone_settled: True`.
- `excluded_peak_MPa` = the raw peak when it lies inside the chosen zone; `excluded_at` = the kind of its nearest source.

- [ ] **Step 1: Write the failing tests** in `tests/test_zones.py`, on a synthetic bar of nodes: x from 0 to 100 mm in 1 mm steps, the support at x = 0 and the load at x = 100.
  - `test_peak_away_from_supports_is_kept`: a stress bump at x = 50 is the maximum. Expect `excluded_peak_MPa is None`, `stress_for_limit_MPa == 50-node value`, `zone_settled is True`.
  - `test_support_spike_is_excluded_and_settles`: stress = 100 + 1000·exp(−x/1) near x = 0, 100 elsewhere, step 3 mm. Expect `excluded_at == "support"`, `excluded_peak_MPa == 1100`, `stress_for_limit_MPa` ≈ 100 (within 5 %), `zone_settled is True`, `zone_mm == 3.0`.
  - `test_load_spike_is_labelled_load`: the same spike at x = 100. Expect `excluded_at == "load"`.
  - `test_guard_inside_first_step_gives_unsettled_raw_peak`: guard node at x = 2, step 3. Expect `zone_mm == 0`, `stress_for_limit_MPa == max_stress`, `zone_settled is False`.
  - `test_guard_limits_growth_to_one_step`: guard at x = 5, step 3. Expect `zone_mm == 3.0`, `zone_settled is False`.
  - `test_never_settling_uses_the_smallest_zone`: stress = 1000 / (x + 0.1) (keeps falling). Expect `zone_mm == step`, `stress_for_limit_MPa == p(step)`, `zone_settled is False`.
  - `test_fixed_zone_mm_skips_the_search`: `zone_mm=6.0`. Expect `zone_mm == 6.0`, `stress_for_limit_MPa == max over x > 6`, `zone_settled is None`.
  - `test_step_for`: `step_for(6.0, 2.0) == 3.0` and `step_for(None, 2.0) == 2.0`.
- [ ] **Step 2: Run** `..\.venv\Scripts\python.exe -m pytest tests/test_zones.py -q`. Expected: fails on the missing module.
- [ ] **Step 3: Implement** `src/sim/zones.py` as above. Coordinates are returned as rounded lists `[x, y, z]` (2 decimals), like `max_stress_at_mm`.
- [ ] **Step 4: Run** the same command. Expected: 8 passed. Then the full suite: all pass.
- [ ] **Step 5: Commit** `git add src/sim/zones.py tests/test_zones.py requirements.txt && git commit -m "Exclusion zone around supports and loads (pure function)"`.

### Task 2: Mechanical gathers the arrays and the mesh check uses the zone stress

**Files:**
- Modify: `src/sim/mechanical.py` (`solve`, `_peak_location`, `refinement_check`)
- Test: `tests/test_mechanical.py`

**Interfaces:**
- Consumes: `zones.exclusion_zone`, `zones.step_for`.
- Produces: `solve(..., guard: str | None = None, min_wall_mm: float | None = None, zone_mm: float | None = None)`. The returned dict gains the 7 fields. `guard` names a named selection that may be absent (no edits): absent = no guard nodes.
- Produces: `_zone_fields(nodes: list[int], values_pa: list[float], coords_m: dict[int, tuple], support_ids: set[int], load_ids: set[int], guard_ids: set[int], step_mm: float, zone_mm: float | None, max_stress_mpa: float) -> dict`. This is the pure glue: Pa to MPa and m to mm (PlotData and MeshData are SI, see `_peak_location`), then index arrays, then `exclusion_zone`. When `nodes` is empty (the PlotData read failed), it returns the fields with None and `stress_for_limit_MPa = max_stress_mpa`.
- Produces: `refinement_check` compares `stress_for_limit_MPa` (coarse vs fine), passes the coarse `zone_mm` into the fine solve, and passes `guard` and `min_wall_mm` through. The returned `stress_change` and `converged` are based on it.

Builder choices left open: read node coordinates in bulk from `analysis.MeshData` (target under 10 s at 120K nodes; measure it in Task 5). Get the face nodes of a named selection via its geometry ids and `MeshData.MeshRegionById(id).NodeIds`. Reuse the one PlotData read that `_peak_location` already does (refactor it to return nodes and values too). Do not read PlotData twice.

- [ ] **Step 1: Write the failing tests** in `tests/test_mechanical.py`:
  - `test_zone_fields_convert_units`: 3 nodes with coordinates in metres and stresses in Pa, the support at node 1. Expect the `_at_mm` values in mm and the stresses in MPa.
  - `test_zone_fields_without_plotdata_fall_back_to_raw_peak`: `nodes=[]`. Expect `stress_for_limit_MPa == max_stress_mpa` and `zone_mm is None`.
  - `test_refinement_check_compares_zone_stress_and_reuses_zone`: monkeypatch `mechanical.solve` with a fake that records its kwargs and returns `max_stress_MPa` 100 then 200 (a growing spike) and `stress_for_limit_MPa` 50 then 51, `zone_mm` 3.0. Expect `converged is True`, `stress_change` ≈ 0.02, and the second call's `zone_mm == 3.0`.
- [ ] **Step 2: Run** `..\.venv\Scripts\python.exe -m pytest tests/test_mechanical.py -q`. Expected: the 3 new tests fail.
- [ ] **Step 3: Implement** `_zone_fields` and the `solve` and `refinement_check` changes. Add a message to `messages` when the zone could not be computed.
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Mechanical: zone stress per solve; mesh check on the zone stress"`.

### Task 3: Workbench uses the zone stress and tags guard faces

**Files:**
- Modify: `src/agent/tools.py` (`_build`, `_evaluate_untimed`, `_responses`, `check_mesh` and `finalize_design` output, `RESPONSES`, the `optimize`/`first_look` limit validation, `table()`), and `app.py` `TABLE_HEADERS`
- Test: `tests/test_workbench.py`

**Interfaces:**
- Consumes: `mechanical.solve(..., guard=, min_wall_mm=)`, `refinement_check(...)`.
- Produces: the constant `GUARD = "NS_GUARD"`. `_build` names as `NS_GUARD` the faces edited by offset or radius plus the new fillet faces, minus the fixed and load face ids. If the set is empty, it names nothing.
- Produces: `_responses` gives `safety_factor = yield / stress_for_limit_MPa`, adds `stress_for_limit_MPa`, and keeps `max_stress_MPa` (raw).
- Produces: `RESPONSES` gains `"stress_for_limit_MPa"`. A limit or objective on `"max_stress_MPa"` raises `ValueError("limit stress with stress_for_limit_MPa (the peak outside the support and load zones); max_stress_MPa includes the singular spike")`.
- Produces: the `check_mesh` coarse/fine dicts include `stress_for_limit_MPa`, `zone_mm`, `zone_settled`, `excluded_peak_MPa`, `excluded_at`. The table gains the columns "stress for limit (MPa)" and "zone settled".
- Produces: the `min_wall_mm` from `_describe` is stored on the Workbench at `load_part`/`scale_part` and passed to every solve.

- [ ] **Step 1: Write the failing tests** in `tests/test_workbench.py`, with the existing fake-geometry and fake-solve pattern of that file. The fake solve returns the new fields.
  - `test_safety_factor_uses_zone_stress`: raw 400, zone 100, yield 250. Expect `safety_factor == 2.5`.
  - `test_guard_excludes_fixed_and_loaded_faces`: edits change face `0:36` (the load face) and `0:39`. Expect `name_faces("NS_GUARD", ["0:39"])` was called (`0:36` left out).
  - `test_guard_includes_new_fillet_faces`: a fillet edit. Expect the new fillet face ids in `NS_GUARD`.
  - `test_limit_on_raw_peak_is_refused`: `optimize` with a limit on `max_stress_MPa` raises a `ValueError` matching `stress_for_limit_MPa`.
  - `test_check_mesh_reports_zone_fields`: the output's coarse dict contains `stress_for_limit_MPa` and `zone_settled`.
- [ ] **Step 2: Run** `..\.venv\Scripts\python.exe -m pytest tests/test_workbench.py -q`. Expected: the new tests fail.
- [ ] **Step 3: Implement.** Update the tool descriptions in `FUNCTIONS` that mention the peak or the safety factor, so they name `stress_for_limit_MPa`.
- [ ] **Step 4: Run** the full suite. Expected: all pass, including `tests/test_app_stream.py`.
- [ ] **Step 5: Commit** `git commit -m "Workbench: safety factor and limits on the zone stress; guard faces"`.

### Task 4: Agent prompt

**Files:**
- Modify: `src/agent/agent.py` (`SYSTEM`)
- Test: `tests/test_agent_prompt.py`

**Interfaces:**
- Consumes: the field names from Task 3.

The prompt rules (exact meaning; wording is the builder's):
- The safety factor uses `stress_for_limit_MPa`, the peak outside the zones around the fixed and loaded faces.
- When `excluded_peak_MPa` is set, say the excluded peak is not physical and where it is. For a support: "the bolted joint needs its own check". For a load: the value depends on how the load is really applied, so check it separately.
- When `zone_settled` is false, say the zone did not settle and that the stress used is the conservative value.
- Judge mesh convergence on `stress_for_limit_MPa`.
- Replace the older support-singularity rule (2026-11-01, "support singularities ... not handled") wherever it conflicts.

- [ ] **Step 1: Write the failing test** `test_prompt_explains_exclusion_zones`. Assert that `SYSTEM` contains `stress_for_limit_MPa`, `excluded_peak_MPa`, `not physical`, `bolted joint needs its own check`, `how the load is really applied` and `zone_settled`.
- [ ] **Step 2: Run** `..\.venv\Scripts\python.exe -m pytest tests/test_agent_prompt.py -q`. Expected: it fails.
- [ ] **Step 3: Edit `SYSTEM`.**
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Agent prompt: report excluded peaks at supports and loads"`.

### Task 5: ANSYS check and the SimJEB key

**Files:**
- Create: `scripts/smoke_zones.py` (no LLM). It loads each part, sets the load case, solves the baseline, runs check_mesh, and prints the zone fields.
- Modify: `practice/problems/jeb_named.effects.yaml`, `practice/problems/jeb_complex.effects.yaml` (rebuilt by `scripts/build_key.py`, `reviewed: false`)
- Modify: the SimpleSim `CLAUDE.md` is untouched. Record the results in Project2 `CLAUDE.md` Decisions.

Before running: check that no ANSYS, SpaceClaim or optiSLang processes are left over, and that free commit memory is at least 8 GB (the 2026-11-15 lesson: close other programs first).

- [ ] **Step 1: Write `scripts/smoke_zones.py`.** It covers three parts with their practice-set load cases:
  - L-bracket (`lbr_clean_auto` setup, plus the 2.5 mm fillet from its key's fixed edits)
  - saddle (`sad_clean_auto`)
  - SimJEB 148 vertical (`jeb_named`)

  It prints per part: raw peak, `stress_for_limit_MPa`, `excluded_at`, `zone_mm`, `zone_settled`, check_mesh `stress_change`, and the time for the zone step.
- [ ] **Step 2: Run** it with `..\.venv\Scripts\python.exe -m scripts.smoke_zones`. Pass criteria:
  - **L-bracket:** nothing excluded (the peak is at the fillet), `stress_for_limit_MPa == max_stress_MPa`.
  - **Saddle:** `excluded_at == "support"`. check_mesh on the zone stress changes less than the raw-peak +34 % of 2026-11-01.
  - **SimJEB:** `excluded_at == "support"`, `stress_for_limit_MPa` within about 15 % of 1,337 MPa (our deflection was +4 % vs SimJEB).
  - **All parts:** the zone step takes under 10 s.

  If a criterion fails, stop and report to the user before changing anything.
- [ ] **Step 3: Rebuild the SimJEB keys** with `..\.venv\Scripts\python.exe -m scripts.build_key practice/problems/jeb_named.yaml --force` (and `jeb_complex` if its features differ). Then **stop: the user reviews the labels** and sets `reviewed: true`.
- [ ] **Step 4: After the user's review,** re-grade the saved "after" run: `..\.venv\Scripts\python.exe -m scripts.run_practice --regrade practice/results/20261115-after-merged`. Report the change to the user. Re-running the agent on SimJEB costs API money and needs the user's OK.
- [ ] **Step 5: Commit** `scripts/smoke_zones.py` and the reviewed keys. Add a GenAI log row and record the results in Project2 `CLAUDE.md`.
