"""Practice results on disk: one JSON per problem, a summary table, a before/after comparison."""
from __future__ import annotations

import json
from pathlib import Path

from src.eval.grader import CHECKS

MARK = {"pass": "✓", "fail": "✗", "n/a": "–"}


def summary_markdown(checklists: list[dict]) -> str:
    head = "| problem | status | " + " | ".join(CHECKS) + " |"
    rows = [head, "|" + "---|" * (len(CHECKS) + 2)]
    passed, counted = dict.fromkeys(CHECKS, 0), dict.fromkeys(CHECKS, 0)
    notes = []
    for c in checklists:
        if c["status"] != "graded":
            rows.append(f"| {c['problem_id']} | {c['status']} | " + " | ".join("" for _ in CHECKS) + " |")
            notes.append(f"- {c['problem_id']}: {c['status']} ({c.get('reason')})")
            continue
        cells = []
        for name in CHECKS:
            r = c["checks"][name]["result"]
            cells.append(MARK[r])
            if r != "n/a":
                counted[name] += 1
                passed[name] += r == "pass"
        rows.append(f"| {c['problem_id']} | graded | " + " | ".join(cells) + " |")
    rows.append("| passed | | " + " | ".join(f"{passed[n]}/{counted[n]}" for n in CHECKS) + " |")
    return "\n".join(rows + ([""] + notes if notes else [])) + "\n"


def write_results(folder: Path, results: list[tuple[dict | None, dict]]) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for run_log, checklist in results:
        path = folder / f"{checklist['problem_id']}.json"
        path.write_text(json.dumps({"run_log": run_log, "checklist": checklist}, indent=1, default=str),
                        encoding="utf-8")
    (folder / "summary.md").write_text(summary_markdown([c for _, c in results]), encoding="utf-8")


def _load(folder: Path) -> dict[str, dict]:
    out = {}
    for p in sorted(Path(folder).glob("*.json")):
        c = json.loads(p.read_text(encoding="utf-8"))["checklist"]
        out[c["problem_id"]] = c
    return out


def _counts(checklists: dict[str, dict]) -> dict[str, tuple[int, int]]:
    out = {}
    for name in CHECKS:
        rs = [c["checks"][name]["result"] for c in checklists.values() if c["status"] == "graded"]
        rs = [r for r in rs if r != "n/a"]
        out[name] = (sum(r == "pass" for r in rs), len(rs))
    return out


def compare_markdown(before: Path, after: Path) -> str:
    b, a = _load(before), _load(after)
    cb, ca = _counts(b), _counts(a)
    rows = [f"# Practice set: {Path(before).name} → {Path(after).name}", "",
            "| check | before | after |", "|---|---|---|"]
    rows += [f"| {n} | {cb[n][0]}/{cb[n][1]} | {ca[n][0]}/{ca[n][1]} |" for n in CHECKS]
    changes = []
    for pid in sorted(set(b) & set(a)):
        if b[pid]["status"] != "graded" or a[pid]["status"] != "graded":
            if b[pid]["status"] != a[pid]["status"]:
                changes.append(f"- {pid}: status {b[pid]['status']} → {a[pid]['status']}")
            continue
        for n in CHECKS:
            rb, ra = b[pid]["checks"][n]["result"], a[pid]["checks"][n]["result"]
            if rb != ra:
                changes.append(f"- {pid}: {n} {rb} → {ra}")
    return "\n".join(rows + ["", "## Changed results", ""] + (changes or ["(none)"])) + "\n"
