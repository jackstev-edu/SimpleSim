"""Item 4: every question, answer, assumption, shortlist and rejected finalist is logged with its reason."""
import json

from src.assistant.decision_log import DecisionLog
from tests.test_assistant_numbers import RESULT

FINALISTS = [
    {"design_id": "a", "feasible": True, "violated": [], "mass_kg": 0.066, "safety_factor": 2.27},
    {"design_id": "b", "feasible": False, "violated": ["safety_factor >= 2"], "mass_kg": 0.060, "safety_factor": 1.9},
]


def test_entries_keep_order_kind_and_data():
    log = DecisionLog()
    log.add("question", "Which material?")
    log.add("answer", "Structural steel")
    log.add("assumption", "Mounting face fully fixed", source="user")
    kinds = [e["kind"] for e in log.entries]
    assert kinds == ["question", "answer", "assumption"]
    assert [e["seq"] for e in log.entries] == [0, 1, 2]
    assert log.entries[2]["data"] == {"source": "user"}


def test_unknown_kind_is_refused():
    import pytest
    with pytest.raises(ValueError, match="kind"):
        DecisionLog().add("musing", "x")


def test_result_logs_shortlist_finalists_rejections_and_pick():
    result = {**RESULT, "finalists": FINALISTS, "recommended": {**RESULT["recommended"], "design_id": "a"}}
    log = DecisionLog()
    log.add_result(result, shortlist_ids=["a", "b", "c"])
    by_kind = {}
    for e in log.entries:
        by_kind.setdefault(e["kind"], []).append(e)
    assert by_kind["shortlist"][0]["data"]["design_ids"] == ["a", "b", "c"]
    rejected = by_kind["rejected"]
    assert len(rejected) == 1 and rejected[0]["data"]["design_id"] == "b"
    assert "safety_factor >= 2" in rejected[0]["text"]
    assert by_kind["recommendation"][0]["data"]["design_id"] == "a"
    assert "binding" in by_kind["recommendation"][0]["text"]


def test_feasible_but_not_picked_finalist_is_rejected_with_reason():
    heavier = {"design_id": "c", "feasible": True, "violated": [], "mass_kg": 0.080, "safety_factor": 2.6}
    result = {**RESULT, "finalists": FINALISTS[:1] + [heavier], "recommended": {**RESULT["recommended"], "design_id": "a"}}
    log = DecisionLog()
    log.add_result(result)
    rej = [e for e in log.entries if e["kind"] == "rejected"]
    assert rej[0]["data"]["design_id"] == "c" and "worse objective" in rej[0]["text"]


def test_save_writes_jsonl_and_readable_markdown(tmp_path):
    log = DecisionLog()
    log.add("question", "Which material?")
    log.add("assumption", "1.5 safety factor accepted by the user")
    log.save(tmp_path / "decision_log")
    lines = (tmp_path / "decision_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["kind"] for x in lines] == ["question", "assumption"]
    md = (tmp_path / "decision_log.md").read_text(encoding="utf-8")
    assert "| 0 | question | Which material? |" in md
