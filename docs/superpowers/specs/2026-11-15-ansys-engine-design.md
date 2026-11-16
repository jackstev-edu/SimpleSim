# Ansys engine for the PyDiscovery emulator (short design)

Approved by the user 2026-11-15.

## Intent

Jack's emulator (`src/pydiscovery`, branch `role2/pydiscovery-emulator`) shortlists L-bracket designs with the fitted power law and then verifies finalists with an engine. With the default engine that check is circular (the result says so). This work fills the `ansys` slot he left for Role 3, so finalists are checked with real ANSYS FE: SpaceClaim edits plus a Mechanical solve, the same pipeline as the Role 3 agent.

Goal: `App("ansys")` solves any L-bracket v0 design in Can's ranges and returns `{max_stress_mpa, max_disp_mm, mass_kg}`.
Non-goals: other part families, changes to the contracts, changes to Jack's session or tools beyond the placeholder class.
Anti-goals: tests or imports that need ANSYS on a teammate's machine; a stress that includes the support singularity being used for the safety factor.

## Approaches

1. **Edit `examples/l_bracket.scdocx` with the existing workbench edits** (picked). Can's v0 family is built from this same file: the back face (X = 0) and the bottom face stay, the hole stays at X 60 / Z 20, and the fillet sits at the inside corner. Each dimension maps to an edit the workbench already does. No new geometry code, and the same zone, mass and solve code as the agent.
2. Build the solid from scratch in SpaceClaim (sketch and extrude). Matches Can's CadQuery code line by line but duplicates geometry code we already have working.
3. Import Can's CadQuery STEP output into Mechanical. Needs Can's packages (CadQuery, gmsh) on the ANSYS machine and a second geometry path.

## Design

- **Branch:** merge `origin/role2/pydiscovery-emulator` into `Yunus` (as `main` was merged on 2026-11-15).
- **Code:** `src/sim/ansys_engine.py` (Role 3 area) holds `AnsysEngine`. In Jack's `src/pydiscovery/backends.py` only the `AnsysBackend` placeholder changes: it imports `AnsysEngine` lazily inside `solve()` and delegates, so tests and teammates without ANSYS never import it. This is the slot Jack left for Role 3.
- **Geometry mapping** (baseline t 6, L 80, w 40, d 8 from `docs/bracket_family_v0.md`; faces found by geometry, not by id):
  - `thickness_mm`: offset the vertical leg's inner face (normal +X) and the horizontal leg's top face (normal +Y) by t − 6.
  - `flange_length_mm`: offset the free-end face (max X) by L − 80.
  - `arm_width_mm`: offset the side face at max Z by w − 40 (Z = 0 stays, so the hole stays at Z 20).
  - `hole_diameter_mm`: radius edit to d / 2.
  - `fillet_radius_mm`: fillet between the two inside-corner faces.
- **Setup:** fixed = back face at X = 0, load = free-end face, force = `force_n × family.load_direction`.
- **Material** (Claude's call): if the ANSYS library has a material with the same name, solve with it. Otherwise solve with Structural Steel and rescale: deflection × E_steel / E, stress unchanged (linear, single material; the Poisson effect is ignored and noted), mass = volume × the part's density. A note says which was used.
- **Return:** `max_stress_mpa` = `stress_for_limit_MPa` (the peak outside the support and load zones, per the 2026-11-11 decision), so Jack's safety factor follows the Role 3 rule. Deflection and mass as above. Pydantic drops extra keys in `Verified`, so the raw peak, zone size and settled flag, peak locations, nodes and notes are kept on the engine as `last_details` (one dict per solve).
- **Mesh check:** not inside `solve()`; `AnsysEngine(mesh_check=True)` runs `check_mesh` after each solve and records the result in `last_details`.
- **Lifetime:** one engine holds one workbench (one SpaceClaim + one embedded Mechanical), started on the first solve; `close()` shuts it down. One embedded Mechanical per process.
- **Tests:** unit tests with a fake workbench (edit mapping, material scaling, return keys, lazy import, no ANSYS needed); one ANSYS smoke script that solves Can's baseline (t 6, L 80, w 40, d 8, r 4, 500 N, steel) and compares with his CalculiX value.

## Left to the builder

The exact workbench calls, face-finding tolerances, and where the smoke script's CalculiX reference value comes from (Can's dataset on `Jin(role--data)`).
