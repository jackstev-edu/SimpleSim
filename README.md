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

## Team

- Arin Rothschild
- Jin Can
- Yunus Polatoglu
- Jack Stevens


## GenAI use

We log how we use GenAI tools in [docs/genai-log.md](docs/genai-log.md), as the course requires.
