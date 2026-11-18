"""Integration tests for the CalculiX bracket solver adapter."""

import shutil

import pytest

pytest.importorskip("cadquery")
pytest.importorskip("gmsh")

from src.data.bracket_spec import make_baseline_case
from src.data.solver import find_ccx, solve_case


def test_calculix_is_available() -> None:
    assert find_ccx()


@pytest.mark.skipif(
    shutil.which("ccx") is None,
    reason="CalculiX ccx is not installed on PATH.",
)
def test_baseline_solver_run(tmp_path) -> None:
    result = solve_case(
        make_baseline_case(),
        tmp_path,
    )

    assert result.deck_path.exists()
    assert result.frd_path.exists()
    assert result.dat_path.exists()
    assert result.log_path.exists()

    assert result.max_stress_mpa > 0.0
    assert result.max_disp_mm > 0.0
    assert result.factor_of_safety > 0.0
    assert result.mass_kg > 0.0

    assert result.max_stress_node_id > 0
    assert len(result.max_stress_location_mm) == 3
    assert result.max_disp_node_id > 0
    assert len(result.max_disp_location_mm) == 3

    assert result.reaction_force_n[1] > 0.0
    assert result.reaction_relative_imbalance < 0.001
    assert result.minimum_element_quality > 0.1
