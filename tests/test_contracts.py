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


# ---------------------------------------------------------------- families (contract 0.2.0)


def test_v1_examples_validate_and_use_the_eight_names():
    from contracts.models import FAMILY_PARAMS

    v1 = EX / "l_bracket_gusset_v1"
    spec = StudySpec.model_validate_json((v1 / "study_spec.json").read_text())
    pred = Prediction.model_validate_json((v1 / "prediction.json").read_text())
    result = Result.model_validate_json((v1 / "result.json").read_text())
    assert spec.bracket_family == "l_bracket_gusset_v1" and spec.param_names() == FAMILY_PARAMS["l_bracket_gusset_v1"]
    assert {p.name for p in spec.parameters} == set(FAMILY_PARAMS["l_bracket_gusset_v1"]) and len(spec.parameters) == 8
    assert all(set(c.design) == set(spec.param_names()) for c in pred.shortlist)
    assert set(result.discovery_values) == set(spec.param_names()) and result.recommended is not None
    assert FAMILY_PARAMS["l_bracket_v0"] == RUN_COLUMNS[7:12]  # the v0 set is still the five Run geometry columns


def test_spec_parameters_must_match_the_family():
    data = json.loads((EX / "study_spec.json").read_text())
    with pytest.raises(ValueError, match="exactly"):
        StudySpec.model_validate({**data, "bracket_family": "l_bracket_gusset_v1"})  # five names for an eight-name family
    with pytest.raises(ValueError, match="unknown bracket_family"):
        StudySpec.model_validate({**data, "bracket_family": "t_bracket"})
    short = {**data, "parameters": data["parameters"][:4]}
    with pytest.raises(ValueError, match="exactly"):
        StudySpec.model_validate(short)


def test_result_designs_must_use_the_family_keys():
    data = json.loads((EX / "result.json").read_text())
    bad = json.loads(json.dumps(data))
    bad["baseline"]["design"]["gusset_length_mm"] = 20.0
    with pytest.raises(ValueError, match="baseline.design"):
        Result.model_validate(bad)
    bad = json.loads(json.dumps(data))
    del bad["discovery_values"]["fillet_radius_mm"]
    with pytest.raises(ValueError, match="discovery_values"):
        Result.model_validate(bad)
    bad = json.loads(json.dumps(data))
    bad["sensitivities"]["gusset_thickness_mm"] = 0.1
    with pytest.raises(ValueError, match="sensitivities"):
        Result.model_validate(bad)
    Result.model_validate(data)  # the example itself is fine


def test_prediction_rejects_mixed_families():
    data = json.loads((EX / "prediction.json").read_text())
    data["shortlist"][0]["design"]["vertical_height_mm"] = 60.0
    with pytest.raises(ValueError, match="mix"):
        Prediction.model_validate(data)
