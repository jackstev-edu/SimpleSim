"""Tests for the parametric L-bracket geometry."""

import pytest

pytest.importorskip("cadquery")

from src.data.bracket_spec import (
    BracketCaseInput,
    make_baseline_case,
)
from src.data.geometry import (
    build_bracket_geometry,
    export_step,
    summarize_geometry,
)


def test_baseline_geometry_is_valid() -> None:
    case = make_baseline_case()
    solid = build_bracket_geometry(case)
    summary = summarize_geometry(solid)

    assert solid.isValid()
    assert summary.volume_mm3 == pytest.approx(
        31995.752280681005,
        rel=1.0e-8,
    )

    assert summary.x_min_mm == pytest.approx(0.0)
    assert summary.x_max_mm == pytest.approx(80.0)
    assert summary.y_min_mm == pytest.approx(0.0)
    assert summary.y_max_mm == pytest.approx(60.0)
    assert summary.z_min_mm == pytest.approx(0.0)
    assert summary.z_max_mm == pytest.approx(40.0)


def test_baseline_geometry_exports_step(tmp_path) -> None:
    output_path = tmp_path / "baseline_l_bracket_v0.step"

    exported_path = export_step(
        make_baseline_case(),
        output_path,
    )

    assert exported_path.exists()
    assert exported_path.stat().st_size > 1000


def test_increasing_thickness_increases_volume() -> None:
    baseline = make_baseline_case()

    thicker_data = baseline.model_dump()
    thicker_data["design_id"] = "thicker_bracket"
    thicker_data["thickness_mm"] = 8.0

    thicker_case = BracketCaseInput.model_validate(
        thicker_data
    )

    baseline_volume = summarize_geometry(
        build_bracket_geometry(baseline)
    ).volume_mm3

    thicker_volume = summarize_geometry(
        build_bracket_geometry(thicker_case)
    ).volume_mm3

    assert thicker_volume > baseline_volume