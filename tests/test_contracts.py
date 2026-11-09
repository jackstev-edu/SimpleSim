"""Contract checks: the examples validate, and the fake pipeline runs end to end.

If this fails after your change, you changed a hand-off. Get the other side to review.
"""

import csv
import json
from pathlib import Path

import pytest

from contracts import RUN_COLUMNS, Prediction, Result, Run, StudySpec, run_pipeline
from contracts.make_examples import BASELINE, example_spec

EX = Path(__file__).parent.parent / "contracts" / "examples"


def test_examples_validate():
    StudySpec.model_validate_json((EX / "study_spec.json").read_text())
    Prediction.model_validate_json((EX / "prediction.json").read_text())
    Result.model_validate_json((EX / "result.json").read_text())


def test_runs_csv_header_and_rows():
    with (EX / "runs.csv").open() as f:
        reader = csv.DictReader(f)
        # Header order is part of the contract, so Role 2's loader can trust it.
        assert tuple(reader.fieldnames) == RUN_COLUMNS
        rows = [Run(**r) for r in reader]
    assert len(rows) >= 10


def test_pipeline_smoke():
    result = run_pipeline(example_spec(), BASELINE)
    assert result.recommended is not None
    assert result.recommended.feasible
    # Explanation is built from solver numbers, so the mass must appear in it.
    assert f"{result.recommended.mass_kg:.3f}" in result.explanation
    assert set(result.discovery_values) == set(BASELINE)


def test_spec_rejects_bad_range():
    data = json.loads((EX / "study_spec.json").read_text())
    data["parameters"][0]["high_mm"] = 0.1
    with pytest.raises(ValueError):
        StudySpec.model_validate(data)
