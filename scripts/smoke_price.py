"""Live test of the price lookup (LLM + web search, no ANSYS). Costs a few cents.

Run from the repo root:  python -m scripts.smoke_price
Asks for the price of the current material (Aluminum Alloy) and checks that the agent
looked in the cache, searched, and stored a sourced entry in data/material_prices.json.
"""
from pathlib import Path

from src.agent.agent import MODEL, Agent
from src.agent.tools import Workbench
from src.sim import prices

ROOT = Path(__file__).resolve().parents[1]


def main():
    wb = Workbench(ROOT / "outputs" / "smoke_price")
    wb.part = Path("no-part-needed")  # the price tools never touch ANSYS
    wb.call("set_material", {"material": "Aluminum Alloy"})
    agent = Agent(wb)
    print("model:", MODEL)
    for ev in agent.run("The part is in the library's 'Aluminum Alloy'. I want to minimise cost later. "
                        "What price per kg will you use? Look it up and record it."):
        if ev["type"] == "text":
            print("AGENT:", ev["text"])
        elif ev["type"] == "tool":
            print("TOOL:", ev["name"], ev["input"])
        else:
            print(ev["type"].upper() + ":", ev.get("name", ""), str(ev.get("content", ev.get("text", "")))[:400])
    print("cache entry:", prices.get("Aluminum Alloy"))
    print("workbench price:", wb.price)


if __name__ == "__main__":
    main()
