# PyDiscovery emulator (`src/pydiscovery`)

Ansys hasn't released PyDiscovery yet. This package stands in for it, so the agent can open a part, read its setup, confirm it with the user, solve, optimize and save without Ansys installed. When PyDiscovery ships, only the engine changes.

## Try it

```
python -m src.pydiscovery.demo --yes                          # synthetic Discovery L-bracket, full loop
python -m src.pydiscovery.demo --family l_bracket_gusset_v1 --yes   # the gusseted bracket: eight dimensions, v1 models
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

## Families

| Family (`families.py`) | Dimensions | Where it comes from |
| --- | --- | --- |
| `l_bracket_v0`, "L-bracket v0" | thickness, flange length, arm width, hole diameter, fillet radius | `docs/bracket_family_v0.md` |
| `l_bracket_gusset_v1`, "Gusseted L-bracket v1" | the five above plus vertical height, gusset thickness, gusset length | `docs/bracket_family_v1_l_bracket_gusset_v1.md` |

A part file is matched to the largest family whose every dimension it names, so a gusset file is never mistaken for a v0 one. The v1 family carries Role 1's named geometry rules (the gusset must clear the hole, the top of the leg, the side faces and the inside fillet): `set_dimensions` refuses a design that breaks one, the confirmation card lists a broken rule as a problem, and the predictors drop such designs from the shortlist before anything is solved. The contracts' `FAMILY_PARAMS` is the source of truth for each family's dimension names; a `StudySpec` and every design in a `Result` use exactly its family's set.

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
| `fitted` (default) | Power laws fitted to Role 1's CalculiX runs (`src/models/power_law.py`): v0 about 1% median error, v1 about 3.5% | ready for both families; checking its own shortlist is circular, so the result says so |
| `calculix` | Role 1's CadQuery, gmsh and CalculiX pipeline through its family registry: 10 to 40 s per v0 solve, 1 to 3 min per v1 solve | needs `src/data/requirements.txt` |
| `formula` | the contracts' beam formula | v0 only (a clear error otherwise); parity with the fakes, far off for this bracket |
| `ansys` | Role 3's SpaceClaim edits and Mechanical solve on `l_bracket.scdocx` (`src/sim/ansys_engine.py`); stress is the peak outside the support and load zones; `backend.last_details` keeps the raw peak and zone data | v0 only (a clear error for v1); needs a local licensed ANSYS 2026 R1 and `src/sim/requirements.txt` |
| `discovery` | the real PyDiscovery | placeholder until Ansys releases it |

An engine is any class with `name` and `solve(family, design_mm, force_n, material) -> {max_stress_mpa, max_disp_mm, mass_kg}`; see `backends.py`.

## Predictors

`App(predictor="power_law")` (default) or `App(predictor="gp")` picks the model that shortlists designs before the engine checks them; each part gets the model of its own family, with the part's Poisson's ratio as an input. The power laws are built in. The GP needs scikit-learn and trains from the family's `model_dataset.csv` under `data/processed` (1 s for v0; 4 to 6 minutes for v1 the first time, then cached as a pickle under `data/processed/gp_cache/`, gitignored). `App(runs={"l_bracket_v0": path})` points a family at other training data. See `docs/models.md` for the accuracy of both models on both families; for v1 the GP is the recommended predictor.

## In the GUI

The normal launcher (`python launch_gui.py`, or `Launch SimpleSim GUI.bat`) now serves the emulator next to the page. When the page is served that way, the Agent window gains a fourth tab, **PyDiscovery**, which walks the same flow as the agent and calls the same 11 tools through `/api/pydiscovery/tool/<name>`:

1. **Part**: click "Use demo L-bracket", or "Open part file..." to upload a `.dsco`, `.scdocx`, `.scdoc` or STEP file (uploads land in `data/raw/uploads/`, gitignored).
2. **Setup**: the confirmation card. Anything the file lacks gets a control under the card: which bracket it is (v0 or the gusseted v1), "Fix the back face", a load in N, a material. Problems show in red. The Variations table, Graphs and Recommendations take their dimension columns from the loaded spec's family, so a v1 run shows eight.
3. **Confirm**: "The setup is right" is enabled only when nothing is missing and there are no problems.
4. **Goal**: minimize mass with a minimum safety factor (default 1.5) and a maximum deflection in mm (default 0.5), then "Optimize". The run lands in the Variations table, the Agent chat, the 3D view, Graphs and Recommendations, and the Fixture menu gains "PyDiscovery (live)" so you can come back to it after looking at a fixture.
5. **Save**: "Save updated part" writes `data/processed/pydiscovery_gui/<part>_optimized.dsco` (a `.json` of values for parts the emulator can't rewrite) and shows the path.

The pane shows the engine and predictor in use. `python launch_gui.py --engine fitted --predictor gp` picks them; the default `--predictor auto` takes the GP when scikit-learn is installed and `data/processed/formal_v0/runs.csv` exists, otherwise the power law. Errors from the tools show inline in the pane, never as browser dialogs.

If the page is opened from disk or served by anything else, `GET /api/pydiscovery` fails, the tab stays hidden and the page behaves exactly as before. `src/pydiscovery/server.py` holds the routes (`PyDiscoveryAPI.handle` is a pure dispatcher, tested without a network in `tests/test_pydiscovery_server.py`); the server uses the standard library only and binds to 127.0.0.1.

`--gui` on the demo (or `python -m src.pydiscovery.gui`) still works for a one-off run: it copies `src/ui` to a temporary folder, adds the run as the fixture `pydiscovery`, and opens the browser on it. Known gap for Role 4: the Recommendations tab's "Mechanics behind the optimum" still describes the fake's beam formula.

## Agent tools

`src/pydiscovery/tools.py` has 11 tools (`TOOL_SPECS`) with JSON in and out: open_part, describe_setup, use_family, set_support, set_load, set_material, confirm_setup, set_dimensions, solve_design, optimize_design, save_design. Route calls through `PyDiscoveryTools().call(name, args)`. Errors return `{"ok": false, "message": ...}`.

## Known limits

- The emulator rebuilds the L-bracket v0 and the gusseted v1 (`families.py`). Other parts are read but not solved.
- The GUI's "Mechanics behind the optimum" equations are the v0 beam model; a v1 result says so in its warnings. The 3D view draws the gusset as a flat triangle on the front face.
- Supports and loads in a file are trusted to sit on the bracket's back face and free-end face. The confirmation card says so, and the user confirms.
- Untested: whether Discovery rebuilds the shape when it opens a `.dsco` whose stored parameter value was edited. Check on vLab before relying on it.
- The synthetic demo part copies a real Discovery file's layout but isn't saved by Discovery. Replace it with a real `l_bracket.dsco` once someone builds one on vLab.
