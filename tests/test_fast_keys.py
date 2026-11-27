"""Fast-path benchmark keys: problem loading, the model's view of a design, and the brute-force optima."""
import pytest

pytest.importorskip("yaml")  # Role 3 package (src/sim/requirements.txt); skip on a root-only install
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


def test_key_reproduces_the_fast_path_search():
    # the scoring key uses Part.optimize's own sample: 2,000 designs, seed 0
    from src.eval.fast_keys import FAST_PATH_CANDIDATES
    from src.pydiscovery.session import Part
    import inspect
    assert FAST_PATH_CANDIDATES == inspect.signature(Part.optimize).parameters["n_candidates"].default


def _write_tables(folder: Path) -> dict:
    """Four rows in Can's multi-family layout: two T01, one T02, one v0, each at its baseline."""
    import json

    import pandas as pd
    rows = [("T01_safe", "t01_double_hole_v1", 150.0, 0.25), ("T01_unsafe", "t01_double_hole_v1", 200.0, 0.20),
            ("T02_light", "t02_single_slot_v1", 100.0, 0.10), ("V0_light", "l_bracket_v0", 100.0, 0.10)]
    pd.DataFrame([{"design_id": d, "bracket_family": f, "reference_max_stress_mpa": s, "reference_max_disp_mm": 0.1,
                   "reference_mass_kg": m} for d, f, s, m in rows]).to_csv(folder / "data.csv", index=False)
    pd.DataFrame([{"design_id": d, "bracket_family": f, "params_json": json.dumps(dict(FAMILIES[f].baseline_mm))}
                  for d, f, _, _ in rows]).to_csv(folder / "manifest.csv", index=False)
    return {"dataset": folder / "data.csv", "manifest": folder / "manifest.csv"}


@pytest.fixture
def fixture_tables(monkeypatch, tmp_path):
    from src.eval import fast_keys
    monkeypatch.setattr(fast_keys, "MULTIFAMILY", _write_tables(tmp_path))


def test_sim_optimum_template_picks_safe_row_of_own_family(fixture_tables):
    # the unsafe T01 row and the lighter rows of other families must not win
    out = sim_optimum(STUDY_500, "t01_double_hole_v1")
    assert out["design_id"] == "T01_safe" and out["max_stress_mpa"] == 150.0
    assert set(out["design"]) == set(FAMILIES["t01_double_hole_v1"].ranges_mm)


def test_sim_optimum_template_missing_table_gives_reason(monkeypatch, tmp_path):
    from src.eval import fast_keys
    monkeypatch.setattr(fast_keys, "MULTIFAMILY", {"dataset": tmp_path / "no.csv", "manifest": tmp_path / "no2.csv"})
    out = sim_optimum(STUDY_500, "t01_double_hole_v1")
    assert out["design_id"] is None and "not in this checkout" in out["reason"]
