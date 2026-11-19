"""Role 2 models on both families: the dataset loader, the closed-form volumes, the power law fit and
calibration, the GP's inputs, the benchmark's calibration path and the v0 mesh-bias table.

Only numpy and pandas are needed except where sklearn is imported. Role 1's datasets are committed
under data/processed, so the real-data checks run everywhere; they skip if the files are missing.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from contracts.models import PARAM_NAMES
from src.models import benchmark, compare, power_law, v0_mesh_bias
from src.models.datasets import FAMILY_DIRS, V0, V1, load_model_dataset, split_indices

ROOT = Path(__file__).resolve().parents[1]
HAVE_DATA = all((ROOT / d / "model_dataset.csv").exists() for d in FAMILY_DIRS.values())
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="Role 1's model_dataset.csv files are missing")


@pytest.fixture(scope="module")
def v1():
    return load_model_dataset(ROOT / FAMILY_DIRS[V1])


@pytest.fixture(scope="module")
def v0():
    return load_model_dataset(ROOT / FAMILY_DIRS[V0])


@needs_data
def test_loader_reads_each_family_with_its_summary_inputs(v0, v1):
    assert v0.family == V0 and v0.inputs == (*PARAM_NAMES, "poissons_ratio") and len(v0.frame) == 200
    assert v1.family == V1 and v1.inputs == (*PARAM_NAMES, "vertical_height_mm", "gusset_thickness_mm", "gusset_length_mm", "poissons_ratio")
    assert len(v1.frame) == 2048 and v1.geometry == v1.inputs[:-1]
    for d in (v0, v1):
        assert {"max_stress_mpa", "max_disp_mm", "mass_kg", "volume_mm3", "load_force_n", "material_key", "mesh_rule"} <= set(d.frame)
        assert d.ranges["poissons_ratio"] == (0.25, 0.35) and d.ref_force_n == 500.0 and d.ref_e_mpa == 200_000.0
    assert v1.ranges["gusset_length_mm"] == (12.0, 35.0)  # from summary.json, not the sampled extremes


def test_loader_takes_a_contract_runs_frame_without_poisson():
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({p: rng.uniform(*power_law.TRAIN_RANGES_MM[p], 30) for p in PARAM_NAMES})
    frame["load_force_n"], frame["max_stress_mpa"], frame["max_disp_mm"] = 500.0, 100.0, 0.1
    data = load_model_dataset("frame", frame=frame)
    assert data.inputs == tuple(PARAM_NAMES) and not data.has_poisson and data.family == V0
    with pytest.raises(FileNotFoundError, match="runs.csv"):
        load_model_dataset(ROOT / "nope" / "runs.csv")


def test_split_is_seeded_disjoint_and_sized():
    train, cal, test = split_indices(2048, seed=0)
    assert len(test) == 410 and len(cal) == 164 and len(train) == 1474
    assert not (set(train) & set(cal)) and not (set(train) & set(test)) and not (set(cal) & set(test))
    assert np.array_equal(train, split_indices(2048, seed=0)[0]) and not np.array_equal(train, split_indices(2048, seed=1)[0])


@needs_data
def test_closed_form_volumes_match_role1_meshes(v0, v1):
    X0 = v0.frame[list(v0.geometry)].to_numpy()
    assert np.abs(power_law.volume_mm3(X0) / v0.frame.volume_mm3.to_numpy() - 1).max() < 1e-12
    X1 = v1.frame[list(v1.geometry)].to_numpy()
    assert np.abs(power_law.volume_v1_mm3(X1) / v1.frame.volume_mm3.to_numpy() - 1).max() < 1e-3  # under the 0.1% gate


@needs_data
def test_v1_power_law_fits_and_calibrates(v1):
    train, cal, test = split_indices(len(v1.frame))
    fit = power_law.PowerLawFit.fit(v1.subset(train))
    assert fit.inputs == v1.inputs and len(fit.coef["max_stress_mpa"]) == len(v1.inputs) + 1
    assert fit.coef["max_stress_mpa"][1] < -1.5  # stress falls steeply with thickness, as in v0
    fit.calibrate(v1.subset(cal).X(), v1.subset(cal).log_targets())
    held = v1.subset(test)
    med, band = fit.predict_arrays(held.X(), 500.0, 200_000.0)
    y = held.frame
    for m, col in (("max_stress_mpa", "max_stress_mpa"), ("max_disp_mm", "max_disp_mm")):
        err = np.abs(med[m] / y[col].to_numpy() - 1)
        inside = np.abs(np.log(med[m] / y[col].to_numpy())) <= np.log(band[m])
        assert np.median(err) < 0.06 and 0.85 <= inside.mean() <= 0.95
    med2, _ = fit.predict_arrays(held.X(), 1000.0, 100_000.0)  # load and stiffness scale exactly
    assert np.allclose(med2["max_stress_mpa"], 2 * med["max_stress_mpa"]) and np.allclose(med2["max_disp_mm"], 4 * med["max_disp_mm"])


@needs_data
def test_builtin_v1_power_law_is_the_calibrated_fit(v1):
    fit = power_law.builtin_fit(V1)
    assert fit.trained_on_runs == 1474 and fit.inputs == v1.inputs
    _, _, test = split_indices(len(v1.frame))
    held = v1.subset(test)
    med, band = fit.predict_arrays(held.X(), 500.0, 200_000.0)
    for m in ("max_stress_mpa", "max_disp_mm"):
        inside = np.abs(np.log(med[m] / held.frame[m].to_numpy())) <= np.log(band[m])
        assert 0.85 <= inside.mean() <= 0.95
    assert power_law.PowerLawPredictor(family=V1).model_name == "power_law_v1"


def test_v0_module_api_is_unchanged():
    X = np.array([[6.0, 80.0, 40.0, 8.0, 4.0]])
    raw = power_law.predict_arrays(X, 500.0, 200_000.0)
    assert raw["max_stress_mpa"][0] == pytest.approx(178.8, rel=0.03)
    med, band = power_law.builtin_fit(V0).predict_arrays(X, 500.0, 200_000.0)
    if power_law.V0_STRESS_CORRECTION is None:
        assert med["max_stress_mpa"][0] == raw["max_stress_mpa"][0]
    assert med["max_disp_mm"][0] == raw["max_disp_mm"][0]
    assert band["max_stress_mpa"][0] == pytest.approx(np.exp(power_law.Z90 * power_law.SIGMA_LOG["max_stress_mpa"]))
    assert power_law.with_poisson(X, tuple(PARAM_NAMES), 0.3).shape == (1, 5)
    assert power_law.with_poisson(X, (*PARAM_NAMES, "poissons_ratio"), None)[0, 5] == 0.30


def test_stress_correction_lifts_the_median_and_widens_the_band():
    med = {"max_stress_mpa": np.array([100.0]), "max_disp_mm": np.array([1.0])}
    band = {"max_stress_mpa": np.array([1.05]), "max_disp_mm": np.array([1.02])}
    m2, b2 = power_law.apply_stress_correction(med, band, np.array([0.02]), {"ratio": 1.1, "log_spread": 0.05})
    assert m2["max_stress_mpa"][0] == pytest.approx(110.0) and b2["max_stress_mpa"][0] > band["max_stress_mpa"][0]
    assert m2["max_disp_mm"][0] == 1.0 and b2["max_disp_mm"][0] == 1.02
    same = power_law.apply_stress_correction(med, band, np.array([0.02]), None)
    assert same[0] is med and same[1] is band


@needs_data
def test_benchmark_calibrates_out_of_fold_when_the_slice_is_small(v0):
    train, cal, test = benchmark.frame_split(v0)
    assert len(cal) < benchmark.MIN_CALIBRATION_ROWS
    r = benchmark.evaluate(compare.PowerLawModel(v0.inputs), train, cal, test, v0.inputs)
    assert r["calibrated_on"].startswith("5-fold") and set(r["band_scale"]) == {"max_stress_mpa", "max_disp_mm"}
    assert r["raw"]["max_stress_mpa"]["median_pct"] == r["calibrated"]["max_stress_mpa"]["median_pct"]  # calibration moves the band only
    assert 0.5 < r["band_scale"]["max_stress_mpa"] < 2.0
    assert benchmark.volume_check(v0)["max_abs_pct"] < 1e-9
    table = benchmark.markdown({"family": V0, "split": {"test": len(test)}, "models": [r]})
    assert "`power_law`" in table and "->" in table


@pytest.mark.skipif(not HAVE_DATA, reason="Role 1's datasets are missing")
def test_gp_takes_poisson_and_the_v1_inputs(v1):
    pytest.importorskip("sklearn")
    from src.models.gp import GPPredictor

    rows = v1.frame.iloc[:150]
    gp = GPPredictor(rows, inputs=v1.inputs, family=V1, restarts=0)
    assert gp.inputs == v1.inputs and gp.geometry == v1.geometry and gp.model_name == "gp_powerlaw_l_bracket_gusset_v1"
    assert gp.stress_correction is None  # v1 labels come from the curvature mesh
    held = v1.subset(np.arange(150, 250))
    med, band = gp.predict_arrays(held.X(), 500.0, 200_000.0)
    err = np.abs(med["max_disp_mm"] / held.frame.max_disp_mm.to_numpy() - 1)
    assert np.median(err) < 0.03 and np.all(band["max_disp_mm"] > 1.0)
    gp.calibrate(held.X(), held.log_targets())
    assert all(0.2 < s < 5 for s in gp.band_scale.values())


@pytest.mark.skipif(not (ROOT / v0_mesh_bias.OUT_CSV).exists(), reason="docs/model_data/v0_mesh_bias.csv missing")
def test_mesh_bias_table_is_the_seeded_spread():
    table = pd.read_csv(ROOT / v0_mesh_bias.OUT_CSV)
    picked = v0_mesh_bias.pick_designs(pd.read_csv(ROOT / FAMILY_DIRS[V0] / "model_dataset.csv"))
    assert list(table.design_id) == list(picked.design_id) and len(table) == 48
    s = v0_mesh_bias.summarise(table)
    assert s["solved"] == 48 and 0.9 < s["stress_ratio"]["median"] < 1.1 and s["disp_ratio"]["std_pct"] < 1.0
