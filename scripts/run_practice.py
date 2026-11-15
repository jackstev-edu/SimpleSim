r"""Run the practice set against the live agent (ANSYS + API) and grade it.

From the repo root:
  set PYTHONIOENCODING=utf-8 && ..\.venv\Scripts\python.exe -m scripts.run_practice --label before
  ..\.venv\Scripts\python.exe -m scripts.run_practice --label after --only lbr_clean_auto
  ..\.venv\Scripts\python.exe -m scripts.run_practice --compare practice/results/A practice/results/B
  ..\.venv\Scripts\python.exe -m scripts.run_practice --regrade practice/results/A
Uses SIMPLESIM_MODE for the agent (default debug = gpt-6-luna); the simulated user is always gpt-6-luna.
Stops at --budget USD (default 0.20) and adds the run to practice/spend.csv. Needs OPENAI_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from src.agent.agent import Agent
from src.eval.grader import grade
from src.eval.problems import PROBLEMS_DIR, REPO, load_effects, load_problems, resolve_part
from src.eval.report import compare_markdown, write_results
from src.eval.runner import RecordingWorkbench, run_problem
from src.eval.sim_user import SimUser
from src.llm import LEDGER, add_usage, append_spend, cost, counted_client

RESULTS = REPO / "practice" / "results"
BUDGET_USD = 0.20  # hard cap per run (decision 2026-11-15); the course key must last until deployment


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--regrade", metavar="RESULTS", help="grade a results folder's saved run logs again, in place")
    ap.add_argument("--budget", type=float, default=BUDGET_USD, help="hard cap on API spend for this run, USD")
    ap.add_argument("--out", help=argparse.SUPPRESS)  # results folder, set by the parent run for its children
    args = ap.parse_args()
    if args.compare:
        after = Path(args.compare[1])
        (after / "compare.md").write_text(compare_markdown(Path(args.compare[0]), after), encoding="utf-8")
        print((after / "compare.md").read_text(encoding="utf-8"))
        return

    problems = [p for p in load_problems(PROBLEMS_DIR) if not args.only or p.id in args.only]
    if args.regrade:  # saved run logs, today's grader and keys; no ANSYS, no API
        folder, results = Path(args.regrade), []
        for p in problems:
            saved = folder / f"{p.id}.json"
            if saved.is_file():
                run_log = json.loads(saved.read_text(encoding="utf-8"))["run_log"]
                results.append((run_log, grade(p, load_effects(p), run_log)))
        write_results(folder, results)
        print((folder / "summary.md").read_text(encoding="utf-8"))
        return
    out = Path(args.out) if args.out else RESULTS / f"{time.strftime('%Y%m%d-%H%M')}-{args.label}"
    usage: dict = {}
    if len(problems) > 1:
        # One process per problem: PyMechanical allows one embedded Mechanical per process, ever, so
        # a second Workbench in the same process fails every solve with "Cannot have more than one
        # embedded mechanical instance!" (seen 2026-11-11).
        results = []
        for p in problems:
            left = args.budget - cost(usage)
            if left <= 0:
                print(f"[{p.id}] skipped: budget cap ${args.budget:.2f} reached", flush=True)
                results.append((None, {"problem_id": p.id, "status": "not_graded",
                                       "reason": f"budget cap ${args.budget:.2f} reached", "checks": {}}))
                continue
            code = subprocess.run([sys.executable, "-m", "scripts.run_practice", "--label", args.label,
                                   "--only", p.id, "--out", str(out), "--budget", f"{left:.6f}"],
                                  cwd=REPO).returncode
            saved = out / f"{p.id}.json"
            if code == 0 and saved.is_file():
                d = json.loads(saved.read_text(encoding="utf-8"))
                results.append((d["run_log"], d["checklist"]))
            else:
                print(f"[{p.id}] runner process failed (exit {code})", flush=True)
                results.append((None, {"problem_id": p.id, "status": "not_graded",
                                       "reason": f"runner process failed (exit {code})", "checks": {}}))
            spent = out / p.id / "usage.json"  # also written when the child failed after some API calls
            if spent.is_file():
                add_usage(usage, json.loads(spent.read_text(encoding="utf-8")))
        write_results(out, results)
        report_spend(args, len(problems), out, usage)
        return
    client = counted_client(usage, args.budget)
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
            (out / p.id).mkdir(parents=True, exist_ok=True)
            (out / p.id / "usage.json").write_text(json.dumps(usage), encoding="utf-8")
        log["seconds"] = round(time.time() - t0, 1)
        checklist = grade(p, effects, log)
        results.append((log, checklist))
        fails = [n for n, c in checklist["checks"].items() if c["result"] == "fail"]
        print(f"[{p.id}] end={log['end']} status={checklist['status']} fails={fails} ({log['seconds']} s)", flush=True)
        write_results(out, results)  # keep partial results if a later problem hangs
    write_results(out, results)
    if args.out:  # a child of a full run: the parent reports and logs the spend
        print(f"usage: {usage}  cost: ${cost(usage):.4f}", flush=True)
    else:
        report_spend(args, len(problems), out, usage)


def report_spend(args, n_problems: int, out: Path, usage: dict) -> None:
    total = append_spend(LEDGER, args.label, n_problems, usage)
    print(f"\nresults: {out}\nusage: {usage}\ncost: ${cost(usage):.4f} (cap ${args.budget:.2f})"
          f"\nall runs so far (practice/spend.csv): ${total:.4f}")


if __name__ == "__main__":
    main()
