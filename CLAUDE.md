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

## Divergence check (every coding agent: Claude Code, Codex, Cursor and others)

At the start of a session and before each commit, check whether the work pulls the project off the shared plan in `README.md` (Team agreement) and `contracts/README.md`. If it does, tell the user in one or two sentences and continue only if they agree.

Signs of divergence:
- edits to `contracts/` or to another role's folder
- new packages in the root `requirements.txt`
- `python -m pytest tests -q` needs Ansys or other local installs to pass
- a parallel solver, optimizer, part format or agent loop outside the shared interfaces
- the branch is many commits behind `main`
