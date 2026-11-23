# Fast-path benchmark: does the assistant pick the right design? (design)

Date: 2026-11-22. Owner: Yunus (Role 3). Status: draft, awaiting approval.

## Intent and why
The team roles doc grades Role 3 on **picking the right design**, with 10 to 20 practice problems that have
brute-force true optima. The existing practice set (`practice/problems/`, 117/121 on 2026-11-21) grades only the
ANSYS agent's setup and feature choices; nothing measures whether the whole assistant (front layer + Jack's
PyDiscovery fast path) turns a student's plain-language request into the right study and ends on the right design.
This benchmark does that, gives numbers for the report, and tests the assistant before the instructor sees it.

## Goals
- 10 fast-path problems, each a plain-language request a student might type, with hidden facts known only to a
  simulated student.
- Per problem, an answer key: the study the assistant should submit, the **model optimum** (brute force on the
  fitted model, the truth used for scoring) and, where the load case matches the simulation tables, the
  **simulation optimum** (reported only).
- A grader that scores the study, the asking behaviour, feasibility of the pick, and the gap to the model optimum.
- A run script with the same spend cap and ledger as the practice set.

## Non-goals
- No change to the existing practice set, its grader or its 117/121 score.
- No ANSYS or CalculiX in the benchmark: finalists are checked by the `fitted` engine.
- Not a test of Jack's model accuracy (that is the simulation gap, reported only) or a replacement for his
  benchmarks in `src/models/benchmark.py`.
- No edits to `contracts/`, `src/pydiscovery/` or `src/models/` (teammates' code).

## Anti-goals
- A score that blames the assistant for the random search's shortfall (see "Search noise").
- Keys nobody checked: the user reviews every key before it is used for grading.
- Spending more than the cap: $0.20 hard cap per run, expected about $0.01 to $0.03.

## Constraints
- Fast path knows two families and two materials only: `l_bracket_v0` (thickness 4 to 10, flange length 75 to
  120, arm width 32 to 60, hole diameter 6 to 12, fillet radius 2 to 8 mm) and `l_bracket_gusset_v1` (same five,
  fillet 2.5 to 8, plus vertical height 50 to 90, gusset thickness 3 to 10, gusset length 12 to 35 mm);
  `structural_steel` (E 200 GPa, yield 250 MPa, 7850 kg/m³) and `aluminum_6061` (E 69 GPa, yield 276 MPa,
  2700 kg/m³) (`contracts/fakes.py` `MATERIALS`, `src/pydiscovery/families.py`).
- The load is on the free-end face of the flange; the contract `Load` stores force and angle (-90° = down).
- Both families accept forces of 100 to 1000 N only (`force_range_n`); the front layer hands any other force off
  to the ANSYS agent (`outside_family` in `src/assistant/front.py`).
- The family fixes the support (back face at X = 0) and the loaded face (free end of the flange); the assistant
  must still get the student's confirmation of both (`set_support` with the exact face text, then
  `confirm_setup` only after the student says yes).
- Simulation tables: `data/processed/formal_v0/runs.csv` (200 rows) and
  `data/processed/formal_v1_l_bracket_gusset_v1/runs.csv` (2,048 rows), all 500 N at -90°, material
  `role1_isotropic_reference` (E 200 GPa, ν sampled 0.25 to 0.35, density 7850; treat as steel), CalculiX.
- The project rule "no assumed calculation inputs" (2026-11-13): the assistant must ask for every input the request
  leaves out, and the student must answer before the value is used. SF 1.5 is the value it may offer, not assume.
- LLM: `gpt-6-luna` for assistant and simulated student (`src/llm.py`), counted client, ledger `practice/spend.csv`.
- Repo conventions: units in field names, no em dashes, GenAI log row, tests skip cleanly without optional packages.

## Approaches (picked: B)
- A. Extend the existing practice set and grader with a second problem kind. One runner and report, but the
  grader is built around face ids and ANSYS features, and mixing kinds risks the reported 117/121 baseline.
- **B. Separate benchmark reusing the practice-set parts.** Own problems folder, small grader and run script;
  reuses `SimUser`-style simulated student, the counted client, ledger and report helpers. Picked by the user.
- C. No-LLM test feeding hand-written specs to the search. Dropped: it tests Jack's search, not whether the
  assistant understood the student.

## Decided by the user
- Model optimum uses the fast path's own rule: limits judged at the pessimistic end of the band (option a,
  2026-11-22); the median optimum is recorded for information.
- Truth for scoring is the **model optimum**; the **simulation optimum** is reported where the load matches
  (option c, 2026-11-22).
- Approach B. About 10 problems over v0 and v1. Provisional pass line: pick within **5 %** of the model optimum,
  checked after the first run.

## Design

### Problem set (10)
Each problem is `practice/fast/<id>.yaml`. Parts are synthetic Discovery files made with
`src.pydiscovery.demo_part.write_demo_part(path, family=..., physics=False)` so the part carries dimensions but no
supports, load or material, and the assistant must establish them in conversation. The mix (ids are proposals):

| id | family | what the request tests |
| --- | --- | --- |
| `v0_clean_mass` | v0 | everything stated (500 N down, steel, SF 1.5, lighter) |
| `v0_missing_sf` | v0 | SF left out: must ask, offer 1.5, wait |
| `v0_missing_force` | v0 | force left out: must ask |
| `v0_alu_vague` | v0 | "aluminium": must confirm aluminum 6061 (the only aluminium it knows) |
| `v0_kn_units` | v0 | 0.8 kN: converted to 800 N (inside the fast path's 100 to 1000 N force range) |
| `v0_stiff` | v0 | minimise deflection (`max_disp_mm`) with a mass cap |
| `v1_clean_mass` | v1 | everything stated, 500 N steel (simulation optimum available) |
| `v1_named_dims` | v1 | student names the dimensions allowed to change: `vary` must be exactly those |
| `v1_sf2_lbf` | v1 | 150 lbf and SF 2.0: converted to 667.2 N, SF 2.0 used |
| `fast_refuse_titanium` | v0 | titanium: outside the fast path, must hand off to the ANSYS agent (graded on the hand-off only) |

### Problem file
```yaml
id: v0_missing_sf
family: l_bracket_v0          # part made by write_demo_part with this family
request: "..."                # first message from the student
hidden_facts: "..."           # what the simulated student knows
key:
  study:                      # what the assistant should submit
    force_n: 500.0
    angle_deg: -90.0
    material_key: structural_steel
    safety_factor: 1.5
    objective: {metric: mass_kg, direction: minimize}
    limits: []                # extra limits besides the SF, e.g. {metric: mass_kg, op: "<=", value: 0.3}
    vary: [all]               # or the exact contract names
  must_ask: [support, load_face, safety_factor]   # inputs the request leaves out; support and load_face
                              # are in must_ask whenever the request does not state them
  expect_handoff: false
  model_optimum: {...}        # written by the key builder (below)
  sim_optimum: {...} | null   # written by the key builder; null when force, angle or material differ
```

### Key builder (`src/eval/fast_keys.py`, `scripts/build_fast_keys.py`)
- **Model optimum:** with the same predictor the fast path uses (`PowerLawPredictor` for that material and family,
  built as in `Part._predictor_for`), find the design that minimises (or maximises) the objective subject to
  SF ≥ the key's SF and the extra limits **judged at the pessimistic end of the model's band**, the same rule
  `build_prediction` in `src/models/power_law.py` uses for the shortlist (user decision 2026-11-22, option a), over the family ranges with the non-varied dimensions at the family
  baseline. Method left to the builder (dense Latin hypercube plus local refinement with `scipy.optimize`, both
  are acceptable); it must be deterministic (fixed seed) and record the method, objective value, SF and the
  design. Also record, for information, the optimum with the limits judged at the model's median.
- **Simulation optimum:** only when force 500 N, angle -90°, steel: the best row of the family's `runs.csv` that
  satisfies SF = 250 / `max_stress_mpa` ≥ the key's SF and the extra limits, honouring `vary` (rows whose fixed
  dimensions differ from the baseline by more than the table's sampling allows are excluded; if none remain,
  `sim_optimum: null` with a reason). Recorded with its `design_id`.
- Keys are written into the YAML, then reviewed by the user; the grader refuses a problem whose key is missing.

### Runner (`scripts/run_fast_practice.py`)
Per problem: write the demo part, start `AssistantSession(client, out_dir, backend="fitted")`, upload the part,
send the request, and alternate assistant turns with the simulated student until a `Result` exists, a hand-off
happens, or a turn cap (12 student replies) is hit. Record every event, tool call and the final `Result` to
`practice/results/fast-<YYYYMMDD-HHMM>/<id>.json`. Same CLI shape as `run_practice.py`: `--only`, `--label`,
`--budget` (default 0.20), `--regrade <dir>`. The simulated student reuses the practice set's rules (answer only
from facts; approve proposals with a fixed reply) through a small adapter, since `SimUser` expects a practice
`Problem`. Missing values follow the practice-set convention: the hidden facts say the student has not thought
about it (e.g. the SF) and accepts the assistant's suggestion; the student never volunteers a value that is not
in the facts. An offered SF of 1.5 that the student accepts passes `asked` and `no_silent_default`.
For the hand-off problem the runner injects stub `make_workbench` / `make_agent` objects (no ANSYS); only the
`path: slow` event and the absence of a fast-path `optimize_design` call are graded.

### Grader (`src/eval/fast_grader.py`)
Checks per problem, each pass / fail / n/a with a detail string:
- `force`, `direction`, `material`, `safety_factor`, `objective`, `limits`, `vary`: the submitted `StudySpec`
  (from the recorded `optimize_design` call or the `Result`'s spec) matches the key (force within 0.5 %).
- `asked`: every `must_ask` item was asked and answered by the student before the value was first used (same rule
  as the practice grader). For support and load face, a setup card shown to the student and confirmed by them
  before `confirm_setup` counts as asked.
- `confirmed`: `confirm_setup` was called only after a student reply that agrees to the setup.
- `no_silent_default`: the spec's SF equals the key's SF (catches `Part.optimize`'s SF ≥ 2.0 default).
- `feasible`: the recommended design meets the key's SF and limits **under the model's pessimistic band**,
  recomputed by the grader with the same rule as the key.
- `pick_gap`: relative objective gap to the model optimum; pass when within 5 % (provisional), detail gives the gap.
- `sim_gap` (report only, never pass/fail): gap of the pick's model objective to the simulation optimum.
- `handoff`: for `expect_handoff` problems, the session handed off and no fast-path optimize ran; other checks n/a.
The report (`summary.md`) lists the checks per problem and the totals, and a separate "pick" table with both gaps.

### Search noise
`Part.optimize` samples 2,000 random designs and verifies the model's top 5. In 8 dimensions this can miss the
model optimum by a few percent even with a perfect study. The grader therefore reports `pick_gap` separately from
the study checks, so a search shortfall shows as a `pick_gap` fail with correct study checks, and the summary says
so. The first run calibrates the 5 % line; changing it is the user's decision.

## Left to the builder
- Brute-force method and sample size (deterministic, recorded).
- Exact request wording and hidden facts for the 10 problems (user reviews them with the keys).
- How the simulated student adapter shares code with `SimUser`.
- Report layout beyond the two tables named above.
