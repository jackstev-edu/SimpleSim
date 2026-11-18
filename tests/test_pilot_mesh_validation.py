"""Tests for representative LHS pilot mesh verification."""

from dataclasses import replace

import pytest

from src.data.pilot_mesh_validation import (
    SelectedPilotCase,
    case_from_run_row,
    compare_mesh_result,
    select_validation_cases,
    verification_mesh_settings,
)
from src.data.solver import SolverResult


def _run_row(
    design_id: str,
    stress: float,
    displacement: float = 1.0,
) -> dict[str, str]:
    return {
        "design_id": design_id,
        "mesh_rule": "tet2_1to2mm",
        "load_force_n": "500",
        "thickness_mm": "6",
        "flange_length_mm": "80",
        "arm_width_mm": "40",
        "hole_diameter_mm": "8",
        "fillet_radius_mm": "4",
        "max_stress_mpa": str(stress),
        "max_disp_mm": str(displacement),
    }


def _diagnostic_row(
    design_id: str,
    stress_x: float,
    quality: float,
) -> dict[str, str]:
    return {
        "design_id": design_id,
        "minimum_element_quality": str(quality),
        "max_stress_x_mm": str(stress_x),
        "max_stress_y_mm": "6",
        "max_stress_z_mm": "20",
    }


def _solver_result() -> SolverResult:
    from pathlib import Path

    return SolverResult(
        design_id="high",
        source="calculix_2.23",
        mesh_rule="tet2_0.75to1.5mm",
        max_stress_mpa=103.0,
        max_stress_node_id=1,
        max_stress_location_mm=(10.0, 6.0, 20.0),
        max_disp_mm=1.02,
        max_disp_node_id=2,
        max_disp_location_mm=(80.0, 0.0, 20.0),
        factor_of_safety=2.4,
        volume_mm3=32000.0,
        mass_kg=0.2512,
        reaction_force_n=(0.0, 500.0, 0.0),
        reaction_relative_imbalance=1.0e-7,
        node_count=100,
        volume_element_count=50,
        minimum_element_quality=0.2,
        mean_element_quality=0.8,
        deck_path=Path("case.inp"),
        frd_path=Path("case.frd"),
        dat_path=Path("case.dat"),
        log_path=Path("case.log"),
    )


def test_selects_required_representative_cases() -> None:
    runs = [
        _run_row("high", 100.0),
        _run_row("low", 20.0),
        _run_row("hole", 60.0),
        _run_row("quality", 50.0),
    ]
    diagnostics = [
        _diagnostic_row("high", 10.0, 0.25),
        _diagnostic_row("low", 11.0, 0.24),
        _diagnostic_row("hole", 60.0, 0.23),
        _diagnostic_row("quality", 12.0, 0.15),
    ]

    selected = select_validation_cases(runs, diagnostics)

    assert [item.reason for item in selected] == [
        "highest_stress",
        "lowest_stress",
        "hole_hotspot",
        "lowest_production_mesh_quality",
    ]


def test_duplicate_selection_is_solved_only_once() -> None:
    runs = [_run_row("high", 100.0), _run_row("low", 20.0)]
    diagnostics = [
        _diagnostic_row("high", 60.0, 0.25),
        _diagnostic_row("low", 10.0, 0.10),
    ]

    selected = select_validation_cases(runs, diagnostics)

    assert {item.run_row["design_id"] for item in selected} == {
        "high",
        "low",
    }


def test_case_is_reconstructed_from_run_row() -> None:
    case = case_from_run_row(_run_row("case_a", 100.0))

    assert case.design_id == "case_a"
    assert case.load_force_n == 500.0
    assert case.flange_length_mm == 80.0


def test_verification_mesh_is_finer_than_adaptive_production() -> None:
    case = case_from_run_row(_run_row("case_a", 100.0))
    mesh = verification_mesh_settings(case)

    assert mesh.minimum_size_mm == pytest.approx(2.0 / 3.0)
    assert mesh.maximum_size_mm == pytest.approx(4.0 / 3.0)


def test_comparison_accepts_changes_below_limits() -> None:
    selected = SelectedPilotCase(
        reason="highest_stress",
        run_row=_run_row("high", 100.0),
        diagnostic_row=_diagnostic_row("high", 10.0, 0.25),
    )

    comparison = compare_mesh_result(selected, _solver_result())

    assert comparison.stress_change_pct == pytest.approx(3.0)
    assert comparison.displacement_change_pct == pytest.approx(2.0)
    assert comparison.all_checks_pass


def test_comparison_rejects_large_stress_change() -> None:
    selected = SelectedPilotCase(
        reason="highest_stress",
        run_row=_run_row("high", 100.0),
        diagnostic_row=_diagnostic_row("high", 10.0, 0.25),
    )
    failed_result = replace(_solver_result(), max_stress_mpa=106.0)

    comparison = compare_mesh_result(selected, failed_result)

    assert comparison.stress_change_pct == pytest.approx(6.0)
    assert not comparison.stress_change_pass
    assert not comparison.all_checks_pass
