# Template-family problems for the fast-path benchmark: design

Date: 2026-11-27. Status: approved by the user 2026-11-27.
Builds on `2026-11-22-fast-path-benchmark-design.md` (the existing ten problems in `practice/fast/`).

## Intent

Jack's PR #9 put Role 1's ten template families (T01 to T10, `src/models/template_families.py`) on the fast path.
The benchmark covers only the plain L-bracket and the gusset. The user wants to know **that the assistant picks the
right design on every new shape**. Each template gets one problem; a run reports one line per shape.

## Goals

- One problem per template family, ten in all, next to the existing ten.
- The **same plain request on every shape** (user's choice), so a failure points at the shape, not the wording.
- Each new problem run **three times**; a shape passes a check only when all three runs pass it (user's choice).
- A new grader check, **`family`**: the study's `bracket_family` must be the problem's family (user's choice, after
  the finding below).
- A CalculiX reference (`sim_optimum`, info only) for every template, from Can's multi-family table.

## Non-goals

- Fixing `match_family` (Jack's code; shared, so a team check-in first). The benchmark only shows the bug.
- Varied requests, other forces, materials or goals on the templates (the existing ten already test those).
- The dev `app.py` error on template parts (`AssistantSession` defaults to the ANSYS engine, which builds only v0 and
  the gusset). Separate item.
- Changing the scoring key (2,000-design search, seed 0; user decision 2026-11-23) or the 5 % pass line.

## Anti-goals

- A wrong-family pick that still shows as a pass.
- Keys graded before the user has reviewed them (README rule).
- Breaking the existing ten problems or their saved results.

## Finding that shaped this design

`match_family` (`src/pydiscovery/families.py`) picks a family from the dimension names alone. A demo part of each
family read back: **T03 twin-slot is read as T02 single-slot** (same two slot dimensions) and **T08 twin-gusset as
the gusset v1 family** (same eight names); the other ten are correct. On T03 and T08 the assistant would search the
wrong model. The grader never compared families, and it scores the pick on the problem's own model, so a wrong-family
pick could pass. With the `family` check, T03 and T08 fail until the matching is fixed. Jack is told.

## Approaches

1. **Ten problem files like the existing ones (picked).** `practice/fast/t01_clean_mass.yaml` to
   `t10_clean_mass.yaml`, keys from `scripts.build_fast_keys`, `sim_optimum` extended to Can's table. Each key is a
   file the user reads and approves; runner, grader and summary keep their shape.
2. One template expanded in code into ten problems. Less repetition, but breaks the rule that each problem file stands
   alone with its reviewed key, which is what lets the team grade its own assistant with the same files. Dropped.

Picked: 1, by the user, with three repeats.

## Design

### Problem files

- Ids `t01_clean_mass` ... `t10_clean_mass`; `family` = the template key (e.g. `t03_twin_slot_v1`).
- `request`: the wording of `v1_clean_mass` with the shape's name, e.g. "This is a twin-slot steel bracket, bolted to
  a wall by the back face of its upright leg. 500 N acts straight down on the free end of the flange. Safety factor
  1.5. Make it as light as possible." The shape name is the template title without its code, lower case
  ("double-hole", "single-slot", "twin-slot", "rounded-window", "side-notch", "tapered", "stepped", "twin-gusset",
  "perforated-gusset", "offset-planform").
- `hidden_facts`: as in `v1_clean_mass` (structural steel; all dimensions may change; accept the file's dimensions).
- `key.study`: 500 N, -90 deg, `structural_steel`, SF 1.5, minimise `mass_kg`, no limits, vary all.
  `must_ask: []`, `states_setup: true`, `expect_handoff: false`.
- Keys from `python -m scripts.build_fast_keys --only t01_clean_mass ...`: `model_optimum` (n = 2,000, seed 0),
  `dense_optimum` (400,000, info), `sim_optimum` (info). The user reviews them before any graded run.

### CalculiX reference for templates (`src/eval/fast_keys.py`)

- Source: `data/processed/formal_multifamily_v1/model_dataset_1000.csv` joined to `design_manifest.csv` on
  `design_id`; dimensions from `params_json`; stress, displacement, mass from `reference_max_stress_mpa`,
  `reference_max_disp_mm`, `reference_mass_kg`. All rows are 500 N, -Y, E 200,000 MPa, yield 250 MPa, 7,850 kg/m3,
  Poisson's ratio 0.25 to 0.35 per row. 80 rows per template; 22 to 43 meet SF 1.5.
- Only rows of the problem's family; `l_bracket_v0` and the gusset keep their own tables (`RUNS`).
- Same selection rule as today (SF from yield 250 / stress, limits, non-varied dimensions at baseline).

### `family` check (`src/eval/fast_grader.py`)

- New entry in `STUDY_CHECKS`. Pass when `result.spec.bracket_family == problem.family`; detail "got X, want Y".
- `n/a` on hand-off problems; `fail` when there is no result, like the other study checks.
- Old runs get the column on regrade; every existing problem should pass it (checked by regrading all saved runs).

### Repeats (`scripts/run_fast_practice.py`, `src/eval/fast_runner.py`)

- `--repeat N` (default 1, so today's runs are unchanged). With N > 1, the run folder holds `r1/` ... `rN/`, each a
  normal run folder (logs, graded files, own `summary.md`), plus a top-level `summary.md` that combines them.
- Combined summary: per problem and check, `pass` only if every repeat passes; otherwise `fail (k/N)` with k the
  passing repeats; `info` columns show the first repeat's value; `n/a` stays `n/a`. The total counts combined checks.
- `--regrade` on a folder with `r*/` subfolders regrades each and rebuilds the combined summary.
- One budget cap and one ledger row for the whole run (all repeats). Expected cost about $0.06 for ten problems
  three times; the default $0.20 cap holds.

### Docs

- `practice/fast/README.md`: twenty problems, the `family` check, `--repeat`, and the template keys' review status.
- `docs/genai-log.md` row.

## Builder's notes and calls made by Claude

- Rows from every split of Can's table (train, validation, held-out) are used for the info-only reference; it never
  feeds a model. Claude's call.
- A repeat that ends on an API error counts as a failed repeat for the checks it could not reach (the runner already
  logs the error and continues). Claude's call.
- The existing ten problems are not re-run in this work; they can be run with `--repeat` later. Claude's call.
- No test that fails on purpose is added for `match_family` (it would break CI on Jack's code); the benchmark shows
  the bug. Claude's call.
- Tests first for: the `family` check, the combined repeat summary, regrade of a repeat folder, and `sim_optimum` on a
  template (a small fixture table).

## Message to Jack (user sends)

The T03/T08 matching finding, with the table above, alongside the search-density note.
