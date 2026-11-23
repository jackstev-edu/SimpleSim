# Fast-path benchmark problems

Ten problems that test the whole assistant (front layer + PyDiscovery fast path) on whether it sets up the right
study and picks the right design. Design: `docs/superpowers/specs/2026-11-22-fast-path-benchmark-design.md`.

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

## Review status

Keys built 2026-11-22. **Not yet reviewed by the user**; a run graded before the review is a trial only.
