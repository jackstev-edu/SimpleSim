# SimpleSim demo front end

This is a demo shell that replays contract fixtures in a Discovery-style layout; it is not Ansys software and is not connected to Ansys.

## Open it

```
cd src/ui
python -m http.server 8000
```

Then open http://localhost:8000/. Double-clicking `index.html` also works: browsers block `fetch` on `file://` pages, so the page shows a file picker. Select the three JSON files of one case from `fixtures/` (for example `study_spec.json`, `prediction.json` and `result.json`).

- **Fixture** switches between the three cases. `?fixture=infeasible` or `?fixture=out_of_range` in the URL picks the starting case.
- **Run demo** replays the agent messages 600 ms apart and fills the Variations table row by row.
- Click a table row, or focus it and use the arrow keys, to show that design in the viewport.

## Fixtures

`python -m src.ui.make_fixtures` (from the repo root) rewrites `fixtures/`. Do not edit the JSON by hand.

| Case | Files | How it is made |
| --- | --- | --- |
| Normal | `study_spec.json`, `prediction.json`, `result.json` | Copies of `contracts/examples/` |
| Infeasible | `*_infeasible.json` | Example spec with a 5000 N load. No sampled design passes, so `recommended` is null and the shortlist holds the nearest misses |
| Out of range | `*_out_of_range.json` | Example spec with bounds widened 5%. Candidates outside the example ranges get `in_training_range` false |

Both extra cases run `FakePredictor`, `FakeSolver` and `run_pipeline` from `contracts/fakes.py` through two thin wrappers in `make_fixtures.py`.

## What each panel reads

| Panel | Contract fields |
| --- | --- |
| Tree: Design, Physics | `study_spec.json`: `bracket_family`, `material_key`, `load.force_n`, `load.angle_deg` |
| Tree: Parameters | `study_spec.json`: `parameters[].name`, `low_mm`, `high_mm` |
| Tree: Monitors | `study_spec.json`: `constraints[]`, `objective`. The amber entry is `result.binding_constraint` |
| Viewport | `design` of the selected row (five dimensions) and `spec.load`. The fillet tint appears only for `result.recommended` |
| Variations rows | One per `prediction.shortlist[]` candidate, joined to `result.finalists[]` by `design_id`. Finalists missing from the shortlist get a row of their own |
| Variations: Baseline row | `result.baseline` |
| Variations: parameter columns | `design.<name>_mm` |
| Variations: monitor columns | Predicted rows: `mass_kg` and the `value`, `p05`, `p95` of `max_stress_mpa`, `max_disp_mm`, `safety_factor`. Verified rows: the solver numbers from `result.finalists[]` |
| Variations: Feasible | `feasible_p95` for predicted rows, the solver's `feasible` for verified rows |
| Variations: In range | `in_training_range` (n/a when the row has no shortlist entry) |
| Variations: footer | Verified rows count as solved, predicted rows as pending. The label beside "Solved locally" is the finalists' `source` |
| Parameters and Test Cases tabs | `study_spec.json` ranges, `result.baseline.design`, `load`, `material_key` |
| Agent: goal | `result.spec`: `objective`, `constraints`, `load`, `material_key` |
| Agent: steps | `spec.assumptions`, `prediction.model_name`, `trained_on_runs`, `spec.n_verify`, finalists' `source`, `result.explanation`, `result.discovery_values` |
| Agent: amber notes | `result.warnings`, `prediction.warnings`, and notes for a null `recommended` or a false `in_training_range` |
| Agent: chips | Fake when the model name, a source or a warning says fake; otherwise Predicted or Verified. The assumptions line is tagged Spec |

Hovering a table cell shows the file, field and raw contract value behind it.

## Rules the page follows

- No number is calculated in the page. The only arithmetic is unit conversion (mm to m, MPa to Pa) and counting rows. The agent's goal and `result.explanation` are quoted in contract units.
- Fields are checked against the names in `contracts/models.py` before anything renders. A missing or wrong field raises the error banner; nothing is filled in with a default.
- The bracket outline uses the five dimensions plus the fixed 60 mm mounting leg from `contracts/README.md`. The hole position and the fillet tint are illustrative.
- The layout follows Ansys Discovery's arrangement and panel names. Icons, fonts and colours are our own.
