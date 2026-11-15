"""Regenerate src/ui/fixtures/ from the contract fakes. Never hand-edit the JSON.

    python -m src.ui.make_fixtures

Three cases, each a study_spec / prediction / result triple:
  normal        copies of contracts/examples/ (no suffix)
  infeasible    a load no design in the family can carry, so recommended is null
  out_of_range  a search wider than the training range, so in_training_range is false
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # lets `python src/ui/make_fixtures.py` find contracts

from contracts import Constraint, FakePredictor, Load, Objective, ParameterRange, Prediction, StudySpec, run_pipeline  # noqa: E402
from contracts.make_examples import BASELINE, RANGES, example_spec  # noqa: E402

EXAMPLES = ROOT / "contracts" / "examples"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CASES = ("", "_infeasible", "_out_of_range")  # filename suffix per case
KINDS = ("study_spec", "prediction", "result")

INFEASIBLE_FORCE_N = 5000.0  # ten times the example load; best design stays under SF 2
WIDEN = 0.05  # out-of-range search moves each training bound outward by this fraction


@dataclass
class NearMissPredictor:
    """FakePredictor that still shortlists when nothing is feasible, so the UI has rows to show."""

    def predict(self, spec: StudySpec, n: int | None = None, seed: int = 0) -> Prediction:
        strict = FakePredictor().predict(spec, n, seed)
        if strict.shortlist:
            return strict
        # Same samples, ranked by safety factor with the constraints switched off.
        relaxed = spec.model_copy(
            update={
                "objective": Objective(metric="safety_factor", direction="maximize"),
                "constraints": [Constraint(metric="safety_factor", op=">=", value=0.0)],
            }
        )
        ranked = FakePredictor().predict(relaxed, n, seed)
        # The strict pass found no feasible sample, so every one of these fails at p95.
        shortlist = [c.model_copy(update={"feasible_p95": False}) for c in ranked.shortlist]
        note = "no candidate met the constraints at p95; shortlist is the nearest misses ranked by safety factor"
        return ranked.model_copy(update={"shortlist": shortlist, "warnings": ranked.warnings + [note]})


@dataclass
class TrainingRangePredictor:
    """FakePredictor that flags candidates outside the ranges the model was trained on."""

    trained: StudySpec

    def predict(self, spec: StudySpec, n: int | None = None, seed: int = 0) -> Prediction:
        pred = FakePredictor().predict(spec, n, seed)
        inside = lambda d: all(p.low_mm <= d[p.name] <= p.high_mm for p in self.trained.parameters)  # noqa: E731
        shortlist = [c.model_copy(update={"in_training_range": inside(c.design)}) for c in pred.shortlist]
        n_out = sum(not c.in_training_range for c in shortlist)
        warnings = list(pred.warnings)
        if n_out:
            warnings.append(f"{n_out} of {len(shortlist)} shortlisted candidates are outside the training range; their estimates are extrapolated")
        return pred.model_copy(update={"shortlist": shortlist, "warnings": warnings})


def infeasible_case():
    spec = example_spec().model_copy(
        update={
            "load": Load(force_n=INFEASIBLE_FORCE_N),
            "assumptions": ["Load acts straight down at the hole", "Mounting face fully fixed", "Load raised to 5000 N for the heavy-duty case"],
        }
    )
    predictor = NearMissPredictor()
    return spec, predictor.predict(spec), run_pipeline(spec, BASELINE, predictor=predictor)


def out_of_range_case():
    wide = [ParameterRange(name=k, low_mm=lo * (1 - WIDEN), high_mm=hi * (1 + WIDEN)) for k, (lo, hi) in RANGES.items()]
    spec = example_spec().model_copy(
        update={
            "parameters": wide,
            "assumptions": ["Load acts straight down at the hole", "Mounting face fully fixed", "Search bounds widened 5% beyond the training ranges"],
        }
    )
    predictor = TrainingRangePredictor(trained=example_spec())
    return spec, predictor.predict(spec), run_pipeline(spec, BASELINE, predictor=predictor)


def _write(path: Path, text: str) -> None:
    # LF only, so the bytes do not depend on the platform.
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")


def main(out_dir: Path = FIXTURES) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    # Normal case: the contract examples, copied so the page works from one folder.
    for kind in KINDS:
        _write(out_dir / f"{kind}.json", (EXAMPLES / f"{kind}.json").read_text(encoding="utf-8"))
        written.append(out_dir / f"{kind}.json")
    for suffix, build in (("_infeasible", infeasible_case), ("_out_of_range", out_of_range_case)):
        for kind, model in zip(KINDS, build()):
            _write(out_dir / f"{kind}{suffix}.json", model.model_dump_json(indent=2) + "\n")
            written.append(out_dir / f"{kind}{suffix}.json")
    return written


if __name__ == "__main__":
    print("wrote", sorted(p.name for p in main()))
