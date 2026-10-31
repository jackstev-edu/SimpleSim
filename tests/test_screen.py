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
