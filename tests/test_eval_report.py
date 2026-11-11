"""Results folder, summary table and before/after comparison."""
import json

from src.eval.grader import CHECKS
from src.eval.report import compare_markdown, summary_markdown, write_results


def checklist(pid, status="graded", results=None):
    results = results or {}
    return {"problem_id": pid, "status": status, "reason": None if status == "graded" else "key not reviewed",
            "checks": {c: {"result": results.get(c, "pass"), "detail": ""} for c in CHECKS} if status == "graded" else {}}


def test_summary_markdown_columns_and_totals():
    md = summary_markdown([checklist("a"), checklist("b", results={"setup_force": "fail", "obedience": "n/a"}),
                           checklist("c", status="not_graded")])
    lines = md.strip().splitlines()
    assert all(c in lines[0] for c in CHECKS) and "status" in lines[0]
    assert "| b | graded |" in md and "✗" in md and "–" in md
    assert "key not reviewed" in md
    total = [l for l in lines if l.startswith("| passed")][0]
    cells = [x.strip() for x in total.strip("|").split("|")]
    assert cells[2 + CHECKS.index("setup_force")] == "1/2" and cells[2 + CHECKS.index("obedience")] == "1/1"


def test_write_results_and_compare(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    write_results(before, [({"problem_id": "a"}, checklist("a", results={"feature_recall": "fail"}))])
    write_results(after, [({"problem_id": "a"}, checklist("a"))])
    assert json.loads((before / "a.json").read_text(encoding="utf-8"))["checklist"]["problem_id"] == "a"
    assert (before / "summary.md").is_file()
    md = compare_markdown(before, after)
    assert "| feature_recall | 0/1 | 1/1 |" in md and "a: feature_recall fail → pass" in md
