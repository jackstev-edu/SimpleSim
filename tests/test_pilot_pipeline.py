"""Unit tests for the Latin-hypercube pilot pipeline."""

import numpy as np
import pytest

from src.data.geometry import build_bracket_geometry
from src.data.pilot_pipeline import (
    PILOT_PARAMETER_RANGES,
    evaluate_pilot_quality,
    latin_hypercube_unit_samples,
    make_latin_hypercube_cases,
)


def test_latin_hypercube_is_deterministic_and_stratified() -> None:
    first = latin_hypercube_unit_samples(40, 5, seed=24679)
    second = latin_hypercube_unit_samples(40, 5, seed=24679)

    assert np.array_equal(first, second)
    assert np.all(first >= 0.0)
    assert np.all(first < 1.0)

    for dimension in range(first.shape[1]):
        strata = np.floor(first[:, dimension] * 40).astype(int)
        assert sorted(strata) == list(range(40))


def test_latin_hypercube_cases_are_valid_and_in_range() -> None:
    cases = make_latin_hypercube_cases(40, seed=24679)

    assert len(cases) == 40
    assert len({case.design_id for case in cases}) == 40

    for case in cases:
        for name, (low, high) in PILOT_PARAMETER_RANGES.items():
            assert low <= getattr(case, name) <= high


def test_invalid_sample_dimensions_are_rejected() -> None:
    with pytest.raises(ValueError):
        latin_hypercube_unit_samples(0, 5, seed=1)
    with pytest.raises(ValueError):
        latin_hypercube_unit_samples(5, 0, seed=1)


def test_quality_check_reports_missing_solver_rows() -> None:
    cases = make_latin_hypercube_cases(4, seed=1)
    checks = evaluate_pilot_quality(
        cases,
        runs=[],
        diagnostics=[],
        failure_count=4,
    )

    assert checks
    assert not all(check.passed for check in checks)


def test_all_sampled_geometries_build() -> None:
    # This is a geometry-only check: no mesh or CalculiX solve is run.
    for case in make_latin_hypercube_cases(40, seed=24679):
        assert build_bracket_geometry(case).isValid()
