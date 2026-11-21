# Exclusion zones at supports and loads: design

Date: 2026-11-15. Status: approved by the user in conversation (2026-11-15).

## Why

A fully fixed face gives a stress singularity at its edge: the peak keeps rising as the mesh is
refined, so it is not a real stress. Today the agent judges the safety factor on that peak. This is
why the SimJEB problems can only be graded on setup (their peak is on a fixed bolt hole), and why
the saddle bracket's peak sits on a bolt hole. A force applied to a small face gives the same kind
of spike at that face's edges. Decision 2026-11-11: judge the limit on the peak outside a zone
around each fixed face, report the excluded peak as not physical, and say the bolted joint needs
its own check. User, 2026-11-15: treat the loaded faces the same way ("for brackets the peak stress
will either be around the mounting holes or where the force is exerted").

## Goals

- The safety factor and every stress limit use the peak outside the zones.
- The full peak is still reported, labelled not physical, with where it is and what to check instead.
- No extra solves: work only on the node results the solver already reads.
- The mesh check compares the outside-zone peak, not the raw spike.
- Afterwards: rebuild the SimJEB answer key and grade SimJEB on features too.

## Non-goals

- The softer-support option (only built on request, decision 2026-11-11).
- A bolted-joint or contact check. The agent only says that one is needed.

## Anti-goals

- A zone that hides a real stress. On the L-bracket the inside-corner fillet, which decides the
  design, is about 6 mm from the fixed wall face.
- A confident number when the peak never settles.

## Approaches considered

1. **Fixed distance** (for example one wall thickness). Simple, but on the L-bracket it reaches the fillet.
2. **Based on the hole size** (2 × the hole radius from the axis, as in the 2026-11-01 SimJEB study).
   It fits bolt holes but has no rule for flat fixed faces.
3. **Grow until the peak settles** (picked by the user). Grow the zones in steps and take the first
   size where the outside peak stops changing. This follows the physics on any part. The SimJEB
   value away from the supports (1,337 MPa) was found to be stable in exactly this way.

## Design

- **Zones:** one around each fixed face and each loaded face, grown together. A node is inside if
  its distance to the nearest node of a fixed or loaded face is at most the zone size.
- **Steps (Claude's call):** ½ × the part's thinnest wall (`min_wall_mm`), up to 3 steps (½, 1, 1½ × the wall).
  The zone has settled at the first step where the outside peak changes by less than 5 %.
- **Guard (Claude's call):** a zone stops growing before it contains a node of a fillet face
  (`NS_FILLETS`) or of a face being changed in the study. If the peak has not settled by then, or
  after the last step, the result says `zone_settled: false` and the agent says so.
- **Result fields (Claude's call):**
  - `max_stress_MPa` stays the raw peak.
  - New: `stress_for_limit_MPa`, `excluded_peak_MPa`, `excluded_peak_at_mm`, `excluded_at`
    (`support` or `load`), `zone_mm`, `zone_settled`.
  - `safety_factor` and the limits use `stress_for_limit_MPa`.
- **Mesh check:** `refinement_check` compares `stress_for_limit_MPa` between the coarse and fine
  meshes, using the same zone size in mm.
- **Agent prompt:** name the excluded peak as not physical, say where it is, and say what to check
  instead:
  - at a support: the bolted joint needs its own check
  - at the load: the value depends on how the load is really applied, so check it separately

  Say plainly when the zone did not settle.
- **Practice set:** rebuild the SimJEB key with an ANSYS first-look run (the user reviews it), and
  compare the stress with SimJEB's 1,337 MPa away from the supports. Then switch SimJEB to feature
  grading.

## Left to the builder

- How to read node coordinates and face nodes in bulk (MeshData) so the distance check stays fast at about 120K nodes.
- Where the zone code lives (a pure function on arrays, unit-tested without ANSYS, called from `mechanical.solve`).
