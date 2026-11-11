"""Generate and validate the first real one-variable-at-a-time dataset.

This module writes a contract-compatible runs.csv without changing the
shared contracts. Additional Role 1 evidence is written separately to
run_diagnostics.csv so no unapproved columns enter the hand-off file.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from contracts import RUN_COLUMNS, SCHEMA_VERSION, Run

from .bracket_spec import (
    ARM_WIDTH_RANGE_MM,
    FILLET_RADIUS_RANGE_MM,
    FLANGE_LENGTH_RANGE_MM,
    HOLE_DIAMETER_RANGE_MM,
    LOAD_ANGLE_DEG,
    LOAD_FORCE_RANGE_N,
    THICKNESS_RANGE_MM,
    BracketCaseInput,
    make_baseline_case,
)
from .mesh import MeshSettings
from .solver import SolverResult, solve_case


# The 1-to-2 mm mesh passed the baseline convergence study. The LHS pilot
# later showed that the thinnest high-stress design required a smaller mesh:
# 0.75-to-1.5 mm versus 0.5-to-1.0 mm changed peak stress by 4.137%.
# Keep 1-to-2 mm for thicknesses of at least 6 mm and scale it down so every
# thinner plate has at least three elements through its nominal thickness.
PRODUCTION_MESH_SETTINGS = MeshSettings(
    minimum_size_mm=1.0,
    maximum_size_mm=2.0,
    element_order=2,
)


def production_mesh_settings(case: BracketCaseInput) -> MeshSettings:
    """Return the thickness-aware production mesh for one design."""

    maximum_size_mm = min(
        PRODUCTION_MESH_SETTINGS.maximum_size_mm,
        case.thickness_mm / 3.0,
    )
    return MeshSettings(
        minimum_size_mm=maximum_size_mm / 2.0,
        maximum_size_mm=maximum_size_mm,
        element_order=2,
    )


DIAGNOSTIC_COLUMNS = (
    "schema_version",
    "design_id",
    "youngs_modulus_mpa",
    "poissons_ratio",
    "yield_strength_mpa",
    "density_kg_m3",
    "factor_of_safety",
    "volume_mm3",
    "reaction_x_n",
    "reaction_y_n",
    "reaction_z_n",
    "reaction_relative_imbalance",
    "node_count",
    "volume_element_count",
    "minimum_element_quality",
    "mean_element_quality",
    "max_stress_node_id",
    "max_stress_x_mm",
    "max_stress_y_mm",
    "max_stress_z_mm",
    "max_disp_node_id",
    "max_disp_x_mm",
    "max_disp_y_mm",
    "max_disp_z_mm",
)


FAILURE_COLUMNS = (
    "schema_version",
    "design_id",
    "failure_stage",
    "error_type",
    "error_message",
    "material_key",
    "load_force_n",
    "load_angle_deg",
    "thickness_mm",
    "flange_length_mm",
    "arm_width_mm",
    "hole_diameter_mm",
    "fillet_radius_mm",
)


TREND_COLUMNS = (
    "check_name",
    "passed",
    "expected",
    "observed",
)


@dataclass(frozen=True)
class TrendCheck:
    """One expected physical trend across three OVAT cases."""

    check_name: str
    passed: bool
    expected: str
    observed: str


@dataclass(frozen=True)
class DatasetSummary:
    """Paths and status returned after one OVAT batch."""

    requested_cases: int
    successful_cases: int
    failed_cases: int
    passed_trend_checks: int
    total_trend_checks: int
    all_trends_passed: bool
    runs_path: Path
    diagnostics_path: Path
    failures_path: Path
    trends_path: Path
    summary_path: Path


def make_ovat_cases() -> tuple[BracketCaseInput, ...]:
    """Return baseline plus low/high cases for six independent inputs."""

    baseline = make_baseline_case()
    variations: tuple[tuple[str, str, float], ...] = (
        ("thickness_low", "thickness_mm", THICKNESS_RANGE_MM[0]),
        ("thickness_high", "thickness_mm", THICKNESS_RANGE_MM[1]),
        (
            "flange_length_low",
            "flange_length_mm",
            FLANGE_LENGTH_RANGE_MM[0],
        ),
        (
            "flange_length_high",
            "flange_length_mm",
            FLANGE_LENGTH_RANGE_MM[1],
        ),
        ("arm_width_low", "arm_width_mm", ARM_WIDTH_RANGE_MM[0]),
        ("arm_width_high", "arm_width_mm", ARM_WIDTH_RANGE_MM[1]),
        (
            "hole_diameter_low",
            "hole_diameter_mm",
            HOLE_DIAMETER_RANGE_MM[0],
        ),
        (
            "hole_diameter_high",
            "hole_diameter_mm",
            HOLE_DIAMETER_RANGE_MM[1],
        ),
        (
            "fillet_radius_low",
            "fillet_radius_mm",
            FILLET_RADIUS_RANGE_MM[0],
        ),
        (
            "fillet_radius_high",
            "fillet_radius_mm",
            FILLET_RADIUS_RANGE_MM[1],
        ),
        ("load_force_low", "load_force_n", LOAD_FORCE_RANGE_N[0]),
        ("load_force_high", "load_force_n", LOAD_FORCE_RANGE_N[1]),
    )

    cases = [baseline]
    for label, field_name, value in variations:
        cases.append(
            baseline.model_copy(
                update={
                    "design_id": f"ovat_{label}",
                    field_name: value,
                }
            )
        )

    # model_copy intentionally does not revalidate in Pydantic v2. Validate
    # every copied case so later edits cannot bypass the input specification.
    return tuple(
        BracketCaseInput.model_validate(case.model_dump())
        for case in cases
    )


def result_to_contract_run(
    case: BracketCaseInput,
    result: SolverResult,
) -> Run:
    """Convert a successful solve into the current shared Run contract."""

    return Run(
        design_id=case.design_id,
        source=result.source,
        mesh_rule=result.mesh_rule,
        material_key=case.material.material_key,
        load_force_n=case.load_force_n,
        load_angle_deg=LOAD_ANGLE_DEG,
        thickness_mm=case.thickness_mm,
        flange_length_mm=case.flange_length_mm,
        arm_width_mm=case.arm_width_mm,
        hole_diameter_mm=case.hole_diameter_mm,
        fillet_radius_mm=case.fillet_radius_mm,
        max_stress_mpa=result.max_stress_mpa,
        max_disp_mm=result.max_disp_mm,
        mass_kg=result.mass_kg,
    )


def result_to_diagnostics(
    case: BracketCaseInput,
    result: SolverResult,
) -> dict[str, object]:
    """Return Role 1 evidence that is not part of shared runs.csv."""

    return {
        "schema_version": SCHEMA_VERSION,
        "design_id": case.design_id,
        "youngs_modulus_mpa": case.material.youngs_modulus_mpa,
        "poissons_ratio": case.material.poissons_ratio,
        "yield_strength_mpa": case.material.yield_strength_mpa,
        "density_kg_m3": case.material.density_kg_m3,
        "factor_of_safety": result.factor_of_safety,
        "volume_mm3": result.volume_mm3,
        "reaction_x_n": result.reaction_force_n[0],
        "reaction_y_n": result.reaction_force_n[1],
        "reaction_z_n": result.reaction_force_n[2],
        "reaction_relative_imbalance": (
            result.reaction_relative_imbalance
        ),
        "node_count": result.node_count,
        "volume_element_count": result.volume_element_count,
        "minimum_element_quality": result.minimum_element_quality,
        "mean_element_quality": result.mean_element_quality,
        "max_stress_node_id": result.max_stress_node_id,
        "max_stress_x_mm": result.max_stress_location_mm[0],
        "max_stress_y_mm": result.max_stress_location_mm[1],
        "max_stress_z_mm": result.max_stress_location_mm[2],
        "max_disp_node_id": result.max_disp_node_id,
        "max_disp_x_mm": result.max_disp_location_mm[0],
        "max_disp_y_mm": result.max_disp_location_mm[1],
        "max_disp_z_mm": result.max_disp_location_mm[2],
    }


def case_to_failure(
    case: BracketCaseInput,
    error: Exception,
) -> dict[str, object]:
    """Record an unsuccessful integrated geometry/mesh/solver run."""

    return {
        "schema_version": SCHEMA_VERSION,
        "design_id": case.design_id,
        "failure_stage": "integrated_solve_pipeline",
        "error_type": type(error).__name__,
        "error_message": str(error),
        "material_key": case.material.material_key,
        "load_force_n": case.load_force_n,
        "load_angle_deg": LOAD_ANGLE_DEG,
        "thickness_mm": case.thickness_mm,
        "flange_length_mm": case.flange_length_mm,
        "arm_width_mm": case.arm_width_mm,
        "hole_diameter_mm": case.hole_diameter_mm,
        "fillet_radius_mm": case.fillet_radius_mm,
    }


def _write_rows(
    path: Path,
    columns: Sequence[str],
    rows: Iterable[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _format_values(
    low: Run,
    baseline: Run,
    high: Run,
    metric: str,
) -> str:
    return (
        f"low={getattr(low, metric):.9g}; "
        f"baseline={getattr(baseline, metric):.9g}; "
        f"high={getattr(high, metric):.9g}"
    )


def evaluate_ovat_trends(runs: Sequence[Run]) -> list[TrendCheck]:
    """Check the most defensible expected trends in the solved rows."""

    by_id = {run.design_id: run for run in runs}
    baseline_id = "baseline_l_bracket_v0"
    checks: list[TrendCheck] = []

    def add_monotonic(
        name: str,
        low_id: str,
        high_id: str,
        metric: str,
        direction: str,
    ) -> None:
        required = (low_id, baseline_id, high_id)
        missing = [design_id for design_id in required if design_id not in by_id]
        expected = (
            f"{metric} strictly {direction} from low to baseline to high"
        )

        if missing:
            checks.append(
                TrendCheck(
                    check_name=name,
                    passed=False,
                    expected=expected,
                    observed="missing: " + ", ".join(missing),
                )
            )
            return

        low = by_id[low_id]
        baseline = by_id[baseline_id]
        high = by_id[high_id]
        values = (
            getattr(low, metric),
            getattr(baseline, metric),
            getattr(high, metric),
        )

        if direction == "increases":
            passed = values[0] < values[1] < values[2]
        elif direction == "decreases":
            passed = values[0] > values[1] > values[2]
        else:
            raise ValueError(f"Unsupported trend direction: {direction}")

        checks.append(
            TrendCheck(
                check_name=name,
                passed=passed,
                expected=expected,
                observed=_format_values(low, baseline, high, metric),
            )
        )

    add_monotonic(
        "load_vs_stress",
        "ovat_load_force_low",
        "ovat_load_force_high",
        "max_stress_mpa",
        "increases",
    )
    add_monotonic(
        "load_vs_displacement",
        "ovat_load_force_low",
        "ovat_load_force_high",
        "max_disp_mm",
        "increases",
    )
    add_monotonic(
        "thickness_vs_stress",
        "ovat_thickness_low",
        "ovat_thickness_high",
        "max_stress_mpa",
        "decreases",
    )
    add_monotonic(
        "thickness_vs_displacement",
        "ovat_thickness_low",
        "ovat_thickness_high",
        "max_disp_mm",
        "decreases",
    )
    add_monotonic(
        "thickness_vs_mass",
        "ovat_thickness_low",
        "ovat_thickness_high",
        "mass_kg",
        "increases",
    )
    add_monotonic(
        "flange_length_vs_stress",
        "ovat_flange_length_low",
        "ovat_flange_length_high",
        "max_stress_mpa",
        "increases",
    )
    add_monotonic(
        "flange_length_vs_displacement",
        "ovat_flange_length_low",
        "ovat_flange_length_high",
        "max_disp_mm",
        "increases",
    )
    add_monotonic(
        "flange_length_vs_mass",
        "ovat_flange_length_low",
        "ovat_flange_length_high",
        "mass_kg",
        "increases",
    )
    add_monotonic(
        "arm_width_vs_stress",
        "ovat_arm_width_low",
        "ovat_arm_width_high",
        "max_stress_mpa",
        "decreases",
    )
    add_monotonic(
        "arm_width_vs_displacement",
        "ovat_arm_width_low",
        "ovat_arm_width_high",
        "max_disp_mm",
        "decreases",
    )
    add_monotonic(
        "arm_width_vs_mass",
        "ovat_arm_width_low",
        "ovat_arm_width_high",
        "mass_kg",
        "increases",
    )
    add_monotonic(
        "hole_diameter_vs_mass",
        "ovat_hole_diameter_low",
        "ovat_hole_diameter_high",
        "mass_kg",
        "decreases",
    )
    add_monotonic(
        "fillet_radius_vs_stress",
        "ovat_fillet_radius_low",
        "ovat_fillet_radius_high",
        "max_stress_mpa",
        "decreases",
    )
    add_monotonic(
        "fillet_radius_vs_mass",
        "ovat_fillet_radius_low",
        "ovat_fillet_radius_high",
        "mass_kg",
        "increases",
    )

    # Geometry is unchanged in the load-only cases, so mass should remain
    # numerically identical. This is checked separately from monotonic trends.
    load_ids = (
        "ovat_load_force_low",
        baseline_id,
        "ovat_load_force_high",
    )
    missing_load = [design_id for design_id in load_ids if design_id not in by_id]
    if missing_load:
        checks.append(
            TrendCheck(
                check_name="load_does_not_change_mass",
                passed=False,
                expected="mass is unchanged when only force changes",
                observed="missing: " + ", ".join(missing_load),
            )
        )
    else:
        masses = [by_id[design_id].mass_kg for design_id in load_ids]
        relative_spread = (max(masses) - min(masses)) / masses[1]
        checks.append(
            TrendCheck(
                check_name="load_does_not_change_mass",
                passed=relative_spread <= 1.0e-9,
                expected="mass is unchanged when only force changes",
                observed=(
                    f"masses={masses}; relative_spread={relative_spread:.3g}"
                ),
            )
        )

    return checks


def generate_ovat_dataset(
    output_directory: str | Path,
    cases: Sequence[BracketCaseInput] | None = None,
) -> DatasetSummary:
    """Solve the OVAT batch and write hand-off and QA artifacts."""

    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_output_dir = output_dir / "cases"

    selected_cases = tuple(cases) if cases is not None else make_ovat_cases()
    runs: list[Run] = []
    diagnostic_rows: list[dict[str, object]] = []
    failure_rows: list[dict[str, object]] = []

    for index, case in enumerate(selected_cases, start=1):
        print(
            f"[{index}/{len(selected_cases)}] Solving {case.design_id}...",
            flush=True,
        )
        try:
            result = solve_case(
                case,
                case_output_dir / case.design_id,
                mesh_settings=production_mesh_settings(case),
            )
        except Exception as error:  # record one failed design and continue
            failure_rows.append(case_to_failure(case, error))
            print(
                f"  FAILED: {type(error).__name__}: {error}",
                flush=True,
            )
            continue

        runs.append(result_to_contract_run(case, result))
        diagnostic_rows.append(result_to_diagnostics(case, result))
        print(
            f"  OK: stress={result.max_stress_mpa:.3f} MPa, "
            f"disp={result.max_disp_mm:.6f} mm, "
            f"mass={result.mass_kg:.6f} kg",
            flush=True,
        )

    runs_path = output_dir / "runs.csv"
    diagnostics_path = output_dir / "run_diagnostics.csv"
    failures_path = output_dir / "failures.csv"
    trends_path = output_dir / "trend_checks.csv"
    summary_path = output_dir / "summary.json"

    _write_rows(
        runs_path,
        RUN_COLUMNS,
        (run.model_dump() for run in runs),
    )
    _write_rows(
        diagnostics_path,
        DIAGNOSTIC_COLUMNS,
        diagnostic_rows,
    )
    _write_rows(
        failures_path,
        FAILURE_COLUMNS,
        failure_rows,
    )

    trend_checks = evaluate_ovat_trends(runs)
    _write_rows(
        trends_path,
        TREND_COLUMNS,
        (asdict(check) for check in trend_checks),
    )

    passed_trends = sum(check.passed for check in trend_checks)
    all_trends_passed = bool(trend_checks) and all(
        check.passed for check in trend_checks
    )
    summary_payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset_name": "l_bracket_v0_ovat",
        "dataset_stage": "one_variable_at_a_time",
        "bracket_family": "l_bracket_v0",
        "baseline_production_mesh_rule": (
            PRODUCTION_MESH_SETTINGS.mesh_rule
        ),
        "production_mesh_strategy": (
            "tet2; maximum_size_mm=min(2.0, thickness_mm/3); "
            "minimum_size_mm=maximum_size_mm/2"
        ),
        "requested_cases": len(selected_cases),
        "successful_cases": len(runs),
        "failed_cases": len(failure_rows),
        "passed_trend_checks": passed_trends,
        "total_trend_checks": len(trend_checks),
        "all_trends_passed": all_trends_passed,
        "ready_for_latin_hypercube_pilot": (
            len(failure_rows) == 0 and all_trends_passed
        ),
        "contract_status": {
            "runs_csv_uses_exact_shared_columns": True,
            "shared_contract_modified": False,
            "role1_diagnostics_are_separate": True,
            "coordination_required": [
                "Confirm whether material_key should be "
                "structural_steel_working or structural_steel.",
                "Approve numerical material fields in StudySpec and Run "
                "before material-varying training data is generated.",
                "Resolve the load-location mismatch: the shared Load "
                "documentation and example say bolt hole, while the verified "
                "Role 1 solver loads the horizontal free-end face.",
                "Synchronize contracts.make_examples BASELINE and RANGES "
                "with the verified Role 1 baseline and pilot ranges.",
                "Update schema_version, examples, fakes, UI fixtures, and "
                "contract tests after an approved contract change.",
            ],
        },
        "artifacts": {
            "runs_csv": str(runs_path),
            "run_diagnostics_csv": str(diagnostics_path),
            "failures_csv": str(failures_path),
            "trend_checks_csv": str(trends_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote: {runs_path}")
    print(f"Wrote: {diagnostics_path}")
    print(f"Wrote: {failures_path}")
    print(f"Wrote: {trends_path}")
    print(f"Wrote: {summary_path}")
    print(
        f"Completed {len(runs)}/{len(selected_cases)} cases; "
        f"trend checks {passed_trends}/{len(trend_checks)} passed."
    )

    return DatasetSummary(
        requested_cases=len(selected_cases),
        successful_cases=len(runs),
        failed_cases=len(failure_rows),
        passed_trend_checks=passed_trends,
        total_trend_checks=len(trend_checks),
        all_trends_passed=all_trends_passed,
        runs_path=runs_path,
        diagnostics_path=diagnostics_path,
        failures_path=failures_path,
        trends_path=trends_path,
        summary_path=summary_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the first real SimpleSim OVAT dataset."
    )
    parser.add_argument(
        "--output",
        default="data/generated/ovat_v0",
        help="Directory for case outputs and dataset CSV files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional first-N case limit for a quick smoke run.",
    )
    arguments = parser.parse_args()

    cases = make_ovat_cases()
    if arguments.limit is not None:
        if arguments.limit < 1:
            parser.error("--limit must be at least 1")
        cases = cases[: arguments.limit]

    generate_ovat_dataset(arguments.output, cases)


if __name__ == "__main__":
    main()
