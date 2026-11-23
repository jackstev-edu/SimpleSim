"""Role 1's ten bracket templates in Role 2's models and the emulator: the dataset loader, the tables
against Role 1's own module, the closed-form volumes against the CAD volumes, the ported geometry rules,
the shipped power laws and a template part through open, confirm, solve, optimize and save.

Only numpy and pandas are needed. Role 1's dataset is committed under data/processed, so the real-data
checks run everywhere; they skip if the folder is missing. The comparison with Role 1's template module
runs only where its scipy import works.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from contracts.models import FAMILY_PARAMS, Result
from src.models import multifamily, power_law, template_families as tf
from src.pydiscovery import App, FAMILIES, PyDiscoveryTools, SetupError, TOOL_SPECS, match_family
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.families import TEMPLATE_FAMILIES

ROOT = Path(__file__).resolve().parents[1]
HAVE_DATA = (ROOT / multifamily.ROOT / "model_dataset_1000.csv").exists()
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="Role 1's formal_multifamily_v1 dataset is missing")
LIMITS = [("safety_factor", ">=", 1.5), ("max_disp_mm", "<=", 0.5)]
T02, T09 = "t02_single_slot_v1", "t09_perforated_gusset_v1"


@pytest.fixture(scope="module")
def data():
    return multifamily.load_multifamily(ROOT / multifamily.ROOT)


# ---------------------------------------------------------------- tables


def test_templates_match_the_contracts_and_name_every_dimension():
    assert set(tf.TEMPLATES) == {k for k in FAMILY_PARAMS if k.startswith("t")} and len(tf.TEMPLATES) == 10
    for key in tf.TEMPLATES:
        names = tf.template_params(key)
        assert names == FAMILY_PARAMS[key]
        assert set(tf.template_ranges(key)) == set(names) == set(tf.template_baseline(key))
        assert all(lo <= tf.template_baseline(key)[n] <= hi for n, (lo, hi) in tf.template_ranges(key).items())
        assert tf.failed_rules(key, tf.template_baseline(key)) == []  # Role 1's baselines all build
    assert tf.template_ranges(T09)["fillet_radius_mm"] == (2.5, 8.0) and tf.template_ranges(T02)["fillet_radius_mm"] == (2.0, 8.0)


def test_tables_agree_with_role1_template_module():
    role1 = pytest.importorskip("src.data.multifamily_v1.templates", reason="Role 1's template module needs scipy")
    for key in tf.TEMPLATES:
        tid = key[:3].upper()
        theirs = {k: v for k, v in role1.ranges(tid).items() if k != "poissons_ratio"}
        assert tf.template_ranges(key) == theirs, key
        base = {**role1.COMMON_BASE, **role1.BASE_PARAMS[tid]}
        assert tf.template_baseline(key) == {n: base[n] for n in tf.template_params(key)}, key
        assert key == role1.FAMILIES[tid].key


def test_rules_refuse_what_role1s_builders_refuse():
    """Role 1 chose the ranges so in-range designs build (one ribbed corner aside), so most of these sit outside them."""
    hole = {**tf.template_baseline("t01_double_hole_v1"), "flange_length_mm": 75.0, "hole_diameter_mm": 30.0}
    assert {r.split(":")[0] for r in tf.failed_rules("t01_double_hole_v1", hole)} == {"opening_clearance", "opening_spacing"}
    slots = {**tf.template_baseline("t03_twin_slot_v1"), "arm_width_mm": 32.0, "slot_width_mm": 14.0}
    assert any(r.startswith("opening_clearance") for r in tf.failed_rules("t03_twin_slot_v1", slots))
    notch = {**tf.template_baseline("t05_side_notches_v1"), "arm_width_mm": 32.0, "notch_radius_mm": 15.0}
    assert any(r.startswith("notch_clearance") for r in tf.failed_rules("t05_side_notches_v1", notch))
    rib = {**tf.template_baseline("t08_twin_gusset_v1"), "gusset_length_mm": 40.0, "vertical_height_mm": 50.0, "thickness_mm": 10.0}
    assert any(r.startswith("rib_reach") for r in tf.failed_rules("t08_twin_gusset_v1", rib))
    perforated = {**tf.template_baseline(T09), "rib_hole_diameter_mm": 10.0, "gusset_length_mm": 28.0}
    assert any(r.startswith("rib_hole_clearance") for r in tf.failed_rules(T09, perforated))
    tip = {**tf.template_baseline("t06_tapered_v1"), "arm_width_mm": 32.0, "tip_trim_mm": 12.0, "hole_diameter_mm": 10.0}
    assert any("clearance" in r for r in tf.failed_rules("t06_tapered_v1", tip))
    assert tf.failed_rules(T09, BAD_T09) == ["rib_hole_clearance: the rib hole comes within 2 mm of the edges of the rib"]
    corners = [{n: hi for n, (lo, hi) in tf.template_ranges(T09).items()}, {n: lo for n, (lo, hi) in tf.template_ranges(T09).items()}]
    assert all(tf.failed_rules(T09, c) == [] for c in corners)  # Role 1's ranges keep every in-range design buildable


BAD_T09 = {**tf.template_baseline(T09), "rib_hole_diameter_mm": 10.0, "gusset_length_mm": 28.0}  # the hole is 2 mm over its range


# ---------------------------------------------------------------- data, volumes and fits


@needs_data
def test_loader_joins_features_labels_split_and_dimensions(data):
    assert len(data.frame) == 1000 and len(data.feature_columns) == 20
    assert {len(data.rows("train")), len(data.rows("validation")), len(data.rows("heldout_test"))} == {736, 184, 80}
    assert set(data.rows("heldout_test")["bracket_family"]) == {"t07_stepped_v1"}  # Role 1 holds out a whole family
    assert set(data.inputs_by_family) == set(tf.TEMPLATES) | {"l_bracket_v0"}
    md = data.model_data(T02)
    assert md.inputs == (*FAMILY_PARAMS[T02], "poissons_ratio") and len(md.frame) == 80 and md.ranges["slot_width_mm"] == (6.0, 10.0)
    assert np.allclose(md.frame["load_force_n"], 500.0)


@needs_data
def test_closed_form_volumes_match_role1_cad_volumes(data):
    for key in tf.TEMPLATES:
        rows = data.rows(family=key)
        X = rows[list(tf.TEMPLATE_PARAMS[key])].to_numpy(dtype=float)
        err = np.abs(tf.template_volume_mm3(key, X) / rows["cad_volume_mm3"].to_numpy() - 1)
        assert err.max() < 2e-4, key  # the ribbed templates leave out the tip blends, as v1 does
        assert all(tf.failed_rules(key, dict(zip(tf.TEMPLATE_PARAMS[key], x))) == [] for x in X), key  # every built row passes the rules
    assert power_law.FAMILY_VOLUME[T02] is tf.TEMPLATE_VOLUME[T02]


def test_shipped_fits_cover_every_template_and_are_the_power_law():
    fits = tf.load_fits()
    assert set(fits) == set(tf.TEMPLATES) and set(power_law.builtin_families()) >= set(tf.TEMPLATES)
    for key, entry in fits.items():
        fit = power_law.builtin_fit(key)
        assert fit.inputs == (*FAMILY_PARAMS[key], "poissons_ratio") and fit.trained_on_runs == 80
        assert len(fit.coef["max_stress_mpa"]) == len(fit.inputs) + 1 and fit.coef["max_stress_mpa"][1] < -1.0  # thickness drives stress down
        assert 0 < fit.sigma["max_stress_mpa"] < 0.08 and 0 < fit.sigma["max_disp_mm"] < 0.05
        assert set(fit.ranges) == set(fit.inputs) and entry["validation"]["rows"] in (0, 16)
    assert power_law.model_name_for(T02) == "power_law_t02_single_slot_v1" and power_law.model_name_for("l_bracket_v0") == "power_law_v0"


@needs_data
def test_shipped_fits_reproduce_role1s_rows(data):
    """The shipped fit on its own rows: stress median under 5%, deflection under 3%, load and stiffness scale exactly."""
    for key in tf.TEMPLATES:
        md = data.model_data(key)
        fit = power_law.builtin_fit(key)
        med, band = fit.predict_arrays(md.X(), 500.0, 200_000.0)
        for m in ("max_stress_mpa", "max_disp_mm"):
            err = np.abs(med[m] / md.frame[m].to_numpy() - 1)
            assert np.median(err) < (0.05 if m == "max_stress_mpa" else 0.03), (key, m, np.median(err))
            inside = np.abs(np.log(med[m] / md.frame[m].to_numpy())) <= np.log(band[m])
            assert inside.mean() >= 0.8, (key, m)
        med2, _ = fit.predict_arrays(md.X(), 1000.0, 100_000.0)
        assert np.allclose(med2["max_stress_mpa"], 2 * med["max_stress_mpa"]) and np.allclose(med2["max_disp_mm"], 4 * med["max_disp_mm"])


@needs_data
def test_refit_reproduces_the_shipped_constants(data):
    fits = multifamily.fit_templates(data)
    shipped = tf.load_fits()
    for key in (T02, T09):
        assert fits[key]["coef"]["max_stress_mpa"] == pytest.approx(shipped[key]["coef"]["max_stress_mpa"], abs=1e-5)
        assert fits[key]["validation"]["rows"] == 16 and fits[key]["validation"]["max_stress_mpa_median"] < 0.05
    assert fits["t07_stepped_v1"]["validation"]["rows"] == 0
    table = multifamily.markdown(multifamily.benchmark(data))
    assert "shared ridge" in table and "power law, t10_offset_planform_v1" in table


# ---------------------------------------------------------------- the emulator


def test_template_families_are_registered_and_matched():
    assert set(FAMILIES) == set(FAMILY_PARAMS) and len(FAMILIES) == 12
    found = match_family(["Thickness", "Flange length", "Arm width", "Fillet radius", "Vertical height", "Slot width", "Slot straight length"])
    assert found and found[0] is TEMPLATE_FAMILIES[T02] and len(found[1]) == 7
    hole = ["Thickness", "Flange length", "Arm width", "Hole diameter", "Fillet radius"]
    assert match_family(hole)[0].key == "l_bracket_v0"  # the five v0 names stay v0
    assert match_family(hole + ["Vertical height"])[0].key == "t01_double_hole_v1"  # the double-hole template is the six
    assert match_family(hole + ["Vertical height", "Gusset thickness", "Gusset length"])[0].key == "l_bracket_gusset_v1"  # not T08: it has no rules on the file
    spec = next(s for s in TOOL_SPECS if s["name"] == "use_family")
    assert set(spec["parameters"]["properties"]["family"]["enum"]) == set(FAMILIES)


def test_template_part_opens_confirms_solves_optimizes_and_saves(tmp_path):
    fam = TEMPLATE_FAMILIES[T02]
    part = App().open(write_demo_part(tmp_path / "slot.dsco", family=fam))
    assert part.family is fam and part.missing == [] and part.problems == []
    assert "Slot width 8 mm (6 to 10)" in part.describe()
    part.confirm()
    v = part.solve()
    assert v.source == "power_law_t02_single_slot_v1" and set(v.design) == set(FAMILY_PARAMS[T02])
    X = np.array([[fam.baseline_mm[p] for p in FAMILY_PARAMS[T02]] + [0.30]])
    med, _ = power_law.builtin_fit(T02).predict_arrays(X, 500.0, 200_000.0)
    assert v.max_stress_mpa == pytest.approx(med["max_stress_mpa"][0]) and v.mass_kg == pytest.approx(tf.template_volume_mm3(T02, X)[0] * 7850e-9)
    with pytest.raises(ValueError, match="outside"):
        part.set_dimensions({"Slot width": 12.0})
    result = part.optimize(constraints=LIMITS, n_candidates=1500, n_verify=3)
    Result.model_validate(result.model_dump())
    assert result.spec.bracket_family == T02 and len(result.spec.parameters) == 7
    best = result.recommended
    assert best and best.feasible and best.safety_factor >= 1.5 and set(best.design) == set(FAMILY_PARAMS[T02])
    assert fam.failed_rules(best.design) == [] and all(fam.valid(c.design) for c in part.last_prediction.shortlist)
    assert part.last_prediction.model_name == "power_law_t02_single_slot_v1"
    assert any("beam equations describe the v0" in w for w in result.warnings)
    saved = part.save(tmp_path / "opt.dsco", best.design)
    assert App().open(saved).values_mm == pytest.approx(best.design)


def test_broken_template_design_is_refused_by_name(tmp_path):
    fam = TEMPLATE_FAMILIES[T09]
    part = App().open(write_demo_part(tmp_path / "bad.dsco", family=fam, values_mm=BAD_T09))
    assert any("rib_hole_clearance" in p for p in part.problems) and any("outside" in p for p in part.problems)
    with pytest.raises(SetupError):
        part.confirm()
    good = App().open(write_demo_part(tmp_path / "good.dsco", family=fam))
    with pytest.raises(ValueError, match="outside"):
        good.set_dimensions({"Rib hole diameter": 10.0, "Gusset length": 28.0})  # the range check fires first, never a clamp
    assert good.values_mm == fam.baseline_mm  # nothing changed on refusal


def test_gp_predictor_falls_back_to_the_power_law_for_a_template(tmp_path):
    part = App(predictor="gp").open(write_demo_part(tmp_path / "tapered.dsco", family=TEMPLATE_FAMILIES["t06_tapered_v1"]))
    part.confirm()
    result = part.optimize(constraints=LIMITS, n_candidates=1000, n_verify=2)  # no GP dataset for a template, so no sklearn needed
    assert part.last_prediction.model_name == "power_law_t06_tapered_v1" and result.spec.bracket_family == "t06_tapered_v1"


def test_agent_tools_drive_a_template(tmp_path):
    tools = PyDiscoveryTools()
    dsco = write_demo_part(tmp_path / "window.dsco", family=TEMPLATE_FAMILIES["t04_rounded_window_v1"])
    assert tools.call("open_part", {"path": str(dsco)})["state"]["family"] == "t04_rounded_window_v1"
    tools.call("confirm_setup", {})
    out = tools.call("solve_design", {"values": {"Window width": 14.0}})["verified"]
    assert out["source"] == "power_law_t04_rounded_window_v1" and out["design"]["window_width_mm"] == 14.0
