"""Live smoke test of the full agent (LLM + tools) with a scripted user.

Run from the repo root:  python -m scripts.smoke_agent
Needs OPENAI_API_KEY. Uses SIMPLESIM_MODE (default "debug" = gpt-6-luna, cheap).
Prints every tool call and the token use and estimated cost of the run.
"""
from pathlib import Path

from src.agent.agent import MODE, MODEL, Agent
from src.agent.tools import Workbench
from src.llm import cost, counted_client

ROOT = Path(__file__).resolve().parents[1]
BUDGET_USD = 0.05  # hard cap; one smoke run has cost about $0.006 on Haiku-priced models

TURNS = [
    "I uploaded an L-shaped steel bracket. Please describe it.",
    "The face at x = 0 (back of the vertical leg) is bolted to a wall, so it is fixed. "
    "The end face of the horizontal leg at x = 80 mm carries 500 N straight down (-Y). "
    "Structural steel is fine. Please evaluate the baseline.",
    "Objective: minimise mass while keeping max stress below 150 MPa. "
    "Try at most 3 designs, then recommend one.",
    "I accept your recommendation. Please finalize it.",
]


def main():
    print(f"mode {MODE}, model {MODEL}")
    usage: dict = {}
    client = counted_client(usage, BUDGET_USD)

    wb = Workbench(ROOT / "outputs" / "smoke_agent")
    try:
        wb.load_part(ROOT / "examples" / "l_bracket.scdocx")
        agent = Agent(wb, client)
        for turn in TURNS:
            print(f"\n=== USER: {turn}")
            for ev in agent.run(turn):
                if ev["type"] == "text":
                    print(f"\n--- AGENT:\n{ev['text']}")
                elif ev["type"] == "tool":
                    print(f"\n[tool] {ev['name']} {ev['input']}")
                elif ev["type"] == "tool_result":
                    flag = "ERROR " if ev["is_error"] else ""
                    print(f"[result] {flag}{ev['content'][:300]}")
                else:
                    print(f"[{ev['type']}] {ev.get('text')}")
        print("\ntable:", wb.table())
    finally:
        wb.close()
        print(f"\nusage: {usage}\nestimated cost: ${cost(usage):.4f}")


if __name__ == "__main__":
    main()
