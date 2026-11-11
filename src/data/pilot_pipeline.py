"""Generate the first 40-case Latin-hypercube L-bracket pilot dataset."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from contracts import RUN_COLUMNS, SCHEMA_VERSION, Run

from .bracket_spec import (
    ARM_WIDTH_RANGE_MM,
    FILLET_RADIUS_RANGE_MM,
    FLANGE_LENGTH_RANGE_MM,
    HOLE_DIAMETER_RANGE_MM,
    THICKNESS_RANGE_MM,
    BracketCaseInput,
    make_baseline_case,
)
from .ovat_pipeline import (
    DIAGNOSTIC_COLUMNS,
    FAILURE_COLUMNS,
    PRODUCTION_MESH_SETTINGS,
    case_to_failure,
    result_to_contract_run,
    result_to_diagnostics,
    production_mesh_settings,
)
from .solver import solve_case


PILOT_PARAMETER_RANGES = {
    "thickness_mm": THICKNESS_RANGE_MM,
    "flange_length_mm": FLANGE_LENGTH_RANGE_MM,
    "arm_width_mm": ARM_WIDTH_RANGE_MM,
    "hole_diameter_mm": HOLE_DIAMETER_RANGE_MM,
    "fillet_radius_mm": FILLET_RADIUS_RANGE_MM,
}

SAMPLE_PLAN_COLUMNS = (
    "design_id",
    *PILOT_PARAMETER_RANGES,
    "load_force_n",
    "material_key",
)

QUALITY_CHECK_COLUMNS = (
    "check_name",
    "passed",
    "expected",
    "observed",
)


@dataclass(frozen=True)
class QualityCheck:
    """One batch-level requirement for accepting the pilot dataset."""

    check_name: str
    passed: bool
    expected: str
    observed: str


@dataclass(frozen=True)
class PilotSummary:
    """Paths and status returned after the pilot batch."""

    requested_cases: int
    successful_cases: int
    failed_cases: int
    passed_quality_checks: int
    total_quality_checks: int
    ready_for_model_training: bool
    runs_path: Path
    diagnostics_path: Path
    failures_path: Path
    sample_plan_path: Path
    quality_checks_path: Path
    summary_path: Path


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


def latin_hypercube_unit_samples(
    sample_count: int,
    dimension_count: int,
    seed: int,
) -> np.ndarray:
    """Return a deterministic randomized Latin hypercube in [0, 1)."""

    if sample_count < 1:
        raise ValueError("sample_count must be at least 1")
    if dimension_count < 1:
        raise ValueError("dimension_count must be at least 1")

    rng = np.random.default_rng(seed)
    samples = np.empty((sample_count, dimension_count), dtype=float)

    for dimension in range(dimension_count):
        values = (
            np.arange(sample_count, dtype=float)
            + rng.random(sample_count)
        ) / sample_count
        rng.shuffle(values)
        samples[:, dimension] = values

    return samples


def make_latin_hypercube_cases(
    sample_count: int = 40,
    seed: int = 24679,
) -> tuple[BracketCaseInput, ...]:
    """Create validated pilot cases across all five geometry ranges."""

    baseline = make_baseline_case()
    parameter_names = tuple(PILOT_PARAMETER_RANGES)
    unit_samples = latin_hypercube_unit_samples(
        sample_count,
        len(parameter_names),
        seed,
    )

    cases: list[BracketCaseInput] = []
    for sample_index, unit_row in enumerate(unit_samples):
        updates: dict[str, object] = {
            "design_id": f"lhs_{seed}_{sample_index:03d}",
        }

        for parameter_name, unit_value in zip(
            parameter_names,
            unit_row,
        ):
            low, high = PILOT_PARAMETER_RANGES[parameter_name]
            updates[parameter_name] = low + float(unit_value) * (high - low)

        copied = baseline.model_copy(update=updates)
        cases.append(
            BracketCaseInput.model_validate(copied.model_dump())
        )

    return tuple(cases)


def case_to_sample_plan(case: BracketCaseInput) -> dict[str, object]:
    """Return the planned input row even if its solve later fails."""

    return {
        "design_id": case.design_id,
        **{
            parameter_name: getattr(case, parameter_name)
            for parameter_name in PILOT_PARAMETER_RANGES
        },
        "load_force_n": case.load_force_n,
        "material_key": case.material.material_key,
    }


def evaluate_pilot_quality(
    cases: Sequence[BracketCaseInput],
    runs: Sequence[Run],
    diagnostics: Sequence[dict[str, object]],
    failure_count: int,
) -> list[QualityCheck]:
    """Evaluate coverage, solver success, and numerical QA requirements."""

    checks: list[QualityCheck] = []
    expected_count = len(cases)

    checks.append(
        QualityCheck(
            check_name="all_cases_solved",
            passed=len(runs) == expected_count and failure_count == 0,
            expected=f"{expected_count} successful rows and zero failures",
            observed=f"successes={len(runs)}; failures={failure_count}",
        )
    )
    checks.append(
        QualityCheck(
            check_name="unique_design_ids",
            passed=(
                len({case.design_id for case in cases}) == expected_count
                and len({run.design_id for run in runs}) == len(runs)
            ),
            expected="all planned and successful design IDs are unique",
            observed=(
                f"planned_unique={len({case.design_id for case in cases})}; "
                f"run_unique={len({run.design_id for run in runs})}"
            ),
        )
    )
    checks.append(
        QualityCheck(
            check_name="positive_solver_outputs",
            passed=all(
                run.max_stress_mpa > 0.0
                and run.max_disp_mm > 0.0
                and run.mass_kg > 0.0
                for run in runs
            ) and len(runs) == expected_count,
            expected="all stress, displacement, and mass values are positive",
            observed=f"checked_rows={len(runs)}",
        )
    )

    max_reaction_imbalance = max(
        (
            float(row["reaction_relative_imbalance"])
            for row in diagnostics
        ),
        default=float("inf"),
    )
    checks.append(
        QualityCheck(
            check_name="reaction_force_balance",
            passed=(
                len(diagnostics) == expected_count
                and max_reaction_imbalance <= 0.01
            ),
            expected="every relative reaction imbalance is at most 0.01",
            observed=f"maximum={max_reaction_imbalance:.9g}",
        )
    )

    minimum_quality = min(
        (
            float(row["minimum_element_quality"])
            for row in diagnostics
        ),
        default=float("-inf"),
    )
    checks.append(
        QualityCheck(
            check_name="minimum_element_quality",
            passed=(
                len(diagnostics) == expected_count
                and minimum_quality > 0.1
            ),
            expected="every minimum element quality is above 0.1",
            observed=f"batch_minimum={minimum_quality:.9g}",
        )
    )

    # In a Latin hypercube, each parameter uses every one of N equal strata
    # exactly once. Recalculate stratum indices from the physical values.
    for parameter_name, (low, high) in PILOT_PARAMETER_RANGES.items():
        strata = []
        for case in cases:
            normalized = (getattr(case, parameter_name) - low) / (high - low)
            stratum = min(
                int(normalized * expected_count),
                expected_count - 1,
            )
            strata.append(stratum)

        unique_strata = len(set(strata))
        checks.append(
            QualityCheck(
                check_name=f"lhs_strata_{parameter_name}",
                passed=unique_strata == expected_count,
                expected=(
                    f"all {expected_count} strata used exactly once"
                ),
                observed=f"unique_strata={unique_strata}",
            )
        )

    return checks


def generate_latin_hypercube_pilot(
    output_directory: str | Path,
    sample_count: int = 40,
    seed: int = 24679,
) -> PilotSummary:
    """Generate, solve, and validate the first joint-parameter pilot."""

    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_output_dir = output_dir / "cases"

    cases = make_latin_hypercube_cases(sample_count, seed)
    runs: list[Run] = []
    diagnostic_rows: list[dict[str, object]] = []
    failure_rows: list[dict[str, object]] = []

    sample_plan_path = output_dir / "sample_plan.csv"
    _write_rows(
        sample_plan_path,
        SAMPLE_PLAN_COLUMNS,
        (case_to_sample_plan(case) for case in cases),
    )

    for index, case in enumerate(cases, start=1):
        print(
            f"[{index}/{len(cases)}] Solving {case.design_id}...",
            flush=True,
        )
        try:
            result = solve_case(
                case,
                case_output_dir / case.design_id,
                mesh_settings=production_mesh_settings(case),
            )
        except Exception as error:  # record the failed sample and continue
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
    quality_checks_path = output_dir / "quality_checks.csv"
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

    quality_checks = evaluate_pilot_quality(
        cases,
        runs,
        diagnostic_rows,
        len(failure_rows),
    )
    _write_rows(
        quality_checks_path,
        QUALITY_CHECK_COLUMNS,
        (asdict(check) for check in quality_checks),
    )

    passed_checks = sum(check.passed for check in quality_checks)
    all_checks_passed = bool(quality_checks) and all(
        check.passed for check in quality_checks
    )
    summary_payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset_name": "l_bracket_v0_lhs_pilot",
        "dataset_stage": "latin_hypercube_pilot",
        "bracket_family": "l_bracket_v0",
        "sample_count": sample_count,
        "random_seed": seed,
        "baseline_production_mesh_rule": (
            PRODUCTION_MESH_SETTINGS.mesh_rule
        ),
        "production_mesh_strategy": (
            "tet2; maximum_size_mm=min(2.0, thickness_mm/3); "
            "minimum_size_mm=maximum_size_mm/2"
        ),
        "load_force_n": make_baseline_case().load_force_n,
        "material_key": make_baseline_case().material.material_key,
        "parameter_ranges": {
            name: {"low": bounds[0], "high": bounds[1]}
            for name, bounds in PILOT_PARAMETER_RANGES.items()
        },
        "successful_cases": len(runs),
        "failed_cases": len(failure_rows),
        "passed_quality_checks": passed_checks,
        "total_quality_checks": len(quality_checks),
        "all_quality_checks_passed": all_checks_passed,
        "ready_for_model_training": all_checks_passed,
        "contract_status": {
            "runs_csv_uses_exact_shared_columns": True,
            "shared_contract_modified": False,
            "material_and_load_are_fixed_for_this_pilot": True,
            "contract_coordination_still_required_before_handoff": True,
        },
        "artifacts": {
            "sample_plan_csv": str(sample_plan_path),
            "runs_csv": str(runs_path),
            "run_diagnostics_csv": str(diagnostics_path),
            "failures_csv": str(failures_path),
            "quality_checks_csv": str(quality_checks_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote: {sample_plan_path}")
    print(f"Wrote: {runs_path}")
    print(f"Wrote: {diagnostics_path}")
    print(f"Wrote: {failures_path}")
    print(f"Wrote: {quality_checks_path}")
    print(f"Wrote: {summary_path}")
    print(
        f"Completed {len(runs)}/{sample_count} cases; "
        f"quality checks {passed_checks}/{len(quality_checks)} passed."
    )

    return PilotSummary(
        requested_cases=sample_count,
        successful_cases=len(runs),
        failed_cases=len(failure_rows),
        passed_quality_checks=passed_checks,
        total_quality_checks=len(quality_checks),
        ready_for_model_training=all_checks_passed,
        runs_path=runs_path,
        diagnostics_path=diagnostics_path,
        failures_path=failures_path,
        sample_plan_path=sample_plan_path,
        quality_checks_path=quality_checks_path,
        summary_path=summary_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the SimpleSim 40-case LHS pilot dataset."
    )
    parser.add_argument(
        "--output",
        default="data/generated/lhs_pilot_v0",
        help="Directory for case outputs and dataset files.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=40,
        help="Number of Latin-hypercube samples (default: 40).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=24679,
        help="Deterministic sampling seed (default: 24679).",
    )
    arguments = parser.parse_args()

    generate_latin_hypercube_pilot(
        arguments.output,
        sample_count=arguments.samples,
        seed=arguments.seed,
    )


if __name__ == "__main__":
    main()
