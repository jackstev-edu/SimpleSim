"""Fast-path benchmark grader on hand-built run logs (no LLM, no network)."""
from pathlib import Path

import pytest

from src.eval.fast_grader import PICK_GAP_PASS, grade_fast
from src.eval.fast_keys import FastProblem, model_eval
from src.pydiscovery.families import FAMILIES

FAM = "l_bracket_v0"
BASE = dict(FAMILIES[FAM].baseline_mm)
DESIGN = {**BASE, "thickness_mm": 7.0}
MASS = model_eval(FAM, "structural_steel", 500.0, DESIGN)["mass_kg"]
STUDY = {"force_n": 500.0, "angle_deg": -90.0, "material_key": "structural_steel", "safety_factor": 1.5,
         "objective": {"metric": "mass_kg", "direction": "minimize"}, "limits": [], "vary": ["all"]}


def problem(study=STUDY, must_ask=("support", "load_face", "safety_factor"), optimum=MASS, handoff=False):
    key = {"study": study, "must_ask": list(must_ask), "expect_handoff": handoff,
           "model_optimum": {"design": DESIGN, "objective_value": optimum},
           "sim_optimum": {"design_id": "formal_x", "objective_value": optimum * 1.2}}
    return FastProblem("p", FAM, "req", "facts", key, Path("p.yaml"))


def spec(force=500.0, angle=-90.0, sf=1.5, material="structural_steel", vary=None, limits=()):
    vary = set(FAMILIES[FAM].ranges_mm) if vary is None else set(vary)
    params = [{"name": k, "low_mm": lo, "high_mm": hi} if k in vary else {"name": k, "low_mm": BASE[k], "high_mm": BASE[k]}
              for k, (lo, hi) in FAMILIES[FAM].ranges_mm.items()]
    cons = [{"metric": "safety_factor", "op": ">=", "value": sf}, *limits]
    return {"bracket_family": FAM, "objective": {"metric": "mass_kg", "direction": "minimize"}, "constraints": cons,
            "parameters": params, "load": {"force_n": force, "angle_deg": angle}, "material_key": material}


ASK = "Where is it bolted, which face carries the load, and what safety factor do you want? 1.5 is common."
GOOD_EVENTS = [
    {"type": "text", "text": ASK},
    {"type": "student", "text": "Back face bolted to the wall, load on the free end. 1.5 is fine."},
    {"type": "tool", "name": "set_support", "input": {}},
    {"type": "tool", "name": "record_study_inputs", "input": {"safety_factor": 1.5}},
    {"type": "text", "text": "Setup: back face fixed, 500 N down on the free end, steel, SF 1.5. Is this right?"},
    {"type": "student", "text": "Yes, that is right."},
    {"type": "tool", "name": "confirm_setup", "input": {}},
    {"type": "tool", "name": "optimize_design", "input": {}},
]


def log(events=GOOD_EVENTS, result_spec=None, recommended=DESIGN, result=True, extra=()):
    ev = [dict(e, seq=i) for i, e in enumerate([*events, *extra])]
    res = None
    if result:
        res = {"spec": result_spec or spec(), "recommended": None if recommended is None else {"design": recommended}}
    return {"id": "p", "events": ev, "result": res, "stopped": None}


def checks(p, lg):
    return {k: v["result"] for k, v in grade_fast(p, lg)["checks"].items()}


def test_clean_run_passes_all():
    out = grade_fast(problem(), log())
    assert all(c["result"] in ("pass", "info", "n/a") for c in out["checks"].values()), out["checks"]
    assert out["checks"]["handoff"]["result"] == "pass"
    assert out["pick"]["gap"] == pytest.approx(0.0, abs=1e-9)


def test_wrong_force_fails_force_only():
    c = checks(problem(), log(result_spec=spec(force=450.0)))
    assert c["force"] == "fail" and c["material"] == "pass" and c["safety_factor"] == "pass"


def test_vary_accepts_labels():
    p = problem(study={**STUDY, "vary": ["Thickness", "Arm width"]})
    assert checks(p, log(result_spec=spec(vary=["thickness_mm", "arm_width_mm"])))["vary"] == "pass"
    assert checks(p, log(result_spec=spec(vary=["thickness_mm"])))["vary"] == "fail"


def test_sf_asked_needs_own_answer():
    events = [{"type": "tool", "name": "record_study_inputs", "input": {"safety_factor": 1.5}}, *GOOD_EVENTS]
    assert checks(problem(), log(events=events))["asked"] == "fail"


def test_confirm_before_yes_fails():
    events = [e for e in GOOD_EVENTS if e.get("text") != "Yes, that is right."]
    assert checks(problem(), log(events=events))["confirmed"] == "fail"


def test_silent_sf_default():
    c = checks(problem(), log(result_spec=spec(sf=2.0)))
    assert c["no_silent_default"] == "fail" and c["safety_factor"] == "fail"


def test_pick_gap_threshold():
    assert PICK_GAP_PASS == 0.05
    assert checks(problem(optimum=MASS / 1.04), log())["pick_gap"] == "pass"
    assert checks(problem(optimum=MASS / 1.06), log())["pick_gap"] == "fail"


def test_no_recommendation():
    c = checks(problem(), log(recommended=None))
    assert c["feasible"] == "fail" and c["pick_gap"] == "fail"


def test_no_result_fails_cleanly():
    out = grade_fast(problem(), {**log(result=False), "stopped": "reply cap"})
    assert out["checks"]["force"]["result"] == "fail" and "reply cap" in out["checks"]["force"]["detail"]
    assert out["checks"]["pick_gap"]["result"] == "fail" and out["pick"]["gap"] is None


def test_handoff_problem():
    p = problem(handoff=True)
    lg = log(events=[{"type": "path", "path": "slow", "reason": "titanium"}], result=False)
    c = checks(p, lg)
    assert c["handoff"] == "pass" and c["force"] == "n/a" and c["pick_gap"] == "n/a"
    assert checks(p, log())["handoff"] == "fail"  # it optimized on the fast path instead


def test_sim_gap_is_info_only():
    out = grade_fast(problem(), log())
    assert out["checks"]["sim_gap"]["result"] == "info"
    assert out["pick"]["sim_gap"] == pytest.approx(MASS / (MASS * 1.2) - 1)


def test_unexpected_handoff_fails():
    lg = log(events=[*GOOD_EVENTS[:2], {"type": "path", "path": "slow", "reason": "direction"}], result=False)
    assert checks(problem(), lg)["handoff"] == "fail"
