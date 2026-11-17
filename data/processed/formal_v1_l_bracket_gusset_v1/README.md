# formal_v1_l_bracket_gusset_v1

Real CalculiX 2.23 solves of the gusseted L-bracket family v1 (`docs/bracket_family_v1_l_bracket_gusset_v1.md`): a scrambled Sobol plan of 2,048 designs (seed 20261116) over eight geometry variables and Poisson's ratio, at the reference load of 500 N in -Y, E = 200,000 MPa, Sy = 250 MPa, rho = 7,850 kg/m3. Every row comes from its own solve; failed cases are logged in `failures.csv`, never filled in. See `summary.json` for counts, output ranges, compute time and the QA result.

| File | What it holds |
|---|---|
| `sample_plan.csv` | the frozen plan: design_id, the nine sampled inputs, the reference conditions |
| `runs.csv` | one row per solved design in the shared `Run` contract columns, followed by `vertical_height_mm`, `gusset_thickness_mm`, `gusset_length_mm` |
| `model_dataset.csv` | the training file: `dataset_schema_version` role1_model_1.0.0, `bracket_family`, all nine inputs, reference stress, displacement, volume and mass |
| `run_diagnostics.csv` | reaction forces and imbalance, node and element counts, element quality, where the maxima sit, elapsed times per stage, platform, ccx version, attempt number, mesh level |
| `failure_attempts.csv`, `failures.csv` | every failed attempt, and the latest attempt of each design that is still unsolved |
| `quality_checks.csv` | completeness, alignment, positivity, reaction balance (<= 0.01), element quality (> 0.1), inputs within range, CalculiX source on every row, Sobol balance per axis |
| `mesh_study.csv`, `mesh_study.json` | the Gate 2 convergence study: three designs at three mesh levels |
| `summary.json`, `progress.json` | dataset summary and resume status |

## Reproduce

From the repo root, with `src/data/requirements.txt` installed (CadQuery 2.8.0, Gmsh 4.15.2, cad-ccx 2.23.0, scipy):

```
python -m src.data.gusset_v1.mesh_study --output data/generated/gusset_v1_mesh_study --workers 3
python -m src.data.gusset_v1.pipeline --output data/processed/formal_v1_l_bracket_gusset_v1 --workers 3 --limit 512 --case-dir <scratch folder>
python -m src.data.gusset_v1.pipeline --output data/processed/formal_v1_l_bracket_gusset_v1 --workers 3 --limit 1024 --case-dir <scratch folder>
...
```

Rerunning the same command resumes: the plan is checked against `sample_plan.csv`, completed design IDs are skipped, failed ones are retried with a new attempt number. `--shard i --num-shards N` runs an interleaved slice on another machine; `python -m src.data.gusset_v1.merge --output <folder> <shard folders>` joins the slices, refusing duplicates and reporting missing IDs. Each CalculiX process runs on one thread. Keep `--case-dir` off a synced folder (OneDrive): the solver writes and deletes hundreds of megabytes per wave.

Mesh: tet10, maximum size min(3 mm, thickness/2, gusset thickness), minimum size a quarter of that, Gmsh curvature sizing at 16 elements per circle, HXT, high-order optimization. A nonpositive Jacobian gets one retry at 0.75 x those sizes (`mesh_level` = `medium_jacobian_retry` in the diagnostics).

Generated overnight on 2026-11-16/17 by Claude Code for Jack Stevens (Role 2) with the authorization recorded in the pull request; Can (Role 1) owns the pipeline.
