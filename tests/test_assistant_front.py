"""FrontAgent: Jack's tools behind the input guard, with the hand-off decided in code."""
import pytest

# Role 3 packages live in src/sim/requirements.txt, not the root one, so CI skips this file.
pytest.importorskip("ansys.geometry.core")
pytest.importorskip("ansys.mechanical.core")
pytest.importorskip("ansys.optislang.core")

import json


import src.assistant.front as front
from src.assistant.front import PROMPT, FrontAgent
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.tools import PyDiscoveryTools
from tests.test_agent_loop import ScriptClient, call, msg, resp

SF2 = {"metric": "safety_factor", "op": ">=", "value": 2.0}
INPUTS = {"safety_factor": 1.5, "objective": "mass_kg", "direction": "minimize"}


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(front, "explain", lambda result, client, model=None: ("Design X won.", []))

    def _make(*script):
        agent = FrontAgent(PyDiscoveryTools(backend="formula"), ScriptClient(*script), tmp_path / "runs")
        assert agent.call_tool("open_part", {"path": str(write_demo_part(tmp_path / "l.dsco"))})["ok"]
        return agent
    return _make


def _events(agent, text="go"):
    return list(agent.run(text))


def _handoffs(events):
    return [e for e in events if e["type"] == "handoff"]


def test_set_load_outside_family_hands_off(make):
    agent = make(resp(call("set_load", {"force_n": 1500})))
    hand = _handoffs(_events(agent))
    assert len(hand) == 1 and "1500" in hand[0]["reason"]
    assert agent.tools.part.force_n == 500.0


def test_material_outside_fast_table_hands_off(make):
    agent = make(resp(call("set_material", {"material": "titanium"})))
    hand = _handoffs(_events(agent))
    assert len(hand) == 1 and "titanium" in hand[0]["reason"]


def test_switch_to_ansys_tool_hands_off(make):
    agent = make(resp(call("switch_to_ansys", {"reason": "the user asked for ANSYS"})))
    hand = _handoffs(_events(agent))
    assert hand == [{"type": "handoff", "reason": "the user asked for ANSYS"}]


def test_optimize_blocked_without_sf(make):
    agent = make(resp(call("optimize_design", {})))
    res = [e for e in _events(agent) if e["type"] == "tool_result"]
    assert res[0]["is_error"] and "a safety factor" in res[0]["content"]
    assert agent.tools.part.last_result is None


def test_model_constraints_are_replaced(make):
    agent = make(resp(call("record_study_inputs", INPUTS, "a")), resp(call("confirm_setup", {}, "b")),
                 resp(call("optimize_design", {"constraints": [SF2], "objective": "max_disp_mm"}, "c")))
    sent = []
    real = agent.tools.call
    agent.tools.call = lambda name, args=None: (sent.append((name, args)), real(name, args))[1]
    _events(agent)
    opt = [a for n, a in sent if n == "optimize_design"]
    assert opt and opt[0]["constraints"] == [{"metric": "safety_factor", "op": ">=", "value": 1.5}]
    assert opt[0]["objective"] == "mass_kg" and opt[0]["direction"] == "minimize"


def test_optimize_writes_run_folder(make, tmp_path):
    agent = make(resp(call("record_study_inputs", INPUTS, "a")), resp(call("confirm_setup", {}, "b")),
                 resp(call("optimize_design", {}, "c")))
    res = [e for e in _events(agent) if e["type"] == "tool_result" and e["name"] == "optimize_design"]
    assert not res[0]["is_error"] and "Design X won." in res[0]["content"]
    run = agent.last_run_dir
    assert run is not None and run.parent == tmp_path / "runs"
    for name in ("study_spec.json", "result.json", "decision_log.jsonl", "decision_log.md", "explanation.txt"):
        assert (run / name).exists(), name
    spec = json.loads((run / "study_spec.json").read_text(encoding="utf-8"))
    assert any(c["metric"] == "safety_factor" and c["value"] == 1.5 for c in spec["constraints"])


def test_failed_optimize_writes_no_run_folder(make, tmp_path):
    agent = make(resp(call("record_study_inputs", INPUTS, "a")), resp(call("confirm_setup", {}, "b")),
                 resp(call("optimize_design", {}, "c")))
    real = agent.tools.call
    agent.tools.call = lambda name, args=None: ({"ok": False, "message": "engine missing"}
                                                if name == "optimize_design" else real(name, args))
    res = [e for e in _events(agent) if e["type"] == "tool_result" and e["name"] == "optimize_design"]
    assert res[0]["is_error"] and "engine missing" in res[0]["content"]
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_text_is_streamed(make):
    agent = make(resp(msg("Which material?")))
    assert {"type": "text", "text": "Which material?"} in _events(agent)


def test_prompt_rules():
    for want in ("1.5", "confirm_setup", "use ANSYS", "L-bracket"):
        assert want in PROMPT


def test_values_the_user_gave_are_tracked(make):
    agent = make(resp(call("set_load", {"force_n": 500})), resp(call("set_material", {"material": "structural_steel"}, "b")))
    _events(agent)
    assert agent.given == {"force_n": 500, "material": "structural_steel"}


def test_triggering_value_is_tracked(make):
    agent = make(resp(call("set_load", {"force_n": 1500, "direction": [0, 0, -1]})))
    _events(agent)
    assert agent.given == {"force_n": 1500, "direction": [0, 0, -1]}


def test_explain_failure_keeps_the_result(make, monkeypatch):
    def boom(result, client, model=None):
        raise RuntimeError("budget cap reached")
    monkeypatch.setattr(front, "explain", boom)
    agent = make(resp(call("record_study_inputs", INPUTS, "a")), resp(call("confirm_setup", {}, "b")),
                 resp(call("optimize_design", {}, "c")))
    res = [e for e in _events(agent) if e["type"] == "tool_result" and e["name"] == "optimize_design"]
    assert not res[0]["is_error"]
    run = agent.last_run_dir
    assert (run / "result.json").exists() and (run / "explanation.txt").read_text(encoding="utf-8")
    assert any(e["kind"] == "warning" and "budget cap reached" in e["text"] for e in agent.log.entries)


def test_conversation_is_logged(make):
    agent = make(resp(msg("Which material?")))
    _events(agent, "It is bolted at the back.")
    kinds = [(e["kind"], e["text"]) for e in agent.log.entries]
    assert ("answer", "It is bolted at the back.") in kinds and ("question", "Which material?") in kinds


def test_prompt_switches_for_other_materials():
    assert "another material" in PROMPT and "switch_to_ansys" in PROMPT.split("another material")[1][:200]


def test_open_part_is_not_offered_to_the_model():
    """The session opens the part by its full path; the model reopening it by a bare name broke the live run."""
    assert "open_part" not in {f["name"] for f in front.FUNCTIONS}


def test_prompt_switches_when_not_the_l_bracket():
    tail = PROMPT.split("not the L-bracket")[1][:120]
    assert "switch_to_ansys" in tail


def test_prompt_uses_the_family_face_text_for_support():
    # gusset smoke 11-21: "back face" was refused by set_support, which wants "back face at X = 0"
    p = " ".join(PROMPT.split())
    assert "exact face text describe_setup shows" in p
