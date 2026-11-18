"""Tests for the L-bracket v0 executable specification."""

import pytest
from pydantic import ValidationError

from src.data.bracket_spec import (
    HOLE_CENTER_X_MM,
    HOLE_CENTER_Z_MM,
    LOAD_ANGLE_DEG,
    VERTICAL_HEIGHT_MM,
    BracketCaseInput,
    MaterialProperties,
    make_baseline_case,
)


def test_baseline_case_matches_documented_setup() -> None:
    case = make_baseline_case()

    assert case.thickness_mm == 6.0
    assert case.flange_length_mm == 80.0
    assert case.arm_width_mm == 40.0
    assert case.hole_diameter_mm == 8.0
    assert case.fillet_radius_mm == 4.0
    assert case.load_force_n == 500.0
    assert case.load_vector_n == (0.0, -500.0, 0.0)


def test_solver_payload_contains_fixed_values() -> None:
    payload = make_baseline_case().to_solver_payload()

    assert payload["geometry"]["vertical_height_mm"] == VERTICAL_HEIGHT_MM
    assert payload["geometry"]["hole_center_x_mm"] == HOLE_CENTER_X_MM
    assert payload["geometry"]["hole_center_z_mm"] == HOLE_CENTER_Z_MM
    assert payload["load"]["angle_deg"] == LOAD_ANGLE_DEG
    assert payload["load"]["surface"] == "horizontal_free_end"
    assert payload["support"]["x_mm"] == 0.0


def test_force_outside_supported_range_is_rejected() -> None:
    baseline = make_baseline_case().model_dump()
    baseline["load_force_n"] = 50.0

    with pytest.raises(ValidationError):
        BracketCaseInput.model_validate(baseline)


def test_invalid_poissons_ratio_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MaterialProperties(
            material_key="invalid_material",
            youngs_modulus_mpa=200000.0,
            poissons_ratio=0.60,
            yield_strength_mpa=250.0,
            density_kg_m3=7850.0,
        )


def test_unknown_input_field_is_rejected() -> None:
    baseline = make_baseline_case().model_dump()
    baseline["unknown_setting"] = 123

    with pytest.raises(ValidationError):
        BracketCaseInput.model_validate(baseline)