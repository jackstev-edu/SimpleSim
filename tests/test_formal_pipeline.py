"""Tests for the resumable 200-case formal data pipeline."""

from pathlib import Path

import pandas as pd
import pytest

from contracts import RUN_COLUMNS
from src.data import formal_pipeline
from src.data.formal_pipeline import (
    FORMAL_PARAMETER_RANGES,
    MODEL_DATASET_COLUMNS,
    cleanup_successful_case_files,
    make_formal_cases,
    refined_retry_mesh_settings,
)
from src.data.ovat_pipeline import production_mesh_settings
from src.data.solver import SolverExecutionError, SolverResult


def _fake_result(case, output_directory, mesh_settings, timeout_seconds):
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    volume = (
        case.thickness_mm
        * case.arm_width_mm
        * (case.flange_length_mm + 60.0)
    )
    stress = (
        100.0
        * (6.0 / case.thickness_mm) ** 2
        * (case.flange_length_mm / 80.0)
        * (0.30 / case.material.poissons_ratio)
    )
    displacement = stress / case.material.youngs_modulus_mpa * 1000.0
    mass = volume * case.material.density_kg_m3 * 1.0e-9
    return SolverResult(
        design_id=case.design_id,
        source="test_solver",
        mesh_rule=mesh_settings.mesh_rule,
        max_stress_mpa=stress,
        max_stress_node_id=1,
        max_stress_location_mm=(10.0, 6.0, 20.0),
        max_disp_mm=displacement,
        max_disp_node_id=2,
        max_disp_location_mm=(case.flange_length_mm, 0.0, 20.0),
        factor_of_safety=case.material.yield_strength_mpa / stress,
        volume_mm3=volume,
        mass_kg=mass,
        reaction_force_n=(0.0, case.load_force_n, 0.0),
        reaction_relative_imbalance=1.0e-8,
        node_count=1000,
        volume_element_count=500,
        minimum_element_quality=0.2,
        mean_element_quality=0.8,
        deck_path=output_dir / "case.inp",
        frd_path=output_dir / "case.frd",
        dat_path=output_dir / "case.dat",
        log_path=output_dir / "case.log",
    )


def test_formal_lhs_is_deterministic_stratified_and_valid() -> None:
    first = make_formal_cases(40, seed=20261111)
    second = make_formal_cases(40, seed=20261111)

    assert [case.model_dump() for case in first] == [
        case.model_dump() for case in second
    ]
    assert len({case.design_id for case in first}) == 40

    for name, (low, high) in FORMAL_PARAMETER_RANGES.items():
        values = [
            (
                case.material.poissons_ratio
                if name == "poissons_ratio"
                else getattr(case, name)
            )
            for case in first
        ]
        assert all(low <= value <= high for value in values)
        strata = sorted(
            min(int((value - low) / (high - low) * 40), 39)
            for value in values
        )
        assert strata == list(range(40))


def test_poissons_ratio_is_a_model_input_not_a_scaling_field() -> None:
    cases = make_formal_cases(20, seed=4)
    ratios = [case.material.poissons_ratio for case in cases]

    assert min(ratios) >= 0.25
    assert max(ratios) <= 0.35
    assert len(set(ratios)) == len(ratios)
    assert all(case.material.youngs_modulus_mpa == 200000.0 for case in cases)


def test_formal_pipeline_checkpoints_and_resumes(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    def recording_solver(*args, **kwargs):
        case = args[0]
        calls.append(case.design_id)
        return _fake_result(*args, **kwargs)

    monkeypatch.setattr(formal_pipeline, "solve_case", recording_solver)
    cases = make_formal_cases(3, seed=9)

    first = formal_pipeline.generate_formal_dataset(
        tmp_path,
        cases,
        case_limit=2,
    )
    assert first.completed_cases == 2
    assert len(calls) == 2

    second = formal_pipeline.generate_formal_dataset(tmp_path, cases)
    assert second.completed_cases == 3
    assert second.skipped_existing_cases == 2
    assert len(calls) == 3
    assert second.all_quality_checks_passed

    runs = pd.read_csv(tmp_path / "runs.csv")
    model_data = pd.read_csv(tmp_path / "model_dataset.csv")
    assert tuple(runs.columns) == RUN_COLUMNS
    assert tuple(model_data.columns) == MODEL_DATASET_COLUMNS
    assert len(runs) == len(model_data) == 3
    assert "poissons_ratio" in model_data


def test_changed_plan_cannot_resume_same_directory(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(formal_pipeline, "solve_case", _fake_result)
    formal_pipeline.generate_formal_dataset(
        tmp_path,
        make_formal_cases(2, seed=1),
    )

    with pytest.raises(ValueError, match="sample_plan"):
        formal_pipeline.generate_formal_dataset(
            tmp_path,
            make_formal_cases(2, seed=2),
        )


def test_successful_case_cleanup_keeps_log(tmp_path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "case.frd").write_text("large result")
    (case_dir / "case.msh").write_text("large mesh")
    (case_dir / "case.log").write_text("solver log")

    removed = cleanup_successful_case_files(case_dir)

    assert {path.suffix for path in removed} == {".frd", ".msh"}
    assert not (case_dir / "case.frd").exists()
    assert not (case_dir / "case.msh").exists()
    assert (case_dir / "case.log").read_text() == "solver log"


def test_nonpositive_jacobian_is_retried_with_refined_mesh(
    tmp_path,
    monkeypatch,
) -> None:
    case = make_formal_cases(1, seed=12)[0]
    primary = production_mesh_settings(case)
    refined = refined_retry_mesh_settings(case)
    attempted_mesh_rules: list[str] = []

    def inverted_then_successful(
        case,
        output_directory,
        mesh_settings,
        timeout_seconds,
    ):
        attempted_mesh_rules.append(mesh_settings.mesh_rule)
        output_dir = Path(output_directory)
        output_dir.mkdir(parents=True, exist_ok=True)
        if mesh_settings.mesh_rule == primary.mesh_rule:
            (output_dir / f"{case.design_id}_ccx.log").write_text(
                "*ERROR in e_c3d: nonpositive jacobian",
                encoding="utf-8",
            )
            raise SolverExecutionError("CalculiX return code 201")
        return _fake_result(
            case,
            output_directory,
            mesh_settings,
            timeout_seconds,
        )

    monkeypatch.setattr(
        formal_pipeline,
        "solve_case",
        inverted_then_successful,
    )

    summary = formal_pipeline.generate_formal_dataset(
        tmp_path,
        (case,),
    )

    assert summary.completed_cases == 1
    assert attempted_mesh_rules == [
        primary.mesh_rule,
        refined.mesh_rule,
    ]
    assert refined.maximum_size_mm == pytest.approx(
        primary.maximum_size_mm * 0.75
    )
