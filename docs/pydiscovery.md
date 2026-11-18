# PyDiscovery emulator (`src/pydiscovery`)

Ansys hasn't released PyDiscovery yet. This package stands in for it, so the agent can open a part, read its setup, confirm it with the user, solve, optimize and save without Ansys installed. When PyDiscovery ships, only the engine changes.

## Try it

```
python -m src.pydiscovery.demo --yes                          # synthetic Discovery L-bracket, full loop
python -m src.pydiscovery.demo --predictor gp --yes --gui     # GP shortlist, then the result in the team GUI
python -m src.pydiscovery.gui                                 # show the last run in the GUI again
python launch_gui.py                                          # the team GUI with the PyDiscovery tab (see "In the GUI")
python -m src.pydiscovery data/raw/<part>.dsco                # print what a part file carries
python -m src.pydiscovery.demo --part data/raw/<part>.scdocx --yes   # SpaceClaim part: gaps filled with defaults
```

Run these from the repo root. Outputs land in `data/processed/pydiscovery_demo/` (gitignored): `study_spec.json`, `prediction.json` and `result.json` in the contract format, plus the updated part.

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
| `ansys` | Role 3's SpaceClaim edits and Mechanical solve on `l_bracket.scdocx` (`src/sim/ansys_engine.py`); stress is the peak outside the support and load zones; `backend.last_details` keeps the raw peak and zone data | ready; needs a local licensed ANSYS 2026 R1 |
| `discovery` | the real PyDiscovery | placeholder until Ansys releases it |

An engine is any class with `name` and `solve(family, design_mm, force_n, material) -> {max_stress_mpa, max_disp_mm, mass_kg}`; see `backends.py`.

## Predictors

`App(predictor="power_law")` (default) or `App(predictor="gp")` picks the model that shortlists designs before the engine checks them. The GP needs scikit-learn and Role 1's `runs.csv`; see `docs/models.md` for both models and their accuracy.

## In the GUI

The normal launcher (`python launch_gui.py`, or `Launch SimpleSim GUI.bat`) now serves the emulator next to the page. When the page is served that way, the Agent window gains a fourth tab, **PyDiscovery**, which walks the same flow as the agent and calls the same 11 tools through `/api/pydiscovery/tool/<name>`:

1. **Part**: click "Use demo L-bracket", or "Open part file..." to upload a `.dsco`, `.scdocx`, `.scdoc` or STEP file (uploads land in `data/raw/uploads/`, gitignored).
2. **Setup**: the confirmation card. Anything the file lacks gets a control under the card: which bracket it is, "Fix the back face", a load in N, a material. Problems show in red.
3. **Confirm**: "The setup is right" is enabled only when nothing is missing and there are no problems.
4. **Goal**: minimize mass with a minimum safety factor (default 1.5) and a maximum deflection in mm (default 0.5), then "Optimize". The run lands in the Variations table, the Agent chat, the 3D view, Graphs and Recommendations, and the Fixture menu gains "PyDiscovery (live)" so you can come back to it after looking at a fixture.
5. **Save**: "Save updated part" writes `data/processed/pydiscovery_gui/<part>_optimized.dsco` (a `.json` of values for parts the emulator can't rewrite) and shows the path.

The pane shows the engine and predictor in use. `python launch_gui.py --engine fitted --predictor gp` picks them; the default `--predictor auto` takes the GP when scikit-learn is installed and `data/processed/formal_v0/runs.csv` exists, otherwise the power law. Errors from the tools show inline in the pane, never as browser dialogs.

If the page is opened from disk or served by anything else, `GET /api/pydiscovery` fails, the tab stays hidden and the page behaves exactly as before. `src/pydiscovery/server.py` holds the routes (`PyDiscoveryAPI.handle` is a pure dispatcher, tested without a network in `tests/test_pydiscovery_server.py`); the server uses the standard library only and binds to 127.0.0.1.

`--gui` on the demo (or `python -m src.pydiscovery.gui`) still works for a one-off run: it copies `src/ui` to a temporary folder, adds the run as the fixture `pydiscovery`, and opens the browser on it. Known gap for Role 4: the Recommendations tab's "Mechanics behind the optimum" still describes the fake's beam formula.

## Agent tools

`src/pydiscovery/tools.py` has 11 tools (`TOOL_SPECS`) with JSON in and out: open_part, describe_setup, use_family, set_support, set_load, set_material, confirm_setup, set_dimensions, solve_design, optimize_design, save_design. Route calls through `PyDiscoveryTools().call(name, args)`. Errors return `{"ok": false, "message": ...}`.

## Known limits

- The emulator only rebuilds the L-bracket v0 (`families.py`). Other parts are read but not solved.
- Supports and loads in a file are trusted to sit on the bracket's back face and free-end face. The confirmation card says so, and the user confirms.
- Untested: whether Discovery rebuilds the shape when it opens a `.dsco` whose stored parameter value was edited. Check on vLab before relying on it.
- The synthetic demo part copies a real Discovery file's layout but isn't saved by Discovery. Replace it with a real `l_bracket.dsco` once someone builds one on vLab.
