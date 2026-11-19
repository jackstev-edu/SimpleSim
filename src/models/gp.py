"""Gaussian process predictor (Role 2): the power law as the mean, plus a GP on what it misses.

Trains from a family's model_dataset.csv (or a contract runs.csv) every time it starts: about
1 s for the 200 v0 runs, about a minute for the 1,474 v1 training rows. The GP has one ARD
length scale per input (the dimensions and Poisson's ratio, all in log space), so an input
that barely matters gets a long scale. Its band widens away from the data; calibrate() then
scales the band so 90% of held-out rows fall inside it, because the raw band ran narrow on
v0 (80 to 86%). Labels from v0's uniform mesh get the same stress correction as the power
law. Needs scikit-learn: pip install -r src/models/requirements.txt. Numbers in docs/models.md.
"""

from __future__ import annotations

import copy
import warnings
from pathlib import Path

import numpy as np

from contracts.fakes import MATERIALS
from contracts.models import PARAM_NAMES, Prediction, StudySpec

from .datasets import METRICS, V0, V1, is_v0_mesh_rule, load_model_dataset
from .power_law import (
    FAMILY_VOLUME,
    V0_STRESS_CORRECTION,
    Z90,
    apply_stress_correction,
    build_prediction,
    resolve_material,
    sample_inside,
    volume_mm3,
    with_poisson,
)

DEFAULT_RUNS = Path("data/processed/formal_v0/runs.csv")
ROLE1_E_MPA = 200_000.0  # Role 1's reference material, role1_isotropic_reference
# Band scales from `python -m src.models.benchmark` (docs/model_data/<family>_benchmark.json), applied when the GP
# trains from a family's file: the raw band ran narrow on v0 and these put 90% of held-out rows inside it.
BAND_SCALE: dict[str, dict[str, float]] = {
    V0: {"max_stress_mpa": 1.118, "max_disp_mm": 0.7399},  # 5-fold out-of-fold on the 160 non-test v0 rows
    V1: {"max_stress_mpa": 1.0089, "max_disp_mm": 0.6611},  # the 164 v1 calibration rows, GP trained on 1,474 with one restart
}


class GPPredictor:
    """predict(spec) -> Prediction, trained on a model_dataset.csv, a runs.csv or a DataFrame in either shape."""

    def __init__(self, runs=DEFAULT_RUNS, e_mpa: float | None = None, yield_mpa: float | None = None,
                 density_kg_m3: float | None = None, restarts: int = 3, inputs: tuple[str, ...] | None = None,
                 family: str | None = None, poisson: float | None = None, max_train: int | None = None, seed: int = 0,
                 calibrated: bool = True):
        """runs: a family key, a path or a DataFrame. inputs default to the dataset's summary.json (or the five v0 names).

        max_train: train the GP on a seeded subset of this many rows when the full table is too slow (the mean still
        uses every row). restarts: optimizer restarts for the kernel hyperparameters. calibrated: apply the family's
        benchmarked band scale (file-trained predictors only; a DataFrame is a study of its own).
        """
        try:  # scikit-learn is Role 2's own requirement, not the team's
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
        except ImportError as exc:
            raise ImportError("The GP needs scikit-learn: pip install -r src/models/requirements.txt") from exc
        import pandas as pd

        data = load_model_dataset(runs, frame=runs if isinstance(runs, pd.DataFrame) else None, inputs=inputs, family=family)
        self.data, self.family, self.inputs = data, data.family, data.inputs
        self.model_name = "gp_powerlaw_v1" if self.family == V0 else f"gp_powerlaw_{self.family}"
        self.override = {"E_mpa": e_mpa, "yield_mpa": yield_mpa, "rho_kg_mm3": density_kg_m3 * 1e-9 if density_kg_m3 else None}
        self.poisson = poisson
        self.n_runs = len(data.frame)
        keys = data.frame["material_key"].unique()
        if len(keys) != 1:
            raise ValueError(f"training data mixes materials {list(keys)}; train on one stiffness at a time")
        self.ref_e = MATERIALS.get(keys[0], {"E_mpa": data.ref_e_mpa})["E_mpa"]
        self.ref_force = data.ref_force_n
        self.ranges = dict(data.ranges)
        self.band_scale = {m: 1.0 for m in METRICS}
        if calibrated and not isinstance(runs, pd.DataFrame) and self.family in BAND_SCALE and max_train is None:
            self.band_scale = dict(BAND_SCALE[self.family])
        self.stress_correction = V0_STRESS_CORRECTION if "mesh_rule" in data.frame and is_v0_mesh_rule(data.frame["mesh_rule"]) else None
        Z = np.log(data.X())
        self.mu, self.sd = Z.mean(0), Z.std(0)
        A = np.column_stack([np.ones(len(Z)), Z])
        rows = np.arange(len(Z))
        if max_train is not None and max_train < len(Z):
            rows = np.sort(np.random.default_rng(seed).choice(len(Z), max_train, replace=False))
        self.n_gp_rows = len(rows)
        self.beta, self.gp = {}, {}
        y = data.log_targets()
        for m in METRICS:
            self.beta[m] = np.linalg.lstsq(A, y[m], rcond=None)[0]  # power-law mean in log space, on every row
            kernel = (ConstantKernel(0.01, (1e-5, 10.0)) * Matern(np.ones(Z.shape[1]), (1e-2, 1e3), nu=2.5)
                      + WhiteKernel(1e-5, (1e-9, 1e-2)))  # one length scale per input (ARD)
            with warnings.catch_warnings():  # long length scales just mean that input barely matters
                warnings.simplefilter("ignore")
                self.gp[m] = GaussianProcessRegressor(kernel, n_restarts_optimizer=restarts, random_state=0).fit(
                    ((Z - self.mu) / self.sd)[rows], (y[m] - A @ self.beta[m])[rows])  # the GP learns only the leftover

    @property
    def geometry(self) -> tuple[str, ...]:
        return tuple(n for n in self.inputs if n != "poissons_ratio")

    def for_material(self, e_mpa: float, yield_mpa: float, density_kg_m3: float, poisson: float | None = None, valid=None) -> "GPPredictor":
        """A copy sharing the trained GP, set to a part's material (and geometry rules), so training happens once."""
        other = copy.copy(self)
        other.override = {"E_mpa": e_mpa, "yield_mpa": yield_mpa, "rho_kg_mm3": density_kg_m3 * 1e-9}
        other.poisson = poisson if poisson is not None else self.poisson
        other.valid = valid if valid is not None else getattr(self, "valid", None)
        return other

    def predict_log(self, X: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Mean and calibrated spread of log(metric) at the reference load and stiffness. X in self.inputs order."""
        Z = np.log(np.asarray(X, dtype=float))
        A = np.column_stack([np.ones(len(Z)), Z])
        out = {}
        for m in METRICS:
            resid, sd = self.gp[m].predict((Z - self.mu) / self.sd, return_std=True)
            out[m] = (A @ self.beta[m] + resid, sd * self.band_scale[m])
        return out

    def calibrate(self, X: np.ndarray, y_log: dict[str, np.ndarray], target: float = 0.90) -> "GPPredictor":
        """Scale each band so `target` of the given held-out rows fall inside it."""
        self.band_scale = {m: 1.0 for m in METRICS}
        log = self.predict_log(X)
        for m in METRICS:
            z = np.abs(log[m][0] - y_log[m]) / (Z90 * log[m][1])
            self.band_scale[m] = round(float(np.quantile(z, target)), 4)
        return self

    def predict_arrays(self, X: np.ndarray, force_n: float, e_mpa: float) -> tuple[dict, dict]:
        """Median values and multiplicative 90% bands, scaled to any load and stiffness, stress corrected for v0 labels."""
        log = self.predict_log(X)
        scale = {"max_stress_mpa": force_n / self.ref_force, "max_disp_mm": (force_n / self.ref_force) * (self.ref_e / e_mpa)}
        med = {m: np.exp(log[m][0]) * scale[m] for m in METRICS}
        band = {m: np.exp(Z90 * log[m][1]) for m in METRICS}
        return apply_stress_correction(med, band, log["max_stress_mpa"][1], self.stress_correction)

    def predict(self, spec: StudySpec, n: int | None = None, seed: int = 0) -> Prediction:
        if spec.bracket_family != self.family:
            raise ValueError(f"this GP is trained on {self.family}, not {spec.bracket_family}")
        mat = resolve_material(spec, self.override)
        names = self.geometry
        X = sample_inside(spec, n or spec.n_candidates, seed, names)
        med, band = self.predict_arrays(with_poisson(X, self.inputs, self.poisson), spec.load.force_n, mat["E_mpa"])
        note = f"GP on a power-law mean, trained on {self.n_runs} runs of {self.family}; 90% bands widen away from the data"
        if self.stress_correction:
            note += f"; stress lifted x{self.stress_correction['ratio']:.3f} for the v0 mesh bias"
        return build_prediction(spec, X, med, band, mat, self.model_name, self.n_runs, self.ranges, f"gp-{seed}", [note],
                                names=names, volume=FAMILY_VOLUME.get(self.family, volume_mm3), valid=getattr(self, "valid", None))


CACHE_DIR = Path("data/processed/gp_cache")  # gitignored (*.pkl); a trained v1 GP is 4 to 6 minutes of fitting
RESTARTS = {V0: 3, V1: 1}  # v1 keeps one restart, as in the benchmark; the band scales above match it


def trained_gp(family: str = V0, source: str | Path | None = None, cache_dir: str | Path = CACHE_DIR, **kwargs) -> GPPredictor:
    """A GP for a family from its model_dataset.csv (or `source`), cached on disk so training happens once per dataset.

    The cache key is the data file's size and modification time, the restarts, the inputs and scikit-learn's version;
    a changed dataset retrains. Pass cache_dir=None to skip the cache.
    """
    import pickle

    from .datasets import FAMILY_DIRS, resolve_source

    path = resolve_source(source if source is not None else family)
    restarts = kwargs.pop("restarts", RESTARTS.get(family, 3))
    if cache_dir is None or not path.exists():
        return GPPredictor(path, family=family, restarts=restarts, **kwargs)
    stat = path.stat()
    try:
        import sklearn

        version = sklearn.__version__
    except ImportError:  # GPPredictor raises the helpful message
        version = "none"
    key = f"{family}_{stat.st_size}_{int(stat.st_mtime)}_{restarts}_{kwargs.get('max_train')}_{version}".replace(".", "_")
    cache = Path(cache_dir) / f"{key}.pkl"
    if cache.exists():
        try:
            gp = pickle.loads(cache.read_bytes())
            gp.override = {"E_mpa": kwargs.get("e_mpa"), "yield_mpa": kwargs.get("yield_mpa"),
                           "rho_kg_mm3": kwargs["density_kg_m3"] * 1e-9 if kwargs.get("density_kg_m3") else None}
            return gp
        except Exception:  # noqa: BLE001  a stale or foreign pickle just means retraining
            pass
    gp = GPPredictor(path, family=family, restarts=restarts, **kwargs)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(gp))
    except OSError:
        pass
    return gp


def cross_validate(runs_csv: str | Path = DEFAULT_RUNS, folds: int = 5, seed: int = 0, inputs: tuple[str, ...] | None = None) -> dict:
    """K-fold errors for the power law and the GP, for the model comparison in docs/models.md."""
    data = load_model_dataset(runs_csv, inputs=inputs)
    split = np.array_split(np.random.default_rng(seed).permutation(len(data.frame)), folds)
    errs = {k: {m: [] for m in METRICS} for k in ("power_law", "gp")}
    covered = {m: [] for m in METRICS}
    for f in split:
        gp = GPPredictor(data.frame.drop(index=data.frame.index[f]), inputs=data.inputs, family=data.family)  # train without the fold
        test = data.subset(f)
        X = test.X()
        Z = np.column_stack([np.ones(len(X)), np.log(X)])
        log = gp.predict_log(X)
        y = test.log_targets()
        for m in METRICS:
            errs["power_law"][m].append(Z @ gp.beta[m] - y[m])  # the GP's own mean is the power law
            errs["gp"][m].append(log[m][0] - y[m])
            covered[m].append(np.abs(log[m][0] - y[m]) <= Z90 * log[m][1])
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
