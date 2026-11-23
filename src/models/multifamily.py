"""Role 1's multi-family dataset (data/processed/formal_multifamily_v1) as model tables, and the fits.

    python -m src.models.multifamily --fit          # refit the ten template power laws into template_fits.json
    python -m src.models.multifamily --benchmark    # the comparison table in docs/models.md (add --gp for the slow GP row)

1,000 CalculiX cases over 11 bracket families: the 200 v0 runs plus 80 rows for each of ten
templates (T01 double hole to T10 offset planform). Every row carries Role 1's 20 shared CAD
features and, from the design manifest, the template's own dimensions. Two ways to learn it:

shared    one model on the 20 CAD features, so an unseen template (T07 in Role 1's split) can
          still be scored; needs the CAD solid at inference to compute the features
template  one power law per template on its own dimensions plus Poisson's ratio, instant and
          dependency-free like the v0 and v1 fits; cannot score a template it never saw

The template power laws are what ships (see docs/models.md for why): each is fitted on all 80
rows of its template with a 5-fold held-out sigma, as the v0 fit was; the benchmark scores a
train-only fit on Role 1's validation split. Role 1's split (train 736, validation 184, held-out
80 = the whole T07 family) is kept for the shared model, which is the comparison only.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .datasets import METRICS, REF_E_MPA, REF_FORCE_N, V0, ModelData
from .template_families import FITS_PATH, TEMPLATE_PARAMS, TEMPLATES, template_ranges

ROOT = Path("data/processed/formal_multifamily_v1")
RENAME = {"reference_max_stress_mpa": "max_stress_mpa", "reference_max_disp_mm": "max_disp_mm", "reference_mass_kg": "mass_kg"}
V0_INPUTS = ("thickness_mm", "flange_length_mm", "arm_width_mm", "hole_diameter_mm", "fillet_radius_mm", "poissons_ratio")


@dataclass
class MultiFamilyData:
    """The joined table: features, labels, split, family and the template dimensions per row."""

    frame: pd.DataFrame
    feature_columns: tuple[str, ...]
    root: Path = ROOT
    inputs_by_family: dict[str, tuple[str, ...]] = field(default_factory=dict)  # template dimensions in model order, Poisson last

    def rows(self, split: str | None = None, family: str | None = None) -> pd.DataFrame:
        f = self.frame
        if split:
            f = f[f["split"] == split]
        if family:
            f = f[f["bracket_family"] == family]
        return f.reset_index(drop=True)

    def model_data(self, family: str, rows: pd.DataFrame | None = None) -> ModelData:
        """One family's rows in the shape PowerLawFit.fit takes, with Role 1's sampled ranges."""
        f = self.rows(family=family) if rows is None else rows[rows["bracket_family"] == family].reset_index(drop=True)
        inputs = self.inputs_by_family[family]
        ranges = {**(template_ranges(family) if family in TEMPLATES else {c: (float(f[c].min()), float(f[c].max())) for c in inputs}),
                  "poissons_ratio": (0.25, 0.35)}
        return ModelData(family, inputs, f, REF_FORCE_N, REF_E_MPA, ranges, str(self.root))


def load_multifamily(root: Path = ROOT) -> MultiFamilyData:
    """Join model_dataset_1000.csv (features, labels, split) with design_manifest.csv (template dimensions)."""
    schema = json.loads((root / "feature_schema.json").read_text(encoding="utf-8"))
    table = pd.read_csv(root / "model_dataset_1000.csv").rename(columns=RENAME)
    manifest = pd.read_csv(root / "design_manifest.csv", usecols=["design_id", "params_json", "reference_load_force_n"])
    params = pd.DataFrame([json.loads(p) for p in manifest["params_json"]])
    params["design_id"] = manifest["design_id"].to_numpy()
    params["load_force_n"] = manifest["reference_load_force_n"].to_numpy()
    frame = table.merge(params, on="design_id", how="left", validate="one_to_one")
    inputs = {fam: (*names, "poissons_ratio") for fam, names in TEMPLATE_PARAMS.items()}
    inputs[V0] = V0_INPUTS
    present = {fam: cols for fam, cols in inputs.items() if fam in set(frame["bracket_family"])}
    for fam, cols in present.items():  # every row of a family names every one of its dimensions
        missing = [c for c in cols if frame.loc[frame["bracket_family"] == fam, c].isna().any()]
        if missing:
            raise ValueError(f"{fam}: the manifest leaves {missing} empty for some rows")
    return MultiFamilyData(frame, tuple(schema["feature_columns"]), root, present)


# ---------------------------------------------------------------- the shared-feature model (comparison only)


SIGNED = ("inertia_xy_mm5", "inertia_xz_mm5", "inertia_yz_mm5")  # may be zero or negative, so not log-transformed
LINEAR = ("poissons_ratio",)


def shared_design(frame: pd.DataFrame, columns: tuple[str, ...], scale: dict | None = None) -> tuple[np.ndarray, dict]:
    """Log of the positive CAD features, signed inertia terms scaled by the volume^(5/3) they live in, all standardized."""
    cols = []
    for c in columns:
        v = frame[c].to_numpy(dtype=float)
        if c in SIGNED:
            cols.append(v / frame["cad_volume_mm3"].to_numpy(dtype=float) ** (5 / 3))
        elif c in LINEAR:
            cols.append(v)
        else:
            cols.append(np.log(v))
    X = np.column_stack(cols)
    if scale is None:
        scale = {"mean": X.mean(axis=0), "std": X.std(axis=0) + 1e-12}
    return (X - scale["mean"]) / scale["std"], scale


@dataclass
class SharedFit:
    """Ridge regression in log space on the standardized shared features, one weight vector per metric."""

    columns: tuple[str, ...]
    scale: dict
    coef: dict[str, np.ndarray]
    sigma: dict[str, float]
    alpha: float
    trained_on_runs: int

    @classmethod
    def fit(cls, data: MultiFamilyData, train: pd.DataFrame, alpha: float = 0.1, folds: int = 5, seed: int = 0) -> "SharedFit":
        X, scale = shared_design(train, data.feature_columns)
        A = np.column_stack([np.ones(len(X)), X])
        y = log_targets(train)
        pen = alpha * np.eye(A.shape[1])
        pen[0, 0] = 0.0  # no shrinkage on the intercept
        solve = lambda a, b: np.linalg.solve(a.T @ a + pen, a.T @ b)
        coef, sigma = {}, {}
        splits = np.array_split(np.random.default_rng(seed).permutation(len(A)), folds)
        for m in METRICS:
            coef[m] = solve(A, y[m])
            resid = []
            for f in splits:
                tr = np.setdiff1d(np.arange(len(A)), f)
                resid.append(A[f] @ solve(A[tr], y[m][tr]) - y[m][f])
            sigma[m] = float(np.concatenate(resid).std())
        return cls(data.feature_columns, scale, coef, sigma, alpha, len(A))

    def predict_log(self, frame: pd.DataFrame) -> dict[str, np.ndarray]:
        X, _ = shared_design(frame, self.columns, self.scale)
        A = np.column_stack([np.ones(len(X)), X])
        return {m: A @ self.coef[m] for m in METRICS}


def shared_gp_predict(data: MultiFamilyData, train: pd.DataFrame, frames: list[pd.DataFrame]) -> list[dict[str, np.ndarray]]:
    """A Matern GP on the same standardized features (scikit-learn), minutes to fit; the benchmark's slow row."""
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

    X, scale = shared_design(train, data.feature_columns)
    y = log_targets(train)
    out = [{} for _ in frames]
    for m in METRICS:
        kernel = ConstantKernel(1.0) * Matern(length_scale=np.ones(X.shape[1]) * 3.0, nu=2.5) + WhiteKernel(1e-3)
        gp = GaussianProcessRegressor(kernel, normalize_y=True, n_restarts_optimizer=1, random_state=0).fit(X, y[m])
        for i, f in enumerate(frames):
            out[i][m] = gp.predict(shared_design(f, data.feature_columns, scale)[0])
    return out


# ---------------------------------------------------------------- scoring


def log_targets(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """log(metric) at the reference load."""
    return {m: np.log(frame[m].to_numpy(dtype=float) * REF_FORCE_N / frame["load_force_n"].to_numpy(dtype=float)) for m in METRICS}


def rel_errors(pred_log: dict[str, np.ndarray], frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """|pred / truth - 1| per metric at the reference load."""
    truth = log_targets(frame)
    return {m: np.abs(np.exp(pred_log[m] - truth[m]) - 1.0) for m in METRICS}


def summarize(err: dict[str, np.ndarray]) -> dict[str, float]:
    return {f"{m}_{q}": float(v) for m in METRICS for q, v in (("median", np.median(err[m])), ("p90", np.quantile(err[m], 0.9)))}


# ---------------------------------------------------------------- the template fits


def fit_templates(data: MultiFamilyData, folds: int = 5, seed: int = 0) -> dict[str, dict]:
    """Every template's power law on all of its rows (5-fold sigma, band scale 1 like v0), plus its train-only validation score."""
    from .power_law import PowerLawFit

    out = {}
    train, val = data.rows("train"), data.rows("validation")
    for key in TEMPLATES:
        fit = PowerLawFit.fit(data.model_data(key), folds=folds, seed=seed)
        entry = {**fit.constants(), "family": key}
        entry["coef"] = {m: list(c) for m, c in entry["coef"].items()}
        entry["inputs"], entry["ranges"] = list(entry["inputs"]), {k: list(v) for k, v in entry["ranges"].items()}
        md_train, md_val = data.model_data(key, train), data.model_data(key, val)
        if len(md_train.frame) and len(md_val.frame):  # T07 is Role 1's held-out family: no train or validation rows
            held = PowerLawFit.fit(md_train, folds=folds, seed=seed)
            log = held.predict_log(md_val.X())
            entry["validation"] = {"train_rows": len(md_train.frame), "rows": len(md_val.frame),
                                   **summarize(rel_errors({m: log[m][0] for m in METRICS}, md_val.frame))}
        else:
            entry["validation"] = {"train_rows": 0, "rows": 0, "note": "held out as a whole family by Role 1; the fit uses its 80 rows"}
        out[key] = entry
    return out


def write_fits(path: Path = FITS_PATH, data: MultiFamilyData | None = None) -> dict[str, dict]:
    fits = fit_templates(data or load_multifamily())
    path.write_text(json.dumps(fits, indent=1) + "\n", encoding="utf-8")
    return fits


def benchmark(data: MultiFamilyData, gp: bool = False) -> list[dict]:
    """Rows of the comparison table: shared ridge (and GP) on validation and T07, the template power laws on validation."""
    train, val, held = data.rows("train"), data.rows("validation"), data.rows("heldout_test")
    rows = []
    ridge = SharedFit.fit(data, train)
    rows.append({"model": "shared ridge, 20 CAD features", "rows": len(train),
                 "validation": summarize(rel_errors(ridge.predict_log(val), val)), "t07": summarize(rel_errors(ridge.predict_log(held), held))})
    if gp:
        pv, ph = shared_gp_predict(data, train, [val, held])
        rows.append({"model": "shared GP, 20 CAD features", "rows": len(train),
                     "validation": summarize(rel_errors(pv, val)), "t07": summarize(rel_errors(ph, held))})
    for key, entry in fit_templates(data).items():
        rows.append({"model": f"power law, {key}", "rows": entry["validation"]["train_rows"], "validation": entry["validation"], "t07": None})
    return rows


def markdown(rows: list[dict]) -> str:
    def cell(s):
        return "" if not s or "max_stress_mpa_median" not in s else \
            f"{100 * s['max_stress_mpa_median']:.1f}% / {100 * s['max_stress_mpa_p90']:.1f}% | {100 * s['max_disp_mm_median']:.2f}% / {100 * s['max_disp_mm_p90']:.2f}%"
    lines = ["| Model | Trained on | Stress median / p90 | Deflection median / p90 | T07 stress | T07 deflection |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        v, h = cell(r["validation"]), cell(r["t07"])
        lines.append(f"| {r['model']} | {r['rows']} | {v or 'held out | held out'} | {h or 'not applicable | not applicable'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fit", action="store_true", help="refit the template power laws into template_fits.json")
    ap.add_argument("--benchmark", action="store_true", help="print the comparison table")
    ap.add_argument("--gp", action="store_true", help="include the shared GP row (minutes)")
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    data = load_multifamily(args.root)
    if args.fit:
        fits = write_fits(FITS_PATH, data)
        for key, e in fits.items():
            v = e["validation"]
            score = f"validation stress {100 * v['max_stress_mpa_median']:.2f}% deflection {100 * v['max_disp_mm_median']:.2f}%" if v["rows"] else v["note"]
            print(f"{key:26s} {e['trained_on_runs']} rows, sigma {e['sigma']['max_stress_mpa']:.4f} / {e['sigma']['max_disp_mm']:.4f}, {score}")
        print(f"wrote {FITS_PATH}")
    if args.benchmark:
        print(markdown(benchmark(data, gp=args.gp)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
