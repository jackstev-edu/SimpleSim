# Ansys engine for the PyDiscovery emulator (Yunus, 2026-11-15)

Branch `Yunus`. For Jack, Can and Arin.

## What changed

- **Jack's `role2/pydiscovery-emulator` is merged into `Yunus`** (not into `main`). The only conflict was `docs/genai-log.md`; all rows are kept.
- **The `ansys` engine works.** `App("ansys")` now solves any L-bracket v0 design with real ANSYS: SpaceClaim edits on `examples/l_bracket.scdocx`, then a Mechanical solve.
  - The code is in `src/sim/ansys_engine.py` (Role 3).
  - In Jack's folder, only the `AnsysBackend` placeholder class changed, plus one row in `docs/pydiscovery.md`.
  - The engine is imported only when it is used. `python -m pytest tests -q` therefore passes without ANSYS: 232 pass and 2 are skipped (Jack's, which need a `.dsco` file or CalculiX).

## Checked against Can's CalculiX runs

Two runs from `data/processed/formal_v0/runs.csv`, at 500 N, in structural steel:

| Run | Mass | Deflection | Stress | Mesh check (halved mesh) |
| --- | --- | --- | --- | --- |
| formal_20261111_085 | identical | −0.6 % | +9.0 % | 1.0 % change |
| formal_20261111_145 | identical | −0.4 % | +7.0 % | 0.1 % change |

Identical mass means the geometry matches Can's CadQuery build exactly. Our stress peak sits on the fillet, and the mesh check says it is converged. To reproduce: `python -m scripts.smoke_ansys_engine` (needs ANSYS 2026 R1, about 4 minutes).

## Things to know

- **Which stress is returned.** `max_stress_mpa` is the peak **outside small zones around the fixed face and the loaded face**, so a support singularity does not set the safety factor. On these designs nothing was excluded, so it equals the raw peak.
  - `Verified` drops extra keys. The raw peak and the zone data are therefore kept in `backend.last_details`.
- **Materials.** The engine matches the material name against the ANSYS library; both contract materials match.
  - Otherwise it solves the design as steel, rescales the deflection by E, and takes the mass from the part's own density.
- **Mesh check.** It is off by default. Use `AnsysBackend(mesh_check=True)` for finalists.
- **Speed and licences.** One engine holds one SpaceClaim and one Mechanical session, about 40–70 s per design. It closes itself when Python exits.

## Questions for the team

1. **Who owns the finalist re-check?** Jack's placeholder said Role 3. The roles doc says finalists are "re-checked by Role 1's simulation". I built it because `src/sim` already existed on my branch. Can, do you want to take it over, or keep CalculiX as the main check and this as a cross-check?
2. **`PartSetupCard` proposal** (`docs/proposals/part-contract.md`). I still need to review it. Arin has to approve it too.
3. **Other parts.** The engine, like the emulator, only takes L-bracket dimensions. My agent also handles other brackets (saddle, SimJEB, a user's own STEP file). Should the contracts open up to other families later?

Open minors, not fixed yet:
- a failed mesh check discards the good solve;
- an unsettled zone is visible only in `last_details`;
- one code comment about the load case is wrong.
