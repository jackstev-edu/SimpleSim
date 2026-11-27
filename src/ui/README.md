# SimpleSim demo front end

The shared GUI displays contract fixtures and live results from the local PyDiscovery emulator.

## Open it

Double-click `Launch SimpleSim GUI.bat` (Windows) or `Launch SimpleSim GUI.command` (macOS) at the top of the repo, or run `python launch_gui.py`. To serve it by hand:

```
cd src/ui
python -m http.server 8000
```

Then open http://localhost:8000/. Double-clicking `index.html` also works: browsers block `fetch` on `file://` pages, so the page shows a file picker. Select the three JSON files of one case from `fixtures/` (for example `study_spec.json`, `prediction.json` and `result.json`).

- **Assistant chat.** When the launcher serves Role 3's assistant (`/api/assistant`, see `docs/assistant-api.md`), a chat bar appears under the **Agent** log: a status line (engine, model, budget left, or what is missing when the assistant can't run), **Upload part**, a message box, **Send**, **Stop** and **Reset**. Upload a part first, then answer the assistant's questions. Its replies stream into the log as messages and collapsible tool steps; when it runs the fast-path optimize, the result lands in Variations, Graphs and Recommendations like a PyDiscovery run. The bar needs the `openai` package and `OPENAI_API_KEY` on the machine running the launcher; the status line says so otherwise.
- **Quick replies.** When the assistant asks a question, buttons under the message box offer the likely answers (a safety factor, "Yes, the setup is right", the material grade, "Use ANSYS"); after a result they offer "Why this design?", "Compare to baseline", "Lighter but less safe" and "Try aluminum". A click sends that text as an ordinary message. Replies render a safe subset of Markdown (bold, italics, inline code, lists) built as DOM nodes.
- **Verdict.** Every optimization result carries a short judgement call (`src/assistant/verdict.py`): worth adopting or not, the trade-off, the binding requirement, how far to trust the numbers, one next step. It is pinned at the top of **Recommendations** and posted as the last agent message in the chat, with a chip saying whether the model wrote it or it is the template from `result.json`.
- **Fixture** switches between the three cases. `?fixture=infeasible` or `?fixture=out_of_range` in the URL picks the starting case.
- **Run demo** replays the agent messages 600 ms apart and fills the Variations table row by row.
- Click a table row, or focus it and use the arrow keys, to show that design in the viewport. Shift-click a row, or press **Compare** in the footer, to open the **Compare** window: two drawings, dimensions and outputs in matching rows, a difference column with the percent change, pass or fail per requirement, and the source of every number. The pair defaults to the baseline against the recommended design.
- **Parameters** lists the dimensions and, under them, the four outputs with the baseline and selected values. Each output row has a slider for its limit, starting from the spec's constraints. Dragging one adds a **Your limits** column to Variations (the contract's Feasible column stays as reported) and enables **Re-optimize with these limits**, which runs `optimize_design` in the emulator when a part is confirmed there, or asks the assistant to search again on the fast path. The stress row says von Mises only when every source is CalculiX, ANSYS or a model fitted on their runs.
- The Variations and Agent windows float inside the viewport as in Discovery. Drag them by their tab bar; the two buttons at the right of the bar maximise and close. The Parameters, Variations and Tables ribbon buttons reopen the Variations window, and the handle on the left edge shows or hides the Agent window.
- The Agent window opens to **PyDiscovery** when the local API is available. Its tabs run left to right as **PyDiscovery**, **Recommendations**, **Graphs**, and **Agent**. The static demo and explicit `?fixture=` links open to **Agent**. **Agent** is the chat log. **Graphs** plots every variation against the requirements; hover a point for its values, click it to select that row. **Recommendations** ranks feasible choices and shows what dimensions to change after the run finishes. Maximise the window (button at the right of its tab bar, or double-click the bar) to see the charts side by side.
- Structural opens its menu (Support, Force and so on). Everything else on the ribbon, the toolbars and the corner wheels is visual only.

## Ranked recommendations

Open **Recommendations** after optimization. The first card shows what to change, why it ranks first for the study goal, and how its mass, factor of safety, and displacement compare with the requirements. Expand the other cards or **Inspect dimensions** to see exact before and after values in millimeters.

**Other feasible options** lists up to two more designs. Evaluated finalists come first. A **Predicted feasible** option meets the requirements at the conservative end of its prediction ranges and still needs evaluation. **Study explanation and model notes** contains the supplied explanation, model name, and warnings. The v0 formula fixtures also have a collapsible beam-equation section.

## Geometry templates

Run `python launch_gui.py --engine fitted --predictor power_law` from the repository root and open **PyDiscovery** in the Agent window.

1. Open a STEP or a part file without named parameters. Choose a geometry in **Which bracket?** and click **Use this bracket**. The picker includes both L-brackets and templates T01 through T10.
2. Review the family’s dimensions and bounds in millimeters. **Apply dimensions** checks values against the backend’s geometry rules. Fill missing support, load, and material, then confirm the setup and click **Optimize**.
3. See the fitted results and the family-specific dimensions in **Variations**, **Graphs**, and **Recommendations**. The viewport reports when a template geometry preview is unavailable.

Named slot files can match T02 and T03 by the same dimensions. The setup asks you which shape the file represents. For T03, use a STEP export or a file without named parameters so you can explicitly select Twin-slot bracket T03. Use the same selection workflow for twin-gusset T08 files that appear as single-gusset v1.

The shared GUI runs these templates using the fitted power-law model. CalculiX and ANSYS FEA through the shared GUI are available only for the two original L-bracket families, subject to local solver setup.

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
| Tree overlay: Design, Physics | `study_spec.json`: `bracket_family`, `material_key`, `load.force_n`, `load.angle_deg` |
| Tree overlay: Parameters | `study_spec.json`: `parameters[].name`, `low_mm`, `high_mm` |
| Tree overlay: Monitors | `study_spec.json`: `constraints[]`, `objective`. The amber entry is `result.binding_constraint` |
| Viewport | `design` of the selected row (five dimensions) and `spec.load`. The fillet tint appears only for `result.recommended` |
| Readout, top right of the scene | Name, `design_id` and the four monitor values of the selected row, reported finalist values when available |
| Stage control, bottom centre | Counts only: `result.warnings` on the left, variation rows on the right. The outline turns green once a finalist result is available |
| Variations rows | One per `prediction.shortlist[]` candidate, joined to `result.finalists[]` by `design_id`. Finalists missing from the shortlist get a row of their own |
| Variations: Baseline row | `result.baseline` |
| Variations: parameter columns | `design.<name>_mm` |
| Variations: monitor columns | Predicted rows: `mass_kg` and the `value`, `p05`, `p95` of `max_stress_mpa`, `max_disp_mm`, `safety_factor`. Evaluated rows: the reported values from `result.finalists[]` |
| Variations: Feasible | `feasible_p95` for predicted rows, the reported `feasible` for evaluated rows |
| Variations: In range | `in_training_range` (n/a when the row has no shortlist entry) |
| Variations: footer | Evaluated finalists count as complete, estimates as pending. The badges identify how the displayed finalists were evaluated |
| Parameters and Test Cases tabs | `study_spec.json` ranges, `result.baseline.design`, `load`, `material_key` |
| Agent: goal | `result.spec`: `objective`, `constraints`, `load`, `material_key` |
| Agent: steps | `spec.assumptions`, `prediction.model_name`, `trained_on_runs`, `spec.n_verify`, finalists' `source`, `result.explanation`, `result.discovery_values` |
| Agent: amber notes | `result.warnings`, `prediction.warnings`, and notes for a null `recommended` or a false `in_training_range` |
| Graphs: scatter charts | One per monitor against the objective, the binding constraint first. Points are the Variations rows: reported values once evaluated, otherwise `value` with a `p05` to `p95` whisker. The dashed line and shaded side come from `spec.constraints` |
| Graphs: What drives mass | `result.sensitivities` |
| Graphs: search range chart | `spec.parameters` bounds with `result.baseline.design` and the recommended design (or the top finalist when `recommended` is null) |
| Recommendations: tables | `result.baseline`, `result.recommended` (or `finalists[0]`), `spec.constraints`, `result.binding_constraint` |
| Recommendations: ranked options | Distinct feasible finalists and eligible shortlist estimates, ordered by the study objective within each evidence group |
| Result badges | Formula demo, Trained prediction, Fitted surrogate check, CalculiX FEA, or a neutral label when provenance is unspecified. Finalist badges use each row’s `source` |
| Parameters: outputs | The selected row's `mass_kg`, `max_stress_mpa`, `max_disp_mm`, `safety_factor` (reported, or the estimate `value`); the sliders start from `spec.constraints` and pass or fail uses the reported value or the conservative end of the band |
| Compare window | The two rows' `design`, the four monitors with their `source` or `model_name` and band, `spec.constraints` and `result.binding_constraint` |
| Verdict | `run.verdict` from the assistant's `done` event, or `verdict` from the emulator's `optimize_design` answer |

Hovering a table cell shows the file, field and raw contract value behind it.

## Rules the page follows

- No number is calculated in the page. The only arithmetic is unit conversion (mm to m, MPa to Pa), counting rows, the percent change between two designs in the Recommendations cards and the Compare window, and the pass or fail comparison of a value against a dragged limit. The agent’s goal and `result.explanation` use contract units. The UI relabels the legacy pipeline’s verification wording for non-FEA sources.
- Fields are checked against the names in `contracts/models.py` before anything renders. A missing or wrong field raises the error banner; nothing is filled in with a default.
- The bracket outline uses the five dimensions plus the fixed 60 mm mounting leg from `contracts/README.md`. The hole position and the fillet tint are illustrative.
- The layout, panel names and colour scheme follow Ansys Discovery closely so the audience feels at home. The icons are our own drawings, there is no Ansys logo, and the DEMO badge stays on screen.
- There is no result contour or colour legend, because the contracts carry no field data.
- The v0 formula fixture equations come from `beam_formula` in `contracts/fakes.py` and are available as MathML and LaTeX. Live recommendations use `result.explanation`. The `calculix` result source identifies an FEA run. A fitted check can reuse the predictor’s power law and provides no independent validation in that case.
- Chart axis ticks are round scale marks and are the only numbers on screen that are not contract values. Chart colours were checked for colour-blind separation, and each state also has its own marker shape.

## Browser tests

With Playwright installed, set `SIMPLESIM_CHROME` to the path of an installed Chrome executable and run `python -m pytest tests/test_ui_provenance.py tests/test_ui_geometry_families.py tests/test_ui_assistant_chat.py tests/test_ui_polish.py -q`. The polish tests cover window stacking (100 focus clicks never cover the tooltip), the chat rendering, quick replies, the output sliders, the Compare window and the verdict, all against the fake model. The provenance tests use contract payloads. The geometry tests launch the local fitted API with temporary files.
