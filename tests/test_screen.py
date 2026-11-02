from src.agent import screen


def test_reference_value_uses_current_inside_range():
    assert screen.reference_value(0.0, -1.0, 2.0) == 0.0


def test_reference_value_falls_back_to_midpoint():
    assert screen.reference_value(4.0, 5.0, 7.0) == 6.0      # Review Focus 5
    assert screen.reference_value(None, 2.0, 6.0) == 4.0     # fillet: no current value


def test_screen_plan_is_2n_plus_1_in_order():
    f = [{"low": -1, "high": 2}, {"low": 2, "high": 6}]
    plan = screen.screen_plan(f, [0.0, None])
    assert [n for n, _ in plan] == ["look_ref", "look_1_low", "look_1_high", "look_2_low", "look_2_high"]
    assert dict(plan)["look_ref"] == [0.0, 4.0]
    assert dict(plan)["look_2_high"] == [0.0, 6.0]


def test_feasible_handles_both_ops_and_missing():
    lim = [{"response": "safety_factor", "op": ">=", "value": 1.5},
           {"response": "max_def_mm", "op": "<=", "value": 0.5}]
    assert screen.feasible({"safety_factor": 1.6, "max_def_mm": 0.4}, lim)
    assert not screen.feasible({"safety_factor": 1.4, "max_def_mm": 0.4}, lim)
    assert not screen.feasible({"max_def_mm": 0.4}, lim)


def test_feasible_ignores_non_numeric_extras_and_errors():
    lim = [{"response": "safety_factor", "op": ">=", "value": 1.5}]
    assert screen.feasible({"safety_factor": 1.6, "note": "x"}, lim)
    assert not screen.feasible({"error": "boom", "safety_factor": 2.0}, lim)


def test_summarize_ranks_by_objective_swing_and_skips_errors():
    f = [{"label": "base"}, {"label": "wall"}]
    r = lambda m, sf: {"mass_kg": m, "safety_factor": sf}
    rows = {"look_ref": {"values": [0, 0], "responses": r(0.25, 1.6)},
            "look_1_low": {"values": [-1, 0], "responses": r(0.24, 1.3)},
            "look_1_high": {"values": [1, 0], "responses": r(0.27, 1.9)},
            "look_2_low": {"values": [0, -1], "error": "RuntimeError: edit failed"},   # Review Focus 2
            "look_2_high": {"values": [0, 1], "responses": r(0.251, 1.61)}}
    s = screen.summarize(f, rows, "mass_kg", "min", [{"response": "safety_factor", "op": ">=", "value": 1.5}])
    assert s["ranking"] == [1, 2]
    assert s["features"][1]["feasible"]["low"] is False
    assert s["best_feasible"] == "look_ref"


SF = [{"response": "safety_factor", "op": ">=", "value": 1.5}]


def _r(m, sf):
    return {"mass_kg": m, "safety_factor": sf}


def test_summarize_reports_errors_and_failed_count():
    rows = {"look_ref": {"values": [0], "responses": _r(0.25, 1.6)},
            "look_1_low": {"values": [-1], "error": "RuntimeError: edit failed"},
            "look_1_high": {"values": [1], "responses": _r(0.27, 1.9)}}
    s = screen.summarize([{"label": "base"}], rows, "mass_kg", "min", SF)
    assert s["errors"] == {"look_1_low": "RuntimeError: edit failed"}
    assert s["failed"] == 1
    # one end failed: the change is the surviving end against the reference, and says so
    assert s["features"][0]["responses"]["mass_kg"]["basis"] == "high_vs_ref"


def test_summarize_basis_is_high_vs_low_when_both_ends_ran():
    rows = {"look_ref": {"values": [0], "responses": _r(0.25, 1.6)},
            "look_1_low": {"values": [-1], "responses": _r(0.24, 1.3)},
            "look_1_high": {"values": [1], "responses": _r(0.27, 1.9)}}
    s = screen.summarize([{"label": "base"}], rows, "mass_kg", "min", SF)
    assert s["features"][0]["responses"]["mass_kg"]["basis"] == "high_vs_low"
    assert s["errors"] == {} and s["failed"] == 0 and "note" not in s


def test_summarize_failed_reference_gives_no_ranking_and_a_note():
    rows = {"look_ref": {"values": [0, 0], "error": "RuntimeError: boom"},
            "look_1_low": {"values": [-1, 0], "responses": _r(0.24, 1.3)},
            "look_1_high": {"values": [1, 0], "responses": _r(0.27, 1.9)},
            "look_2_low": {"values": [0, -1], "responses": _r(0.25, 1.6)},
            "look_2_high": {"values": [0, 1], "responses": _r(0.26, 1.7)}}
    s = screen.summarize([{"label": "a"}, {"label": "b"}], rows, "mass_kg", "min", SF)
    assert s["ranking"] == []
    assert "look_ref" in s["note"]
    assert s["best_feasible"] == "look_2_low"    # completed designs are still usable


def test_summarize_feature_with_both_ends_failed_is_unranked():
    rows = {"look_ref": {"values": [0, 0], "responses": _r(0.25, 1.6)},
            "look_1_low": {"values": [-1, 0], "responses": _r(0.24, 1.3)},
            "look_1_high": {"values": [1, 0], "responses": _r(0.27, 1.9)},
            "look_2_low": {"values": [0, -1], "error": "E: x"},
            "look_2_high": {"values": [0, 1], "error": "E: y"}}
    s = screen.summarize([{"label": "a"}, {"label": "b"}], rows, "mass_kg", "min", SF)
    assert s["ranking"] == [1]
    assert s["unranked"] == [2]
    assert "feature 2" in s["note"]


def test_summarize_sense_max_and_tie_break():
    lim = [{"response": "safety_factor", "op": ">=", "value": 1.0}]
    rows = {"look_ref": {"values": [0, 0], "responses": _r(0.25, 1.6)},
            "look_1_low": {"values": [-1, 0], "responses": _r(0.24, 1.2)},
            "look_1_high": {"values": [1, 0], "responses": _r(0.26, 2.0)},
            "look_2_low": {"values": [0, -1], "responses": _r(0.24, 1.5)},
            "look_2_high": {"values": [0, 1], "responses": _r(0.26, 1.7)}}
    s = screen.summarize([{"label": "a"}, {"label": "b"}], rows, "mass_kg", "max", lim)
    assert s["ranking"] == [1, 2]   # equal mass swing; feature 1 swings the safety factor more
    assert s["best_feasible"] in ("look_1_high", "look_2_high")


def test_summarize_ranks_features_per_limit_and_notes_when_nothing_is_feasible():
    # feature 1 moves mass most but barely the safety factor; feature 2 moves the safety factor most
    rows = {"look_ref": {"values": [0, 0], "responses": _r(0.040, 0.48)},
            "look_1_low": {"values": [-1, 0], "responses": _r(0.037, 0.46)},
            "look_1_high": {"values": [1, 0], "responses": _r(0.044, 0.47)},
            "look_2_low": {"values": [0, -1], "responses": _r(0.039, 0.36)},
            "look_2_high": {"values": [0, 1], "responses": _r(0.042, 0.56)}}
    s = screen.summarize([{"label": "right"}, {"label": "left"}], rows, "mass_kg", "min", SF)
    assert s["ranking"] == [1, 2]
    assert s["limit_ranking"] == {"safety_factor": [2, 1]}
    assert s["best_feasible"] is None
    assert "limit_ranking" in s["note"]


def _plane(n, lo, hi):
    return {"id": f"{n}{lo}", "type": "plane", "normal": n, "bbox_min_mm": lo, "bbox_max_mm": hi}


def test_min_wall_finds_sheet_thickness_and_ignores_faces_pointing_at_each_other():
    faces = [_plane([0, -1, 0], [0, 0, 0], [20, 0, 30]),       # sheet bottom, y = 0
             _plane([0, 1, 0], [0, 1.5, 0], [20, 1.5, 30]),     # sheet top, y = 1.5 -> wall 1.5
             _plane([0, -1, 0], [0, 6, 0], [20, 6, 30]),        # next flange bottom, faces the sheet top: a gap
             _plane([0, 1, 0], [0, 8, 0], [20, 8, 30]),         # next flange top -> wall 2.0
             _plane([1, 0, 0], [50, 0, 0], [50, 1, 30])]        # far away, no partner
    assert screen.min_wall_mm(faces) == 1.5


def test_default_element_size_is_half_wall_but_capped_by_node_budget_and_2mm():
    assert screen.default_element_size(6.0, 32000) == 2.0     # thick L-bracket keeps 2 mm
    assert screen.default_element_size(None, 32000) == 2.0    # no wall found
    h = screen.default_element_size(1.33, 4991)               # thin saddle: budget wins over t/2
    assert 1.0 < h < 1.2
    assert screen.default_element_size(1.34, 300) == 0.67     # tiny part: half the wall
    assert screen.default_element_size(6.0, 130190) > 3.0    # large SimJEB bracket: node budget beats 2 mm


def _cyl(fid, r, p, d=(0.0, 0.0, 1.0)):
    return {"id": fid, "type": "cylinder", "radius_mm": r, "axis_point_mm": list(p), "axis_dir": list(d)}


def test_hole_faces_groups_split_hole_halves():
    faces = [_cyl("a", 5.0, (10, 0, 0)), _cyl("b", 5.0, (10, 0, 7.0), (0, 0, -1)),  # other half, axis point shifted
             _cyl("c", 5.0, (30, 0, 0)),  # another hole, same radius, different axis
             _cyl("d", 3.0, (10, 0, 0)),  # coaxial but different radius (counterbore)
             {"id": "p", "type": "plane", "normal": [0, 0, 1]}]
    assert screen.hole_faces(faces, "a") == ["a", "b"]
    assert screen.hole_faces(faces, "b") == ["a", "b"]
    assert screen.hole_faces(faces, "c") == ["c"]
    assert screen.hole_faces(faces, "d") == ["d"]
    assert screen.hole_faces(faces, "p") == ["p"]
