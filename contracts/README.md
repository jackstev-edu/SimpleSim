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

## How to use the fakes

```python
from contracts import FakePredictor, FakeSolver, run_pipeline
from contracts.make_examples import BASELINE, example_spec

result = run_pipeline(example_spec(), BASELINE)        # spec -> predict -> verify -> Result
prediction = FakePredictor().predict(example_spec())   # same interface the GP will have
```

Swap order at team meetings: real `runs.csv`, then the trained predictor, then the agent on real tools, then the UI on the live agent. Each swap replaces one fake and the tests stay the same.

After changing a model, regenerate the examples: `python -m contracts.make_examples`.
