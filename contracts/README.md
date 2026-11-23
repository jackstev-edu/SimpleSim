# Hand-off contracts

Four files connect the four roles. Each has a Pydantic model in `models.py`, a passing example in `examples/`, and a fake in `fakes.py` so you can build before the real part exists.

| File | From | To | Model | Day-one stand-in |
| --- | --- | --- | --- | --- |
| `study_spec.json` | Agent (Yunus) | Model (Jack) | `StudySpec` | `examples/study_spec.json` |
| `runs.csv` | Data (Can) | Model (Jack) | `Run`, header = `RUN_COLUMNS` | `examples/runs.csv`, formula-labelled |
| `prediction.json` | Model (Jack) | Agent (Yunus) | `Prediction` | `FakePredictor` (beam formula, fixed 10% band) |
| `result.json` | Agent (Yunus) | UI (Arin) | `Result` | `examples/result.json` from `run_pipeline` |

## Rules

1. Import from `contracts`, never copy these shapes into your own folder.
2. Units live in the field names (`_mm`, `_n`, `_mpa`, `_kg`). No separate unit columns.
3. Every file carries `schema_version`. Bump it when a field changes meaning.
4. A change to any model is a contract change: the PR needs approval from both sides of that hand-off.
5. `tests/test_contracts.py` runs on every push. If it fails, you changed a hand-off.
6. Explanations and UI text quote solver numbers only. The fakes mark themselves as fake in `warnings`.

## Bracket family v0

Five dimensions of the MVP L-bracket in mm: `thickness_mm`, `flange_length_mm`, `arm_width_mm`, `hole_diameter_mm`, `fillet_radius_mm`. Mounting leg height is fixed at 60 mm. Can and Jack confirm the 3D family this week; renaming a dimension is a contract change.

Training rows use one load, one material and one mesh rule, recorded in each row. Stress scales with load and displacement with load over stiffness, so the model handles other loads and materials by scaling.

## Bracket family v1 (gusseted)

`l_bracket_gusset_v1` adds three dimensions to the five: `vertical_height_mm` (the leg height v0 fixes at 60 mm), `gusset_thickness_mm` and `gusset_length_mm`. `FAMILY_PARAMS` in `models.py` lists each family's set; `StudySpec.bracket_family` picks it, a spec's parameters must be exactly that set, and every design in a `Result` uses those keys. `ParamName` covers every family's names. `examples/l_bracket_gusset_v1/` holds a formula-labelled v1 example set from the fakes. The v1 `runs.csv` from Role 1 carries the three extra columns after the `Run` columns; `Run` itself is unchanged.

## Bracket templates T01 to T10

Role 1's ten templates (`docs/role1_multifamily_handoff.md`, keys `t01_double_hole_v1` to `t10_offset_planform_v1`) share the v0 frame with a variable `vertical_height_mm`, keep `hole_diameter_mm` only where the template has a plain hole (T01 and T05 to T10), and add their own cut or rib dimensions: `slot_width_mm` and `slot_straight_length_mm` (T02, T03), `window_length_mm`, `window_width_mm` and `window_radius_mm` (T04), `notch_radius_mm` (T05), `tip_trim_mm` (T06), `step_trim_mm` (T07), `gusset_thickness_mm` and `gusset_length_mm` (T08, T09), `rib_hole_diameter_mm` (T09) and `offset_trim_mm` (T10). The entries in `FAMILY_PARAMS` and the ten new names in `ParamName` are additive: nothing about v0, v1, `Run` or the examples changed, and the schema version stays 0.2.0. Their datasets live in `data/processed/formal_multifamily_v1/`, not in a per-family `runs.csv`.

## How to use the fakes

```python
from contracts import FakePredictor, FakeSolver, run_pipeline
from contracts.make_examples import BASELINE, example_spec

result = run_pipeline(example_spec(), BASELINE)        # spec -> predict -> verify -> Result
prediction = FakePredictor().predict(example_spec())   # same interface the GP will have
```

Swap order at team meetings: real `runs.csv`, then the trained predictor, then the agent on real tools, then the UI on the live agent. Each swap replaces one fake and the tests stay the same.

After changing a model, regenerate the examples: `python -m contracts.make_examples`.
