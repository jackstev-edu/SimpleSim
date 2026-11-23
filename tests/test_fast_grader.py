"""Fast-path benchmark grader on hand-built run logs (no LLM, no network)."""
import pytest

pytest.importorskip("yaml")  # Role 3 package (src/sim/requirements.txt); skip on a root-only install
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


def test_asked_needs_keyword_in_the_question():
    # I2: a statement about the load followed by an unrelated question is not asking about the load face
    events = [{"type": "text", "text": "Where is it bolted?"},
              {"type": "student", "text": "Back face."},
              {"type": "text", "text": "I've recorded the 500 N load at the free end. Which dimensions may the search change?"},
              {"type": "student", "text": "All of them."},
              *(e for e in GOOD_EVENTS[2:] if e["type"] == "tool")]  # no setup card shown
    out = grade_fast(problem(must_ask=("load_face",)), log(events=events))
    assert out["checks"]["asked"]["result"] == "fail"


def test_confirm_rejects_negated_agreement():
    # I3: "not right" is a correction, not agreement
    events = [e if e.get("text") != "Yes, that is right." else {**e, "text": "No, that's not right."} for e in GOOD_EVENTS]
    assert checks(problem(), log(events=events))["confirmed"] == "fail"


def test_confirm_allows_text_between_yes_and_confirm():
    # I3: the model may say "Confirming." in the same turn before calling confirm_setup
    events = [*GOOD_EVENTS[:6], {"type": "text", "text": "Great, confirming the setup now."}, *GOOD_EVENTS[6:]]
    assert checks(problem(), log(events=events))["confirmed"] == "pass"


def test_confirmed_setup_card_counts_as_asking_load_face():
    # spec: a setup card the student confirms counts as asking for the support and the load face
    events = [{"type": "text", "text": "Setup: back face fixed, 500 N down on the free-end face, steel. Is this setup correct?"},
              {"type": "student", "text": "Yes."},
              {"type": "tool", "name": "confirm_setup", "input": {}},
              {"type": "tool", "name": "optimize_design", "input": {}}]
    assert checks(problem(must_ask=("support", "load_face")), log(events=events))["asked"] == "pass"


def test_dense_gap_is_info_only():
    # user decision 2026-11-23: the key is the fast path's own 2,000-design search; the 400,000-design best is info
    p = problem()
    p.key["dense_optimum"] = {"objective_value": MASS / 1.20}
    out = grade_fast(p, log())
    assert out["checks"]["dense_gap"]["result"] == "info"
    assert out["pick"]["dense_gap"] == pytest.approx(0.20)
    assert out["checks"]["pick_gap"]["result"] == "pass"


def test_support_question_with_held():
    # real log 2026-11-23: "Which face is the bracket held by?" is a support question
    events = [{"type": "text", "text": "Which face is the bracket held by?"},
              {"type": "student", "text": "The back face."},
              *(e for e in GOOD_EVENTS[2:] if e["type"] == "tool")]
    assert checks(problem(must_ask=("support",)), log(events=events))["asked"] == "pass"
