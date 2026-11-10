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
  agent/        Role 3: goal parsing, tool loop, explanations
  ui/           Role 4: interface on result.json fixtures
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

## Team

- Arin Rothschild
- Jin Can
- Yunus Polatoglu
- Jack Stevens


## GenAI use

We log how we use GenAI tools in [docs/genai-log.md](docs/genai-log.md), as the course requires.
