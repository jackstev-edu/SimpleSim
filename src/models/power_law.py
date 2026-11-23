"""Power-law surrogates (Role 2): log-linear in the log of a family's inputs, built in for v0, v1
and Role 1's ten bracket templates (T01 to T10).

log(stress) and log(deflection) are linear in the log of the dimensions and of Poisson's ratio
at the reference load. For a linear elastic part, load and material scale exactly: stress with
force, deflection with force over E. Mass needs no model: closed-form volumes match Role 1's
meshes to 1e-15 (v0), 0.012% (v1, four tip blends of the junction fillet left out) and 0.02%
(the templates, src/models/template_families.py).

v0: fitted to Role 1's 200 CalculiX runs (data/processed/formal_v0), five dimensions, no
Poisson term. Those labels come from v0's uniform 1 to 2 mm mesh, which under-reads peak
stress at the fillet; V0_STRESS_CORRECTION (from src/models/v0_mesh_bias.py, 48 re-solves on
the v1 mesh rule) lifts predictions and widens the band. v1: fitted to the 2,048-run gusseted
dataset with eight dimensions plus Poisson's ratio, band calibrated on held-out rows. The
templates: one fit each on its 80 rows of data/processed/formal_multifamily_v1, constants in
template_fits.json (refit with `python -m src.models.multifamily --fit`). Numbers and protocol
in docs/models.md; refit v1 with `python -m src.models.benchmark`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from contracts.fakes import MATERIALS
from contracts.models import PARAM_NAMES, Candidate, Estimate, Prediction, StudySpec

from .datasets import DEFAULT_POISSON, FAMILY_GEOMETRY, METRICS, REF_E_MPA, REF_FORCE_N, V0, V1, ModelData, load_model_dataset
from .template_families import TEMPLATE_VOLUME, TEMPLATES, load_fits

# ---------------------------------------------------------------- v0, built in (unchanged since the 200-run fit)
# log(y) = intercept + sum(exponent * log(dimension_mm)) at the reference load and stiffness.
COEF = {
    "max_stress_mpa": (7.426128, -1.89175, 1.164444, -0.993156, -0.007968, -0.206133),
    "max_disp_mm": (-5.545976, -3.074263, 3.248545, -1.046525, 0.026359, -0.08748),
}
SIGMA_LOG = {"max_stress_mpa": 0.017938, "max_disp_mm": 0.012194}  # CV residual spread in log space
Z90 = 1.645  # two-sided 90% interval; CV coverage was 92% for stress, 90% for deflection
TRAINED_ON_RUNS = 200
TRAIN_RANGES_MM = {
    "thickness_mm": (4.01, 9.98),
    "flange_length_mm": (75.19, 119.82),
    "arm_width_mm": (32.09, 59.98),
    "hole_diameter_mm": (6.01, 11.98),
    "fillet_radius_mm": (2.02, 7.99),
}
MOUNT_HEIGHT_MM, HOLE_X_MM = 60.0, 60.0  # fixed in the v0 family
GUSSET_JUNCTION_FILLET_MM = 2.0  # fixed in the v1 family (src/data/gusset_v1/spec.py)

# v0 labels against the v1 mesh rule on 48 re-solved v0 designs (docs/model_data/v0_mesh_bias.csv):
# stress ratio median and the spread of its log, added to the band in quadrature. None means no correction.
V0_STRESS_CORRECTION: dict[str, float] | None = None

# ---------------------------------------------------------------- v1, built in (filled by `python -m src.models.benchmark --family l_bracket_gusset_v1 --print-constants`)
INPUTS_V1 = (*FAMILY_GEOMETRY[V1], "poissons_ratio")
COEF_V1 = {
    "max_stress_mpa": (6.336239, -1.890336, 1.40602, -0.67724, -0.006863, -0.009917, -0.002009, -0.146532, -0.259673, -0.004063),
    "max_disp_mm": (-7.08483, -2.877543, 3.76089, -0.892189, 0.055127, -0.006453, -0.008093, -0.133955, -0.712857, -0.067582),
}
SIGMA_LOG_V1 = {"max_stress_mpa": 0.046405, "max_disp_mm": 0.062692}  # 5-fold held-out spread on the 1,474 training rows
BAND_SCALE_V1 = {"max_stress_mpa": 0.9583, "max_disp_mm": 0.9176}  # puts 90% of the 164 calibration rows inside the band
TRAINED_ON_RUNS_V1 = 1474
TRAIN_RANGES_V1 = {
    "thickness_mm": (4.0, 10.0),
    "flange_length_mm": (75.0, 120.0),
    "arm_width_mm": (32.0, 60.0),
    "hole_diameter_mm": (6.0, 12.0),
    "fillet_radius_mm": (2.5, 8.0),
    "vertical_height_mm": (50.0, 90.0),
    "gusset_thickness_mm": (3.0, 10.0),
    "gusset_length_mm": (12.0, 35.0),
    "poissons_ratio": (0.25, 0.35),
}


# ---------------------------------------------------------------- closed-form volumes


def volume_mm3(X: np.ndarray) -> np.ndarray:
    """v0 solid volume for rows of (t, L, w, d, r) in contract order."""
    t, L, w, d, r = X.T[:5]
    return w * (MOUNT_HEIGHT_MM * t + (L - t) * t + (1 - math.pi / 4) * r**2) - math.pi * (d / 2) ** 2 * t


def volume_v1_mm3(X: np.ndarray) -> np.ndarray:
    """v1 solid volume for rows of (t, L, w, d, r, H, gusset t, gusset length): legs, fillet, hole, gusset, junction fillet."""
    t, L, w, d, r, H, gt, gl = X.T[:8]
    k90 = 1 - math.pi / 4  # material a concave fillet adds per unit length at a 90 degree edge, over R^2
    k135 = 1 / math.tan(3 * math.pi / 8) - math.pi / 8  # same at the 135 degree edge where the gusset tip meets a leg
    jf = GUSSET_JUNCTION_FILLET_MM
    legs = w * (H * t + (L - t) * t + k90 * r**2) - math.pi * (d / 2) ** 2 * t
    gusset = 0.5 * gl * gl * gt - k90 * r**2 * gt  # the triangle, minus the inside-fillet fill it already covers
    junction = k90 * jf**2 * (4 * (gl - r) + math.pi * r) + 2 * k135 * jf**2 * gt  # fillets along the 8 junction edges
    return legs + gusset + junction


FAMILY_VOLUME = {V0: volume_mm3, V1: volume_v1_mm3, **TEMPLATE_VOLUME}


# ---------------------------------------------------------------- the fitted model


@dataclass
class PowerLawFit:
    """One family's log-linear fit: coefficients per target, a residual spread and a calibrated band scale."""

    family: str
    inputs: tuple[str, ...]
    coef: dict[str, tuple[float, ...]]  # intercept first, then one exponent per input
    sigma: dict[str, float]  # held-out residual spread in log space
    band_scale: dict[str, float] = field(default_factory=lambda: {m: 1.0 for m in METRICS})
    ref_force_n: float = REF_FORCE_N
    ref_e_mpa: float = REF_E_MPA
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    trained_on_runs: int = 0
    stress_correction: dict[str, float] | None = None  # {"ratio", "log_spread"}: labels from a mesh that under-reads stress

    @property
    def geometry(self) -> tuple[str, ...]:
        return tuple(n for n in self.inputs if n != "poissons_ratio")

    @classmethod
    def fit(cls, data: ModelData, folds: int = 5, seed: int = 0) -> "PowerLawFit":
        """Least squares in log space; sigma from K-fold held-out residuals so the band is honest."""
        A = design_matrix(data.X())
        y = data.log_targets()
        splits = np.array_split(np.random.default_rng(seed).permutation(len(A)), folds)
        coef, sigma = {}, {}
        for m in METRICS:
            coef[m] = tuple(round(float(c), 6) for c in np.linalg.lstsq(A, y[m], rcond=None)[0])
            resid = []
            for f in splits:
                train = np.setdiff1d(np.arange(len(A)), f)
                resid.append(A[f] @ np.linalg.lstsq(A[train], y[m][train], rcond=None)[0] - y[m][f])
            sigma[m] = round(float(np.concatenate(resid).std()), 6)
        return cls(data.family, data.inputs, coef, sigma, ref_force_n=data.ref_force_n, ref_e_mpa=data.ref_e_mpa,
                   ranges=dict(data.ranges), trained_on_runs=len(A))

    def predict_log(self, X: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Mean and (calibrated) spread of log(metric) at the reference load and stiffness. X in self.inputs order."""
        A = design_matrix(X)
        return {m: (A @ np.array(self.coef[m]), np.full(len(A), self.sigma[m] * self.band_scale[m])) for m in METRICS}

    def calibrate(self, X: np.ndarray, y_log: dict[str, np.ndarray], target: float = 0.90) -> "PowerLawFit":
        """Scale each band so `target` of the given rows fall inside it."""
        log = self.predict_log(X)
        for m in METRICS:
            z = np.abs(log[m][0] - y_log[m]) / (Z90 * self.sigma[m])
            self.band_scale[m] = round(float(np.quantile(z, target)), 4)
        return self

    def predict_arrays(self, X: np.ndarray, force_n: float, e_mpa: float) -> tuple[dict, dict]:
        """Median values and multiplicative 90% bands at any load and stiffness, stress corrected when the labels need it."""
        log = self.predict_log(X)
        med = {"max_stress_mpa": np.exp(log["max_stress_mpa"][0]) * force_n / self.ref_force_n,
               "max_disp_mm": np.exp(log["max_disp_mm"][0]) * (force_n / self.ref_force_n) * (self.ref_e_mpa / e_mpa)}
        band = {m: np.exp(Z90 * log[m][1]) for m in METRICS}
        return apply_stress_correction(med, band, log["max_stress_mpa"][1], self.stress_correction)

    def constants(self) -> dict:
        """What to paste into this module to make the fit built in."""
        return {"inputs": self.inputs, "coef": self.coef, "sigma": self.sigma, "band_scale": self.band_scale,
                "trained_on_runs": self.trained_on_runs, "ranges": {k: (round(lo, 4), round(hi, 4)) for k, (lo, hi) in self.ranges.items()}}


def design_matrix(X: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(X)), np.log(np.asarray(X, dtype=float))])


def apply_stress_correction(med: dict, band: dict, sd_log: np.ndarray, correction: dict[str, float] | None) -> tuple[dict, dict]:
    """Lift the stress median by the mesh-bias ratio and add the ratio's spread to the band in quadrature."""
    if correction:
        med = {**med, "max_stress_mpa": med["max_stress_mpa"] * correction["ratio"]}
        band = {**band, "max_stress_mpa": np.exp(Z90 * np.sqrt(sd_log**2 + correction["log_spread"] ** 2))}
    return med, band


def with_poisson(X_geom: np.ndarray, inputs: tuple[str, ...], poisson: float | None) -> np.ndarray:
    """Append the Poisson column when the fit takes one; geometry columns come first in every family."""
    if "poissons_ratio" not in inputs:
        return X_geom
    return np.column_stack([X_geom, np.full(len(X_geom), poisson if poisson is not None else DEFAULT_POISSON)])


def builtin_fit(family: str = V0) -> PowerLawFit:
    """The shipped coefficients for a family, with the v0 mesh correction attached for v0."""
    if family == V0:
        return PowerLawFit(V0, tuple(PARAM_NAMES), COEF, SIGMA_LOG, ranges=dict(TRAIN_RANGES_MM), trained_on_runs=TRAINED_ON_RUNS,
                           stress_correction=V0_STRESS_CORRECTION)
    if family == V1 and COEF_V1:
        return PowerLawFit(V1, INPUTS_V1, COEF_V1, SIGMA_LOG_V1, band_scale=dict(BAND_SCALE_V1), ranges=dict(TRAIN_RANGES_V1),
                           trained_on_runs=TRAINED_ON_RUNS_V1)
    fits = load_fits()
    if family in fits:  # Role 1's templates, from template_fits.json
        c = fits[family]
        return PowerLawFit(family, tuple(c["inputs"]), {m: tuple(v) for m, v in c["coef"].items()}, dict(c["sigma"]),
                           band_scale=dict(c["band_scale"]), ranges={k: tuple(v) for k, v in c["ranges"].items()}, trained_on_runs=c["trained_on_runs"])
    raise ValueError(f"No built-in power law for {family!r}; known: {builtin_families()}")


def builtin_families() -> list[str]:
    """Every family with shipped coefficients: v0, v1 and the templates that template_fits.json carries."""
    return [V0] + ([V1] if COEF_V1 else []) + [k for k in TEMPLATES if k in load_fits()]


def model_name_for(family: str) -> str:
    return f"power_law_{'v0' if family == V0 else 'v1' if family == V1 else family}"


# ---------------------------------------------------------------- v0 module API, kept for callers and tests


def predict_arrays(X: np.ndarray, force_n: float, e_mpa: float) -> dict[str, np.ndarray]:
    """v0 median stress and deflection at the v0 mesh (no bias correction), for rows of (t, L, w, d, r)."""
    A = design_matrix(X)
    stress = np.exp(A @ np.array(COEF["max_stress_mpa"])) * force_n / REF_FORCE_N
    disp = np.exp(A @ np.array(COEF["max_disp_mm"])) * (force_n / REF_FORCE_N) * (REF_E_MPA / e_mpa)
    return {"max_stress_mpa": stress, "max_disp_mm": disp}


def fit_power_law(runs_csv: "str | Path | pd.DataFrame", inputs: tuple[str, ...] = tuple(PARAM_NAMES)) -> tuple[dict, dict]:
    """Refit COEF and SIGMA_LOG from a runs.csv or model_dataset.csv (one load, one stiffness).

    Takes a path or a DataFrame, so compare.py can refit on training subsets.
    """
    import pandas as pd

    data = load_model_dataset(runs_csv, frame=runs_csv if isinstance(runs_csv, pd.DataFrame) else None, inputs=inputs)
    fit = PowerLawFit.fit(data)
    return fit.coef, fit.sigma


# ---------------------------------------------------------------- the shortlist step, shared with the GP


def build_prediction(spec: StudySpec, X: np.ndarray, med: dict[str, np.ndarray], band: dict[str, np.ndarray],
                     mat: dict, model_name: str, trained_on_runs: int, train_ranges: dict, id_prefix: str,
                     warnings: list[str], names: tuple[str, ...] = tuple(PARAM_NAMES), volume=volume_mm3, valid=None) -> Prediction:
    """Judge every constraint at the pessimistic end of a multiplicative band. X holds the geometry columns in `names` order.

    valid: an optional design dict -> bool; designs that fail a family's geometry rules never reach the shortlist.
    """
    mass = volume(X) * mat["rho_kg_mm3"]
    worst = {"mass_kg": mass, "max_stress_mpa": med["max_stress_mpa"] * band["max_stress_mpa"],
             "max_disp_mm": med["max_disp_mm"] * band["max_disp_mm"]}
    worst["safety_factor"] = mat["yield_mpa"] / worst["max_stress_mpa"]
    ok = np.ones(len(X), dtype=bool)
    for c in spec.constraints:  # feasible only if every constraint holds at the pessimistic end
        ok &= worst[c.metric] >= c.value if c.op == ">=" else worst[c.metric] <= c.value
    if valid is not None:
        ok &= np.array([valid({p: float(X[i, j]) for j, p in enumerate(names)}) for i in range(len(X))], dtype=bool)
    score = mass if spec.objective.metric == "mass_kg" else (
        mat["yield_mpa"] / med["max_stress_mpa"] if spec.objective.metric == "safety_factor" else med[spec.objective.metric])
    sign = 1 if spec.objective.direction == "minimize" else -1
    order = [i for i in np.argsort(sign * score) if ok[i]][: spec.n_verify * 4]
    lo = np.array([train_ranges[p][0] for p in names])
    hi = np.array([train_ranges[p][1] for p in names])
    shortlist = []
    for i in order:
        s, d = float(med["max_stress_mpa"][i]), float(med["max_disp_mm"][i])
        ks, kd = float(band["max_stress_mpa"][i]), float(band["max_disp_mm"][i])
        shortlist.append(Candidate(
            design_id=f"{id_prefix}-{i:05d}",
            design={p: float(X[i, j]) for j, p in enumerate(names)},
            mass_kg=float(mass[i]),
            max_stress_mpa=Estimate(value=s, p05=s / ks, p95=s * ks),
            max_disp_mm=Estimate(value=d, p05=d / kd, p95=d * kd),
            safety_factor=Estimate(value=mat["yield_mpa"] / s, p05=mat["yield_mpa"] / (s * ks), p95=mat["yield_mpa"] * ks / s),
            feasible_p95=True,
            in_training_range=bool(np.all((X[i] >= lo) & (X[i] <= hi))),
        ))
    if not shortlist:
        warnings = warnings + [f"no design in {len(X)} samples met every constraint at the 90% bound"]
    return Prediction(model_name=model_name, trained_on_runs=trained_on_runs, shortlist=shortlist, warnings=warnings)


def sample_inside(spec: StudySpec, n: int, seed: int, names: tuple[str, ...] = tuple(PARAM_NAMES)) -> np.ndarray:
    """Uniform designs inside the spec's ranges, one column per name in order."""
    ranges = {p.name: (p.low_mm, p.high_mm) for p in spec.parameters}
    missing = [p for p in names if p not in ranges]
    if missing:
        raise ValueError(f"the spec gives no range for {missing}; the {spec.bracket_family} family needs every one of {list(names)}")
    rng = np.random.default_rng(seed)
    return np.column_stack([rng.uniform(*ranges[name], n) for name in names])


def resolve_material(spec: StudySpec, override: dict) -> dict:
    """The part's own material wins; otherwise the contracts' table entry for spec.material_key."""
    return {k: v if v is not None else MATERIALS[spec.material_key][k] for k, v in override.items()}


class PowerLawPredictor:
    """Same interface as contracts.fakes.FakePredictor: predict(spec) -> Prediction, for one family's built-in fit."""

    def __init__(self, e_mpa: float | None = None, yield_mpa: float | None = None, density_kg_m3: float | None = None,
                 family: str = V0, poisson: float | None = None, valid=None):
        """valid: an optional design dict -> bool from the family's geometry rules (PyDiscovery passes Family.valid)."""
        self.fit = builtin_fit(family)
        self.family, self.poisson, self.valid = family, poisson, valid
        self.model_name = model_name_for(family)
        self.override = {"E_mpa": e_mpa, "yield_mpa": yield_mpa, "rho_kg_mm3": density_kg_m3 * 1e-9 if density_kg_m3 else None}

    def predict(self, spec: StudySpec, n: int | None = None, seed: int = 0) -> Prediction:
        if spec.bracket_family != self.family:
            raise ValueError(f"this predictor is fitted to {self.family}, not {spec.bracket_family}")
        mat = resolve_material(spec, self.override)
        names = self.fit.geometry
        X = sample_inside(spec, n or spec.n_candidates, seed, names)
        med, band = self.fit.predict_arrays(with_poisson(X, self.fit.inputs, self.poisson), spec.load.force_n, mat["E_mpa"])
        note = f"power-law surrogate fitted to {self.fit.trained_on_runs} CalculiX runs of {self.family}; see docs/models.md for its error"
        if self.fit.stress_correction:
            note += f"; stress lifted x{self.fit.stress_correction['ratio']:.3f} for the v0 mesh bias"
        return build_prediction(spec, X, med, band, mat, self.model_name, self.fit.trained_on_runs, self.fit.ranges, f"pl-{seed}", [note],
                                names=names, volume=FAMILY_VOLUME[self.family], valid=self.valid)


class PowerLawBackend:
    """The surrogate as an engine: instant, but not an independent check of its own predictions."""

    name = "power_law_v0"

    def source(self, family) -> str:
        """What Verified.source says for a solve of this family."""
        return model_name_for(family.key)

    def solve(self, family, design_mm, force_n, material):
        try:
            fit = builtin_fit(family.key)
        except ValueError as exc:
            raise ValueError(f"the power law is fitted to {', '.join(builtin_families())} only, not {family.key}") from exc
        X = with_poisson(np.array([[design_mm[p] for p in fit.geometry]]), fit.inputs, getattr(material, "poisson", None))
        med, _ = fit.predict_arrays(X, force_n, material.youngs_mpa)
        return {"max_stress_mpa": float(med["max_stress_mpa"][0]), "max_disp_mm": float(med["max_disp_mm"][0]),
                "mass_kg": float(FAMILY_VOLUME[family.key](X)[0] * material.density_kg_m3 * 1e-9)}
