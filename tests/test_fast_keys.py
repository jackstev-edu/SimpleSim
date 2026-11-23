"""Fast-path benchmark keys: problem loading, the model's view of a design, and the brute-force optima."""
from pathlib import Path

import pytest

from src.eval.fast_keys import load_fast_problem, meets, model_eval, model_optimum, sim_optimum
from src.pydiscovery.families import FAMILIES

BASELINE_V0 = dict(FAMILIES["l_bracket_v0"].baseline_mm)
STUDY_500 = {"force_n": 500.0, "angle_deg": -90.0, "material_key": "structural_steel", "safety_factor": 1.5,
             "objective": {"metric": "mass_kg", "direction": "minimize"}, "limits": [], "vary": ["all"]}
V0_RUNS = Path(__file__).resolve().parents[1] / "data" / "processed" / "formal_v0" / "runs.csv"


def test_model_eval_worst_is_more_pessimistic():
    ev = model_eval("l_bracket_v0", "structural_steel", 500.0, BASELINE_V0)
    assert ev["max_stress_mpa_worst"] > ev["max_stress_mpa_med"] > 0
    assert ev["safety_factor_worst"] < ev["safety_factor_med"]
    assert ev["mass_kg"] > 0


def test_model_optimum_feasible_and_median_not_heavier():
    opt = model_optimum(STUDY_500, "l_bracket_v0", n=20_000)
    ev = model_eval("l_bracket_v0", "structural_steel", 500.0, opt["design"])
    assert meets(ev, 1.5, [], at="worst")
    assert opt["objective_value"] == pytest.approx(ev["mass_kg"])
    assert opt["median"]["objective_value"] <= opt["objective_value"]
    assert "n=20000" in opt["method"]


def test_model_optimum_holds_unvaried_dims_at_baseline():
    opt = model_optimum({**STUDY_500, "vary": ["thickness_mm"]}, "l_bracket_v0", n=5_000)
    assert all(opt["design"][k] == pytest.approx(BASELINE_V0[k]) for k in BASELINE_V0 if k != "thickness_mm")


def test_meets_checks_extra_limits():
    ev = model_eval("l_bracket_v0", "structural_steel", 500.0, BASELINE_V0)
    assert not meets(ev, 1.5, [{"metric": "mass_kg", "op": "<=", "value": ev["mass_kg"] * 0.5}])
    assert meets(ev, 0.1, [{"metric": "mass_kg", "op": "<=", "value": ev["mass_kg"] * 2}])


def test_sim_optimum_only_for_500n_steel():
    out = sim_optimum({**STUDY_500, "force_n": 800.0}, "l_bracket_v0")
    assert out["design_id"] is None and out["reason"]


@pytest.mark.skipif(not V0_RUNS.exists(), reason="Role 1's v0 table is not in this checkout")
def test_sim_optimum_meets_sf_in_table():
    out = sim_optimum(STUDY_500, "l_bracket_v0")
    assert out["design_id"] and 250.0 / out["max_stress_mpa"] >= 1.5


def test_missing_key_refused(tmp_path):
    (tmp_path / "x.yaml").write_text("id: x\nfamily: l_bracket_v0\nrequest: r\nhidden_facts: f\n"
                                     "key: {study: {}, expect_handoff: false}\n")
    with pytest.raises(ValueError, match="model_optimum"):
        load_fast_problem(tmp_path / "x.yaml")
    assert load_fast_problem(tmp_path / "x.yaml", require_key=False).id == "x"
