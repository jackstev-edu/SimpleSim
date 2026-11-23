"""Run the fast-path benchmark (practice/fast) against the assistant with the fitted engine, then grade it.

    python -m scripts.run_fast_practice [--only ID ...] [--label L] [--budget 0.20]
    python -m scripts.run_fast_practice --regrade practice/results/fast-<stamp>

No ANSYS needed. Stops at --budget USD and adds the run to practice/spend.csv. Needs OPENAI_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from src.eval.fast_grader import grade_fast
from src.eval.fast_keys import REPO, load_fast_problems
from src.eval.fast_runner import regrade, run_fast_problem, summary_markdown
from src.llm import LEDGER, append_spend, cost, counted_client

BUDGET_USD = 0.20  # hard cap per run (decision 2026-11-15)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="problem ids")
    ap.add_argument("--label", default="", help="suffix for the results folder and ledger row")
    ap.add_argument("--budget", type=float, default=BUDGET_USD, help="hard cap on API spend for this run, USD")
    ap.add_argument("--regrade", help="grade the saved logs in this folder again, no API calls")
    args = ap.parse_args()
    problems = {p.id: p for p in load_fast_problems() if not args.only or p.id in args.only}

    if args.regrade:
        folder = Path(args.regrade)
        graded = regrade(folder, problems)
        (folder / "summary.md").write_text(summary_markdown(graded), encoding="utf-8")
        print(summary_markdown(graded))
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = REPO / "practice" / "results" / (f"fast-{stamp}" + (f"-{args.label}" if args.label else ""))
    out.mkdir(parents=True, exist_ok=True)
    usage: dict = {}
    client = counted_client(usage, args.budget)
    graded = []
    for p in problems.values():
        print(f"[{p.id}] running ...", flush=True)
        t0 = time.time()
        log = run_fast_problem(p, client, out)
        log["seconds"] = round(time.time() - t0, 1)
        (out / f"{p.id}.json").write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
        g = grade_fast(p, log)
        (out / f"{p.id}.graded.json").write_text(json.dumps(g, indent=2), encoding="utf-8")
        graded.append(g)
        fails = [n for n, c in g["checks"].items() if c["result"] == "fail"]
        print(f"[{p.id}] stopped={log['stopped']} fails={fails} cost so far ${cost(usage):.4f} ({log['seconds']} s)", flush=True)
        (out / "summary.md").write_text(summary_markdown(graded), encoding="utf-8")  # keep partial results
    total = append_spend(LEDGER, f"fast-{args.label or 'run'}", len(problems), usage)
    print(f"run cost ${cost(usage):.4f}; ledger total ${total:.4f}; results in {out}")


if __name__ == "__main__":
    main()
