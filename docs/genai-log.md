# GenAI Use Log

Record each meaningful use of a GenAI tool on this project.

| Date | Who | Tool | What it was used for | What we kept or changed |
| --- | --- | --- | --- | --- |
| 2026-10-30 | Yunus | Claude Code (Claude Opus 5.5) | Set up ANSYS 2026 R1 + PyAnsys locally; tested which SpaceClaim export formats the Student licence allows; wrote `src/sim` (geometry edits, Mechanical solve, reaction-balance and mesh-refinement checks) and `scripts/smoke_loop.py` | Verified by running the smoke test: volumes match hand calculations, reactions balance the load, stresses match beam theory. |
