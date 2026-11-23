"""Fast-path benchmark: problem files and their keys (spec docs/superpowers/specs/2026-11-22-fast-path-benchmark-design.md).

The key's truth is the fitted model's best design, judged with the same rule as Jack's shortlist: every limit
must hold at the pessimistic end of the model's 90 % band (build_prediction in src/models/power_law.py).
The median optimum and the best CalculiX row (500 N steel only) are recorded for information.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from contracts.models import Constraint, Load, Objective, ParameterRange, StudySpec
from src.models.power_law import FAMILY_VOLUME, PowerLawPredictor, sample_inside, with_poisson
from src.pydiscovery.families import FAMILIES
from src.pydiscovery.session import table_material

REPO = Path(__file__).resolve().parents[2]
FAST_DIR = REPO / "practice" / "fast"
RUNS = {"l_bracket_v0": REPO / "data" / "processed" / "formal_v0" / "runs.csv",
        "l_bracket_gusset_v1": REPO / "data" / "processed" / "formal_v1_l_bracket_gusset_v1" / "runs.csv"}
SIM_YIELD_MPA = 250.0  # Role 1's reference steel
FAST_PATH_CANDIDATES = 2000  # Part.optimize's sample, seed 0: the scoring key (user decision 2026-11-23)
DENSE_CANDIDATES = 400_000  # the much denser search, reported as information only
FIXED_TOL = 0.01  # a table row counts as "not varied" when within 1 % of the baseline


@dataclass
class FastProblem:
    id: str
    family: str
    request: str
    hidden_facts: str
    key: dict
    path: Path


def load_fast_problem(path: Path, require_key: bool = True) -> FastProblem:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for field in ("id", "family", "request", "hidden_facts", "key"):
        if not data.get(field):
            raise ValueError(f"{path.name}: missing field '{field}'")
    if data["family"] not in FAMILIES:
        raise ValueError(f"{path.name}: unknown family {data['family']!r}; known {sorted(FAMILIES)}")
    key = data["key"]
    if require_key and not key.get("expect_handoff") and not key.get("model_optimum"):
        raise ValueError(f"{path.name}: key has no model_optimum; run python -m scripts.build_fast_keys first")
    return FastProblem(data["id"], data["family"], data["request"], data["hidden_facts"], key, path)


def load_fast_problems(folder: Path = FAST_DIR, require_key: bool = True) -> list[FastProblem]:
    return [load_fast_problem(p, require_key) for p in sorted(Path(folder).glob("*.yaml"))]


def _predictor(family: str, material_key: str) -> PowerLawPredictor:
    m = table_material(material_key)  # the same material record the fast path builds from the key
    return PowerLawPredictor(e_mpa=m.youngs_mpa, yield_mpa=m.yield_mpa, density_kg_m3=m.density_kg_m3,
                             family=family, poisson=m.poisson, valid=FAMILIES[family].valid)


def _arrays(pred: PowerLawPredictor, material_key: str, force_n: float, X: np.ndarray) -> dict:
    """Mass, median and pessimistic responses for each row of X (geometry columns in fit order)."""
    m = table_material(material_key)
    med, band = pred.fit.predict_arrays(with_poisson(X, pred.fit.inputs, pred.poisson), force_n, m.youngs_mpa)
    mass = FAMILY_VOLUME[pred.family](X) * m.density_kg_m3 * 1e-9
    out = {"mass_kg": mass}
    for metric in ("max_stress_mpa", "max_disp_mm"):
        out[f"{metric}_med"] = med[metric]
        out[f"{metric}_worst"] = med[metric] * band[metric]
    out["safety_factor_med"] = m.yield_mpa / out["max_stress_mpa_med"]
    out["safety_factor_worst"] = m.yield_mpa / out["max_stress_mpa_worst"]
    return out


def model_eval(family: str, material_key: str, force_n: float, design: dict[str, float]) -> dict:
    pred = _predictor(family, material_key)
    X = np.array([[float(design[n]) for n in pred.fit.geometry]])
    return {k: float(v[0]) for k, v in _arrays(pred, material_key, force_n, X).items()}


def _value(ev: dict, metric: str, at: str):
    return ev["mass_kg"] if metric == "mass_kg" else ev[f"{metric}_{at}"]


def meets(ev: dict, safety_factor: float, limits: list[dict], at: str = "worst") -> bool:
    ok = _value(ev, "safety_factor", at) >= safety_factor
    for lim in limits:
        v = _value(ev, lim["metric"], at)
        ok = ok & (v >= lim["value"] if lim["op"] == ">=" else v <= lim["value"])
    return bool(np.all(ok))


def objective_value(ev: dict, metric: str) -> float:
    """What the shortlist ranks by: mass, or the median of a response."""
    return float(_value(ev, metric, "med"))


def _varied(study: dict, family: str) -> set[str]:
    vary = study.get("vary") or ["all"]
    return set(FAMILIES[family].ranges_mm) if "all" in vary else set(vary)


def study_spec(study: dict, family: str, n: int) -> StudySpec:
    fam, varied = FAMILIES[family], _varied(study, family)
    params = [ParameterRange(name=k, low_mm=lo, high_mm=hi) if k in varied
              else ParameterRange(name=k, low_mm=fam.baseline_mm[k], high_mm=fam.baseline_mm[k])
              for k, (lo, hi) in fam.ranges_mm.items()]
    cons = [Constraint(metric="safety_factor", op=">=", value=study["safety_factor"])]
    cons += [Constraint(**lim) for lim in study.get("limits") or []]
    return StudySpec(bracket_family=family, objective=Objective(**study["objective"]), constraints=cons,
                     parameters=params, load=Load(force_n=study["force_n"], angle_deg=study.get("angle_deg", -90.0)),
                     material_key=study["material_key"], n_candidates=n)


def model_optimum(study: dict, family: str, n: int = 400_000, seed: int = 0) -> dict:
    pred = _predictor(family, study["material_key"])
    spec = study_spec(study, family, n)
    obj = study["objective"]
    best = pred.predict(spec, n=n, seed=seed).shortlist  # ranked, all feasible at the pessimistic bound
    if not best:
        raise ValueError(f"no design meets the limits at the pessimistic bound in {n} samples")
    design = dict(best[0].design)
    ev = model_eval(family, study["material_key"], study["force_n"], design)
    # median optimum: the same sample, limits judged at the model's median instead
    X = sample_inside(spec, n, seed, pred.fit.geometry)
    arr = _arrays(pred, study["material_key"], study["force_n"], X)
    ok = np.array([meets({k: v[i] for k, v in arr.items()}, study["safety_factor"], study.get("limits") or [], at="med")
                   for i in range(len(X))]) & np.array([FAMILIES[family].valid(dict(zip(pred.fit.geometry, row))) for row in X])
    score = np.asarray(_value(arr, obj["metric"], "med"), dtype=float) * (1 if obj["direction"] == "minimize" else -1)
    i = int(np.argmin(np.where(ok, score, np.inf)))
    return {"design": design, "objective_value": objective_value(ev, obj["metric"]),
            "safety_factor_worst": ev["safety_factor_worst"], "method": f"power_law predict, n={n}, seed={seed}",
            "median": {"design": dict(zip(pred.fit.geometry, map(float, X[i]))),
                       "objective_value": float(abs(score[i]))}}


def sim_optimum(study: dict, family: str) -> dict:
    none = {"design_id": None, "design": None, "objective_value": None, "max_stress_mpa": None}
    if not (study["force_n"] == 500.0 and study.get("angle_deg", -90.0) == -90.0
            and study["material_key"] == "structural_steel"):
        return {**none, "reason": "the CalculiX tables are 500 N, -90 deg, steel only"}
    path = RUNS[family]
    if not path.exists():
        return {**none, "reason": f"{path.name} not in this checkout"}
    df = pd.read_csv(path)
    fam, varied = FAMILIES[family], _varied(study, family)
    ok = SIM_YIELD_MPA / df["max_stress_mpa"] >= study["safety_factor"]
    for lim in study.get("limits") or []:
        col = df[lim["metric"]] if lim["metric"] != "safety_factor" else SIM_YIELD_MPA / df["max_stress_mpa"]
        ok &= col >= lim["value"] if lim["op"] == ">=" else col <= lim["value"]
    if not ok.any():
        return {**none, "reason": "no table row meets the safety factor and limits"}
    for k in fam.ranges_mm:
        if k not in varied:
            ok &= (df[k] - fam.baseline_mm[k]).abs() <= FIXED_TOL * fam.baseline_mm[k]
    rows = df[ok]
    if rows.empty:
        return {**none, "reason": "no table row that meets the limits has the non-varied dimensions at baseline"}
    obj = study["objective"]
    col = rows[obj["metric"]] if obj["metric"] != "safety_factor" else SIM_YIELD_MPA / rows["max_stress_mpa"]
    r = rows.loc[col.idxmin() if obj["direction"] == "minimize" else col.idxmax()]
    return {"design_id": str(r["design_id"]), "design": {k: float(r[k]) for k in fam.ranges_mm},
            "objective_value": float(r[obj["metric"]] if obj["metric"] != "safety_factor" else SIM_YIELD_MPA / r["max_stress_mpa"]),
            "max_stress_mpa": float(r["max_stress_mpa"])}
