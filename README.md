# AI Project 2

Course project for 24-679 Designing & Prototyping AI Systems (CMU, Fall 2026).

Track: Synopsys (Ansys) **Simulation-Driven Design Optimization**. We are exploring an AI advisor that helps engineers decide what to change after a simulation, and explains why.

**Status:** early exploration. Scope, data, models and interface are not decided yet, including whether the advisor runs inside Ansys tools or in a separate app.

## Structure

```
src/
  data/         simulation data generation and processing
  models/       model training and inference
  agent/        advisor agent logic
  sim/          SpaceClaim geometry edits and Mechanical solves (PyAnsys)
scripts/        runnable checks, e.g. smoke_loop.py (edit -> solve -> plausibility checks)
examples/       sample parts for development
outputs/        generated part and result files (not committed)
data/
  raw/          raw simulation outputs (not committed)
  processed/    cleaned datasets (not committed)
notebooks/      exploration and experiments
docs/           design notes and GenAI use log
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The `src/sim` tools need a local ANSYS 2026 R1 install (Mechanical and SpaceClaim; the Student version works) and Python 3.12+. Check the whole chain with:

```bash
python -m scripts.smoke_loop     # geometry edit -> solve -> plausibility checks
python -m scripts.smoke_tools    # the agent's tools, scripted, no LLM (no API cost)
```

Run the chat app (needs `ANTHROPIC_API_KEY` set as an environment variable; never put the key in code):

```bash
python app.py                    # opens http://127.0.0.1:7860
```

Upload a part (`.scdocx`, `.stp`, `.step`; try `examples/l_bracket.scdocx`), then describe the load case and the objective in the chat.

Student licence note: SpaceClaim can only save `.scdocx` and `.pmdb` (STEP/IGES/Parasolid export is blocked), but STEP import works.

## Team

- Arin Rothschild
- Jin Can
- Yunus Polatoglu
- Jack Stevens


## GenAI use

We log how we use GenAI tools in [docs/genai-log.md](docs/genai-log.md), as the course requires.
