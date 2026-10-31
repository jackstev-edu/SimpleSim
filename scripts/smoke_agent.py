"""Live smoke test of the full agent (LLM + tools) with a scripted user.

Run from the repo root:  python -m scripts.smoke_agent
Needs ANTHROPIC_API_KEY. Uses SIMPLESIM_MODE (default "debug" = Haiku, cheap).
Prints every tool call and the token use and estimated cost of the run.
"""
from pathlib import Path

import anthropic

from src.agent.agent import MODE, MODEL, Agent
from src.agent.tools import Workbench

ROOT = Path(__file__).resolve().parents[1]

# USD per million tokens: input, output, cache read, cache write (5 min)
PRICES = {"claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
          "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50)}

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
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0, "calls": 0}
    client = anthropic.Anthropic()
    create = client.messages.create

    def counted_create(**kwargs):
        r = create(**kwargs)
        usage["calls"] += 1
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            usage[k] += getattr(r.usage, k, 0) or 0
        return r

    client.messages.create = counted_create

    wb = Workbench(ROOT / "outputs" / "smoke_agent")
    try:
        wb.load_part(ROOT / "examples" / "l_bracket.scdocx")
        agent = Agent(wb, client)
        for turn in TURNS:
            print(f"\n=== USER: {turn}")
            for ev in agent.run(turn):
                if ev["type"] == "text":
                    print(f"\n--- CLAUDE:\n{ev['text']}")
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
        p_in, p_out, p_cr, p_cw = PRICES[MODEL]
        cost = (usage["input_tokens"] * p_in + usage["output_tokens"] * p_out
                + usage["cache_read_input_tokens"] * p_cr
                + usage["cache_creation_input_tokens"] * p_cw) / 1e6
        print(f"\nusage: {usage}\nestimated cost: ${cost:.4f}")


if __name__ == "__main__":
    main()
