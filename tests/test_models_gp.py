"""GP predictor (Role 2): trains on contract runs, shortlists through the emulator, stays calibrated.

Skips without scikit-learn (Role 2's own requirement, not the team's). The real-data check runs only
when Role 1's runs.csv is at data/processed/formal_v0/runs.csv (gitignored).
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")

from contracts.models import Prediction  # noqa: E402
from src.models import power_law  # noqa: E402
from src.models.gp import DEFAULT_RUNS, GPPredictor, cross_validate  # noqa: E402
from src.pydiscovery import App, L_BRACKET_V0  # noqa: E402
from src.pydiscovery.demo_part import write_demo_part  # noqa: E402

NAMES = list(L_BRACKET_V0.labels)
REAL = Path(__file__).resolve().parents[1] / DEFAULT_RUNS


def synthetic_runs(n: int = 80, seed: int = 0, noise: float = 0.01) -> pd.DataFrame:
    """Runs in the contract's runs.csv shape, labelled by the power law plus a little noise."""
    rng = np.random.default_rng(seed)
    X = np.column_stack([rng.uniform(*L_BRACKET_V0.ranges_mm[p], n) for p in NAMES])
    truth = power_law.predict_arrays(X, 500.0, 200_000.0)
    df = pd.DataFrame({p: X[:, i] for i, p in enumerate(NAMES)})
    df["material_key"], df["load_force_n"] = "structural_steel", 500.0
    for m, v in truth.items():
        df[m] = v * np.exp(rng.normal(0, noise, n))
    df["mass_kg"] = power_law.volume_mm3(X) * 7850e-9
    return df


def test_gp_learns_and_scales():
    gp = GPPredictor(synthetic_runs(), restarts=1)
    X = np.array([[L_BRACKET_V0.baseline_mm[p] for p in NAMES]])
    med, band = gp.predict_arrays(X, 500.0, 200_000.0)
    truth = power_law.predict_arrays(X, 500.0, 200_000.0)
    for m in med:
        assert med[m][0] == pytest.approx(truth[m][0], rel=0.03)
        assert 1.0 < band[m][0] < 1.2  # a real, finite 90% band
    med2, _ = gp.predict_arrays(X, 1000.0, 100_000.0)  # load and stiffness scale exactly
    assert med2["max_stress_mpa"][0] == pytest.approx(2 * med["max_stress_mpa"][0])
    assert med2["max_disp_mm"][0] == pytest.approx(4 * med["max_disp_mm"][0])


def test_band_widens_away_from_the_data():
    runs = synthetic_runs()
    gp = GPPredictor(runs[runs.thickness_mm < 7.0], restarts=1)  # no thick designs in training
    inside = np.array([[5.5, 90, 45, 9, 5]])
    outside = np.array([[9.8, 90, 45, 9, 5]])
    assert gp.predict_log(outside)["max_stress_mpa"][1][0] > gp.predict_log(inside)["max_stress_mpa"][1][0]


def test_gp_drives_the_emulator(tmp_path):
    runs = tmp_path / "runs.csv"
    synthetic_runs().to_csv(runs, index=False)
    part = App(predictor="gp", runs=runs).open(write_demo_part(tmp_path / "lb.dsco"))
    part.confirm()
    result = part.optimize(constraints=[("safety_factor", ">=", 1.5)])
    assert isinstance(part.last_prediction, Prediction) and part.last_prediction.model_name == "gp_powerlaw_v1"
    assert result.recommended and result.recommended.feasible
    assert result.finalists[0].design_id == part.last_prediction.shortlist[0].design_id  # GUI joins on these ids


def test_missing_runs_file_says_where_to_put_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="runs.csv"):
        GPPredictor(tmp_path / "nope.csv")


@pytest.mark.skipif(not REAL.exists(), reason="Role 1's runs.csv not at data/processed/formal_v0/")
def test_cross_validation_on_role1_runs():
    report = cross_validate(REAL)
    assert report["gp/max_stress_mpa"]["median_pct"] < 1.0
    assert report["gp/max_disp_mm"]["p95_pct"] < 2.0
    for m in ("max_stress_mpa", "max_disp_mm"):
        assert 0.8 <= report[f"gp/{m}"]["coverage_90"] <= 0.97  # honest 90% bands
        assert report[f"gp/{m}"]["median_pct"] <= report[f"power_law/{m}"]["median_pct"]
