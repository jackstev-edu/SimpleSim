"""Model comparison (Role 2): learning curves for the power law, the GP and AutoGluon on Role 1's runs.

    python -m src.models.compare                   # v0 runs.csv, every model; writes results.json and docs/figures/learning_curve.png
    python -m src.models.compare --no-autogluon    # power law, GP and the tree stand-in only
    python -m src.models.compare --quick           # one repeat and short AutoGluon fits, as a smoke run

Protocol (v0): 50 held-out runs (seeded), then train on n = 25, 50, 100 and 150 of the other 150, with
3 random draws per n. Error is the absolute percent error on the original scale over the held-out
runs; a model with a 90% band also reports the share of held-out runs inside it. The model classes
here take any family's inputs (benchmark.py runs them on the v1 dataset with calibration). Needs
scikit-learn and matplotlib (src/models/requirements.txt). autogluon.tabular is optional: when it is
missing the run says so and scikit-learn's HistGradientBoostingRegressor stands in as the tree model.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from contracts.models import PARAM_NAMES

from .datasets import METRICS, REF_FORCE_N, load_model_dataset
from .power_law import Z90, PowerLawFit

DEFAULT_RUNS = Path("data/processed/formal_v0/runs.csv")
OUT_DIR = Path("data/processed/model_comparison")  # gitignored: results.json and AutoGluon's folders
FIGURE = Path("docs/figures/learning_curve.png")
LABELS = {"max_stress_mpa": "Peak stress", "max_disp_mm": "Deflection"}
N_TRAIN = (25, 50, 100, 150)
TEST_SIZE, REPEATS, SEED = 50, 3, 0
QUANTILES = (0.05, 0.5, 0.95)  # a 90% band and its centre
INPUTS = tuple(PARAM_NAMES)


def log_xy(runs: pd.DataFrame, inputs: tuple[str, ...] = INPUTS) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Inputs and targets in log space, targets normalised to the reference load as gp.py does."""
    f = runs.rename(columns={"reference_max_stress_mpa": "max_stress_mpa", "reference_max_disp_mm": "max_disp_mm",
                             "reference_load_force_n": "load_force_n"})
    load = f["load_force_n"].values if "load_force_n" in f else REF_FORCE_N
    X = np.log(f[list(inputs)].values)
    y = {m: np.log(f[m].values * REF_FORCE_N / load) for m in METRICS}
    return X, y


# Every model: fit(runs) then predict_log(runs) -> {metric: (median, lower, upper)} in log space.
# lower and upper are None for a model without a band. calibrate(runs) rescales the band so 90% of
# those rows fall inside it; predict_log applies the scale afterwards.


class _Banded:
    """Calibration shared by the models with a band: one scale per target on the half-widths."""

    def __init__(self, inputs: tuple[str, ...] = INPUTS):
        self.inputs = tuple(inputs)
        self.band_scale = {m: 1.0 for m in METRICS}

    def raw_log(self, runs: pd.DataFrame) -> dict:
        raise NotImplementedError

    def predict_log(self, runs: pd.DataFrame) -> dict:
        return {m: scale_band(pred, self.band_scale[m]) for m, pred in self.raw_log(runs).items()}

    def calibrate(self, runs: pd.DataFrame, target: float = 0.90) -> dict[str, float]:
        self.band_scale = {m: 1.0 for m in METRICS}
        _, y = log_xy(runs, self.inputs)
        raw = self.raw_log(runs)
        self.band_scale = {m: calibration_scale(raw[m], y[m], target) for m in METRICS}
        return self.band_scale


def scale_band(pred: tuple, s: float) -> tuple:
    """Stretch a (median, lower, upper) band about its median by s."""
    mean, lo, hi = pred
    if lo is None:
        return pred
    return mean, mean - s * (mean - lo), mean + s * (hi - mean)


def calibration_scale(pred: tuple, y_true: np.ndarray, target: float = 0.90) -> float:
    """The factor on the half-width that puts `target` of the rows inside the band."""
    mean, lo, hi = pred
    if lo is None:
        return 1.0
    half = np.where(y_true >= mean, hi - mean, mean - lo)
    z = np.abs(y_true - mean) / np.maximum(half, 1e-12)
    return round(float(np.quantile(z, target)), 4)


class MedianBaseline:
    """Predicts the training median of each target: the floor any real model has to beat."""

    name = "median_baseline"

    def __init__(self, inputs: tuple[str, ...] = INPUTS):
        self.inputs = tuple(inputs)

    def fit(self, runs: pd.DataFrame) -> None:
        _, y = log_xy(runs, self.inputs)
        self.median = {m: float(np.median(v)) for m, v in y.items()}

    def predict_log(self, runs: pd.DataFrame) -> dict:
        return {m: (np.full(len(runs), self.median[m]), None, None) for m in METRICS}


class PowerLawModel(_Banded):
    """power_law.PowerLawFit on the training rows: a log-linear mean with one band width everywhere."""

    name = "power_law"

    def fit(self, runs: pd.DataFrame) -> None:
        self.fit_ = PowerLawFit.fit(load_model_dataset("frame", frame=runs, inputs=self.inputs))
        self.coef, self.sigma = self.fit_.coef, self.fit_.sigma

    def raw_log(self, runs: pd.DataFrame) -> dict:
        X, _ = log_xy(runs, self.inputs)
        A = np.column_stack([np.ones(len(X)), X])
        out = {}
        for m in METRICS:
            mean, half = A @ np.array(self.coef[m]), Z90 * self.sigma[m]
            out[m] = (mean, mean - half, mean + half)
        return out


class GPModel(_Banded):
    """gp.GPPredictor: the power law as the mean plus a GP on the leftover; its band widens off the data."""

    name = "gp"

    def __init__(self, restarts: int = 3, inputs: tuple[str, ...] = INPUTS, max_train: int | None = None):
        super().__init__(inputs)
        self.restarts, self.max_train = restarts, max_train

    def fit(self, runs: pd.DataFrame) -> None:
        from .gp import GPPredictor  # needs scikit-learn

        self.gp = GPPredictor(runs, restarts=self.restarts, inputs=self.inputs, max_train=self.max_train)

    def raw_log(self, runs: pd.DataFrame) -> dict:
        log = self.gp.predict_log(runs[list(self.inputs)].values)
        return {m: (mean, mean - Z90 * sd, mean + Z90 * sd) for m, (mean, sd) in log.items()}


class TreeQuantileModel(_Banded):
    """scikit-learn HistGradientBoostingRegressor with quantile loss: the stand-in when AutoGluon is missing."""

    name = "hgb_quantile"

    def fit(self, runs: pd.DataFrame) -> None:
        from sklearn.ensemble import HistGradientBoostingRegressor

        X, y = log_xy(runs, self.inputs)
        self.models = {}
        for m in METRICS:  # one booster per quantile, small leaves because n is small
            self.models[m] = {q: HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=300, learning_rate=0.05,
                                                               min_samples_leaf=5, random_state=0).fit(X, y[m]) for q in QUANTILES}

    def raw_log(self, runs: pd.DataFrame) -> dict:
        X, _ = log_xy(runs, self.inputs)
        return {m: tuple(self.models[m][q].predict(X) for q in (0.5, 0.05, 0.95)) for m in METRICS}


class AutoGluonModel(_Banded):
    """AutoGluon TabularPredictor in quantile mode, one predictor per target. Its folders stay under data/."""

    name = "autogluon"

    def __init__(self, path: str | Path = OUT_DIR / "autogluon", time_limit: float = 60, presets: str = "medium_quality", min_n: int = 0,
                 inputs: tuple[str, ...] = INPUTS):
        super().__init__(inputs)
        self.path, self.time_limit, self.presets, self.min_n = Path(path), time_limit, presets, min_n

    @property
    def log_columns(self) -> list[str]:
        return [f"log_{p}" for p in self.inputs]

    def fit(self, runs: pd.DataFrame) -> None:
        from autogluon.tabular import TabularPredictor

        X, y = log_xy(runs, self.inputs)
        frame = pd.DataFrame(X, columns=self.log_columns)
        self.predictors = {}
        for m in METRICS:
            folder = self.path / m
            shutil.rmtree(folder, ignore_errors=True)  # a fresh folder per fit
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.predictors[m] = TabularPredictor(label="target", problem_type="quantile", quantile_levels=list(QUANTILES),
                                                      path=str(folder), verbosity=0).fit(
                    frame.assign(target=y[m]), presets=self.presets, time_limit=self.time_limit)

    def raw_log(self, runs: pd.DataFrame) -> dict:
        X, _ = log_xy(runs, self.inputs)
        frame = pd.DataFrame(X, columns=self.log_columns)
        out = {}
        for m, predictor in self.predictors.items():
            q = predictor.predict(frame)  # one column per quantile level, in QUANTILES order
            out[m] = (q.iloc[:, 1].values, q.iloc[:, 0].values, q.iloc[:, 2].values)
        return out


def autogluon_available() -> bool:
    try:
        import autogluon.tabular  # noqa: F401
    except ImportError:
        return False
    return True


def build_models(names: list[str], time_limit: float = 60, restarts: int = 3, autogluon_min_n: int = 0,
                 inputs: tuple[str, ...] = INPUTS, gp_max_train: int | None = None) -> tuple[list, dict[str, str]]:
    """Models by name, plus a reason for each one that had to be skipped."""
    models, skipped = [], {}
    for name in names:
        if name == "autogluon":
            if autogluon_available():
                models.append(AutoGluonModel(time_limit=time_limit, min_n=autogluon_min_n, inputs=inputs))
            else:
                skipped[name] = "autogluon.tabular is not installed (pip install autogluon.tabular); hgb_quantile stands in"
                if not any(m.name == "hgb_quantile" for m in models) and "hgb_quantile" not in names:
                    models.append(TreeQuantileModel(inputs))
        elif name == "gp":
            models.append(GPModel(restarts=restarts, inputs=inputs, max_train=gp_max_train))
        elif name == "hgb_quantile":
            models.append(TreeQuantileModel(inputs))
        elif name == "power_law":
            models.append(PowerLawModel(inputs))
        elif name == "median_baseline":
            models.append(MedianBaseline(inputs))
        else:
            raise ValueError(f"Unknown model {name!r}")
    return models, skipped


def split(runs: pd.DataFrame, seed: int = SEED, test_size: int = TEST_SIZE) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A fixed held-out set and the pool the training subsets are drawn from."""
    order = np.random.default_rng(seed).permutation(len(runs))
    return runs.iloc[order[test_size:]].reset_index(drop=True), runs.iloc[order[:test_size]].reset_index(drop=True)


def score(pred: tuple, y_true: np.ndarray) -> dict:
    """Absolute percent error on the original scale, and band coverage when the model has one."""
    mean, lo, hi = pred
    e = np.abs(np.exp(mean - y_true) - 1) * 100
    out = {"median_pct": float(np.median(e)), "p95_pct": float(np.percentile(e, 95))}
    if lo is not None:
        out["coverage_90"] = float(np.mean((y_true >= lo) & (y_true <= hi)))
    return out


def learning_curve(runs: pd.DataFrame, models: list, n_train=N_TRAIN, repeats: int = REPEATS, seed: int = SEED,
                   test_size: int = TEST_SIZE, log=lambda s: None, pool: pd.DataFrame | None = None,
                   test: pd.DataFrame | None = None) -> dict:
    """Fit every model on random subsets of each size, score on the same held-out runs, return all records.

    Pass pool and test to use a split made elsewhere (benchmark.py); otherwise split() makes one.
    """
    if pool is None or test is None:
        pool, test = split(runs, seed, test_size)
    inputs = models[0].inputs if models else INPUTS
    _, y_test = log_xy(test, inputs)
    records = []
    for n in n_train:
        if n > len(pool):
            raise ValueError(f"n_train {n} exceeds the {len(pool)} runs left after holding out {len(test)}")
        for rep in range(1 if n == len(pool) else repeats):  # every draw of the whole pool is the same rows
            rows = np.random.default_rng([seed, n, rep]).choice(len(pool), n, replace=False)  # a fresh draw per repeat
            train = pool.iloc[np.sort(rows)].reset_index(drop=True)
            for model in models:
                if n < getattr(model, "min_n", 0):
                    continue  # slow models can sit out the small sizes
                t0 = time.perf_counter()
                model.fit(train)
                secs = time.perf_counter() - t0
                pred = model.predict_log(test)
                for m in METRICS:
                    records.append({"model": model.name, "n_train": n, "repeat": rep, "metric": m,
                                    "fit_seconds": round(secs, 2), **score(pred[m], y_test[m])})
                r = records[-2]
                log(f"n={n:>4} rep={rep} {model.name:>15}: stress median {r['median_pct']:.2f}%, "
                    f"deflection median {records[-1]['median_pct']:.2f}%, fit {secs:.1f} s")
    return {"test_size": len(test), "n_train": list(n_train), "repeats": repeats, "seed": seed,
            "models": [m.name for m in models], "records": records, "summary": summarise(records)}


def summarise(records: list[dict]) -> list[dict]:
    """Mean over repeats for each model, training size and target."""
    df = pd.DataFrame(records)
    cols = [c for c in ("median_pct", "p95_pct", "coverage_90", "fit_seconds") if c in df]
    g = df.groupby(["model", "n_train", "metric"], sort=False)[cols].mean().round(3).reset_index()
    return json.loads(g.to_json(orient="records"))  # NaN coverage becomes null


def markdown_table(summary: list[dict], n: int | None = None) -> str:
    """The accuracy table for docs/models.md at one training size (the largest by default)."""
    df = pd.DataFrame(summary)
    n = n or int(df.n_train.max())
    at = df[df.n_train == n]
    lines = [f"| Model (n = {n}) | Stress median | Stress p95 | Deflection median | Deflection p95 | 90% band coverage |",
             "| --- | --- | --- | --- | --- | --- |"]
    for model in at.model.unique():
        row = {r["metric"]: r for r in at[at.model == model].to_dict("records")}
        s, d = row["max_stress_mpa"], row["max_disp_mm"]
        cov = "none" if pd.isna(s.get("coverage_90")) else f"{s['coverage_90']:.0%} stress, {d['coverage_90']:.0%} deflection"
        lines.append(f"| `{model}` | {s['median_pct']:.2f}% | {s['p95_pct']:.2f}% | {d['median_pct']:.2f}% | {d['p95_pct']:.2f}% | {cov} |")
    return "\n".join(lines)


def plot_learning_curve(summary: list[dict], path: str | Path = FIGURE, skip: tuple[str, ...] = ("median_baseline",),
                        title: str | None = None) -> Path:
    """Two panels, error against training runs: median solid, p95 dashed, one colour per model."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df = pd.DataFrame(summary)
    df = df[~df.model.isin(skip)]  # the baseline is tens of percent and would flatten every other line
    fig, axes = plt.subplots(1, 2, figsize=(7.5, 3.1), dpi=150, sharex=True)
    for ax, m in zip(axes, METRICS):
        for model in df.model.unique():
            d = df[(df.model == model) & (df.metric == m)].sort_values("n_train")
            line, = ax.plot(d.n_train, d.median_pct, marker="o", ms=4, label=model)
            ax.plot(d.n_train, d.p95_pct, ls="--", lw=1, color=line.get_color(), alpha=0.7)
        ax.set_title(LABELS[m], fontsize=10)
        ax.set_xlabel("training runs")
        ax.set_xticks(sorted(df.n_train.unique()))
        if df.n_train.max() / max(df.n_train.min(), 1) > 5:
            ax.set_xscale("log")  # v1 spans 100 to 1,474 rows
            ax.set_xticks(sorted(df.n_train.unique()), [str(n) for n in sorted(df.n_train.unique())], fontsize=7, rotation=45)
            ax.minorticks_off()
        ax.set_yscale("log")  # the models sit an order of magnitude apart
        ax.grid(True, which="both", lw=0.4, alpha=0.5)
    axes[0].set_ylabel("error on held-out runs (%)\nsolid median, dashed p95")
    axes[0].legend(fontsize=8, frameon=False)
    if title:
        fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="folder for results.json and AutoGluon's models")
    ap.add_argument("--figure", type=Path, default=FIGURE)
    ap.add_argument("--models", nargs="+", default=["median_baseline", "power_law", "gp", "autogluon"])
    ap.add_argument("--no-autogluon", action="store_true", help="drop autogluon from --models")
    ap.add_argument("--autogluon-min-n", type=int, default=0, help="fit AutoGluon only at this many runs or more")
    ap.add_argument("--time-limit", type=float, default=60, help="seconds per AutoGluon fit (one per target)")
    ap.add_argument("--n", type=int, nargs="+", default=list(N_TRAIN))
    ap.add_argument("--repeats", type=int, default=REPEATS)
    ap.add_argument("--quick", action="store_true", help="one repeat and 20 s AutoGluon fits")
    args = ap.parse_args(argv)
    if not args.runs.exists():
        print(f"No runs at {args.runs}. Copy Role 1's runs.csv there (see docs/models.md).")
        return 1
    names = [m for m in args.models if not (args.no_autogluon and m == "autogluon")]
    if args.quick:
        args.repeats, args.time_limit = 1, 20
    AutoGluonModel.__init__.__defaults__ = (args.out / "autogluon",) + AutoGluonModel.__init__.__defaults__[1:]  # keep folders under --out
    models, skipped = build_models(names, args.time_limit, autogluon_min_n=args.autogluon_min_n)
    for name, why in skipped.items():
        print(f"Skipping {name}: {why}")
    runs = pd.read_csv(args.runs)
    print(f"{len(runs)} runs from {args.runs}; holding out {TEST_SIZE}; models: {', '.join(m.name for m in models)}")
    result = learning_curve(runs, models, args.n, args.repeats, log=print)
    result["skipped"], result["runs"] = skipped, str(args.runs)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out / 'results.json'}")
    try:
        print(f"Wrote {plot_learning_curve(result['summary'], args.figure)}")
    except ImportError:
        print("matplotlib is missing, so no figure (pip install -r src/models/requirements.txt)")
    print("\n" + markdown_table(result["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
