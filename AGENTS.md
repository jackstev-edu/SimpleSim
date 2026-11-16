# Agent rules

Follow `CLAUDE.md`: it holds the team layout and conventions for every coding agent, not only Claude.

## Divergence check (every coding agent: Claude Code, Codex, Cursor and others)

At the start of a session and before each commit, check whether the work pulls the project off the shared plan in `README.md` (Team agreement) and `contracts/README.md`. If it does, tell the user in one or two sentences and continue only if they agree.

Signs of divergence:
- edits to `contracts/` or to another role's folder
- new packages in the root `requirements.txt`
- `python -m pytest tests -q` needs Ansys or other local installs to pass
- a parallel solver, optimizer, part format or agent loop outside the shared interfaces
- the branch is many commits behind `main`
