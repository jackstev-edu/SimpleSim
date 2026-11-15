# SimpleSim: notes for Claude Code sessions

Read `contracts/README.md` before writing code. Every role's code talks to the others only through the four contract files, and `tests/test_contracts.py` must pass before a push.

## Layout

- `contracts/` shared Pydantic models, fakes and examples. Changing a model needs both sides of that hand-off to approve.
- `src/data/` Role 1 (Can): solver backends and the generator that writes `runs.csv`.
- `src/models/` Role 2 (Jack): Gaussian process and AutoGluon predictors, learning curves, the design benchmark.
- `src/agent/` Role 3 (Yunus): goal parsing, tool loop, verification, explanations.
- `src/ui/` Role 4 (Arin): the interface, built on `result.json` fixtures first.

## Conventions

- Units in field names (mm, N, MPa, kg). Convert at the boundary, never inside.
- Prefer the fakes in `contracts/fakes.py` to mocking by hand.
- Comments: one short line on non-trivial lines, moderately technical. No em dashes or double-dash separators.
- Log GenAI use in `docs/genai-log.md`.
- Never commit `.env`, Ansys result files or raw data; see `.gitignore`.
