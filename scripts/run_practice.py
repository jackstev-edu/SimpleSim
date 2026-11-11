r"""Run the practice set against the live agent (ANSYS + API) and grade it.

From the repo root:
  set PYTHONIOENCODING=utf-8 && ..\.venv\Scripts\python.exe -m scripts.run_practice --label before
  ..\.venv\Scripts\python.exe -m scripts.run_practice --label after --only lbr_clean_auto
  ..\.venv\Scripts\python.exe -m scripts.run_practice --compare practice/results/A practice/results/B
Uses SIMPLESIM_MODE for the agent (default debug = Haiku); the simulated user is always Haiku.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import anthropic

from src.agent.agent import Agent
from src.eval.grader import grade
from src.eval.problems import PROBLEMS_DIR, REPO, load_effects, load_problems, resolve_part
from src.eval.report import compare_markdown, write_results
from src.eval.runner import RecordingWorkbench, run_problem
from src.eval.sim_user import SimUser

RESULTS = REPO / "practice" / "results"
# USD per million tokens: input, output, cache read, cache write (5 min); same table as smoke_agent.py
PRICES = {"claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
          "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50)}
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def counted_client(usage: dict) -> anthropic.Anthropic:
    client = anthropic.Anthropic()
    create = client.messages.create

    def counted(**kwargs):
        r = create(**kwargs)
        u = usage.setdefault(kwargs["model"], dict.fromkeys(USAGE_KEYS, 0))
        for k in USAGE_KEYS:
            u[k] += getattr(r.usage, k, 0) or 0
        return r

    client.messages.create = counted
    return client


def cost(usage: dict) -> float:
    total = 0.0
    for model, u in usage.items():
        p = PRICES[model]
        total += sum(u[k] * p[i] for i, k in enumerate(USAGE_KEYS)) / 1e6
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = ap.parse_args()
    if args.compare:
        after = Path(args.compare[1])
        (after / "compare.md").write_text(compare_markdown(Path(args.compare[0]), after), encoding="utf-8")
        print((after / "compare.md").read_text(encoding="utf-8"))
        return

    problems = [p for p in load_problems(PROBLEMS_DIR) if not args.only or p.id in args.only]
    out = RESULTS / f"{time.strftime('%Y%m%d-%H%M')}-{args.label}"
    usage: dict = {}
    client = counted_client(usage)
    results = []
    for p in problems:
        part = resolve_part(p)
        effects = load_effects(p)
        if part is None:
            print(f"[{p.id}] skipped: part file missing ({p.part})")
            results.append((None, grade(p, effects, None)))
            continue
        print(f"[{p.id}] running on {part.name} ...", flush=True)
        t0 = time.time()
        wb = RecordingWorkbench(out / p.id / "ws")
        try:
            log = run_problem(p, part, wb, Agent(wb, client), SimUser(p, client), args.label)
        finally:
            wb.close()
        log["seconds"] = round(time.time() - t0, 1)
        checklist = grade(p, effects, log)
        results.append((log, checklist))
        fails = [n for n, c in checklist["checks"].items() if c["result"] == "fail"]
        print(f"[{p.id}] end={log['end']} status={checklist['status']} fails={fails} ({log['seconds']} s)", flush=True)
        write_results(out, results)  # keep partial results if a later problem hangs
    write_results(out, results)
    print(f"\nresults: {out}\nusage: {usage}\nestimated cost: ${cost(usage):.4f}")


if __name__ == "__main__":
    main()
