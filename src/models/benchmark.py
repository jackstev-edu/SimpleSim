"""Family benchmark (Role 2): train, calibrate and test the predictors on one family's dataset.

    python -m src.models.benchmark --family l_bracket_gusset_v1          # writes docs/model_data/l_bracket_gusset_v1_benchmark.json
    python -m src.models.benchmark --family l_bracket_v0 --no-autogluon
    python -m src.models.benchmark --family l_bracket_gusset_v1 --print-constants   # the block to paste into power_law.py

Protocol: a seeded 20% test split; 10% of the rest is held back for calibration and the remainder
trains. Each model is fitted on the training rows, scored raw on the test rows, then its 90% band
is rescaled on the calibration rows so 90% of them fall inside, and scored again. The learning
curve refits the power law and the GP on n = 100, 250, 500, 1,000 and every training row (three
draws per size, one at the full size). The Poisson check refits the power law and the GP without
Poisson's ratio so the doc can say whether that input earns its place. Mass is checked against
the closed-form volume. Results and the figure go under docs/, as docs/models.md quotes them.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import compare
from .datasets import FAMILY_DIRS, METRICS, V0, V1, ModelData, load_model_dataset, split_indices
from .power_law import FAMILY_VOLUME, PowerLawFit

OUT_DIR = Path("docs/model_data")
FIGURES = Path("docs/figures")
N_TRAIN = {V0: (25, 50, 100), V1: (100, 250, 500, 1000)}  # the full training size is appended


def frame_split(data: ModelData, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train, cal, test = split_indices(len(data.frame), seed)
    f = data.frame
    return f.iloc[train].reset_index(drop=True), f.iloc[cal].reset_index(drop=True), f.iloc[test].reset_index(drop=True)


MIN_CALIBRATION_ROWS = 50  # below this the 90% quantile of 16-odd rows is noise, so out-of-fold residuals calibrate instead


def evaluate(model, train: pd.DataFrame, cal: pd.DataFrame, test: pd.DataFrame, inputs: tuple[str, ...], folds: int = 5) -> dict:
    """Fit, score raw, calibrate, score again. Returns both scores and the band scales."""
    _, y_test = compare.log_xy(test, inputs)
    t0 = time.perf_counter()
    model.fit(train)
    fit_s = time.perf_counter() - t0
    raw = {m: compare.score(p, y_test[m]) for m, p in model.predict_log(test).items()}
    scales, how = {}, "none"
    if hasattr(model, "calibrate"):
        if len(cal) >= MIN_CALIBRATION_ROWS:
            scales, how = model.calibrate(cal), f"{len(cal)} held-out rows"
        else:
            scales, how = out_of_fold_calibration(model, pd.concat([train, cal], ignore_index=True), inputs, folds), f"{folds}-fold out-of-fold on {len(train) + len(cal)} rows"
            model.fit(train)  # back to the training fit the scores describe
            model.band_scale = scales
    calibrated = {m: compare.score(p, y_test[m]) for m, p in model.predict_log(test).items()}
    return {"model": model.name, "inputs": list(inputs), "fit_seconds": round(fit_s, 1), "raw": raw, "calibrated": calibrated,
            "band_scale": scales, "calibrated_on": how}


def out_of_fold_calibration(model, rows: pd.DataFrame, inputs: tuple[str, ...], folds: int = 5, seed: int = 0) -> dict[str, float]:
    """Band scales from residuals of rows each model never trained on, for families with few rows."""
    _, y = compare.log_xy(rows, inputs)
    preds = {m: [] for m in METRICS}
    order = []
    for held in np.array_split(np.random.default_rng(seed).permutation(len(rows)), folds):
        model.band_scale = {m: 1.0 for m in METRICS}
        model.fit(rows.drop(index=rows.index[held]).reset_index(drop=True))
        for m, p in model.raw_log(rows.iloc[held]).items():
            preds[m].append(p)
        order.append(held)
    idx = np.concatenate(order)
    return {m: compare.calibration_scale(tuple(np.concatenate(parts) for parts in zip(*preds[m])), y[m][idx]) for m in METRICS}


def volume_check(data: ModelData) -> dict:
    """How far the closed-form volume sits from Role 1's mesh volumes."""
    X = data.frame[list(data.geometry)].to_numpy(dtype=float)
    err = FAMILY_VOLUME[data.family](X) / data.frame["volume_mm3"].to_numpy(dtype=float) - 1
    return {"rows": int(len(err)), "max_abs_pct": float(np.abs(err).max() * 100), "mean_pct": float(err.mean() * 100)}


def run(family: str, models: list[str], seed: int = 0, repeats: int = 3, restarts: int = 3, gp_max_train: int | None = None,
        time_limit: float = 60, curve: bool = True, log=print) -> dict:
    data = load_model_dataset(family)
    train, cal, test = frame_split(data, seed)
    log(f"{family}: {len(data.frame)} rows, inputs {list(data.inputs)}; train {len(train)}, calibration {len(cal)}, test {len(test)}")
    result = {"family": family, "source": data.source, "rows": len(data.frame), "inputs": list(data.inputs), "seed": seed,
              "split": {"train": len(train), "calibration": len(cal), "test": len(test)},
              "volume_check": volume_check(data), "models": [], "poisson_check": [], "skipped": {}}
    log(f"closed-form volume: max error {result['volume_check']['max_abs_pct']:.4f}%")
    built, skipped = compare.build_models(models, time_limit, restarts, inputs=data.inputs, gp_max_train=gp_max_train)
    result["skipped"] = skipped
    for model in built:
        r = evaluate(model, train, cal, test, data.inputs)
        result["models"].append(r)
        log(f"  {model.name:>14}: " + "; ".join(f"{m[:4]} median {r['calibrated'][m]['median_pct']:.2f}% p95 {r['calibrated'][m]['p95_pct']:.2f}%"
                                               + (f" coverage {r['raw'][m]['coverage_90']:.0%} -> {r['calibrated'][m]['coverage_90']:.0%}" if "coverage_90" in r["raw"][m] else "")
                                               for m in METRICS) + f" (fit {r['fit_seconds']} s)")
        if model.name == "power_law":
            result["power_law_constants"] = constants_block(model.fit_, r["band_scale"], data)
    if data.has_poisson:  # the same models without Poisson's ratio, so the doc can say what it buys
        without = tuple(n for n in data.inputs if n != "poissons_ratio")
        for name in [n for n in ("power_law", "gp") if n in models]:
            model, _ = compare.build_models([name], restarts=restarts, inputs=without, gp_max_train=gp_max_train)
            r = evaluate(model[0], train, cal, test, without)
            result["poisson_check"].append(r)
            log(f"  {name:>14} without Poisson: " + "; ".join(f"{m[:4]} median {r['calibrated'][m]['median_pct']:.2f}%" for m in METRICS))
    if curve:
        sizes = tuple(n for n in N_TRAIN.get(family, (100, 250, 500, 1000)) if n < len(train)) + (len(train),)
        curve_models, _ = compare.build_models([n for n in ("power_law", "gp") if n in models], restarts=restarts, inputs=data.inputs, gp_max_train=gp_max_train)
        result["learning_curve"] = compare.learning_curve(data.frame, curve_models, sizes, repeats, seed, pool=train, test=test, log=log)
    return result


def constants_block(fit: PowerLawFit, band_scale: dict[str, float], data: ModelData) -> dict:
    """The calibrated fit's constants, for power_law.py's built-in v1 block."""
    calibrated = PowerLawFit(fit.family, fit.inputs, fit.coef, fit.sigma, band_scale=dict(band_scale), ranges=dict(data.ranges),
                             trained_on_runs=fit.trained_on_runs)
    return calibrated.constants()


def print_constants(c: dict) -> str:
    lines = [f"INPUTS_V1 = {tuple(c['inputs'])!r}",
             "COEF_V1 = {", *(f"    {m!r}: {tuple(v)!r}," for m, v in c["coef"].items()), "}",
             f"SIGMA_LOG_V1 = {c['sigma']!r}", f"BAND_SCALE_V1 = {c['band_scale']!r}",
             f"TRAINED_ON_RUNS_V1 = {c['trained_on_runs']}",
             "TRAIN_RANGES_V1 = {", *(f"    {k!r}: {tuple(v)!r}," for k, v in c["ranges"].items()), "}"]
    return "\n".join(lines)


def markdown(result: dict) -> str:
    """The test-split table for docs/models.md: calibrated numbers, raw coverage in brackets."""
    lines = [f"| Model ({result['family']}, test n = {result['split']['test']}) | Stress median | Stress p95 | Deflection median | Deflection p95 | 90% coverage (raw -> calibrated) |",
             "| --- | --- | --- | --- | --- | --- |"]
    for r in result["models"]:
        s, d = r["calibrated"]["max_stress_mpa"], r["calibrated"]["max_disp_mm"]
        if "coverage_90" in s:
            cov = (f"stress {r['raw']['max_stress_mpa']['coverage_90']:.0%} -> {s['coverage_90']:.0%}, "
                   f"deflection {r['raw']['max_disp_mm']['coverage_90']:.0%} -> {d['coverage_90']:.0%}")
        else:
            cov = "none"
        lines.append(f"| `{r['model']}` | {s['median_pct']:.2f}% | {s['p95_pct']:.2f}% | {d['median_pct']:.2f}% | {d['p95_pct']:.2f}% | {cov} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--family", default=V1, choices=sorted(FAMILY_DIRS))
    ap.add_argument("--models", nargs="+", default=["median_baseline", "power_law", "gp", "autogluon"])
    ap.add_argument("--no-autogluon", action="store_true")
    ap.add_argument("--no-curve", action="store_true", help="skip the learning curve")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--restarts", type=int, default=3, help="GP optimizer restarts")
    ap.add_argument("--gp-max-train", type=int, default=None, help="train the GP on a seeded subset of this many rows")
    ap.add_argument("--time-limit", type=float, default=60, help="seconds per AutoGluon fit")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--figure", type=Path, default=None)
    ap.add_argument("--print-constants", action="store_true")
    args = ap.parse_args(argv)
    names = [m for m in args.models if not (args.no_autogluon and m == "autogluon")]
    compare.AutoGluonModel.__init__.__defaults__ = (Path("data/processed/model_comparison") / f"autogluon_{args.family}",) + compare.AutoGluonModel.__init__.__defaults__[1:]
    result = run(args.family, names, args.seed, args.repeats, args.restarts, args.gp_max_train, args.time_limit, curve=not args.no_curve)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.family}_benchmark.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nWrote {path}")
    if "learning_curve" in result:
        figure = args.figure or FIGURES / f"learning_curve_{'v0' if args.family == V0 else 'v1'}.png"
        try:
            print(f"Wrote {compare.plot_learning_curve(result['learning_curve']['summary'], figure, title=args.family)}")
        except ImportError:
            print("matplotlib is missing, so no figure")
    print("\n" + markdown(result))
    if args.print_constants and "power_law_constants" in result:
        print("\n" + print_constants(result["power_law_constants"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
