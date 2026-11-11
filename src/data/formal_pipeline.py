"""Generate the resumable 200-case Role 1 model-training dataset."""

from __future__ import annotations

import argparse
import csv
import json
import math
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from contracts import RUN_COLUMNS, SCHEMA_VERSION, Run

from .bracket_spec import (
    ARM_WIDTH_RANGE_MM,
    FILLET_RADIUS_RANGE_MM,
    FLANGE_LENGTH_RANGE_MM,
    HOLE_DIAMETER_RANGE_MM,
    THICKNESS_RANGE_MM,
    BracketCaseInput,
    MaterialProperties,
    make_baseline_case,
)
from .ovat_pipeline import (
    DIAGNOSTIC_COLUMNS,
    case_to_failure,
    production_mesh_settings,
    result_to_contract_run,
    result_to_diagnostics,
)
from .mesh import MeshSettings
from .pilot_pipeline import latin_hypercube_unit_samples
from .solver import SolverExecutionError, SolverResult, solve_case


FORMAL_SAMPLE_COUNT = 200
FORMAL_RANDOM_SEED = 20261111
POISSONS_RATIO_RANGE = (0.25, 0.35)
FORMAL_MATERIAL_KEY = "role1_isotropic_reference"
MODEL_DATASET_SCHEMA_VERSION = "role1_model_0.1.0"
DEFAULT_SOLVER_TIMEOUT_SECONDS = 300.0
NONPOSITIVE_JACOBIAN_RETRY_SCALE = 0.75

FORMAL_PARAMETER_RANGES = {
    "thickness_mm": THICKNESS_RANGE_MM,
    "flange_length_mm": FLANGE_LENGTH_RANGE_MM,
    "arm_width_mm": ARM_WIDTH_RANGE_MM,
    "hole_diameter_mm": HOLE_DIAMETER_RANGE_MM,
    "fillet_radius_mm": FILLET_RADIUS_RANGE_MM,
    "poissons_ratio": POISSONS_RATIO_RANGE,
}

SAMPLE_PLAN_COLUMNS = (
    "design_id",
    *FORMAL_PARAMETER_RANGES,
    "reference_load_force_n",
    "reference_youngs_modulus_mpa",
    "reference_yield_strength_mpa",
    "reference_density_kg_m3",
    "material_key",
)

MODEL_DATASET_COLUMNS = (
    "dataset_schema_version",
    "design_id",
    "source",
    "mesh_rule",
    "thickness_mm",
    "flange_length_mm",
    "arm_width_mm",
    "hole_diameter_mm",
    "fillet_radius_mm",
    "poissons_ratio",
    "reference_load_force_n",
    "reference_youngs_modulus_mpa",
    "reference_max_stress_mpa",
    "reference_max_disp_mm",
    "volume_mm3",
    "reference_mass_kg",
)

FAILURE_ATTEMPT_COLUMNS = (
    "attempt_number",
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
    "poissons_ratio",
)

QUALITY_CHECK_COLUMNS = (
    "check_name",
    "passed",
    "expected",
    "observed",
)


@dataclass(frozen=True)
class FormalQualityCheck:
    """One acceptance check for the complete formal dataset."""

    check_name: str
    passed: bool
    expected: str
    observed: str


@dataclass(frozen=True)
class FormalDatasetSummary:
    """Paths and completion status for one resumable batch invocation."""

    planned_cases: int
    completed_cases: int
    unresolved_failures: int
    skipped_existing_cases: int
    attempted_this_run: int
    all_quality_checks_passed: bool
    output_directory: Path


def refined_retry_mesh_settings(case: BracketCaseInput) -> MeshSettings:
    """Return the one-step finer mesh used after an inverted tet10."""

    primary = production_mesh_settings(case)
    return MeshSettings(
        minimum_size_mm=(
            primary.minimum_size_mm * NONPOSITIVE_JACOBIAN_RETRY_SCALE
        ),
        maximum_size_mm=(
            primary.maximum_size_mm * NONPOSITIVE_JACOBIAN_RETRY_SCALE
        ),
        element_order=primary.element_order,
    )


def _has_nonpositive_jacobian_log(case_directory: Path) -> bool:
    """Check whether CalculiX rejected an inverted volume element."""

    for log_path in case_directory.glob("*_ccx.log"):
        try:
            log_text = log_path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            continue
        if "nonpositive jacobian" in log_text.lower():
            return True
    return False


def solve_formal_case(
    case: BracketCaseInput,
    case_directory: Path,
    timeout_seconds: float,
) -> SolverResult:
    """Solve once, then refine once if a tet10 has a bad Jacobian."""

    try:
        return solve_case(
            case,
            case_directory,
            mesh_settings=production_mesh_settings(case),
            timeout_seconds=timeout_seconds,
        )
    except SolverExecutionError:
        if not _has_nonpositive_jacobian_log(case_directory):
            raise

    retry_settings = refined_retry_mesh_settings(case)
    print(
        "  Retrying after a nonpositive Jacobian with refined mesh "
        f"{retry_settings.mesh_rule}...",
        flush=True,
    )
    return solve_case(
        case,
        case_directory,
        mesh_settings=retry_settings,
        timeout_seconds=timeout_seconds,
    )


def _write_rows_atomic(
    path: Path,
    columns: Sequence[str],
    rows: Iterable[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _deduplicate_rows(
    rows: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    by_id: dict[str, dict[str, object]] = {}
    for row in rows:
        design_id = str(row["design_id"])
        if design_id in by_id:
            raise ValueError(f"Duplicate design_id in saved data: {design_id}")
        by_id[design_id] = dict(row)
    return list(by_id.values())


def make_formal_cases(
    sample_count: int = FORMAL_SAMPLE_COUNT,
    seed: int = FORMAL_RANDOM_SEED,
) -> tuple[BracketCaseInput, ...]:
    """Create six-dimensional LHS cases for geometry and Poisson ratio."""

    baseline = make_baseline_case()
    parameter_names = tuple(FORMAL_PARAMETER_RANGES)
    unit_samples = latin_hypercube_unit_samples(
        sample_count,
        len(parameter_names),
        seed,
    )

    cases: list[BracketCaseInput] = []
    for sample_index, unit_row in enumerate(unit_samples):
        sampled_values: dict[str, float] = {}
        for parameter_name, unit_value in zip(parameter_names, unit_row):
            low, high = FORMAL_PARAMETER_RANGES[parameter_name]
            sampled_values[parameter_name] = (
                low + float(unit_value) * (high - low)
            )

        material = MaterialProperties(
            material_key=FORMAL_MATERIAL_KEY,
            youngs_modulus_mpa=(
                baseline.material.youngs_modulus_mpa
            ),
            poissons_ratio=sampled_values.pop("poissons_ratio"),
            yield_strength_mpa=(
                baseline.material.yield_strength_mpa
            ),
            density_kg_m3=baseline.material.density_kg_m3,
        )
        copied = baseline.model_copy(
            update={
                "design_id": f"formal_{seed}_{sample_index:03d}",
                "material": material,
                **sampled_values,
            }
        )
        cases.append(
            BracketCaseInput.model_validate(copied.model_dump())
        )

    return tuple(cases)


def case_to_sample_plan(case: BracketCaseInput) -> dict[str, object]:
    """Record all formal model inputs and reference conditions."""

    return {
        "design_id": case.design_id,
        "thickness_mm": case.thickness_mm,
        "flange_length_mm": case.flange_length_mm,
        "arm_width_mm": case.arm_width_mm,
        "hole_diameter_mm": case.hole_diameter_mm,
        "fillet_radius_mm": case.fillet_radius_mm,
        "poissons_ratio": case.material.poissons_ratio,
        "reference_load_force_n": case.load_force_n,
        "reference_youngs_modulus_mpa": (
            case.material.youngs_modulus_mpa
        ),
        "reference_yield_strength_mpa": (
            case.material.yield_strength_mpa
        ),
        "reference_density_kg_m3": case.material.density_kg_m3,
        "material_key": case.material.material_key,
    }


def result_to_model_row(
    case: BracketCaseInput,
    run: Run,
    diagnostics: dict[str, object],
) -> dict[str, object]:
    """Create the explicit six-input model dataset row."""

    return {
        "dataset_schema_version": MODEL_DATASET_SCHEMA_VERSION,
        "design_id": case.design_id,
        "source": run.source,
        "mesh_rule": run.mesh_rule,
        "thickness_mm": case.thickness_mm,
        "flange_length_mm": case.flange_length_mm,
        "arm_width_mm": case.arm_width_mm,
        "hole_diameter_mm": case.hole_diameter_mm,
        "fillet_radius_mm": case.fillet_radius_mm,
        "poissons_ratio": case.material.poissons_ratio,
        "reference_load_force_n": case.load_force_n,
        "reference_youngs_modulus_mpa": (
            case.material.youngs_modulus_mpa
        ),
        "reference_max_stress_mpa": run.max_stress_mpa,
        "reference_max_disp_mm": run.max_disp_mm,
        "volume_mm3": diagnostics["volume_mm3"],
        "reference_mass_kg": run.mass_kg,
    }


def _validate_existing_plan(
    path: Path,
    expected_rows: Sequence[dict[str, object]],
) -> None:
    existing = _read_rows(path)
    if not existing:
        return
    if len(existing) != len(expected_rows):
        raise ValueError(
            "Existing sample_plan.csv has a different number of cases. "
            "Use a new output directory for a different plan."
        )

    numeric_columns = set(SAMPLE_PLAN_COLUMNS) - {
        "design_id",
        "material_key",
    }
    for saved, expected in zip(existing, expected_rows):
        if saved["design_id"] != expected["design_id"]:
            raise ValueError(
                "Existing sample_plan.csv does not match this seed/order."
            )
        if saved["material_key"] != expected["material_key"]:
            raise ValueError(
                "Existing sample_plan.csv uses a different material key."
            )
        for column in numeric_columns:
            if not math.isclose(
                float(saved[column]),
                float(expected[column]),
                rel_tol=1.0e-12,
                abs_tol=1.0e-12,
            ):
                raise ValueError(
                    "Existing sample_plan.csv values do not match the "
                    f"requested plan at {saved['design_id']}:{column}."
                )


def evaluate_formal_quality(
    cases: Sequence[BracketCaseInput],
    runs: Sequence[Run],
    diagnostics: Sequence[dict[str, object]],
    model_rows: Sequence[dict[str, object]],
    unresolved_failure_count: int,
) -> list[FormalQualityCheck]:
    """Evaluate completeness, numerical QA, and LHS coverage."""

    expected_count = len(cases)
    checks: list[FormalQualityCheck] = []

    checks.append(
        FormalQualityCheck(
            "all_cases_solved",
            len(runs) == expected_count and unresolved_failure_count == 0,
            f"{expected_count} successful rows and zero unresolved failures",
            f"successes={len(runs)}; failures={unresolved_failure_count}",
        )
    )
    checks.append(
        FormalQualityCheck(
            "aligned_output_tables",
            (
                len(runs) == len(diagnostics) == len(model_rows)
                and {
                    run.design_id for run in runs
                } == {
                    str(row["design_id"]) for row in diagnostics
                } == {
                    str(row["design_id"]) for row in model_rows
                }
            ),
            "runs, diagnostics, and model rows have identical design IDs",
            (
                f"runs={len(runs)}; diagnostics={len(diagnostics)}; "
                f"model_rows={len(model_rows)}"
            ),
        )
    )
    checks.append(
        FormalQualityCheck(
            "positive_model_outputs",
            len(model_rows) == expected_count
            and all(
                float(row["reference_max_stress_mpa"]) > 0.0
                and float(row["reference_max_disp_mm"]) > 0.0
                and float(row["volume_mm3"]) > 0.0
                for row in model_rows
            ),
            "all reference stress, displacement, and volume values are positive",
            f"checked_rows={len(model_rows)}",
        )
    )

    maximum_reaction_imbalance = max(
        (
            float(row["reaction_relative_imbalance"])
            for row in diagnostics
        ),
        default=float("inf"),
    )
    checks.append(
        FormalQualityCheck(
            "reaction_force_balance",
            len(diagnostics) == expected_count
            and maximum_reaction_imbalance <= 0.01,
            "every relative reaction imbalance is at most 0.01",
            f"maximum={maximum_reaction_imbalance:.9g}",
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
        FormalQualityCheck(
            "minimum_element_quality",
            len(diagnostics) == expected_count and minimum_quality > 0.1,
            "every minimum element quality is above 0.1",
            f"batch_minimum={minimum_quality:.9g}",
        )
    )

    for parameter_name, (low, high) in FORMAL_PARAMETER_RANGES.items():
        values = [
            (
                case.material.poissons_ratio
                if parameter_name == "poissons_ratio"
                else getattr(case, parameter_name)
            )
            for case in cases
        ]
        strata = [
            min(
                int((value - low) / (high - low) * expected_count),
                expected_count - 1,
            )
            for value in values
        ]
        unique_strata = len(set(strata))
        checks.append(
            FormalQualityCheck(
                f"lhs_strata_{parameter_name}",
                unique_strata == expected_count,
                f"all {expected_count} strata used exactly once",
                f"unique_strata={unique_strata}",
            )
        )

    return checks


def _progress_payload(
    *,
    planned: int,
    completed: int,
    unresolved: int,
    attempted_this_run: int,
) -> dict[str, object]:
    return {
        "dataset_name": "l_bracket_v0_formal_200",
        "planned_cases": planned,
        "completed_cases": completed,
        "remaining_cases": planned - completed,
        "unresolved_failures": unresolved,
        "attempted_this_run": attempted_this_run,
        "resume_instruction": (
            "Run the same command and output directory again. "
            "Completed design IDs will be skipped."
        ),
    }


def cleanup_successful_case_files(case_directory: Path) -> tuple[Path, ...]:
    """Remove reproducible large solver files while retaining text logs."""

    removable_suffixes = {
        ".step",
        ".msh",
        ".inp",
        ".frd",
        ".dat",
        ".cvg",
        ".sta",
        ".12d",
        ".out",
    }
    removed: list[Path] = []
    if not case_directory.exists():
        return tuple()

    for path in case_directory.iterdir():
        if path.is_file() and path.suffix.lower() in removable_suffixes:
            path.unlink()
            removed.append(path)
    return tuple(removed)


def generate_formal_dataset(
    output_directory: str | Path,
    cases: Sequence[BracketCaseInput] | None = None,
    *,
    case_limit: int | None = None,
    timeout_seconds: float = DEFAULT_SOLVER_TIMEOUT_SECONDS,
    keep_case_files: bool = False,
) -> FormalDatasetSummary:
    """Generate or resume the formal dataset, checkpointing every case."""

    if timeout_seconds <= 0.0:
        raise ValueError("timeout_seconds must be positive.")
    if case_limit is not None and case_limit < 1:
        raise ValueError("case_limit must be at least 1.")

    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_output_dir = output_dir / "cases"
    selected_cases = tuple(cases) if cases is not None else make_formal_cases()
    planned_cases = len(selected_cases)
    cases_by_id = {case.design_id: case for case in selected_cases}
    if len(cases_by_id) != planned_cases:
        raise ValueError("Formal case design IDs must be unique.")

    sample_plan_path = output_dir / "sample_plan.csv"
    runs_path = output_dir / "runs.csv"
    diagnostics_path = output_dir / "run_diagnostics.csv"
    model_dataset_path = output_dir / "model_dataset.csv"
    failure_attempts_path = output_dir / "failure_attempts.csv"
    failures_path = output_dir / "failures.csv"
    quality_checks_path = output_dir / "quality_checks.csv"
    progress_path = output_dir / "progress.json"
    summary_path = output_dir / "summary.json"

    plan_rows = [case_to_sample_plan(case) for case in selected_cases]
    _validate_existing_plan(sample_plan_path, plan_rows)
    _write_rows_atomic(sample_plan_path, SAMPLE_PLAN_COLUMNS, plan_rows)

    existing_run_dicts = _deduplicate_rows(_read_rows(runs_path))
    existing_diagnostics = _deduplicate_rows(
        _read_rows(diagnostics_path)
    )
    existing_model_rows = _deduplicate_rows(
        _read_rows(model_dataset_path)
    )
    failure_attempts: list[dict[str, object]] = list(
        _read_rows(failure_attempts_path)
    )

    runs_by_id: dict[str, Run] = {
        str(row["design_id"]): Run.model_validate(row)
        for row in existing_run_dicts
    }
    diagnostics_by_id = {
        str(row["design_id"]): dict(row)
        for row in existing_diagnostics
    }
    model_rows_by_id = {
        str(row["design_id"]): dict(row)
        for row in existing_model_rows
    }

    unknown_ids = set(runs_by_id) - set(cases_by_id)
    if unknown_ids:
        raise ValueError(
            "Existing runs.csv contains design IDs outside this plan: "
            + ", ".join(sorted(unknown_ids))
        )
    if set(runs_by_id) != set(diagnostics_by_id) or set(runs_by_id) != set(
        model_rows_by_id
    ):
        raise ValueError(
            "Saved runs, diagnostics, and model_dataset checkpoints are "
            "not aligned. Restore the matching files before resuming."
        )

    completed_at_start = len(runs_by_id)
    remaining_cases = [
        case for case in selected_cases if case.design_id not in runs_by_id
    ]
    if case_limit is not None:
        remaining_cases = remaining_cases[:case_limit]

    prior_attempt_counts: dict[str, int] = {}
    for row in failure_attempts:
        design_id = str(row["design_id"])
        prior_attempt_counts[design_id] = max(
            prior_attempt_counts.get(design_id, 0),
            int(row["attempt_number"]),
        )

    attempted_this_run = 0
    latest_failure_by_id: dict[str, dict[str, object]] = {
        str(row["design_id"]): dict(row) for row in failure_attempts
    }

    for case in remaining_cases:
        attempted_this_run += 1
        index = len(runs_by_id) + 1
        print(
            f"[{index}/{planned_cases}] Solving {case.design_id} "
            f"(nu={case.material.poissons_ratio:.5f})...",
            flush=True,
        )
        try:
            result = solve_formal_case(
                case,
                case_output_dir / case.design_id,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:
            failure = case_to_failure(case, error)
            failure["poissons_ratio"] = case.material.poissons_ratio
            attempt_number = prior_attempt_counts.get(case.design_id, 0) + 1
            prior_attempt_counts[case.design_id] = attempt_number
            failure_row = {
                "attempt_number": attempt_number,
                **failure,
            }
            failure_attempts.append(failure_row)
            latest_failure_by_id[case.design_id] = failure_row
            print(
                f"  FAILED attempt {attempt_number}: "
                f"{type(error).__name__}: {error}",
                flush=True,
            )
        else:
            run = result_to_contract_run(case, result)
            diagnostic = result_to_diagnostics(case, result)
            model_row = result_to_model_row(case, run, diagnostic)
            runs_by_id[case.design_id] = run
            diagnostics_by_id[case.design_id] = diagnostic
            model_rows_by_id[case.design_id] = model_row
            latest_failure_by_id.pop(case.design_id, None)
            if not keep_case_files:
                try:
                    cleanup_successful_case_files(
                        case_output_dir / case.design_id
                    )
                except OSError as cleanup_error:
                    warnings.warn(
                        "Could not remove generated case files for "
                        f"{case.design_id}: {cleanup_error}",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            print(
                f"  OK: stress={result.max_stress_mpa:.3f} MPa, "
                f"disp={result.max_disp_mm:.6f} mm, "
                f"mesh={result.mesh_rule}",
                flush=True,
            )

        ordered_success_ids = [
            case_item.design_id
            for case_item in selected_cases
            if case_item.design_id in runs_by_id
        ]
        _write_rows_atomic(
            runs_path,
            RUN_COLUMNS,
            (
                runs_by_id[design_id].model_dump()
                for design_id in ordered_success_ids
            ),
        )
        _write_rows_atomic(
            diagnostics_path,
            DIAGNOSTIC_COLUMNS,
            (
                diagnostics_by_id[design_id]
                for design_id in ordered_success_ids
            ),
        )
        _write_rows_atomic(
            model_dataset_path,
            MODEL_DATASET_COLUMNS,
            (
                model_rows_by_id[design_id]
                for design_id in ordered_success_ids
            ),
        )
        _write_rows_atomic(
            failure_attempts_path,
            FAILURE_ATTEMPT_COLUMNS,
            failure_attempts,
        )
        unresolved_rows = [
            latest_failure_by_id[design_id]
            for design_id in sorted(latest_failure_by_id)
            if design_id not in runs_by_id
        ]
        _write_rows_atomic(
            failures_path,
            FAILURE_ATTEMPT_COLUMNS,
            unresolved_rows,
        )
        _write_json_atomic(
            progress_path,
            _progress_payload(
                planned=planned_cases,
                completed=len(runs_by_id),
                unresolved=len(unresolved_rows),
                attempted_this_run=attempted_this_run,
            ),
        )

    # Ensure empty checkpoint files still exist if no case was attempted.
    if not runs_path.exists():
        _write_rows_atomic(runs_path, RUN_COLUMNS, [])
        _write_rows_atomic(diagnostics_path, DIAGNOSTIC_COLUMNS, [])
        _write_rows_atomic(model_dataset_path, MODEL_DATASET_COLUMNS, [])
        _write_rows_atomic(
            failure_attempts_path,
            FAILURE_ATTEMPT_COLUMNS,
            failure_attempts,
        )
        _write_rows_atomic(failures_path, FAILURE_ATTEMPT_COLUMNS, [])

    ordered_success_ids = [
        case.design_id
        for case in selected_cases
        if case.design_id in runs_by_id
    ]
    ordered_runs = [runs_by_id[design_id] for design_id in ordered_success_ids]
    ordered_diagnostics = [
        diagnostics_by_id[design_id] for design_id in ordered_success_ids
    ]
    ordered_model_rows = [
        model_rows_by_id[design_id] for design_id in ordered_success_ids
    ]
    unresolved_count = len(
        [
            design_id
            for design_id in latest_failure_by_id
            if design_id not in runs_by_id
        ]
    )
    quality_checks = evaluate_formal_quality(
        selected_cases,
        ordered_runs,
        ordered_diagnostics,
        ordered_model_rows,
        unresolved_count,
    )
    _write_rows_atomic(
        quality_checks_path,
        QUALITY_CHECK_COLUMNS,
        (asdict(check) for check in quality_checks),
    )
    all_checks_passed = bool(quality_checks) and all(
        check.passed for check in quality_checks
    )

    summary_payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "model_dataset_schema_version": MODEL_DATASET_SCHEMA_VERSION,
        "dataset_name": "l_bracket_v0_formal_200",
        "dataset_stage": "formal_training_data",
        "planned_cases": planned_cases,
        "successful_cases": len(ordered_runs),
        "remaining_cases": planned_cases - len(ordered_runs),
        "unresolved_failures": unresolved_count,
        "random_seed": FORMAL_RANDOM_SEED,
        "model_inputs": list(FORMAL_PARAMETER_RANGES),
        "poissons_ratio_range": {
            "low": POISSONS_RATIO_RANGE[0],
            "high": POISSONS_RATIO_RANGE[1],
        },
        "reference_conditions": {
            "load_force_n": make_baseline_case().load_force_n,
            "load_direction": "-Y",
            "youngs_modulus_mpa": (
                make_baseline_case().material.youngs_modulus_mpa
            ),
            "yield_strength_mpa": (
                make_baseline_case().material.yield_strength_mpa
            ),
            "density_kg_m3": (
                make_baseline_case().material.density_kg_m3
            ),
        },
        "production_mesh_strategy": (
            "tet2; maximum_size_mm=min(2.0, thickness_mm/3); "
            "minimum_size_mm=maximum_size_mm/2"
        ),
        "successful_case_files_retained": keep_case_files,
        "successful_case_file_policy": (
            "keep all generated solver files"
            if keep_case_files
            else "keep logs; remove reproducible large solver files"
        ),
        "all_quality_checks_passed": all_checks_passed,
        "role1_dataset_complete": all_checks_passed,
        "contract_status": {
            "shared_contract_modified": False,
            "runs_csv_uses_current_shared_columns": True,
            "poissons_ratio_is_in_model_dataset_csv": True,
            "contract_changes_documented_in_final_word_handoff": True,
        },
        "artifacts": {
            "sample_plan_csv": str(sample_plan_path),
            "runs_csv": str(runs_path),
            "model_dataset_csv": str(model_dataset_path),
            "run_diagnostics_csv": str(diagnostics_path),
            "failure_attempts_csv": str(failure_attempts_path),
            "failures_csv": str(failures_path),
            "quality_checks_csv": str(quality_checks_path),
            "progress_json": str(progress_path),
        },
    }
    _write_json_atomic(summary_path, summary_payload)
    _write_json_atomic(
        progress_path,
        _progress_payload(
            planned=planned_cases,
            completed=len(ordered_runs),
            unresolved=unresolved_count,
            attempted_this_run=attempted_this_run,
        ),
    )

    print(f"Wrote: {sample_plan_path}")
    print(f"Wrote: {runs_path}")
    print(f"Wrote: {model_dataset_path}")
    print(f"Wrote: {diagnostics_path}")
    print(f"Wrote: {failure_attempts_path}")
    print(f"Wrote: {failures_path}")
    print(f"Wrote: {quality_checks_path}")
    print(f"Wrote: {progress_path}")
    print(f"Wrote: {summary_path}")
    print(
        f"Completed {len(ordered_runs)}/{planned_cases}; "
        f"unresolved failures={unresolved_count}; "
        f"all checks passed={all_checks_passed}."
    )

    return FormalDatasetSummary(
        planned_cases=planned_cases,
        completed_cases=len(ordered_runs),
        unresolved_failures=unresolved_count,
        skipped_existing_cases=completed_at_start,
        attempted_this_run=attempted_this_run,
        all_quality_checks_passed=all_checks_passed,
        output_directory=output_dir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate or resume the 200-case SimpleSim formal dataset."
        )
    )
    parser.add_argument(
        "--output",
        default="data/generated/formal_v0",
        help="Output/checkpoint directory.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional number of currently incomplete cases to run.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_SOLVER_TIMEOUT_SECONDS,
        help="CalculiX timeout for each case (default: 300).",
    )
    parser.add_argument(
        "--keep-case-files",
        action="store_true",
        help=(
            "Retain STEP, mesh, deck, FRD, and DAT files for successful "
            "cases. By default only compact logs and CSV/JSON outputs remain."
        ),
    )
    arguments = parser.parse_args()
    generate_formal_dataset(
        arguments.output,
        case_limit=arguments.limit,
        timeout_seconds=arguments.timeout_seconds,
        keep_case_files=arguments.keep_case_files,
    )


if __name__ == "__main__":
    main()
