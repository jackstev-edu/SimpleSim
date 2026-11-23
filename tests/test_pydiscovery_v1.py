"""The gusseted bracket (v1) in PyDiscovery: family matching, geometry rules, the end-to-end loop, the
agent tools, the demo, the engines and the GP. v0 behaviour is covered by test_pydiscovery.py, unchanged.

Every test writes its own part files. The GP test skips without scikit-learn; the CalculiX test
runs only where Role 1's solver packages are installed.
"""

import json
import shutil

import numpy as np
import pandas as pd
import pytest

from contracts.models import FAMILY_PARAMS, Result, StudySpec
from src.models import power_law
from src.pydiscovery import App, L_BRACKET_GUSSET_V1, L_BRACKET_V0, PyDiscoveryTools, SetupError, TOOL_SPECS, match_family
from src.pydiscovery.backends import BackendUnavailable
from src.pydiscovery.demo import main as demo_main
from src.pydiscovery.demo_part import write_demo_part

V1 = "l_bracket_gusset_v1"
LIMITS = [("safety_factor", ">=", 1.5), ("max_disp_mm", "<=", 0.5)]


@pytest.fixture
def dsco(tmp_path):
    return write_demo_part(tmp_path / "gusset.dsco", family=L_BRACKET_GUSSET_V1)


def test_family_matches_the_contracts_and_wins_over_v0():
    assert tuple(L_BRACKET_GUSSET_V1.labels) == FAMILY_PARAMS[V1] and tuple(L_BRACKET_V0.labels) == FAMILY_PARAMS["l_bracket_v0"]
    found = match_family(["Thickness", "Flange length", "Arm width", "Hole diameter", "Fillet radius", "Vertical Height (mm)", "gusset_thickness_mm", "Gusset length"])
    assert found and found[0] is L_BRACKET_GUSSET_V1 and len(found[1]) == 8
    assert match_family(["Thickness", "Flange length", "Arm width", "Hole diameter", "Fillet radius"])[0] is L_BRACKET_V0  # a v0 file stays v0
    assert L_BRACKET_GUSSET_V1.ranges_mm["fillet_radius_mm"] == (2.5, 8.0) and L_BRACKET_GUSSET_V1.baseline_mm["gusset_length_mm"] == 20.0


def test_geometry_rules_are_named_and_enforced(dsco):
    """Role 1 chose the v1 ranges so every in-range design builds; the rules still guard the corners and wider ranges."""
    assert L_BRACKET_GUSSET_V1.failed_rules(L_BRACKET_GUSSET_V1.baseline_mm) == []
    corners = [{"thickness_mm": 10.0, "flange_length_mm": 75.0, "arm_width_mm": 32.0, "hole_diameter_mm": 12.0, "fillet_radius_mm": 8.0,
                "vertical_height_mm": 50.0, "gusset_thickness_mm": 10.0, "gusset_length_mm": 35.0}]
    assert all(L_BRACKET_GUSSET_V1.valid(c) for c in corners)
    broken = {**L_BRACKET_GUSSET_V1.baseline_mm, "fillet_radius_mm": 8.0, "gusset_length_mm": 10.0}  # the gusset cannot clear the fillet
    assert any("gusset_past_fillet" in r for r in L_BRACKET_GUSSET_V1.failed_rules(broken))
    part = App().open(dsco)
    with pytest.raises(ValueError, match="outside"):
        part.set_dimensions({"Fillet radius": 8.0, "Gusset length": 10.0})  # the range check fires first, never a clamp
    assert part.values_mm == L_BRACKET_GUSSET_V1.baseline_mm  # nothing changed on refusal
    bad = App().open(write_demo_part(dsco.with_name("bad.dsco"), family=L_BRACKET_GUSSET_V1, values_mm=broken))
    assert any("gusset_past_fillet" in p for p in bad.problems) and any("outside" in p for p in bad.problems)
    with pytest.raises(SetupError):
        bad.confirm()


def test_v1_part_opens_confirms_optimizes_and_saves(tmp_path, dsco):
    part = App().open(dsco)
    assert part.family is L_BRACKET_GUSSET_V1 and part.missing == [] and part.problems == []
    assert "Gusset length 20 mm (12 to 35)" in part.describe()
    part.confirm()
    v = part.solve()
    assert v.source == "power_law_v1" and set(v.design) == set(FAMILY_PARAMS[V1])
    # The built-in v1 power law at the baseline, with the part's Poisson's ratio of 0.30.
    X = np.array([[L_BRACKET_GUSSET_V1.baseline_mm[p] for p in FAMILY_PARAMS[V1]] + [0.30]])
    med, _ = power_law.builtin_fit(V1).predict_arrays(X, 500.0, 200_000.0)
    assert v.max_stress_mpa == pytest.approx(med["max_stress_mpa"][0]) and v.mass_kg == pytest.approx(power_law.volume_v1_mm3(X)[0] * 7850e-9)
    result = part.optimize(constraints=LIMITS)
    Result.model_validate(result.model_dump())
    assert result.spec.bracket_family == V1 and len(result.spec.parameters) == 8
    best = result.recommended
    assert not result.baseline.feasible and result.baseline.safety_factor < 1.5  # 500 N is a lot for the v1 baseline (SF 0.9)
    assert best and best.feasible and best.safety_factor >= 1.5 and set(best.design) == set(FAMILY_PARAMS[V1])
    assert L_BRACKET_GUSSET_V1.failed_rules(best.design) == []  # the shortlist never holds an unbuildable design
    assert part.last_prediction.model_name == "power_law_v1"
    assert all(L_BRACKET_GUSSET_V1.valid(c.design) for c in part.last_prediction.shortlist)
    assert any("beam equations describe the v0" in w for w in result.warnings)
    saved = part.save(tmp_path / "opt.dsco", best.design)
    assert App().open(saved).values_mm == pytest.approx(best.design)


def test_spaceclaim_part_can_be_declared_a_gusset_bracket(tmp_path):
    part = App().open(write_demo_part(tmp_path / "shape.scdocx", family=L_BRACKET_GUSSET_V1))
    part.use_family(V1)
    assert part.values_mm == L_BRACKET_GUSSET_V1.baseline_mm
    part.set_support()
    part.set_load(400.0)
    part.set_material("aluminum_6061")
    part.confirm()
    assert part.solve().mass_kg == pytest.approx(0.0, abs=0.5)
    out = json.loads(part.save(tmp_path / "values.json").read_text(encoding="utf-8"))
    assert out["family"] == V1 and out["values_mm"]["Gusset thickness"] == 6.0


def test_agent_tools_offer_both_families(tmp_path, dsco):
    spec = next(s for s in TOOL_SPECS if s["name"] == "use_family")
    assert spec["parameters"]["properties"]["family"]["enum"][:2] == ["l_bracket_v0", V1]  # the templates follow
    tools = PyDiscoveryTools()
    assert tools.call("open_part", {"path": str(dsco)})["state"]["family"] == V1
    assert tools.call("confirm_setup")["ok"]
    out = tools.call("optimize_design", {"constraints": [{"metric": "safety_factor", "op": ">=", "value": 1.5}]})
    assert out["ok"] and StudySpec.model_validate(out["study_spec"]).bracket_family == V1
    assert Result.model_validate(out["result"]).recommended is not None
    saved = tools.call("save_design", {"path": str(tmp_path / "best.dsco")})
    assert saved["ok"] and App().open(saved["path"]).family is L_BRACKET_GUSSET_V1


def test_demo_runs_the_gusset_family(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the demo writes its synthetic part under data/raw
    assert demo_main(["--family", V1, "--yes", "--out", str(tmp_path / "out")]) == 0
    result = Result.model_validate_json((tmp_path / "out" / "result.json").read_text(encoding="utf-8"))
    assert result.spec.bracket_family == V1 and result.recommended is not None
    assert (tmp_path / "data" / "raw" / "l_bracket_gusset_demo.dsco").exists()


def test_formula_engine_refuses_v1_with_a_clear_message(dsco):
    part = App("formula").open(dsco)
    part.confirm()
    with pytest.raises(BackendUnavailable, match="v0 L-bracket only"):
        part.solve()


def test_predictors_check_the_family_of_the_spec():
    from contracts.make_examples import example_spec, example_spec_v1

    with pytest.raises(ValueError, match="fitted to l_bracket_v0"):
        power_law.PowerLawPredictor().predict(example_spec_v1(), n=100)
    pred = power_law.PowerLawPredictor(family=V1).predict(example_spec_v1(), n=300)
    assert pred.model_name == "power_law_v1" and all(set(c.design) == set(FAMILY_PARAMS[V1]) for c in pred.shortlist)
    with pytest.raises(ValueError, match="fitted to l_bracket_gusset_v1"):
        power_law.PowerLawPredictor(family=V1).predict(example_spec(), n=100)


def test_gp_predictor_drives_the_gusset_family(tmp_path, dsco):
    pytest.importorskip("sklearn")
    from src.models.datasets import FAMILY_DIRS

    source = FAMILY_DIRS[V1] / "model_dataset.csv"
    if not source.exists():
        pytest.skip("Role 1's v1 model_dataset.csv is missing")
    small = tmp_path / "v1_small.csv"
    pd.read_csv(source).iloc[:120].to_csv(small, index=False)  # a quick GP; the family comes from the bracket_family column
    part = App(predictor="gp", runs={V1: small}).open(dsco)
    part.confirm()
    result = part.optimize(constraints=LIMITS)
    assert part.last_prediction.model_name == "gp_powerlaw_l_bracket_gusset_v1"
    assert result.recommended and result.recommended.feasible and result.spec.bracket_family == V1
    assert all(L_BRACKET_GUSSET_V1.valid(c.design) for c in part.last_prediction.shortlist)


@pytest.mark.skipif(shutil.which("ccx") is None, reason="CalculiX (ccx) not installed")
def test_calculix_engine_builds_the_gusset_baseline(dsco):
    pytest.importorskip("cadquery")
    pytest.importorskip("gmsh")
    part = App("calculix").open(dsco)
    part.confirm()
    v = part.solve()
    assert v.source == "calculix" and v.max_stress_mpa == pytest.approx(273.6, rel=0.03)  # Role 1's converged v1 baseline
