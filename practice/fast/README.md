# Fast-path benchmark problems

Twenty problems that test the whole assistant (front layer + PyDiscovery fast path) on whether it sets up the right
study and picks the right design. Design: `docs/superpowers/specs/2026-11-22-fast-path-benchmark-design.md`.

The ten `t01_clean_mass` ... `t10_clean_mass` problems (2026-11-27, design
`docs/superpowers/specs/2026-11-27-template-benchmark-design.md`) send the same plain request on each of Can's
template families T01 to T10, to show that the assistant picks the right design on every new shape. Their
`sim_optimum` comes from Can's multi-family table (`data/processed/formal_multifamily_v1`).

Each `<id>.yaml` holds the student's first message (`request`), what the simulated student knows (`hidden_facts`)
and the `key`:

- `study`: what the assistant should submit (force in N, angle, material key, safety factor, objective, extra
  limits, dimensions that may vary).
- `must_ask`: inputs the request leaves out; `states_setup: true` when the request states the support and the
  loaded face.
- `model_optimum`: the fitted model's best design with every limit judged at the pessimistic end of the model's
  band (the fast path's own shortlist rule). This is the truth for scoring. `median` is the best design with the
  limits judged at the median, for information.
- `sim_optimum`: the best row of Role 1's CalculiX table, only for 500 N, -90 deg, steel; otherwise `null` with a
  reason. Reported, never scored.

Rebuild the keys with `python -m scripts.build_fast_keys` (deterministic: n = 400,000, seed 0).

## Grading notes

- `family`: the study the assistant submits must be for the problem's own family. Jack's `match_family` picks a
  family from dimension names alone and reads T03 twin-slot as T02 and T08 twin-gusset as the gusset v1 family, so
  T03 and T08 are expected to fail this check until that is fixed.
- `--repeat N` runs every problem N times into `r1/` ... `rN/` with one budget cap and one ledger row. The
  top-level `summary.md` passes a check only when every repeat passes it, else `fail (k/N)`; a repeat that never
  reached a problem counts as not passing. `--regrade` rebuilds it.

## Review status

Keys built 2026-11-22 and **approved by the user 2026-11-22**. Template keys (`t01` ... `t10`) built 2026-11-27,
**approved by the user 2026-11-27**. The run `fast-20261122-2239-costcheck` came before the approval and is a trial only.
