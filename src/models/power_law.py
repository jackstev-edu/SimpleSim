"""Power-law surrogate for the L-bracket v0, fitted to Role 1's 200 CalculiX runs (Role 2, v0).

log(stress) and log(deflection) are linear in the log of the five dimensions. For a linear
elastic part, load and material scale exactly: stress with force, deflection with force over E.
Mass needs no model: the closed-form volume matches Role 1's meshes to 1e-15.

Fitted on branch Jin(role--data) at a4af313, data/processed/formal_v0/runs.csv: 200 runs,
500 N along -Y, E 200,000 MPa. 5-fold CV error: stress median 1.0%, p95 3.5%; deflection
median 0.8%, p95 2.7%. Labels use Role 1's production mesh. A 30% finer mesh read peak stress
2 to 7% higher on three rows, so stress here leans optimistic. Refit with fit_power_law() when
Role 1 adds runs; the GP and AutoGluon models replace this when they land.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from contracts.fakes import MATERIALS
from contracts.models import PARAM_NAMES, Candidate, Estimate, Prediction, StudySpec

# log(y) = intercept + sum(exponent * log(dimension_mm)) at the reference load and stiffness.
COEF = {
    "max_stress_mpa": (7.426128, -1.89175, 1.164444, -0.993156, -0.007968, -0.206133),
    "max_disp_mm": (-5.545976, -3.074263, 3.248545, -1.046525, 0.026359, -0.08748),
}
SIGMA_LOG = {"max_stress_mpa": 0.017938, "max_disp_mm": 0.012194}  # CV residual spread in log space
Z90 = 1.645  # two-sided 90% interval; CV coverage was 92% for stress, 90% for deflection
REF_FORCE_N, REF_E_MPA = 500.0, 200_000.0
TRAINED_ON_RUNS = 200
TRAIN_RANGES_MM = {
    "thickness_mm": (4.01, 9.98),
    "flange_length_mm": (75.19, 119.82),
    "arm_width_mm": (32.09, 59.98),
    "hole_diameter_mm": (6.01, 11.98),
    "fillet_radius_mm": (2.02, 7.99),
}
MOUNT_HEIGHT_MM, HOLE_X_MM = 60.0, 60.0  # fixed in the v0 family


def volume_mm3(X: np.ndarray) -> np.ndarray:
    """Closed-form solid volume for rows of (t, L, w, d, r) in contract order."""
    t, L, w, d, r = X.T
    return w * (MOUNT_HEIGHT_MM * t + (L - t) * t + (1 - math.pi / 4) * r**2) - math.pi * (d / 2) ** 2 * t


def predict_arrays(X: np.ndarray, force_n: float, e_mpa: float) -> dict[str, np.ndarray]:
    """Median stress and deflection for rows of designs, at any load and stiffness."""
    A = np.column_stack([np.ones(len(X)), np.log(X)])
    stress = np.exp(A @ np.array(COEF["max_stress_mpa"])) * force_n / REF_FORCE_N
    disp = np.exp(A @ np.array(COEF["max_disp_mm"])) * (force_n / REF_FORCE_N) * (REF_E_MPA / e_mpa)
    return {"max_stress_mpa": stress, "max_disp_mm": disp}


class PowerLawPredictor:
    """Same interface as contracts.fakes.FakePredictor: predict(spec) -> Prediction."""

    model_name = "power_law_v0"

    def __init__(self, e_mpa: float | None = None, yield_mpa: float | None = None, density_kg_m3: float | None = None):
        self.override = {"E_mpa": e_mpa, "yield_mpa": yield_mpa, "rho_kg_mm3": density_kg_m3 * 1e-9 if density_kg_m3 else None}

    def predict(self, spec: StudySpec, n: int | None = None, seed: int = 0) -> Prediction:
        mat = {k: v if v is not None else MATERIALS[spec.material_key][k] for k, v in self.override.items()}  # part's own material wins
        ranges = {p.name: (p.low_mm, p.high_mm) for p in spec.parameters}
        rng = np.random.default_rng(seed)
        n = n or spec.n_candidates
        X = np.column_stack([rng.uniform(*ranges[name], n) for name in PARAM_NAMES])
        med = predict_arrays(X, spec.load.force_n, mat["E_mpa"])
        k = {m: math.exp(Z90 * SIGMA_LOG[m]) for m in SIGMA_LOG}  # multiplicative 90% band
        mass = volume_mm3(X) * mat["rho_kg_mm3"]
        worst = {"mass_kg": mass, "max_stress_mpa": med["max_stress_mpa"] * k["max_stress_mpa"],
                 "max_disp_mm": med["max_disp_mm"] * k["max_disp_mm"]}
        worst["safety_factor"] = mat["yield_mpa"] / worst["max_stress_mpa"]
        ok = np.ones(n, dtype=bool)
        for c in spec.constraints:  # feasible only if every constraint holds at the pessimistic end
            ok &= worst[c.metric] >= c.value if c.op == ">=" else worst[c.metric] <= c.value
        score = mass if spec.objective.metric == "mass_kg" else (
            mat["yield_mpa"] / med["max_stress_mpa"] if spec.objective.metric == "safety_factor" else med[spec.objective.metric])
        sign = 1 if spec.objective.direction == "minimize" else -1
        order = [i for i in np.argsort(sign * score) if ok[i]][: spec.n_verify * 4]
        lo = np.array([TRAIN_RANGES_MM[p][0] for p in PARAM_NAMES])
        hi = np.array([TRAIN_RANGES_MM[p][1] for p in PARAM_NAMES])
        shortlist = []
        for i in order:
            s, d = med["max_stress_mpa"][i], med["max_disp_mm"][i]
            shortlist.append(Candidate(
                design_id=f"pl-{seed}-{i:05d}",
                design={p: float(X[i, j]) for j, p in enumerate(PARAM_NAMES)},
                mass_kg=float(mass[i]),
                max_stress_mpa=Estimate(value=s, p05=s / k["max_stress_mpa"], p95=s * k["max_stress_mpa"]),
                max_disp_mm=Estimate(value=d, p05=d / k["max_disp_mm"], p95=d * k["max_disp_mm"]),
                safety_factor=Estimate(value=mat["yield_mpa"] / s, p05=mat["yield_mpa"] / (s * k["max_stress_mpa"]),
                                       p95=mat["yield_mpa"] * k["max_stress_mpa"] / s),
                feasible_p95=True,
                in_training_range=bool(np.all((X[i] >= lo) & (X[i] <= hi))),
            ))
        warnings = [f"power-law surrogate fitted to {TRAINED_ON_RUNS} CalculiX runs; CV error about 1% median, 3.5% p95 on stress"]
        if not shortlist:
            warnings.append(f"no design in {n} samples met every constraint at the 90% bound")
        return Prediction(model_name=self.model_name, trained_on_runs=TRAINED_ON_RUNS, shortlist=shortlist, warnings=warnings)


class PowerLawBackend:
    """The surrogate as an engine: instant, but not an independent check of its own predictions."""

    name = "power_law_v0"

    def solve(self, family, design_mm, force_n, material):
        if family.key != "l_bracket_v0":
            raise ValueError(f"the power law is fitted to l_bracket_v0 only, not {family.key}")
        X = np.array([[design_mm[p] for p in PARAM_NAMES]])
        out = predict_arrays(X, force_n, material.youngs_mpa)
        return {"max_stress_mpa": float(out["max_stress_mpa"][0]), "max_disp_mm": float(out["max_disp_mm"][0]),
                "mass_kg": float(volume_mm3(X)[0] * material.density_kg_m3 * 1e-9)}


def fit_power_law(runs_csv: str | Path) -> tuple[dict, dict]:
    """Refit COEF and SIGMA_LOG from a runs.csv in the contract format (one load, one stiffness)."""
    import pandas as pd

    runs = pd.read_csv(runs_csv)
    A = np.column_stack([np.ones(len(runs)), np.log(runs[list(PARAM_NAMES)].values)])
    folds = np.array_split(np.random.default_rng(0).permutation(len(runs)), 5)
    coef, sigma = {}, {}
    for metric in COEF:
        y = np.log(runs[metric].values * REF_FORCE_N / runs["load_force_n"].values)  # normalise to the reference load
        coef[metric] = tuple(round(float(c), 6) for c in np.linalg.lstsq(A, y, rcond=None)[0])
        resid = []
        for f in folds:  # held-out residuals give an honest interval width
            train = np.setdiff1d(np.arange(len(runs)), f)
            resid.append(A[f] @ np.linalg.lstsq(A[train], y[train], rcond=None)[0] - y[f])
        sigma[metric] = round(float(np.concatenate(resid).std()), 6)
    return coef, sigma
