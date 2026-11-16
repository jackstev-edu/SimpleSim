"""PyDiscovery emulator: setup gating, the optimize loop, saving, agent tools, the demo and the fitted model.

Every test builds its own part files, so nothing under data/ is needed. The CalculiX test runs
only where Role 1's solver and its packages are installed; CI skips it.
"""

import dataclasses
import json
import shutil

import numpy as np
import pandas as pd
import pytest

from contracts.models import Result
from src.models import power_law
from src.pydiscovery import App, L_BRACKET_V0, PyDiscoveryTools, SetupError, TOOL_SPECS, match_family
from src.pydiscovery.demo import main as demo_main
from src.pydiscovery.demo_part import write_demo_part

LIMITS = [("safety_factor", ">=", 1.5), ("max_disp_mm", "<=", 0.5)]


@pytest.fixture
def dsco(tmp_path):
    return write_demo_part(tmp_path / "l_bracket.dsco")


def test_file_names_map_to_contract_names():
    found = match_family(["Thickness", "flange_length_mm", "Arm Width (mm)", "hole diameter", "Fillet_Radius"])
    assert found and found[0] is L_BRACKET_V0
    assert match_family(["Thickness"]) is None  # a partial match is not this bracket


def test_discovery_part_needs_only_confirmation(dsco):
    part = App().open(dsco)
    assert part.family is L_BRACKET_V0 and part.missing == [] and part.problems == []
    assert part.values_mm == L_BRACKET_V0.baseline_mm
    with pytest.raises(SetupError, match="Confirm the setup"):
        part.solve()
    part.confirm()
    v = part.solve()
    assert v.source == "power_law_v0" and v.safety_factor == pytest.approx(250.0 / v.max_stress_mpa)


def test_setup_changes_reset_confirmation_but_design_changes_do_not(dsco):
    part = App().open(dsco)
    part.confirm()
    part.set_dimensions({"Thickness": 7.0})
    assert part.confirmed and part.values_mm["thickness_mm"] == 7.0
    part.set_load(600.0)
    assert not part.confirmed


def test_out_of_range_and_unmodelled_loads_are_refused(tmp_path, dsco):
    part = App().open(dsco)
    with pytest.raises(ValueError, match="outside"):
        part.set_dimensions({"Thickness": 20.0})  # never clamped silently
    with pytest.raises(SetupError, match="takes loads"):
        part.set_load(500.0, (1.0, 0.0, 0.0))
    sideways = App().open(write_demo_part(tmp_path / "side.dsco", direction=(0.0, 0.0, -1.0)))
    assert any("isn't modelled" in p for p in sideways.problems)
    with pytest.raises(SetupError):
        sideways.confirm()


def test_optimize_returns_a_contract_result_that_meets_the_limits(dsco):
    part = App().open(dsco)
    part.confirm()
    result = part.optimize(constraints=LIMITS)
    Result.model_validate(result.model_dump())
    best = result.recommended
    assert best and best.feasible and best.safety_factor >= 1.5 and best.max_disp_mm <= 0.5
    assert best.mass_kg < result.baseline.mass_kg
    for name, value in result.discovery_values.items():
        lo, hi = L_BRACKET_V0.ranges_mm[name]
        assert lo <= value <= hi
    assert any("not a real solver" in w for w in result.warnings)  # honest about what checked the finalists


def test_save_writes_new_values_into_a_discovery_copy(tmp_path, dsco):
    part = App().open(dsco)
    out = part.save(tmp_path / "opt.dsco", {"Thickness": 7.5, "Fillet radius": 6.0})
    reopened = App().open(out)
    assert reopened.values_mm["thickness_mm"] == 7.5 and reopened.values_mm["fillet_radius_mm"] == 6.0
    with pytest.raises(ValueError):
        part.save(tmp_path / "opt.json")


def test_spaceclaim_part_asks_for_everything_then_solves(tmp_path):
    part = App().open(write_demo_part(tmp_path / "l_bracket.scdocx"))
    assert len(part.missing) == 4
    with pytest.raises(SetupError, match="Can't confirm"):
        part.confirm()
    part.use_family("l_bracket_v0")
    part.set_support()
    part.set_load(500.0)
    part.set_material("aluminum_6061")
    part.confirm()
    assert part.solve().mass_kg > 0
    saved = json.loads(part.save(tmp_path / "values.json").read_text(encoding="utf-8"))
    assert saved["values_mm"]["Thickness"] == 6.0


def test_unknown_shape_is_read_but_not_solved(tmp_path):
    only_thickness = dataclasses.replace(L_BRACKET_V0, labels={"thickness_mm": "Thickness"})  # like Sanjay's demo file
    part = App().open(write_demo_part(tmp_path / "other.dsco", family=only_thickness))
    assert part.family is None and part.missing == []
    assert any("can't rebuild" in p for p in part.problems)
    with pytest.raises(SetupError):
        part.confirm()
    with pytest.raises(SetupError):
        part.use_family("l_bracket_v0")


def test_agent_tools_follow_the_same_rules(tmp_path, dsco):
    names = {s["name"] for s in TOOL_SPECS}
    tools = PyDiscoveryTools()
    assert all(hasattr(tools, f"_{n}") for n in names)
    assert tools.call("solve_design")["ok"] is False  # nothing open yet
    assert tools.call("open_part", {"path": str(dsco)})["state"]["missing"] == []
    assert tools.call("optimize_design")["ok"] is False  # not confirmed
    assert tools.call("confirm_setup")["ok"]
    out = tools.call("optimize_design", {"constraints": [{"metric": "safety_factor", "op": ">=", "value": 1.5}]})
    assert out["ok"] and Result.model_validate(out["result"]).recommended
    saved = tools.call("save_design", {"path": str(tmp_path / "best.dsco")})
    assert saved["ok"] and App().open(saved["path"]).values_mm == pytest.approx(out["result"]["recommended"]["design"])
    assert tools.call("no_such_tool")["ok"] is False


def test_demo_runs_end_to_end(tmp_path, dsco):
    assert demo_main(["--part", str(dsco), "--yes", "--out", str(tmp_path / "out")]) == 0
    result = Result.model_validate_json((tmp_path / "out" / "result.json").read_text(encoding="utf-8"))
    assert result.recommended is not None
    assert (tmp_path / "out" / "l_bracket_optimized.dsco").exists()


def test_power_law_matches_calculix_and_scales_exactly():
    base = np.array([[L_BRACKET_V0.baseline_mm[p] for p in L_BRACKET_V0.labels]])
    out = power_law.predict_arrays(base, 500.0, 200_000.0)
    # Role 1's CalculiX baseline through this emulator: 178.8 MPa, 0.4804 mm, 0.2512 kg.
    assert out["max_stress_mpa"][0] == pytest.approx(178.8, rel=0.03)
    assert out["max_disp_mm"][0] == pytest.approx(0.4804, rel=0.03)
    assert power_law.volume_mm3(base)[0] * 7850e-9 == pytest.approx(0.2512, rel=1e-3)
    doubled = power_law.predict_arrays(base, 1000.0, 100_000.0)
    assert doubled["max_stress_mpa"][0] == pytest.approx(2 * out["max_stress_mpa"][0])
    assert doubled["max_disp_mm"][0] == pytest.approx(4 * out["max_disp_mm"][0])


def test_refit_recovers_the_coefficients(tmp_path):
    rng = np.random.default_rng(1)
    names = list(L_BRACKET_V0.labels)
    X = np.column_stack([rng.uniform(*L_BRACKET_V0.ranges_mm[n], 60) for n in names])
    preds = power_law.predict_arrays(X, 500.0, 200_000.0)
    pd.DataFrame({**{n: X[:, i] for i, n in enumerate(names)}, "load_force_n": 500.0, **preds}).to_csv(tmp_path / "runs.csv", index=False)
    coef, sigma = power_law.fit_power_law(tmp_path / "runs.csv")
    for metric, values in coef.items():
        assert values == pytest.approx(power_law.COEF[metric], abs=1e-5)
        assert sigma[metric] < 1e-5


@pytest.mark.skipif(shutil.which("ccx") is None, reason="CalculiX (ccx) not installed")
def test_calculix_engine_matches_role1_baseline(dsco):
    pytest.importorskip("cadquery")
    pytest.importorskip("gmsh")
    pytest.importorskip("src.data.solver", reason="Role 1's solver isn't merged on this branch")
    part = App("calculix").open(dsco)
    part.confirm()
    v = part.solve()
    assert v.source == "calculix" and v.max_stress_mpa == pytest.approx(178.8, rel=0.02)
