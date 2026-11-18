"""Tests for the baseline-validation calculations."""

import pytest

pytest.importorskip("cadquery")
pytest.importorskip("gmsh")

from src.data.bracket_spec import make_baseline_case
from src.data.validation import cantilever_reference, percent_difference


def test_percent_difference() -> None:
    assert percent_difference(105.0, 100.0) == pytest.approx(5.0)
    assert percent_difference(95.0, 100.0) == pytest.approx(5.0)


def test_zero_reference_is_rejected() -> None:
    with pytest.raises(ValueError):
        percent_difference(1.0, 0.0)


def test_baseline_cantilever_reference() -> None:
    result = cantilever_reference(make_baseline_case())

    assert result.stress_mpa == pytest.approx(166.6666667)
    assert result.displacement_mm == pytest.approx(0.5925925926)
    assert result.factor_of_safety == pytest.approx(1.5)
