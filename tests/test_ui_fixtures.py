"""UI fixture checks: every fixture matches its contract, and make_fixtures.py is deterministic.

If this fails after a contract change, rerun `python -m src.ui.make_fixtures` and commit the result.
"""

import inspect
import json
import math
from pathlib import Path

import pytest

from contracts import Prediction, Result, StudySpec, beam_formula
from src.ui import make_fixtures

FIX = Path(__file__).parent.parent / "src" / "ui" / "fixtures"
EX = Path(__file__).parent.parent / "contracts" / "examples"
MODELS = {"study_spec": StudySpec, "prediction": Prediction, "result": Result}


def _load(kind: str, suffix: str):
    return MODELS[kind].model_validate_json((FIX / f"{kind}{suffix}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("suffix", make_fixtures.CASES)
def test_fixtures_validate(suffix):
    for kind in make_fixtures.KINDS:
        _load(kind, suffix)


def test_normal_case_is_the_contract_example():
    # Compare parsed JSON so line endings on a Windows checkout do not matter.
    for kind in make_fixtures.KINDS:
        assert json.loads((FIX / f"{kind}.json").read_text()) == json.loads((EX / f"{kind}.json").read_text())


def test_infeasible_has_no_recommendation():
    result = _load("result", "_infeasible")
    assert result.recommended is None
    assert result.finalists and not any(f.feasible for f in result.finalists)
    # The Discovery values fall back to the baseline.
    assert result.discovery_values == result.baseline.design
    assert not any(c.feasible_p95 for c in _load("prediction", "_infeasible").shortlist)


def test_out_of_range_is_flagged():
    prediction = _load("prediction", "_out_of_range")
    assert any(not c.in_training_range for c in prediction.shortlist)
    assert any("training range" in w for w in _load("result", "_out_of_range").warnings)


def _assert_fixture_matches(actual, expected, path):
    assert type(actual) is type(expected), path
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), path
        for key in expected:
            _assert_fixture_matches(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert len(actual) == len(expected), path
        for i, (actual_value, expected_value) in enumerate(zip(actual, expected)):
            _assert_fixture_matches(actual_value, expected_value, f"{path}[{i}]")
    elif isinstance(expected, float):
        # Allow platform roundoff in NumPy calculations, including correlations near zero.
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-14), (
            f"{path}: generated {actual!r}, expected {expected!r}"
        )
    else:
        assert actual == expected, path


def test_make_fixtures_is_deterministic(tmp_path):
    first = {p.name: p.read_bytes() for p in make_fixtures.main(tmp_path / "a")}
    second = {p.name: p.read_bytes() for p in make_fixtures.main(tmp_path / "b")}
    assert first == second
    assert len(first) == len(make_fixtures.CASES) * len(make_fixtures.KINDS)
    # Keep local generation byte-for-byte deterministic and compare the tracked fixtures by value.
    for name, data in first.items():
        _assert_fixture_matches(json.loads(data), json.loads((FIX / name).read_text(encoding="utf-8")), name)


def test_equations_match_fake_model():
    # The Recommendations tab typesets beam_formula; its constants must not drift from the code.
    source = inspect.getsource(beam_formula)
    page = (FIX.parent / "index.html").read_text(encoding="utf-8")
    for constant in ("0.72", "0.675", "1.65"):
        assert constant in source, f"{constant} left beam_formula: update the equations in src/ui/index.html"
        assert constant in page
