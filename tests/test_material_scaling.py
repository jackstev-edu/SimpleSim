"""Tests for deterministic user material/load scaling."""

import pytest

from src.data.material_scaling import scale_reference_response


def test_reference_inputs_reproduce_reference_response() -> None:
    response = scale_reference_response(
        reference_max_stress_mpa=200.0,
        reference_max_disp_mm=0.5,
        volume_mm3=32000.0,
        reference_load_force_n=500.0,
        reference_youngs_modulus_mpa=200000.0,
        user_load_force_n=500.0,
        user_youngs_modulus_mpa=200000.0,
        user_yield_strength_mpa=250.0,
        user_density_kg_m3=7850.0,
    )

    assert response.max_stress_mpa == pytest.approx(200.0)
    assert response.max_disp_mm == pytest.approx(0.5)
    assert response.mass_kg == pytest.approx(0.2512)
    assert response.factor_of_safety == pytest.approx(1.25)


def test_force_and_modulus_scaling() -> None:
    response = scale_reference_response(
        reference_max_stress_mpa=100.0,
        reference_max_disp_mm=1.0,
        volume_mm3=1000.0,
        reference_load_force_n=500.0,
        reference_youngs_modulus_mpa=200000.0,
        user_load_force_n=1000.0,
        user_youngs_modulus_mpa=100000.0,
        user_yield_strength_mpa=300.0,
        user_density_kg_m3=2700.0,
    )

    assert response.max_stress_mpa == pytest.approx(200.0)
    assert response.max_disp_mm == pytest.approx(4.0)
    assert response.mass_kg == pytest.approx(0.0027)
    assert response.factor_of_safety == pytest.approx(1.5)


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_load_force_n", 0.0),
        ("user_youngs_modulus_mpa", -1.0),
        ("user_density_kg_m3", float("nan")),
    ],
)
def test_invalid_scaling_inputs_are_rejected(field: str, value: float) -> None:
    inputs = {
        "reference_max_stress_mpa": 100.0,
        "reference_max_disp_mm": 1.0,
        "volume_mm3": 1000.0,
        "reference_load_force_n": 500.0,
        "reference_youngs_modulus_mpa": 200000.0,
        "user_load_force_n": 500.0,
        "user_youngs_modulus_mpa": 200000.0,
        "user_yield_strength_mpa": 250.0,
        "user_density_kg_m3": 7850.0,
    }
    inputs[field] = value

    with pytest.raises(ValueError):
        scale_reference_response(**inputs)
