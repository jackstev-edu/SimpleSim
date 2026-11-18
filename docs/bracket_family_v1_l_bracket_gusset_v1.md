# Gusseted L-Bracket Family v1 (`l_bracket_gusset_v1`)

The v0 L-bracket (`docs/bracket_family_v0.md`) plus one triangular 45 degree gusset rib at the inside corner, centred across the width. Built, meshed and solved with Role 1's v0 CadQuery, Gmsh and CalculiX pipeline, generalized through a geometry family rather than edited in place. Code: `src/data/gusset_v1/`. Dataset: `data/processed/formal_v1_l_bracket_gusset_v1/`.

One interpretation to flag for Can and Jack: the overnight brief says "keep the v0 variables and add vertical height, gusset thickness, and gusset leg length". This family reads "vertical height" as the bracket's vertical-leg height, which v0 held fixed at 60 mm, and gives the gusset two parameters: its thickness across the width and one leg length used for both legs (a 45 degree rib). If the gusset's own height was meant instead, the geometry builder takes a second leg length with a one-line change.

## Coordinates and baseline

Same frame as v0: X from the fixed back face toward the free end, Y up the vertical leg, Z across the width, load in -Y.

| Property | Baseline |
|---|---:|
| Horizontal-leg length (flange) | 80 mm |
| Vertical-leg height | 60 mm |
| Width | 40 mm |
| Plate thickness (both legs) | 6 mm |
| Hole | 8 mm diameter through the horizontal leg at X = 60, Z = 20 |
| Inside-corner analysis fillet | 4 mm |
| Gusset | right triangle with 20 mm legs on the inner faces of both legs, 6 mm thick, centred at Z = 20 |
| Gusset junction fillet (fixed) | 2 mm along every edge where the gusset meets the legs or the inside fillet |

The gusset's right-angle corner sits at the inside corner (X = t, Y = t); its hypotenuse runs from (t + g, t) to (t, t + g) where t is the thickness and g the gusset leg length. The junction fillet is analysis geometry, for the same reason v0 rounds the inside corner: a sharp rib-to-plate edge has no converged peak stress. The baseline solid has 22 faces and a volume of 33,240 mm3 (v0 plate 32,000 mm3 plus the rib).

## Design variables

| Variable | Baseline | Range | Status |
|---|---:|---:|---|
| `thickness_mm` | 6 | 4 to 10 | v0, unchanged |
| `flange_length_mm` | 80 | 75 to 120 | v0, unchanged |
| `arm_width_mm` | 40 | 32 to 60 | v0, unchanged |
| `hole_diameter_mm` | 8 | 6 to 12 | v0, unchanged |
| `fillet_radius_mm` | 4 | 2.5 to 8 | v0 range, lower bound raised from 2 (see Gate 1) |
| `vertical_height_mm` | 60 | 50 to 90 | new (fixed at 60 in v0) |
| `gusset_thickness_mm` | 6 | 3 to 10 | new |
| `gusset_length_mm` | 20 | 12 to 35 | new, both gusset legs |
| `poissons_ratio` | 0.30 | 0.25 to 0.35 | sampled, as in v0 |

Fixed: hole centre (X = 60, Z = 20), load 500 N in -Y on the free-end face, load angle -90 degrees, E = 200,000 MPa, Sy = 250 MPa, rho = 7,850 kg/m3, junction fillet 2 mm. Every shared variable's range includes its v0 baseline value (Gate 1, item 4). The fillet range starts at 2.5 mm rather than v0's 2 mm: an inside fillet within about 0.4 mm of the 2 mm junction fillet makes OpenCascade blend two near-equal radii into sliver faces that mesh to inverted elements (details under Gate 1).

Why these ranges: the gusset reach (t + g + 2 mm fillet) is at most 47 mm. The hole edge is never nearer than X = 54 mm and the vertical leg never shorter than 50 mm, so every combination clears the hole by at least 7 mm and the leg top by at least 3 mm. The rules below are still enforced on every design, so the guarantee comes from validation, not from luck.

## Boundary conditions, material, analysis

Unchanged from v0: the back face at X = 0 is fully fixed (all translations); a total of 500 N in -Y is spread over the free-end face at X = flange length as consistent nodal loads (never at the hole); linear elastic, isotropic, small deformation, static; tet10 elements in CalculiX 2.23; reported outputs are the maximum nodal von Mises stress, the maximum displacement magnitude, volume and mass, and the reaction force at the fixed face.

## Geometry validity rules (`src/data/gusset_v1/spec.py`, `RULES`)

| Rule | Condition | Margin |
|---|---|---:|
| `hole_inside_flange` | hole stays inside the horizontal leg (v0) | |
| `gusset_clears_hole` | t + g + 2 mm fillet + margin <= 60 - d/2 | 3 mm |
| `gusset_clears_vertical_top` | t + g + 2 mm fillet + margin <= vertical height | 3 mm |
| `gusset_within_width` | g_t/2 + 2 mm fillet + margin <= width/2 | 3 mm |
| `gusset_past_fillet` | g >= inside fillet radius + 2 mm fillet + margin | 2 mm |
| `fillet_distinct_from_junction` | abs(inside fillet radius - 2 mm junction fillet) >= gap | 0.4 mm |

Plus the pydantic range checks on every variable (out-of-range values are rejected, never clamped). A failed rule is reported by name with the measured gap, in the model's error and in `failure_attempts.csv` under stage `geometry_validation`.

## Sampling

Scrambled Sobol (`scipy.stats.qmc.Sobol`, seed 20261116), n = 2048 = 2^11 over the nine sampled variables. Design IDs `v1_20261116_0000` to `v1_20261116_2047`. The plan is written once to `sample_plan.csv` and checked row for row on every resume. A prefix of a Sobol sequence stays space filling, so waves (`--limit 512, 1024, 1536, 2048`) and shards (`--shard i --num-shards N`, an interleaved slice) are plain slices of one frozen plan. A 2^11 point Sobol net puts exactly 128 planned points in each of 16 equal bins on every axis; `quality_checks.csv` verifies this as `sobol_balance_<variable>`.

## Mesh

Rule at scale s: tet10, maximum element size = s x min(3 mm, thickness/2, gusset thickness), minimum size = maximum/4, Gmsh `Mesh.MeshSizeFromCurvature` = round(16/s) elements per full circle on fillets and the hole, `Mesh.Algorithm3D` = 10 (HXT) and `Mesh.HighOrderOptimize` = 2. Levels: coarse s = 1.5, medium s = 1.0, fine s = 2/3. All three settings are new fields on v0's `MeshSettings` with defaults that leave v0 meshes and rule names unchanged. The production level is chosen at Gate 2 (below) and recorded in `summary.json` as `mesh_level` and `production_mesh_strategy`. Retry policy as in v0: a CalculiX "nonpositive Jacobian" gets one retry at 0.75 x the production sizes.

Why curvature sizing: on the baseline the peak stress sits on the 2 mm junction fillet at the gusset tip. v0's uniform 1 to 2 mm rule gave 240.7 MPa there and kept climbing with refinement (226 -> 241 -> 261 MPa). With curvature sizing the medium and fine levels agree within 0.5% on the baseline (273.7 vs 275.1 MPa), so the fillet is resolved.

Why HXT and high-order optimization: with Gmsh's default Delaunay algorithm about 1 design in 10 produced curved tet10 elements with a negative or near-zero scaled Jacobian (minSICN) on the junction fillets; HXT with `HighOrderOptimize = 2` gave minSICN above 0.18 on every one of the first 50 plan rows except the two near-equal-radius designs that the `fillet_distinct_from_junction` rule now excludes. Re-solving the three study designs at the medium level with HXT gave 273.6, 1081.8 and 62.8 MPa, within 0.6%, 0.4% and 1.0% of the fine-level Delaunay results, so the study's conclusion stands.

## Output schema

Same file set as `formal_v0`: `sample_plan.csv`, `runs.csv`, `model_dataset.csv`, `run_diagnostics.csv`, `failure_attempts.csv`, `failures.csv`, `quality_checks.csv`, `summary.json`, `progress.json`, plus a `README.md`.

- `runs.csv`: the shared `Run` contract columns in contract order, followed by three extra columns `vertical_height_mm`, `gusset_thickness_mm`, `gusset_length_mm`. v0 column names are kept where the meaning is identical. `source` is `calculix_2.23`, `mesh_rule` names the per-case Gmsh sizes and curvature setting (for example `tet2_1.125to4.5mm_curv11`).
- `model_dataset.csv`: `dataset_schema_version` = `role1_model_1.0.0`, plus `bracket_family` = `l_bracket_gusset_v1`, all eight geometry columns, `poissons_ratio`, the reference load and modulus, and `reference_max_stress_mpa`, `reference_max_disp_mm`, `volume_mm3`, `reference_mass_kg`. Scaling to other loads and materials follows `material_scaling.py`, as in v0.
- `run_diagnostics.csv`: the v0 diagnostic columns plus `elapsed_geometry_s` (rebuilding the solid for its volume), `elapsed_mesh_s` (STEP export and Gmsh), `elapsed_solve_s` (deck, CalculiX, parsing), `elapsed_total_s` (the whole case in its worker, retries included), `platform`, `ccx_version`, `attempt_number`, `mesh_level`.
- `failure_attempts.csv` and `failures.csv`: every attempt, and the latest attempt of each still-unsolved design, with the stage (`geometry_validation`, `geometry`, `mesh`, `solve`, `worker`), error type and message. A failed case is never filled in.

## Proposed contract changes (for the team, not applied here)

1. `Run` and `StudySpec` need a `bracket_family` field; today `runs.csv` has no way to say which shape a row belongs to, and the v1 file carries it only in `model_dataset.csv`.
2. `Run` needs the three new dimensions (`vertical_height_mm`, `gusset_thickness_mm`, `gusset_length_mm`), or a generic `design_json` column, so `RUN_COLUMNS` can describe more than the five v0 dimensions. Until then v1's `runs.csv` appends them after the contract columns.
3. `PARAM_NAMES` and the GUI's `PARAMS` list are v0-specific; the predictor, the emulator's `families.py` and the viewport need per-family parameter lists before they can use this dataset.
4. The four numerical material properties proposed in the v0 doc still apply.

## Gate 1 (checked 2026-11-16, replaces STOP 1)

| Check | Result |
|---|---|
| 1. Baseline builds, meshes and passes validation | Pass. CadQuery solid valid, 22 faces, 33,240 mm3; Gmsh mesh with non-empty fixed and load node sets; CalculiX solves (reaction imbalance 1e-7). |
| 2. Gusset-to-hole clearance is a named rule with a stated margin | Pass. `gusset_clears_hole`: gusset reach (t + g + 2 mm junction fillet) at least 3 mm short of the hole edge. Three more named rules cover the leg top, the side faces and the inside fillet. |
| 3. At least 99% of a 256-row Sobol preview passes validation | Pass. First parameterization (fillet 2 to 8 mm): 256 of 256 rows pass and build. One re-parameterization followed the timing pilot: designs whose inside fillet sits within about 0.4 mm of the 2 mm junction fillet meshed to sliver elements (minSICN 0.002 to 0.007 under every Gmsh setting tried, against 0.25 to 0.28 with the junction fillet at 1.5 or 2.5 mm), so the fillet range now starts at 2.5 mm and `fillet_distinct_from_junction` names the rule. Rechecked: 256 of 256 preview rows pass and build (25 s), and all 2,048 plan rows pass. |
| 4. Ranges include the v0 baseline for every shared variable | Pass. 6, 80, 40, 8 and 4 mm all sit inside their unchanged v0 ranges; the three new ranges include 60, 6 and 20 mm. |

Parameter table: see "Design variables" above. v0 pipeline reuse, in short: the deck builder, FRD/DAT parsers, reaction check and checkpoint writers are reused as they are; `generate_mesh` and `solve_case` gained an optional geometry family (default v0) and `MeshSettings` an optional curvature setting (default off, so v0 mesh rules and names are unchanged); the v0 spec, geometry, LHS and serial loop stay as they were.

## Gate 2 (checked 2026-11-16, replaces STOP 2)

Mesh study (`mesh_study.csv` and `mesh_study.json` in the dataset folder; 3 workers, one thread per CalculiX process):

| Design | Coarse (s = 1.5) | Medium (s = 1.0) | Fine (s = 2/3) | Coarse vs medium | Medium vs fine |
|---|---:|---:|---:|---:|---:|
| baseline | 255.65 MPa, 16.6k nodes, 12 s | 273.72 MPa, 39.7k nodes, 35 s | 275.05 MPa, 107k nodes, 216 s | 6.60% | 0.48% |
| thinnest/longest | 975.03 MPa, 27.2k nodes, 16 s | 1062.17 MPa, 70.2k nodes, 55 s | 1077.77 MPa, 202k nodes, 696 s | 8.20% | 1.45% |
| thickest/shortest | 60.28 MPa, 22.1k nodes, 19 s | 63.28 MPa, 54.5k nodes, 69 s | 63.40 MPa, 157k nodes, 1031 s | 4.74% | 0.18% |

1. Mesh rule: **medium** (s = 1.0), the coarsest level within 3% of the next finer level on all three designs. Displacement changes are below 0.2% at every level. The study ran with Gmsh's Delaunay algorithm; the production rule uses HXT with high-order optimization, re-checked at the medium level on the same designs: 273.55, 1081.77 and 62.75 MPa, within 0.55%, 0.37% and 1.01% of the fine results.
2. Timing pilot: the first 20 plan rows at 3 workers (physical cores minus one; 4 workers was measured twice as slow per case on this 4-core laptop). 20 of 20 solved, 0 failed, wall 8.1 min. Seconds per case inside its worker: mean 68.2, max 131.3 (mesh 9.6, CalculiX 58.4 on average; 57.7k nodes mean, 79.1k max). Minimum element quality 0.235, reaction imbalance at most 1.7e-7. Output ranges over the pilot: stress 94 to 672 MPa, displacement 0.055 to 2.605 mm, mass 0.220 to 0.763 kg.
3. Projection: 20 cases per 8.1 min is 24.3 s per case, so about 13.8 hours of wall clock for all 2,048 cases on this machine. That is over the 12 hour mark, so it is flagged here; the run proceeds in four committed waves (512, 1024, 1536, 2048) because every wave is resumable.
4. Result (2026-11-18): all 2,048 designs solved, every quality check passed, `role1_dataset_complete` true. Ten attempts failed on the way, all CalculiX timeouts while the laptop slept mid-solve (elapsed 1,350 to 33,000 s against a 600 s budget); all ten solved on attempt 2 with unchanged settings, and no design needed the Jacobian retry mesh. Compute: 2,048 cases, mean 120 s per case, 68.5 CPU-hours across 3 workers, over the nights of 2026-11-16 to 2026-11-18. Output ranges: stress 67 to 1,032 MPa, displacement 0.029 to 4.61 mm, mass 0.149 to 0.864 kg.

## Reproducing

See `data/processed/formal_v1_l_bracket_gusset_v1/README.md`. In short, from the repo root with `src/data/requirements.txt` installed:

```
python -m src.data.gusset_v1.mesh_study --output data/generated/gusset_v1_mesh_study
python -m src.data.gusset_v1.pipeline --output data/processed/formal_v1_l_bracket_gusset_v1 --workers 3 --limit 512
```

Rerunning the same command resumes; completed design IDs are skipped.
