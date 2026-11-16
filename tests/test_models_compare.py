"""Model comparison (Role 2): learning curves on synthetic runs, result structure, clean skips.

Only numpy and pandas are needed for the baseline and power-law tests. The GP and tree-model tests
skip without scikit-learn, the figure test without matplotlib. AutoGluon is never run here.
"""

import json

import numpy as np
import pandas as pd
import pytest

from contracts.models import PARAM_NAMES
from src.models import compare, power_law

NAMES = list(PARAM_NAMES)


def synthetic_runs(n: int = 90, seed: int = 0, noise: float = 0.01) -> pd.DataFrame:
    """Runs in the contract's runs.csv shape, labelled by the built-in power law plus a little noise."""
    rng = np.random.default_rng(seed)
    X = np.column_stack([rng.uniform(*power_law.TRAIN_RANGES_MM[p], n) for p in NAMES])
    truth = power_law.predict_arrays(X, 500.0, 200_000.0)
    df = pd.DataFrame({p: X[:, i] for i, p in enumerate(NAMES)})
    df["material_key"], df["load_force_n"] = "structural_steel", 500.0
    for m, v in truth.items():
        df[m] = v * np.exp(rng.normal(0, noise, n))
    df["mass_kg"] = power_law.volume_mm3(X) * 7850e-9
    return df


@pytest.fixture(scope="module")
def basic():
    models = [compare.MedianBaseline(), compare.PowerLawModel()]
    return compare.learning_curve(synthetic_runs(), models, n_train=(15, 40), repeats=2, test_size=20)


def test_result_structure(basic):
    assert basic["test_size"] == 20 and basic["n_train"] == [15, 40] and basic["repeats"] == 2
    assert basic["models"] == ["median_baseline", "power_law"]
    assert len(basic["records"]) == 2 * 2 * 2 * 2  # models x sizes x repeats x targets
    assert len(basic["summary"]) == 2 * 2 * 2  # mean over repeats
    assert {r["metric"] for r in basic["records"]} == set(compare.METRICS)
    json.dumps(basic)  # what results.json holds


def test_errors_are_finite_and_ordered(basic):
    for r in basic["records"]:
        assert np.isfinite(r["median_pct"]) and np.isfinite(r["p95_pct"]) and r["fit_seconds"] >= 0
        assert 0 <= r["median_pct"] <= r["p95_pct"]
        if r["model"] == "power_law":
            assert 0.0 <= r["coverage_90"] <= 1.0
        else:
            assert "coverage_90" not in r  # the baseline has no band


def test_power_law_beats_the_median_baseline(basic):
    at = {(r["model"], r["metric"]): r for r in basic["summary"] if r["n_train"] == 40}
    for m in compare.METRICS:
        assert at[("power_law", m)]["median_pct"] < at[("median_baseline", m)]["median_pct"] / 5
        assert at[("median_baseline", m)]["coverage_90"] is None


def test_held_out_rows_never_train():
    runs = synthetic_runs()
    pool, test = compare.split(runs, seed=0, test_size=20)
    assert len(pool) == 70 and len(test) == 20
    assert not set(map(tuple, pool[NAMES].values)) & set(map(tuple, test[NAMES].values))
    with pytest.raises(ValueError, match="exceeds"):
        compare.learning_curve(runs, [compare.MedianBaseline()], n_train=(80,), repeats=1, test_size=20)


def test_gp_joins_the_comparison():
    pytest.importorskip("sklearn")
    out = compare.learning_curve(synthetic_runs(), [compare.GPModel(restarts=1)], n_train=(40,), repeats=1, test_size=20)
    for r in out["records"]:
        assert r["median_pct"] < 3.0 and 0.5 <= r["coverage_90"] <= 1.0


def test_tree_stand_in_gives_a_band():
    pytest.importorskip("sklearn")
    runs = synthetic_runs()
    model = compare.TreeQuantileModel()
    pool, test = compare.split(runs, test_size=20)
    model.fit(pool)
    for mean, lo, hi in model.predict_log(test).values():
        assert np.all(np.isfinite(mean)) and np.all(lo <= hi)


def test_autogluon_is_skipped_cleanly_when_missing(monkeypatch):
    monkeypatch.setattr(compare, "autogluon_available", lambda: False)
    models, skipped = compare.build_models(["power_law", "autogluon"])
    assert [m.name for m in models] == ["power_law", "hgb_quantile"]  # the stand-in takes its place
    assert "pip install autogluon.tabular" in skipped["autogluon"]
    with pytest.raises(ValueError, match="Unknown model"):
        compare.build_models(["nope"])


def test_markdown_table_and_figure(basic, tmp_path):
    table = compare.markdown_table(basic["summary"])
    assert "n = 40" in table and "`power_law`" in table and "`median_baseline`" in table
    pytest.importorskip("matplotlib")
    assert compare.plot_learning_curve(basic["summary"], tmp_path / "lc.png").stat().st_size > 1000


def test_cli_runs_without_optional_models(tmp_path):
    runs = tmp_path / "runs.csv"
    synthetic_runs().to_csv(runs, index=False)
    code = compare.main(["--runs", str(runs), "--out", str(tmp_path / "out"), "--figure", str(tmp_path / "lc.png"),
                         "--models", "median_baseline", "power_law", "--n", "15", "40", "--repeats", "1"])
    assert code == 0
    result = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert result["models"] == ["median_baseline", "power_law"] and result["skipped"] == {}
    assert compare.main(["--runs", str(tmp_path / "missing.csv")]) == 1
