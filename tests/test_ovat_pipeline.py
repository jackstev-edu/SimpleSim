"""Unit tests for the first real OVAT dataset pipeline."""

from types import SimpleNamespace

import pytest

from contracts import RUN_COLUMNS, Run
from src.data.bracket_spec import make_baseline_case
from src.data.ovat_pipeline import (
    PRODUCTION_MESH_SETTINGS,
    evaluate_ovat_trends,
    make_ovat_cases,
    production_mesh_settings,
    result_to_contract_run,
)


def test_ovat_cases_are_unique_valid_and_complete() -> None:
    cases = make_ovat_cases()

    assert len(cases) == 13
    assert len({case.design_id for case in cases}) == len(cases)
    assert cases[0].design_id == "baseline_l_bracket_v0"

    by_id = {case.design_id: case for case in cases}
    assert by_id["ovat_thickness_low"].thickness_mm == 4.0
    assert by_id["ovat_thickness_high"].thickness_mm == 10.0
    assert by_id["ovat_flange_length_low"].flange_length_mm == 75.0
    assert by_id["ovat_flange_length_high"].flange_length_mm == 120.0
    assert by_id["ovat_arm_width_low"].arm_width_mm == 32.0
    assert by_id["ovat_arm_width_high"].arm_width_mm == 60.0
    assert by_id["ovat_hole_diameter_low"].hole_diameter_mm == 6.0
    assert by_id["ovat_hole_diameter_high"].hole_diameter_mm == 12.0
    assert by_id["ovat_fillet_radius_low"].fillet_radius_mm == 2.0
    assert by_id["ovat_fillet_radius_high"].fillet_radius_mm == 8.0
    assert by_id["ovat_load_force_low"].load_force_n == 100.0
    assert by_id["ovat_load_force_high"].load_force_n == 1000.0

    assert PRODUCTION_MESH_SETTINGS.mesh_rule == "tet2_1to2mm"


def test_production_mesh_scales_with_thin_designs() -> None:
    baseline = make_baseline_case()
    baseline_mesh = production_mesh_settings(baseline)
    thin_case = baseline.model_copy(update={"thickness_mm": 4.0})
    thin_mesh = production_mesh_settings(thin_case)

    assert baseline_mesh.minimum_size_mm == pytest.approx(1.0)
    assert baseline_mesh.maximum_size_mm == pytest.approx(2.0)
    assert thin_mesh.minimum_size_mm == pytest.approx(2.0 / 3.0)
    assert thin_mesh.maximum_size_mm == pytest.approx(4.0 / 3.0)


def test_result_converts_to_exact_current_run_contract() -> None:
    case = make_baseline_case()
    fake_result = SimpleNamespace(
        source="calculix_2.23",
        mesh_rule="tet2_1to2mm",
        max_stress_mpa=178.0,
        max_disp_mm=0.48,
        mass_kg=0.251,
    )

    run = result_to_contract_run(case, fake_result)

    assert tuple(run.model_dump()) == RUN_COLUMNS
    assert run.design_id == case.design_id
    assert run.source == "calculix_2.23"
    assert run.material_key == "structural_steel_working"


def _synthetic_run(
    design_id: str,
    *,
    stress: float,
    displacement: float,
    mass: float,
) -> Run:
    return Run(
        design_id=design_id,
        source="test",
        mesh_rule="tet2_1to2mm",
        material_key="structural_steel_working",
        load_force_n=500.0,
        load_angle_deg=-90.0,
        thickness_mm=6.0,
        flange_length_mm=80.0,
        arm_width_mm=40.0,
        hole_diameter_mm=8.0,
        fillet_radius_mm=4.0,
        max_stress_mpa=stress,
        max_disp_mm=displacement,
        mass_kg=mass,
    )


def test_expected_synthetic_trends_pass() -> None:
    baseline = _synthetic_run(
        "baseline_l_bracket_v0",
        stress=100.0,
        displacement=1.0,
        mass=1.0,
    )
    rows = [baseline]

    increasing_groups = {
        "load_force": (50.0, 0.5, 1.0, 200.0, 2.0, 1.0),
        "flange_length": (80.0, 0.8, 0.8, 120.0, 1.2, 1.2),
    }
    decreasing_groups = {
        "thickness": (150.0, 1.5, 0.7, 60.0, 0.6, 1.3),
        "arm_width": (140.0, 1.4, 0.7, 70.0, 0.7, 1.3),
        "fillet_radius": (130.0, 1.0, 0.9, 80.0, 1.0, 1.1),
    }

    for name, values in increasing_groups.items():
        low_stress, low_disp, low_mass, high_stress, high_disp, high_mass = values
        rows.extend(
            [
                _synthetic_run(
                    f"ovat_{name}_low",
                    stress=low_stress,
                    displacement=low_disp,
                    mass=low_mass,
                ),
                _synthetic_run(
                    f"ovat_{name}_high",
                    stress=high_stress,
                    displacement=high_disp,
                    mass=high_mass,
                ),
            ]
        )

    for name, values in decreasing_groups.items():
        low_stress, low_disp, low_mass, high_stress, high_disp, high_mass = values
        rows.extend(
            [
                _synthetic_run(
                    f"ovat_{name}_low",
                    stress=low_stress,
                    displacement=low_disp,
                    mass=low_mass,
                ),
                _synthetic_run(
                    f"ovat_{name}_high",
                    stress=high_stress,
                    displacement=high_disp,
                    mass=high_mass,
                ),
            ]
        )

    rows.extend(
        [
            _synthetic_run(
                "ovat_hole_diameter_low",
                stress=100.0,
                displacement=1.0,
                mass=1.1,
            ),
            _synthetic_run(
                "ovat_hole_diameter_high",
                stress=100.0,
                displacement=1.0,
                mass=0.9,
            ),
        ]
    )

    checks = evaluate_ovat_trends(rows)

    assert len(checks) == 15
    assert all(check.passed for check in checks)


def test_missing_solve_is_reported_as_failed_trend() -> None:
    checks = evaluate_ovat_trends([])

    assert checks
    assert not any(check.passed for check in checks)
    assert all("missing:" in check.observed for check in checks)
