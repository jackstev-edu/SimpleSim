"""Live check of the assistant front layer: one fast-path run and one hand-off, in one process.

Needs ANSYS 2026 R1 (SpaceClaim + Mechanical) and OPENAI_API_KEY. Close other programs first
(the commit-charge limit stops MAPDL from starting). Spend cap $0.20; the cost goes to practice/spend.csv.
Run from the SimpleSim root:  PYTHONIOENCODING=utf-8 ..\\.venv\\Scripts\\python.exe -m scripts.smoke_assistant
"""
from __future__ import annotations

import json
from pathlib import Path

from src.assistant.session import AssistantSession
from src.llm import LEDGER, append_spend, cost, counted_client

ROOT = Path(__file__).resolve().parents[1]
L_BRACKET = ROOT / "examples" / "l_bracket.scdocx"
SADDLE = ROOT / "examples" / "pyansys" / "saddle_bracket.scdoc"
FAST_TURNS = [  # a scripted beginner; the run stops early once a result exists
    "Yes, it is the L-bracket.",
    "It is bolted at the back face. 500 N pushes straight down on the free end.",
    "Structural steel.",
    "Yes, a safety factor of 1.5 is fine. Make it as light as possible. Any dimension may change.",
    "Yes, the setup is right. Go ahead.",
    "Yes, run the search.",
    "Go ahead.",
]


def show(ev: dict) -> None:
    kind = ev["type"]
    if kind in ("text", "error"):
        print(f"  [{kind}] {ev['text']}")
    elif kind == "path":
        print(f"  [PATH {ev['path']}] {ev['reason']}")
    elif kind == "tool":
        print(f"  [tool] {ev['name']} {json.dumps(ev['input'])[:200]}")
    elif kind == "tool_result":
        print(f"  [{'ERR' if ev['is_error'] else 'ok'}] {ev['name']}: {str(ev['content'])[:300]}")


def main() -> None:
    usage: dict = {}
    s = AssistantSession(counted_client(usage, 0.20), ROOT / "outputs" / "smoke_assistant")
    checks = {}
    try:
        print(f"== (a) fast path: {L_BRACKET.name}")
        for ev in s.upload(L_BRACKET):
            show(ev)
        for turn in FAST_TURNS:
            if s.path == "slow" or s.front.last_run_dir is not None:
                break
            print(f"USER: {turn}")
            for ev in s.send(turn):
                show(ev)
        run = s.front.last_run_dir if s.front else None
        checks["fast path kept"] = s.path == "fast"
        checks["run folder written"] = run is not None
        if run is not None:
            result = json.loads((run / "result.json").read_text(encoding="utf-8"))
            sources = {f.get("source") for f in result.get("finalists", [])}
            print(f"  run folder {run}; finalist sources {sources}")
            checks["finalists solved in ANSYS"] = sources == {"ansys_mechanical"}
            spec = json.loads((run / "study_spec.json").read_text(encoding="utf-8"))
            checks["SF 1.5 reached the spec"] = any(c["metric"] == "safety_factor" and c["value"] == 1.5
                                                    for c in spec["constraints"])

        print(f"== (b) hand-off: {SADDLE.name}")
        for ev in s.upload(SADDLE):
            show(ev)
        checks["saddle went to ANSYS"] = s.path == "slow"
    finally:
        s.shutdown()
        total = append_spend(LEDGER, "assistant-smoke", 2, usage)
        print(f"cost ${cost(usage):.4f}; ledger total ${total:.4f}")
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")


if __name__ == "__main__":
    main()
