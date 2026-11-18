"""Gaussian process predictor (Role 2): the power law as the mean, plus a GP on what it misses.

Learns from Role 1's runs.csv (contract Run rows) every time it starts, about 1 s for 200 runs.
On the 200 CalculiX runs, 5-fold CV: stress median 0.8%, p95 3.2%; deflection median 0.6%,
p95 1.3%; the 90% bands held the true value 89% (stress) and 91% (deflection) of the time.
Unlike the power law, its band widens away from the data, so it gets more careful where it
knows less. Needs scikit-learn: pip install -r src/models/requirements.txt.
"""

from __future__ import annotations

import copy
import warnings
from pathlib import Path

import numpy as np

from contracts.fakes import MATERIALS
from contracts.models import PARAM_NAMES, Prediction, StudySpec

from .power_law import Z90, build_prediction, resolve_material, sample_inside

DEFAULT_RUNS = Path("data/processed/formal_v0/runs.csv")
METRICS = ("max_stress_mpa", "max_disp_mm")
ROLE1_E_MPA = 200_000.0  # Role 1's reference material, role1_isotropic_reference


class GPPredictor:
    """predict(spec) -> Prediction, trained on a runs.csv in the contract format."""

    model_name = "gp_powerlaw_v1"

    def __init__(self, runs=DEFAULT_RUNS, e_mpa: float | None = None, yield_mpa: float | None = None,
                 density_kg_m3: float | None = None, restarts: int = 3):
        """runs: a path to runs.csv or a DataFrame with the same columns."""
        try:  # scikit-learn is Role 2's own requirement, not the team's
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
        except ImportError as exc:
            raise ImportError("The GP needs scikit-learn: pip install -r src/models/requirements.txt") from exc
        import pandas as pd

        if not isinstance(runs, pd.DataFrame):
            if not Path(runs).exists():
                raise FileNotFoundError(f"No training runs at {runs}. Copy Role 1's runs.csv there (see docs/models.md).")
            runs = pd.read_csv(runs)
        self.override = {"E_mpa": e_mpa, "yield_mpa": yield_mpa, "rho_kg_mm3": density_kg_m3 * 1e-9 if density_kg_m3 else None}
        self.n_runs = len(runs)
        keys = runs["material_key"].unique()
        if len(keys) != 1:
            raise ValueError(f"runs.csv mixes materials {list(keys)}; train on one stiffness at a time")
        self.ref_e = MATERIALS.get(keys[0], {"E_mpa": ROLE1_E_MPA})["E_mpa"]
        self.ref_force = 500.0
        Z = np.log(runs[list(PARAM_NAMES)].values)
        self.mu, self.sd = Z.mean(0), Z.std(0)
        self.ranges = {p: (float(runs[p].min()), float(runs[p].max())) for p in PARAM_NAMES}
        A = np.column_stack([np.ones(len(Z)), Z])
        self.beta, self.gp = {}, {}
        for m in METRICS:
            y = np.log(runs[m].values * self.ref_force / runs["load_force_n"].values)  # normalise to one load
            self.beta[m] = np.linalg.lstsq(A, y, rcond=None)[0]  # power-law mean in log space
            kernel = ConstantKernel(0.01, (1e-5, 10.0)) * Matern(np.ones(5), (1e-2, 1e3), nu=2.5) + WhiteKernel(1e-5, (1e-9, 1e-2))
            with warnings.catch_warnings():  # long length scales just mean that dimension barely matters
                warnings.simplefilter("ignore")
                self.gp[m] = GaussianProcessRegressor(kernel, n_restarts_optimizer=restarts, random_state=0).fit(
                    (Z - self.mu) / self.sd, y - A @ self.beta[m])  # the GP learns only the leftover

    def for_material(self, e_mpa: float, yield_mpa: float, density_kg_m3: float) -> "GPPredictor":
        """A copy sharing the trained GP, set to a part's material, so training happens once."""
        other = copy.copy(self)
        other.override = {"E_mpa": e_mpa, "yield_mpa": yield_mpa, "rho_kg_mm3": density_kg_m3 * 1e-9}
        return other

    def predict_log(self, X: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Mean and spread of log(metric) at the reference load and stiffness."""
        Z = np.log(X)
        A = np.column_stack([np.ones(len(Z)), Z])
        out = {}
        for m in METRICS:
            resid, sd = self.gp[m].predict((Z - self.mu) / self.sd, return_std=True)
            out[m] = (A @ self.beta[m] + resid, sd)
        return out

    def predict_arrays(self, X: np.ndarray, force_n: float, e_mpa: float) -> tuple[dict, dict]:
        """Median values and multiplicative 90% bands, scaled to any load and stiffness."""
        log = self.predict_log(X)
        scale = {"max_stress_mpa": force_n / self.ref_force, "max_disp_mm": (force_n / self.ref_force) * (self.ref_e / e_mpa)}
        med = {m: np.exp(log[m][0]) * scale[m] for m in METRICS}
        band = {m: np.exp(Z90 * log[m][1]) for m in METRICS}
        return med, band

    def predict(self, spec: StudySpec, n: int | None = None, seed: int = 0) -> Prediction:
        mat = resolve_material(spec, self.override)
        X = sample_inside(spec, n or spec.n_candidates, seed)
        med, band = self.predict_arrays(X, spec.load.force_n, mat["E_mpa"])
        note = f"GP on a power-law mean, trained on {self.n_runs} runs; 90% bands widen away from the data"
        return build_prediction(spec, X, med, band, mat, self.model_name, self.n_runs, self.ranges, f"gp-{seed}", [note])


def cross_validate(runs_csv: str | Path = DEFAULT_RUNS, folds: int = 5, seed: int = 0) -> dict:
    """K-fold errors for the power law and the GP, for the model comparison in docs/models.md."""
    import pandas as pd

    runs = pd.read_csv(runs_csv)
    split = np.array_split(np.random.default_rng(seed).permutation(len(runs)), folds)
    errs = {k: {m: [] for m in METRICS} for k in ("power_law", "gp")}
    covered = {m: [] for m in METRICS}
    for f in split:
        gp = GPPredictor(runs.drop(index=runs.index[f]))  # train without the held-out fold
        test = runs.iloc[f]
        X = test[list(PARAM_NAMES)].values
        Z = np.column_stack([np.ones(len(X)), np.log(X)])
        log = gp.predict_log(X)
        for m in METRICS:
            y = np.log(test[m].values * gp.ref_force / test["load_force_n"].values)
            errs["power_law"][m].append(Z @ gp.beta[m] - y)  # the GP's own mean is the power law
            errs["gp"][m].append(log[m][0] - y)
            covered[m].append(np.abs(log[m][0] - y) <= Z90 * log[m][1])
    report = {}
    for model, by_metric in errs.items():
        for m, chunks in by_metric.items():
            e = np.abs(np.exp(np.concatenate(chunks)) - 1) * 100
            report[f"{model}/{m}"] = {"median_pct": round(float(np.median(e)), 2), "p95_pct": round(float(np.percentile(e, 95)), 2)}
    for m in METRICS:
        report[f"gp/{m}"]["coverage_90"] = round(float(np.concatenate(covered[m]).mean()), 2)
    return report


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(cross_validate(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUNS), indent=2))
