# PyDiscovery emulator (`src/pydiscovery`)

Ansys hasn't released PyDiscovery yet. This package stands in for it, so the agent can open a part, read its setup, confirm it with the user, solve, optimize and save without Ansys installed. When PyDiscovery ships, only the engine changes.

## Try it

```
python -m src.pydiscovery.demo --yes                          # synthetic Discovery L-bracket, full loop
python -m src.pydiscovery data/raw/<part>.dsco                # print what a part file carries
python -m src.pydiscovery.demo --part data/raw/<part>.scdocx --yes   # SpaceClaim part: gaps filled with defaults
```

Outputs land in `data/processed/pydiscovery_demo/` (gitignored): `result.json` in the contract format, plus the updated part.

## The flow

```python
from src.pydiscovery import App
part = App().open("data/raw/l_bracket_demo.dsco")   # parameters and physics load with the file
print(part.describe())                               # the confirmation card; part.missing lists gaps
part.confirm()                                       # only after the user says the card is right
result = part.optimize(constraints=[("safety_factor", ">=", 1.5), ("max_disp_mm", "<=", 0.5)])
part.save("data/processed/l_bracket_opt.dsco", result.recommended.design)
```

`solve()` and `optimize()` refuse until the setup is confirmed. Changing supports, load or material resets the confirmation; changing dimensions doesn't. Out-of-range values raise an error and are never clamped.

## What each file type gives

| File | Loads automatically | The agent asks for |
| --- | --- | --- |
| Discovery `.dsco` | named parameters, supports, loads, material, saved goals and results | nothing, the user confirms |
| SpaceClaim `.scdocx` / `.scdoc` | the shape and named groups | which bracket it is, supports, load, material |
| STEP | the shape only | everything |
| STL | refused: no faces, units or dimensions | |

## Engines

`App(engine)` picks what computes stress and deflection. The agent, UI and contracts don't change.

| Engine | What it is | Status |
| --- | --- | --- |
| `fitted` (default) | Power law fitted to Role 1's 200 CalculiX runs (`src/models/power_law.py`), about 1% median error | ready; checking its own shortlist is circular, so the result says so |
| `calculix` | Role 1's CadQuery, gmsh and CalculiX pipeline, about 10 to 40 s per solve | needs `src/data` from Role 1's branch |
| `formula` | the contracts' beam formula | parity with the fakes only; far off for this bracket |
| `ansys` | Role 3 wraps `src/sim` (SpaceClaim edits, Mechanical solve) | placeholder |
| `discovery` | the real PyDiscovery | placeholder until Ansys releases it |

An engine is any class with `name` and `solve(family, design_mm, force_n, material) -> {max_stress_mpa, max_disp_mm, mass_kg}`; see `backends.py`.

## Agent tools

`src/pydiscovery/tools.py` has 11 tools (`TOOL_SPECS`) with JSON in and out: open_part, describe_setup, use_family, set_support, set_load, set_material, confirm_setup, set_dimensions, solve_design, optimize_design, save_design. Route calls through `PyDiscoveryTools().call(name, args)`. Errors return `{"ok": false, "message": ...}`.

## Known limits

- The emulator only rebuilds the L-bracket v0 (`families.py`). Other parts are read but not solved.
- Supports and loads in a file are trusted to sit on the bracket's back face and free-end face. The confirmation card says so, and the user confirms.
- Untested: whether Discovery rebuilds the shape when it opens a `.dsco` whose stored parameter value was edited. Check on vLab before relying on it.
- The synthetic demo part copies a real Discovery file's layout but isn't saved by Discovery. Replace it with a real `l_bracket.dsco` once someone builds one on vLab.
