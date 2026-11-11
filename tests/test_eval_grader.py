"""Grader checklist on hand-written run logs (no ANSYS, no API)."""
import copy

from src.eval.grader import CHECKS, grade
from src.eval.problems import Problem


def cyl(fid, x, r=4.0):
    return {"id": fid, "type": "cylinder", "radius_mm": r, "axis_point_mm": [x, 0.0, 0.0], "axis_dir": [0.0, 0.0, 1.0]}


FACES = [cyl("h1a", 10), cyl("h1b", 10), cyl("h2a", 50), cyl("h2b", 50),
         {"id": "w1", "type": "plane"}, {"id": "w2", "type": "plane"}, {"id": "w3", "type": "plane"},
         {"id": "end", "type": "plane"}]
KEY = {
    "setup": {"fixed_face_ids": ["h1a", "h1b"], "load_face_ids": ["end"], "force_N": [0, -500, 0],
              "material": "Structural Steel", "safety_factor": 1.5},
    "must_ask": [], "must_flag": [], "objective": "mass_kg",
    "limits": [{"response": "safety_factor", "op": ">=", "value": 1.5}],
    "features": [
        {"id": "wall", "face_id": "w1", "action": "offset", "alt_face_ids": ["w2"], "min_value": None, "max_value": None},
        {"id": "corner", "face_id": "w1", "other_face_id": "w3", "action": "fillet", "alt_face_ids": [],
         "min_value": 0.5, "max_value": 6},
        {"id": "hole2", "face_id": "h2a", "action": "radius", "alt_face_ids": []},
    ],
}
EFFECTS = {"reviewed": True, "effects": {"wall": "controls_limit", "corner": "controls_limit", "hole2": "negligible"}}
GOOD_LC = {"fixed_face_ids": ["h1a"], "load_face_ids": ["end"], "force_N": [0, -500, 0]}
GOOD_FEATS = [{"face_id": "w2", "action": "offset", "low": -1, "high": 2},
              {"face_id": "w3", "action": "fillet", "other_face_id": "w1", "low": 1, "high": 4}]


def problem(kind="clean_auto", named=(), **key_changes):
    key = copy.deepcopy(KEY)
    key.update(key_changes)
    return Problem(id="p", part="x", kind=kind, request="r", hidden_facts="h", user_named_features=list(named), key=key)


def log(*events, proposal_feats=None, tool="first_look", end="proposal"):
    evs = [dict(e, seq=i) for i, e in enumerate(events)]
    proposal = None
    if proposal_feats is not None:
        proposal = {"tool": tool, "input": {"features": proposal_feats}}
        evs.append({"seq": len(evs), "type": "tool_call", "name": tool, "input": proposal["input"]})
    return {"problem_id": "p", "faces": FACES, "events": evs, "proposal": proposal if end == "proposal" else None,
            "end": end, "error": None}


def say(text):
    return {"type": "agent_text", "text": text}


def call(name, **inp):
    return {"type": "tool_call", "name": name, "input": inp}


def good_log(feats=GOOD_FEATS, **kw):
    return log(say("Safety factor 1.5 (a default you can change)."), call("set_load_case", **GOOD_LC),
               proposal_feats=feats, **kw)


def res(checklist, name):
    return checklist["checks"][name]["result"]


def test_all_checks_present_and_good_run_passes():
    c = grade(problem(), EFFECTS, good_log())
    assert c["status"] == "graded" and list(c["checks"]) == list(CHECKS)
    for name in ("setup_fixed_faces", "setup_load_faces", "setup_force", "setup_material", "safety_factor_stated",
                 "feature_recall", "ranges_valid", "needed_correction", "extra_negligible"):
        assert res(c, name) == "pass", name
    for name in ("asked_before_acting", "flagged_before_acting", "obedience", "complex_part"):
        assert res(c, name) == "n/a", name


def test_no_run_log_is_skipped():
    assert grade(problem(), EFFECTS, None)["status"] == "skipped"


def test_unreviewed_key_not_graded():
    c = grade(problem(), {"reviewed": False, "effects": {}}, good_log())
    assert c["status"] == "not_graded" and c["reason"] == "key not reviewed"


def test_error_run_not_graded_with_reason():
    lg = good_log()
    lg.update(end="error", error="MeshTooLarge: 152000 nodes")
    c = grade(problem(), EFFECTS, lg)
    assert c["status"] == "not_graded" and "MeshTooLarge" in c["reason"]


def test_turn_cap_fails_proposal_checks_without_crash():
    c = grade(problem(), EFFECTS, log(say("Which face is fixed?"), end="turn_cap"))
    assert c["status"] == "graded"
    assert res(c, "setup_fixed_faces") == "fail" and "no set_load_case" in c["checks"]["setup_fixed_faces"]["detail"]
    for name in ("feature_recall", "ranges_valid", "extra_negligible"):
        assert res(c, name) == "fail" and c["checks"][name]["detail"] == "no proposal"


def test_optimize_proposal_graded():
    feats = [{"face_id": "w1", "action": "offset", "lower": -1, "upper": 1, "start": 0},
             {"face_id": "w1", "action": "fillet", "other_face_id": "w3", "lower": 1, "upper": 3, "start": 2}]
    c = grade(problem(), EFFECTS, good_log(feats, tool="optimize"))
    assert res(c, "ranges_valid") == "pass" and res(c, "feature_recall") == "pass"


def test_second_set_load_case_marks_correction_first_is_graded():
    lg = log(say("safety factor 1.5"),
             call("set_load_case", fixed_face_ids=["w3"], load_face_ids=["end"], force_N=[0, -500, 0]),
             call("set_load_case", **GOOD_LC), proposal_feats=GOOD_FEATS)
    c = grade(problem(), EFFECTS, lg)
    assert res(c, "setup_fixed_faces") == "fail" and res(c, "needed_correction") == "fail"


def test_split_hole_fixed_faces_pass():
    assert res(grade(problem(), EFFECTS, good_log()), "setup_fixed_faces") == "pass"


def test_force_off_by_more_than_1_percent_fails():
    lc = dict(GOOD_LC, force_N=[0, -490.5, 0])
    c = grade(problem(), EFFECTS, log(say("safety factor 1.5"), call("set_load_case", **lc), proposal_feats=GOOD_FEATS))
    assert res(c, "setup_force") == "fail"


def test_wrong_material_fails():
    lg = log(say("safety factor 1.5"), call("set_material", material="Aluminum Alloy"),
             call("set_load_case", **GOOD_LC), proposal_feats=GOOD_FEATS)
    assert res(grade(problem(), EFFECTS, lg), "setup_material") == "fail"


def test_safety_factor_not_stated_fails():
    lg = log(call("set_load_case", **GOOD_LC), proposal_feats=GOOD_FEATS)
    assert res(grade(problem(), EFFECTS, lg), "safety_factor_stated") == "fail"


def test_must_ask_force_asked_before_set_load_case():
    p = problem(must_ask=["force"])
    ok = log(say("How large is the force, in N?"), call("set_load_case", **GOOD_LC), proposal_feats=GOOD_FEATS)
    late = log(call("set_load_case", **GOOD_LC), say("What force did you want?"), proposal_feats=GOOD_FEATS)
    assert res(grade(p, EFFECTS, ok), "asked_before_acting") == "pass"
    assert res(grade(p, EFFECTS, late), "asked_before_acting") == "fail"


def test_sf_flag_after_failed_set_material_fails():
    p = problem(must_flag=["safety_factor_below_1"])
    lg = log(call("set_material", safety_factor=0.8),
             {"type": "tool_result", "name": "set_material", "is_error": True, "content": "ValueError"},
             say("A safety factor below 1 means the part yields; shall I use 1.5?"),
             call("set_load_case", **GOOD_LC), proposal_feats=GOOD_FEATS)
    assert res(grade(p, EFFECTS, lg), "flagged_before_acting") == "fail"


def test_sf_flag_before_use_passes():
    p = problem(must_flag=["safety_factor_below_1"])
    lg = log(say("A safety factor of 0.8 is below 1, so the part would yield. Use 1.5?"),
             call("set_load_case", **GOOD_LC), proposal_feats=GOOD_FEATS)
    assert res(grade(p, EFFECTS, lg), "flagged_before_acting") == "pass"


def test_unit_conversion_flag_passes_with_490():
    p = problem(must_flag=["unit_conversion"])
    p.key["setup"]["force_N"] = [0, -490.5, 0]
    lc = dict(GOOD_LC, force_N=[0, -490.5, 0])
    ok = log(say("50 kg x 9.81 = 490.5 N downward."), call("set_load_case", **lc), proposal_feats=GOOD_FEATS)
    bad = log(call("set_load_case", **lc), proposal_feats=GOOD_FEATS)
    assert res(grade(p, EFFECTS, ok), "flagged_before_acting") == "pass"
    assert res(grade(p, EFFECTS, bad), "flagged_before_acting") == "fail"


def test_named_features_exact_passes_and_recall_is_na():
    c = grade(problem("clean_named", named=["wall", "corner"]), EFFECTS, good_log())
    assert res(c, "obedience") == "pass" and res(c, "feature_recall") == "n/a"


def test_named_features_extra_unlabelled_fails_obedience():
    feats = GOOD_FEATS + [{"face_id": "h2b", "action": "radius", "low": 3, "high": 5}]
    c = grade(problem("clean_named", named=["wall", "corner"]), EFFECTS, good_log(feats))
    assert res(c, "obedience") == "fail" and res(c, "extra_negligible") == "fail"


def test_named_feature_dropped_fails_obedience():
    c = grade(problem("clean_named", named=["wall", "corner"]), EFFECTS, good_log(GOOD_FEATS[:1]))
    assert res(c, "obedience") == "fail"


def test_recall_misses_controls_limit():
    c = grade(problem(), EFFECTS, good_log(GOOD_FEATS[:1]))
    assert res(c, "feature_recall") == "fail" and "corner" in c["checks"]["feature_recall"]["detail"]


def test_offset_range_excluding_zero_invalid():
    feats = [{"face_id": "w1", "action": "offset", "low": 5, "high": 10}] + GOOD_FEATS[1:]
    assert res(grade(problem(), EFFECTS, good_log(feats)), "ranges_valid") == "fail"


def test_fillet_above_key_max_invalid():
    feats = GOOD_FEATS[:1] + [{"face_id": "w1", "action": "fillet", "other_face_id": "w3", "low": 1, "high": 9}]
    assert res(grade(problem(), EFFECTS, good_log(feats)), "ranges_valid") == "fail"


def test_complex_part_says_complex():
    p = problem("complex")
    lg = log(say("This part looks complex. Which features would you like to change?"), say("safety factor 1.5"),
             call("set_load_case", **GOOD_LC), end="turn_cap")
    assert res(grade(p, EFFECTS, lg), "complex_part") == "pass"
    assert res(grade(p, EFFECTS, log(call("set_load_case", **GOOD_LC), end="turn_cap")), "complex_part") == "fail"
