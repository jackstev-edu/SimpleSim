"""Live check of the assistant front layer: one fast-path run and one hand-off, in one process.

(a) fast path on a known family, finalists solved in ANSYS: the gusseted L-bracket v1 by default
    (a synthetic .dsco with Role 1's parameter names and no physics), or the plain v0 L-bracket
    with --fast v0.
(b) hand-off of the saddle bracket to the ANSYS agent, driven to a baseline Mechanical solve:
    a slow-path solve after a fast-path solve in the same process, on the shared workbench.

Needs ANSYS 2026 R1 (SpaceClaim + Mechanical) and OPENAI_API_KEY. Close other programs first
(the commit-charge limit stops MAPDL from starting). Spend cap $0.20; the cost goes to practice/spend.csv.
Run from the SimpleSim root:  PYTHONIOENCODING=utf-8 ..\\.venv\\Scripts\\python.exe -m scripts.smoke_assistant
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.assistant.session import AssistantSession
from src.llm import LEDGER, append_spend, cost, counted_client
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.families import FAMILIES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "smoke_assistant"
L_BRACKET = ROOT / "examples" / "l_bracket.scdocx"
SADDLE = ROOT / "examples" / "pyansys" / "saddle_bracket.scdoc"
_COMMON = [
    "It is bolted at the back face. 500 N pushes straight down on the free end.",
    "Structural steel.",
    "Yes, a safety factor of 1.5 is fine. Make it as light as possible. Any dimension may change.",
    "Yes, the setup is right. Go ahead.",
    "Yes, run the search.",
    "Go ahead.",
]
FAST_TURNS = {  # a scripted beginner; the run stops early once a result exists
    "v0": ["Yes, it is the L-bracket.", *_COMMON],
    "v1": ["Yes, it is the gusseted L-bracket.", *_COMMON],
}
SADDLE_NOT_L = "No, it is a saddle bracket, not the L-bracket."
SLOW_TURNS = [  # stops once the saddle has a solved design
    SADDLE_NOT_L,
    "Both small bolt holes are fixed. 20 N pushes in the -Y direction on the large hole. "
    "Structural steel, a safety factor of 1.5, and make it as light as possible.",
    "Yes, that setup is right. Solve the baseline design now; do not start a search yet.",
    "Go ahead with the baseline solve.",
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


def fast_part(which: str) -> Path:
    if which == "v0":
        return L_BRACKET
    return write_demo_part(OUT / "gusset_v1_demo.dsco", family=FAMILIES["l_bracket_gusset_v1"], physics=False)


def solved_designs(s: AssistantSession) -> list[dict]:
    wb = s.workbench
    return [] if wb is None else [d for d in wb.designs.values() if d["results"].get("max_stress_MPa", 0) > 0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", choices=("v0", "v1"), default="v1", help="family for the fast-path run")
    args = ap.parse_args()
    usage: dict = {}
    s = AssistantSession(counted_client(usage, 0.20), OUT)
    checks = {}
    try:
        part = fast_part(args.fast)
        print(f"== (a) fast path: {part.name}")
        for ev in s.upload(part):
            show(ev)
        for turn in FAST_TURNS[args.fast]:
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
            want = "l_bracket_gusset_v1" if args.fast == "v1" else "l_bracket_v0"
            checks[f"spec family is {want}"] = spec.get("bracket_family", "l_bracket_v0") == want

        print(f"== (b) hand-off + baseline solve: {SADDLE.name}")
        for ev in s.upload(SADDLE):
            show(ev)
        for turn in SLOW_TURNS:
            if solved_designs(s):
                break
            if turn == SADDLE_NOT_L and s.path == "slow":
                continue  # already handed off without the "which bracket" question
            print(f"USER: {turn}")
            for ev in s.send(turn):
                show(ev)
        checks["saddle went to ANSYS"] = s.path == "slow"
        checks["ANSYS agent answered"] = s.agent is not None
        solved = solved_designs(s)
        for d in solved:
            r = d["results"]
            print(f"  saddle {d['name']}: {r['max_stress_MPa']:.1f} MPa, {r['max_def_mm']:.4f} mm, "
                  f"balance error {r['balance_error']:.2%}")
        checks["slow-path Mechanical solve after the fast path"] = bool(solved)
    finally:
        s.shutdown()
        total = append_spend(LEDGER, "assistant-smoke", 2, usage)
        print(f"cost ${cost(usage):.4f}; ledger total ${total:.4f}")
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")


if __name__ == "__main__":
    main()
