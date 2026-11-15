# AI Project 2

Course project for 24-679 Designing & Prototyping AI Systems (CMU, Fall 2026).

Track: Synopsys (Ansys) **Simulation-Driven Design Optimization**. We are exploring an AI advisor that helps engineers decide what to change after a simulation, and explains why.

**Status:** building in parallel against agreed hand-offs. Target users are students and early-career engineers working on structural brackets. A student states a goal in plain language, the agent turns it into a study, a fast model shortlists designs, a real solver verifies the finalists, and the UI explains the pick with values to enter in Ansys Discovery.

## Team agreement (read this first)

Four people build four layers at the same time. The layers connect only through the four contract files in [`contracts/`](contracts/README.md):

| Hand-off | File | Owner pair |
| --- | --- | --- |
| Agent to Model | `study_spec.json` | Yunus, Jack |
| Data to Model | `runs.csv` | Can, Jack |
| Model to Agent | `prediction.json` | Jack, Yunus |
| Agent to UI | `result.json` | Yunus, Arin |

Each contract has a Pydantic model, a passing example and a fake stand-in, so nobody waits on anyone. Changing a contract needs approval from both owners. `python -m pytest tests -q` runs on every push and fails when a hand-off breaks.

Fakes get swapped for real parts in this order: `runs.csv`, trained predictor, agent on real tools, UI on the live agent.

## Structure

```
contracts/      shared models, fakes, examples (the team agreement)
src/
  data/         Role 1: solver backends, runs.csv generator
  models/       Role 2: GP and AutoGluon predictors, benchmarks
  agent/        Role 3: ANSYS design agent (tool loop, prompt)
  assistant/    Role 3: request to spec, explanation with number check, decision log
  eval/         Role 3: practice-set runner support (simulated user, grader)
  sim/          SpaceClaim geometry edits and Mechanical solves (PyAnsys)
  ui/           Role 4: interface on result.json fixtures
scripts/        runnable checks, e.g. smoke_loop.py (edit -> solve -> plausibility checks)
practice/       practice problems, answer keys and graded results
examples/       sample parts for development
outputs/        generated part and result files (not committed)
tests/          contract and smoke tests
data/
  raw/          raw simulation outputs (not committed)
  processed/    cleaned datasets (not committed)
notebooks/      exploration and experiments
docs/           design notes and GenAI use log
```

## Launch the demo GUI

Double-click **`Launch SimpleSim GUI.bat`** (Windows) or **`Launch SimpleSim GUI.command`** (macOS) at the top of the repo, or run `python launch_gui.py`. It opens the Discovery-style demo in your browser; it needs only Python, no packages. Details in [src/ui/README.md](src/ui/README.md).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest tests -q          # contract checks, should pass on a fresh clone
```

The `src/sim` tools need a local ANSYS 2026 R1 install (Mechanical and SpaceClaim; the Student version works) and Python 3.12+. Check the whole chain with:

```bash
python -m scripts.smoke_loop     # geometry edit -> solve -> plausibility checks
python -m scripts.smoke_tools    # the agent's tools, scripted, no LLM (no API cost)
```

Run the chat app (needs `OPENAI_API_KEY` set as an environment variable; never put the key in code):

```bash
python app.py                    # opens http://127.0.0.1:7860
```

Upload a part (`.scdocx`, `.stp`, `.step`; try `examples/l_bracket.scdocx`), then describe the load case and the objective in the chat.

Each app session has a spending cap: $0.20 in `debug`/`demo` mode and $2.00 in `deploy` mode, set with `SIMPLESIM_MODE`. Every mode uses `gpt-6-luna` for now; `deploy` moves to `gpt-6.1-sol` before submission. Override it with `SIMPLESIM_SESSION_BUDGET_USD`. When the app closes, its cost is added to `practice/spend.csv`.

Student licence note: SpaceClaim can only save `.scdocx` and `.pmdb` (STEP/IGES/Parasolid export is blocked), but STEP import works.

## Team

- Arin Rothschild
- Jin Can
- Yunus Polatoglu
- Jack Stevens


## GenAI use

We log how we use GenAI tools in [docs/genai-log.md](docs/genai-log.md), as the course requires.
