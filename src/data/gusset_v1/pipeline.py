"""Generate the resumable 2,048-case l_bracket_gusset_v1 dataset with a process pool.

    python -m src.data.gusset_v1.pipeline --output data/processed/formal_v1_l_bracket_gusset_v1 --workers 3 --limit 512
    python -m src.data.gusset_v1.pipeline --output shards/s3 --shard 3 --num-shards 20

Behaviour kept from v0 (formal_pipeline.py): the plan is frozen on first write and checked on
every resume, completed design IDs are skipped, every attempt is logged, a nonpositive Jacobian
gets one retry on a finer mesh, reaction balance and element quality feed the QA, and heavy
solver files are deleted after a success. New: scrambled Sobol plan, --workers, --shard, the
elapsed-time, platform and ccx_version diagnostics, and a failure-rate guard for unattended waves.
Every row is a real CalculiX solve; a failed case is logged, never filled in.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import re
import shutil
import sys
import time
import traceback
import warnings
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import ValidationError

from contracts import RUN_COLUMNS, SCHEMA_VERSION, Run

from ..formal_pipeline import QUALITY_CHECK_COLUMNS, FormalQualityCheck, cleanup_successful_case_files
from ..ovat_pipeline import DIAGNOSTIC_COLUMNS
from ..solver import SolverExecutionError
from .meshing import PRODUCTION_LEVEL, describe_rule, production_mesh_settings, retry_mesh_settings
from .plan import (
    PLAN_SAMPLE_COUNT,
    PLAN_SEED,
    SAMPLE_PLAN_COLUMNS,
    check_frozen_plan,
    make_plan,
    plan_row_to_case,
    write_plan,
)
from .spec import (
    DATASET_SCHEMA_VERSION,
    FAMILY_KEY,
    GEOMETRY_NAMES,
    LOAD_ANGLE_DEG,
    NEW_GEOMETRY_NAMES,
    SAMPLED_RANGES,
    GussetCaseInput,
)

DATASET_NAME = "l_bracket_gusset_v1_formal_2048"
DEFAULT_OUTPUT = "data/processed/formal_v1_l_bracket_gusset_v1"
DEFAULT_SOLVER_TIMEOUT_SECONDS = 600.0
FAILURE_RATE_LIMIT = 0.05  # stop submitting new cases when this fraction of attempts has failed
FAILURE_RATE_MIN_ATTEMPTS = 20
THREAD_VARIABLES = ("OMP_NUM_THREADS", "CCX_NPROC_EQUATION_SOLVER", "CCX_NPROC_STIFFNESS", "CCX_NPROC_RESULTS")
SOBOL_BALANCE_BINS = 16  # a 2**11 point Sobol net puts exactly 128 points in each of 16 bins per axis

# runs.csv: the shared Run columns first, then the three v1 variables the contract does not know yet.
RUNS_V1_COLUMNS = (*RUN_COLUMNS, *NEW_GEOMETRY_NAMES)
MODEL_DATASET_COLUMNS = (
    "dataset_schema_version", "design_id", "bracket_family", "source", "mesh_rule",
    *GEOMETRY_NAMES, "poissons_ratio", "reference_load_force_n", "reference_youngs_modulus_mpa",
    "reference_max_stress_mpa", "reference_max_disp_mm", "volume_mm3", "reference_mass_kg",
)
DIAGNOSTIC_V1_COLUMNS = (
    *DIAGNOSTIC_COLUMNS, "elapsed_geometry_s", "elapsed_mesh_s", "elapsed_solve_s", "elapsed_total_s",
    "platform", "ccx_version", "attempt_number", "mesh_level",
)
FAILURE_V1_COLUMNS = (
    "attempt_number", "schema_version", "design_id", "failure_stage", "error_type", "error_message",
    "material_key", "load_force_n", "load_angle_deg", *GEOMETRY_NAMES, "poissons_ratio", "elapsed_total_s", "platform",
)


@dataclass(frozen=True)
class WaveSummary:
    """What one invocation did, for the caller and the console."""

    planned_cases: int
    selected_cases: int
    completed_cases: int
    unresolved_failures: int
    attempted_this_run: int
    failed_this_run: int
    stopped_early: bool
    all_quality_checks_passed: bool
    output_directory: Path


# ------------------------------------------------------------------ small file helpers (v0 style)

def _write_rows_atomic(path: Path, columns: Sequence[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _by_id(rows: Sequence[dict[str, object]], what: str) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for row in rows:
        design_id = str(row["design_id"])
        if design_id in out:
            raise ValueError(f"Duplicate design_id in {what}: {design_id}")
        out[design_id] = dict(row)
    return out


def _ccx_version(log_path: Path | None) -> str:
    """CalculiX prints 'This is Version 2.23' at the top of its output; fall back to the launcher's number."""
    if log_path is not None and log_path.exists():
        match = re.search(r"This is Version\s+(\S+)", log_path.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1)
    try:
        import cad_ccx

        return cad_ccx.version()
    except Exception:  # noqa: BLE001
        return "unknown"


def default_workers() -> int:
    """Physical cores minus one, with a floor of one. Falls back to half the logical count."""
    physical = None
    try:
        import psutil  # optional; not in any requirements file

        physical = psutil.cpu_count(logical=False)
    except Exception:  # noqa: BLE001
        physical = None
    if physical is None:
        physical = max(1, (os.cpu_count() or 2) // 2)
    return max(1, physical - 1)


# ------------------------------------------------------------------ one case, in a worker process

def _single_thread() -> None:
    """Each CalculiX process gets one thread, so N workers use N cores and solves stay deterministic."""
    for name in THREAD_VARIABLES:
        os.environ[name] = "1"


def _has_nonpositive_jacobian_log(case_directory: Path) -> bool:
    for log_path in case_directory.glob("*_ccx.log"):
        try:
            if "nonpositive jacobian" in log_path.read_text(encoding="utf-8", errors="replace").lower():
                return True
        except OSError:
            continue
    return False


def solve_v1_case(case: GussetCaseInput, case_directory: Path, timeout_seconds: float):
    """Production mesh first, then one finer retry if CalculiX reports an inverted tet10 (v0 policy)."""
    from ..solver import solve_case
    from .geometry import L_BRACKET_GUSSET_V1

    try:
        return solve_case(case, case_directory, mesh_settings=production_mesh_settings(case),
                          timeout_seconds=timeout_seconds, family=L_BRACKET_GUSSET_V1), PRODUCTION_LEVEL
    except SolverExecutionError:
        if not _has_nonpositive_jacobian_log(case_directory):
            raise
    retry = retry_mesh_settings(case)
    return solve_case(case, case_directory, mesh_settings=retry, timeout_seconds=timeout_seconds,
                      family=L_BRACKET_GUSSET_V1), f"{PRODUCTION_LEVEL}_jacobian_retry"


def run_one_case(job: tuple[dict[str, str], str, float, int, bool]) -> dict[str, object]:
    """Worker entry point: validate, solve, convert. Returns plain dicts so the result pickles."""
    row, case_dir, timeout_seconds, attempt_number, keep_files = job
    case_directory = Path(case_dir)
    started = time.perf_counter()
    base = {"design_id": row["design_id"], "attempt_number": attempt_number}
    try:
        case = plan_row_to_case(row)
    except ValidationError as exc:
        return {**base, "ok": False, "failure": _failure_row(row, None, "geometry_validation", exc, attempt_number, started)}
    try:
        result, level = solve_v1_case(case, case_directory, timeout_seconds)
    except Exception as exc:  # noqa: BLE001  every failure is logged with its stage and message
        stage = "integrated_solve_pipeline"
        name = type(exc).__name__
        if "Geometry" in name:
            stage = "geometry"
        elif "Mesh" in name:
            stage = "mesh"
        elif "Solver" in name or "Timeout" in name:
            stage = "solve"
        return {**base, "ok": False, "failure": _failure_row(row, case, stage, exc, attempt_number, started)}
    elapsed_total = time.perf_counter() - started
    run = Run(
        design_id=case.design_id, source=result.source, mesh_rule=result.mesh_rule, material_key=case.material.material_key,
        load_force_n=case.load_force_n, load_angle_deg=LOAD_ANGLE_DEG, thickness_mm=case.thickness_mm,
        flange_length_mm=case.flange_length_mm, arm_width_mm=case.arm_width_mm, hole_diameter_mm=case.hole_diameter_mm,
        fillet_radius_mm=case.fillet_radius_mm, max_stress_mpa=result.max_stress_mpa, max_disp_mm=result.max_disp_mm,
        mass_kg=result.mass_kg,
    )
    run_row = {**run.model_dump(), **{name: getattr(case, name) for name in NEW_GEOMETRY_NAMES}}
    diagnostic = {
        "schema_version": SCHEMA_VERSION, "design_id": case.design_id,
        "youngs_modulus_mpa": case.material.youngs_modulus_mpa, "poissons_ratio": case.material.poissons_ratio,
        "yield_strength_mpa": case.material.yield_strength_mpa, "density_kg_m3": case.material.density_kg_m3,
        "factor_of_safety": result.factor_of_safety, "volume_mm3": result.volume_mm3,
        "reaction_x_n": result.reaction_force_n[0], "reaction_y_n": result.reaction_force_n[1], "reaction_z_n": result.reaction_force_n[2],
        "reaction_relative_imbalance": result.reaction_relative_imbalance, "node_count": result.node_count,
        "volume_element_count": result.volume_element_count, "minimum_element_quality": result.minimum_element_quality,
        "mean_element_quality": result.mean_element_quality, "max_stress_node_id": result.max_stress_node_id,
        "max_stress_x_mm": result.max_stress_location_mm[0], "max_stress_y_mm": result.max_stress_location_mm[1],
        "max_stress_z_mm": result.max_stress_location_mm[2], "max_disp_node_id": result.max_disp_node_id,
        "max_disp_x_mm": result.max_disp_location_mm[0], "max_disp_y_mm": result.max_disp_location_mm[1],
        "max_disp_z_mm": result.max_disp_location_mm[2],
        "elapsed_geometry_s": result.elapsed_geometry_s, "elapsed_mesh_s": result.elapsed_mesh_s,
        "elapsed_solve_s": result.elapsed_solve_s, "elapsed_total_s": elapsed_total,
        "platform": platform.platform(), "ccx_version": _ccx_version(result.log_path),
        "attempt_number": attempt_number, "mesh_level": level,
    }
    model_row = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION, "design_id": case.design_id, "bracket_family": FAMILY_KEY,
        "source": result.source, "mesh_rule": result.mesh_rule, **case.geometry_mm(),
        "poissons_ratio": case.material.poissons_ratio, "reference_load_force_n": case.load_force_n,
        "reference_youngs_modulus_mpa": case.material.youngs_modulus_mpa, "reference_max_stress_mpa": result.max_stress_mpa,
        "reference_max_disp_mm": result.max_disp_mm, "volume_mm3": result.volume_mm3, "reference_mass_kg": result.mass_kg,
    }
    if not keep_files:
        try:
            cleanup_successful_case_files(case_directory)
        except OSError as exc:
            warnings.warn(f"Could not remove case files for {case.design_id}: {exc}", RuntimeWarning, stacklevel=2)
    return {**base, "ok": True, "run": run_row, "diagnostic": diagnostic, "model_row": model_row}


def _failure_row(row: dict[str, str], case: GussetCaseInput | None, stage: str, error: Exception,
                 attempt_number: int, started: float) -> dict[str, object]:
    geometry = {name: (getattr(case, name) if case else float(row[name])) for name in GEOMETRY_NAMES}
    return {
        "attempt_number": attempt_number, "schema_version": SCHEMA_VERSION, "design_id": row["design_id"],
        "failure_stage": stage, "error_type": type(error).__name__, "error_message": str(error).replace("\n", " ")[:1000],
        "material_key": row["material_key"], "load_force_n": float(row["reference_load_force_n"]), "load_angle_deg": LOAD_ANGLE_DEG,
        **geometry, "poissons_ratio": float(row["poissons_ratio"]), "elapsed_total_s": time.perf_counter() - started,
        "platform": platform.platform(),
    }


# ------------------------------------------------------------------ QA and summaries

def evaluate_quality(plan: Sequence[dict[str, object]], runs: Sequence[dict[str, object]], diagnostics: Sequence[dict[str, object]],
                     model_rows: Sequence[dict[str, object]], unresolved_failures: int) -> list[FormalQualityCheck]:
    """Completeness, numerical QA and plan balance. Numerical checks hold after every wave; completeness only at the end."""
    expected = len(plan)
    checks = [
        FormalQualityCheck("all_cases_solved", len(runs) == expected and unresolved_failures == 0,
                           f"{expected} successful rows and zero unresolved failures", f"successes={len(runs)}; failures={unresolved_failures}"),
        FormalQualityCheck("aligned_output_tables",
                           len(runs) == len(diagnostics) == len(model_rows)
                           and {str(r["design_id"]) for r in runs} == {str(r["design_id"]) for r in diagnostics} == {str(r["design_id"]) for r in model_rows},
                           "runs, diagnostics and model rows have identical design IDs",
                           f"runs={len(runs)}; diagnostics={len(diagnostics)}; model_rows={len(model_rows)}"),
        FormalQualityCheck("positive_model_outputs",
                           all(float(r["reference_max_stress_mpa"]) > 0 and float(r["reference_max_disp_mm"]) > 0 and float(r["volume_mm3"]) > 0 for r in model_rows),
                           "all reference stress, displacement and volume values are positive", f"checked_rows={len(model_rows)}"),
    ]
    imbalance = max((float(r["reaction_relative_imbalance"]) for r in diagnostics), default=0.0)
    checks.append(FormalQualityCheck("reaction_force_balance", imbalance <= 0.01, "every relative reaction imbalance is at most 0.01", f"maximum={imbalance:.9g}"))
    quality = min((float(r["minimum_element_quality"]) for r in diagnostics), default=1.0)
    checks.append(FormalQualityCheck("minimum_element_quality", quality > 0.1, "every minimum element quality is above 0.1", f"batch_minimum={quality:.9g}"))
    in_range = all(SAMPLED_RANGES[n][0] - 1e-9 <= float(r[n]) <= SAMPLED_RANGES[n][1] + 1e-9 for r in model_rows for n in SAMPLED_RANGES)
    checks.append(FormalQualityCheck("inputs_within_family_ranges", in_range, "every model row stays inside the documented ranges", f"checked_rows={len(model_rows)}"))
    real = all(str(r["source"]).startswith("calculix") for r in runs)
    checks.append(FormalQualityCheck("every_row_from_calculix", real, "source starts with calculix on every row", f"checked_rows={len(runs)}"))
    bins = SOBOL_BALANCE_BINS
    if expected % bins == 0:
        per_bin = expected // bins
        for name, (low, high) in SAMPLED_RANGES.items():
            counts = [0] * bins
            for row in plan:
                counts[min(int((float(row[name]) - low) / (high - low) * bins), bins - 1)] += 1
            checks.append(FormalQualityCheck(f"sobol_balance_{name}", all(c == per_bin for c in counts),
                                             f"each of {bins} equal bins holds {per_bin} planned points", f"counts={min(counts)}..{max(counts)}"))
    return checks


def _output_ranges(runs: Sequence[dict[str, object]]) -> dict[str, dict[str, float]]:
    out = {}
    for name in ("max_stress_mpa", "max_disp_mm", "mass_kg"):
        values = [float(r[name]) for r in runs]
        if values:
            out[name] = {"min": min(values), "max": max(values), "mean": sum(values) / len(values)}
    return out


def _elapsed_stats(diagnostics: Sequence[dict[str, object]]) -> dict[str, float]:
    values = [float(r["elapsed_total_s"]) for r in diagnostics if r.get("elapsed_total_s") not in (None, "")]
    if not values:
        return {}
    return {"cases": len(values), "mean_s": sum(values) / len(values), "max_s": max(values), "total_s": sum(values)}


def write_outputs(output_dir: Path, plan: Sequence[dict[str, object]], runs_by_id: dict[str, dict[str, object]],
                  diagnostics_by_id: dict[str, dict[str, object]], model_rows_by_id: dict[str, dict[str, object]],
                  failure_attempts: Sequence[dict[str, object]], attempted_this_run: int, *, workers: int | None = None,
                  shard: tuple[int, int] | None = None) -> tuple[bool, int]:
    """Rewrite every table in plan order, then the QA, summary and progress files. Returns (all checks passed, unresolved)."""
    order = [str(row["design_id"]) for row in plan if str(row["design_id"]) in runs_by_id]
    runs = [runs_by_id[i] for i in order]
    diagnostics = [diagnostics_by_id[i] for i in order]
    model_rows = [model_rows_by_id[i] for i in order]
    latest_failure: dict[str, dict[str, object]] = {}
    for row in failure_attempts:
        latest_failure[str(row["design_id"])] = row
    unresolved = [latest_failure[i] for i in sorted(latest_failure) if i not in runs_by_id]
    _write_rows_atomic(output_dir / "runs.csv", RUNS_V1_COLUMNS, runs)
    _write_rows_atomic(output_dir / "run_diagnostics.csv", DIAGNOSTIC_V1_COLUMNS, diagnostics)
    _write_rows_atomic(output_dir / "model_dataset.csv", MODEL_DATASET_COLUMNS, model_rows)
    _write_rows_atomic(output_dir / "failure_attempts.csv", FAILURE_V1_COLUMNS, failure_attempts)
    _write_rows_atomic(output_dir / "failures.csv", FAILURE_V1_COLUMNS, unresolved)
    checks = evaluate_quality(plan, runs, diagnostics, model_rows, len(unresolved))
    _write_rows_atomic(output_dir / "quality_checks.csv", QUALITY_CHECK_COLUMNS, (asdict(c) for c in checks))
    numeric_passed = all(c.passed for c in checks if c.check_name != "all_cases_solved")
    all_passed = bool(checks) and all(c.passed for c in checks)
    stage_counts: dict[str, int] = {}
    for row in unresolved:
        stage_counts[str(row["failure_stage"])] = stage_counts.get(str(row["failure_stage"]), 0) + 1
    summary = {
        "schema_version": SCHEMA_VERSION, "model_dataset_schema_version": DATASET_SCHEMA_VERSION,
        "dataset_name": DATASET_NAME, "bracket_family": FAMILY_KEY, "dataset_stage": "formal_training_data",
        "planned_cases": len(plan), "successful_cases": len(runs), "remaining_cases": len(plan) - len(runs),
        "unresolved_failures": len(unresolved), "failure_attempts": len(failure_attempts), "unresolved_failures_by_stage": stage_counts,
        "sampling": {"method": "scipy.stats.qmc.Sobol, scrambled", "seed": PLAN_SEED, "count": len(plan), "design_id_format": f"v1_{PLAN_SEED}_<0000>"},
        "model_inputs": list(SAMPLED_RANGES), "ranges": {k: {"low": v[0], "high": v[1]} for k, v in SAMPLED_RANGES.items()},
        "reference_conditions": {"load_force_n": 500.0, "load_direction": "-Y", "youngs_modulus_mpa": 200000.0,
                                 "yield_strength_mpa": 250.0, "density_kg_m3": 7850.0},
        "production_mesh_strategy": describe_rule(), "mesh_level": PRODUCTION_LEVEL,
        "retry_policy": "one retry at 0.75 times the production sizes after a nonpositive Jacobian",
        "output_ranges": _output_ranges(runs), "elapsed": _elapsed_stats(diagnostics),
        "platform": platform.platform(), "ccx_version": next((str(r["ccx_version"]) for r in diagnostics), _ccx_version(None)),
        "workers": workers, "shard": None if shard is None else {"index": shard[0], "count": shard[1]},
        "successful_case_file_policy": "keep logs; remove reproducible large solver files",
        "all_quality_checks_passed": all_passed, "numerical_checks_passed": numeric_passed, "role1_dataset_complete": all_passed,
        "contract_status": {"shared_contract_modified": False,
                            "runs_csv_columns": "Run contract columns followed by vertical_height_mm, gusset_thickness_mm, gusset_length_mm",
                            "proposed_changes_documented_in": "docs/bracket_family_v1_l_bracket_gusset_v1.md"},
        "artifacts": {name: str(output_dir / f"{name}.csv") for name in ("sample_plan", "runs", "model_dataset", "run_diagnostics", "failure_attempts", "failures", "quality_checks")},
    }
    _write_json_atomic(output_dir / "summary.json", summary)
    _write_json_atomic(output_dir / "progress.json", {
        "dataset_name": DATASET_NAME, "planned_cases": len(plan), "completed_cases": len(runs), "remaining_cases": len(plan) - len(runs),
        "unresolved_failures": len(unresolved), "attempted_this_run": attempted_this_run,
        "resume_instruction": "Run the same command and output directory again. Completed design IDs are skipped.",
    })
    return all_passed, len(unresolved)


def load_checkpoint(output_dir: Path, plan_ids: set[str]):
    """Existing tables, checked for duplicates, alignment and membership in this plan."""
    runs_by_id = _by_id(_read_rows(output_dir / "runs.csv"), "runs.csv")
    diagnostics_by_id = _by_id(_read_rows(output_dir / "run_diagnostics.csv"), "run_diagnostics.csv")
    model_rows_by_id = _by_id(_read_rows(output_dir / "model_dataset.csv"), "model_dataset.csv")
    failure_attempts = [dict(r) for r in _read_rows(output_dir / "failure_attempts.csv")]
    unknown = set(runs_by_id) - plan_ids
    if unknown:
        raise ValueError("Existing runs.csv contains design IDs outside this plan: " + ", ".join(sorted(unknown)[:5]))
    if set(runs_by_id) != set(diagnostics_by_id) or set(runs_by_id) != set(model_rows_by_id):
        raise ValueError("Saved runs, diagnostics and model_dataset checkpoints are not aligned. Restore matching files before resuming.")
    return runs_by_id, diagnostics_by_id, model_rows_by_id, failure_attempts


# ------------------------------------------------------------------ the wave runner

def generate_dataset(output_directory: str | Path, *, limit: int | None = None, workers: int | None = None,
                     shard: int = 0, num_shards: int = 1, timeout_seconds: float = DEFAULT_SOLVER_TIMEOUT_SECONDS,
                     keep_case_files: bool = False, failure_rate_limit: float = FAILURE_RATE_LIMIT,
                     plan: Sequence[dict[str, object]] | None = None, solve=run_one_case,
                     case_directory: str | Path | None = None) -> WaveSummary:
    """Run (or resume) the plan rows selected by --limit and --shard, checkpointing after every case."""
    if num_shards < 1 or not 0 <= shard < num_shards:
        raise ValueError("Need 0 <= shard < num_shards.")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1.")
    worker_count = workers or default_workers()
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_root = Path(case_directory) if case_directory else output_dir / "cases"  # heavy files; keep them off synced folders

    full_plan = list(plan) if plan is not None else make_plan(PLAN_SAMPLE_COUNT, PLAN_SEED)
    plan_path = output_dir / "sample_plan.csv"
    check_frozen_plan(plan_path, full_plan)
    if not plan_path.exists():
        write_plan(plan_path, full_plan)  # frozen from here on
    plan_ids = {str(r["design_id"]) for r in full_plan}
    runs_by_id, diagnostics_by_id, model_rows_by_id, failure_attempts = load_checkpoint(output_dir, plan_ids)

    # --limit is a prefix of the frozen plan (waves), --shard an interleaved slice of it (machines).
    selected = [row for index, row in enumerate(full_plan[: limit or len(full_plan)]) if index % num_shards == shard]
    todo = [row for row in selected if str(row["design_id"]) not in runs_by_id]
    attempts: dict[str, int] = {}
    for row in failure_attempts:
        attempts[str(row["design_id"])] = max(attempts.get(str(row["design_id"]), 0), int(row["attempt_number"]))

    print(f"{DATASET_NAME}: {len(full_plan)} planned, {len(selected)} selected (limit={limit}, shard {shard + 1}/{num_shards}), "
          f"{len(runs_by_id)} done, {len(todo)} to run on {worker_count} workers; mesh {describe_rule()}", flush=True)

    attempted = failed = 0
    stopped_early = False
    started = time.perf_counter()
    pending: dict = {}
    queue = iter(todo)

    def submit_next(pool) -> bool:
        row = next(queue, None)
        if row is None:
            return False
        design_id = str(row["design_id"])
        attempt = attempts.get(design_id, 0) + 1
        attempts[design_id] = attempt
        future = pool.submit(solve, (dict(row), str(case_root / design_id), timeout_seconds, attempt, keep_case_files))
        pending[future] = design_id
        return True

    if todo:
        with ProcessPoolExecutor(max_workers=worker_count, initializer=_single_thread) as pool:
            for _ in range(worker_count):
                if not submit_next(pool):
                    break
            while pending:
                done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
                for future in done:
                    design_id = pending.pop(future)
                    attempted += 1
                    try:
                        outcome = future.result()
                    except Exception as exc:  # noqa: BLE001  a crashed worker is a logged failure, not a dead run
                        traceback.print_exc()
                        row = next(r for r in todo if str(r["design_id"]) == design_id)
                        outcome = {"ok": False, "failure": _failure_row(row, None, "worker", exc, attempts[design_id], time.perf_counter())}
                    if outcome["ok"]:
                        runs_by_id[design_id] = outcome["run"]
                        diagnostics_by_id[design_id] = outcome["diagnostic"]
                        model_rows_by_id[design_id] = outcome["model_row"]
                        d = outcome["diagnostic"]
                        print(f"[{len(runs_by_id)}/{len(full_plan)}] {design_id} OK stress={float(outcome['run']['max_stress_mpa']):.2f} MPa "
                              f"disp={float(outcome['run']['max_disp_mm']):.5f} mm mesh={outcome['run']['mesh_rule']} {float(d['elapsed_total_s']):.1f}s", flush=True)
                    else:
                        failed += 1
                        failure_attempts.append(outcome["failure"])
                        print(f"[{len(runs_by_id)}/{len(full_plan)}] {design_id} FAILED attempt {outcome['failure']['attempt_number']} "
                              f"({outcome['failure']['failure_stage']}): {outcome['failure']['error_type']}: {str(outcome['failure']['error_message'])[:160]}", flush=True)
                    write_outputs(output_dir, full_plan, runs_by_id, diagnostics_by_id, model_rows_by_id, failure_attempts, attempted,
                                  workers=worker_count, shard=(shard, num_shards))
                    rate = failed / attempted
                    if attempted >= FAILURE_RATE_MIN_ATTEMPTS and rate > failure_rate_limit and not stopped_early:
                        stopped_early = True
                        print(f"Failure rate {rate:.1%} is above {failure_rate_limit:.0%} after {attempted} attempts: no new cases will start.", flush=True)
                    if not stopped_early:
                        submit_next(pool)

    all_passed, unresolved = write_outputs(output_dir, full_plan, runs_by_id, diagnostics_by_id, model_rows_by_id, failure_attempts, attempted,
                                           workers=worker_count, shard=(shard, num_shards))
    elapsed = time.perf_counter() - started
    print(f"Wave done in {elapsed / 60:.1f} min: attempted {attempted}, failed {failed}, completed {len(runs_by_id)}/{len(full_plan)}, "
          f"unresolved failures {unresolved}, all checks passed {all_passed}.", flush=True)
    return WaveSummary(len(full_plan), len(selected), len(runs_by_id), unresolved, attempted, failed, stopped_early, all_passed, output_dir)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or resume the 2,048-case gusseted bracket dataset.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="output and checkpoint folder")
    parser.add_argument("--limit", type=int, default=None, help="run only the first K plan rows (waves: 512, 1024, 1536, 2048)")
    parser.add_argument("--workers", type=int, default=None, help="parallel cases (default: physical cores minus one)")
    parser.add_argument("--shard", type=int, default=0, help="this machine's slice index, 0-based")
    parser.add_argument("--num-shards", type=int, default=1, help="how many slices the plan is split into")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_SOLVER_TIMEOUT_SECONDS)
    parser.add_argument("--keep-case-files", action="store_true", help="retain STEP, mesh, deck, FRD and DAT files")
    parser.add_argument("--failure-rate-limit", type=float, default=FAILURE_RATE_LIMIT, help="stop starting cases above this failed fraction")
    parser.add_argument("--case-dir", default=None, help="folder for per-case solver files (default: <output>/cases)")
    args = parser.parse_args(argv)
    summary = generate_dataset(args.output, limit=args.limit, workers=args.workers, shard=args.shard, num_shards=args.num_shards,
                               timeout_seconds=args.timeout_seconds, keep_case_files=args.keep_case_files,
                               failure_rate_limit=args.failure_rate_limit, case_directory=args.case_dir)
    if summary.stopped_early:
        return 3
    return 0 if summary.failed_this_run == 0 or summary.failed_this_run / max(summary.attempted_this_run, 1) <= args.failure_rate_limit else 3


if __name__ == "__main__":
    sys.exit(main())
