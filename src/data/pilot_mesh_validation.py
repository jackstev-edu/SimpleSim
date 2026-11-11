"""Verify the production mesh on representative LHS pilot designs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from .bracket_spec import BracketCaseInput, make_baseline_case
from .mesh import MeshSettings
from .solver import SolverResult, solve_case
from .validation import percent_difference
from .ovat_pipeline import production_mesh_settings


STRESS_CHANGE_LIMIT_PCT = 5.0
DISPLACEMENT_CHANGE_LIMIT_PCT = 5.0
REACTION_IMBALANCE_LIMIT = 0.01
MINIMUM_ELEMENT_QUALITY_LIMIT = 0.1
DEFAULT_SOLVER_TIMEOUT_SECONDS = 300.0

GEOMETRY_COLUMNS = (
    "thickness_mm",
    "flange_length_mm",
    "arm_width_mm",
    "hole_diameter_mm",
    "fillet_radius_mm",
)


def verification_mesh_settings(case: BracketCaseInput) -> MeshSettings:
    """Return a mesh one level finer than the adaptive production mesh."""

    production = production_mesh_settings(case)
    refinement_factor = 2.0 / 3.0
    return MeshSettings(
        minimum_size_mm=(
            production.minimum_size_mm * refinement_factor
        ),
        maximum_size_mm=(
            production.maximum_size_mm * refinement_factor
        ),
        element_order=production.element_order,
    )


@dataclass(frozen=True)
class SelectedPilotCase:
    """One pilot design selected for independent mesh verification."""

    reason: str
    run_row: dict[str, str]
    diagnostic_row: dict[str, str]


@dataclass(frozen=True)
class MeshVerificationRow:
    """Production-versus-extra-fine comparison for one pilot design."""

    design_id: str
    selection_reason: str
    production_mesh_rule: str
    verification_mesh_rule: str
    production_max_stress_mpa: float
    verification_max_stress_mpa: float
    stress_change_pct: float
    production_max_disp_mm: float
    verification_max_disp_mm: float
    displacement_change_pct: float
    production_minimum_element_quality: float
    verification_minimum_element_quality: float
    verification_reaction_relative_imbalance: float
    stress_location_shift_mm: float
    stress_change_pass: bool
    displacement_change_pass: bool
    element_quality_pass: bool
    reaction_balance_pass: bool
    all_checks_pass: bool


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _float(row: dict[str, str], name: str) -> float:
    try:
        return float(row[name])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"Column '{name}' is missing or not numeric."
        ) from error


def select_validation_cases(
    run_rows: Sequence[dict[str, str]],
    diagnostic_rows: Sequence[dict[str, str]],
) -> tuple[SelectedPilotCase, ...]:
    """Select high, low, hole-hotspot, and lowest-quality pilot cases."""

    if not run_rows:
        raise ValueError("runs.csv contains no data rows.")
    if len(run_rows) != len(diagnostic_rows):
        raise ValueError("Run and diagnostic row counts do not match.")

    diagnostics_by_id = {
        row["design_id"]: row for row in diagnostic_rows
    }
    if len(diagnostics_by_id) != len(diagnostic_rows):
        raise ValueError("Diagnostic design IDs must be unique.")

    for row in run_rows:
        if row["design_id"] not in diagnostics_by_id:
            raise ValueError(
                f"Missing diagnostic row for {row['design_id']}."
            )

    choices: list[tuple[str, dict[str, str]]] = [
        (
            "highest_stress",
            max(run_rows, key=lambda row: _float(row, "max_stress_mpa")),
        ),
        (
            "lowest_stress",
            min(run_rows, key=lambda row: _float(row, "max_stress_mpa")),
        ),
    ]

    # The hole center is at X = 60 mm. The largest stress-X coordinate
    # identifies a case whose maximum is most likely controlled by the hole
    # rather than by the inside fillet.
    hole_diagnostic = max(
        diagnostic_rows,
        key=lambda row: _float(row, "max_stress_x_mm"),
    )
    hole_run = next(
        row
        for row in run_rows
        if row["design_id"] == hole_diagnostic["design_id"]
    )
    choices.append(("hole_hotspot", hole_run))

    quality_diagnostic = min(
        diagnostic_rows,
        key=lambda row: _float(row, "minimum_element_quality"),
    )
    quality_run = next(
        row
        for row in run_rows
        if row["design_id"] == quality_diagnostic["design_id"]
    )
    choices.append(("lowest_production_mesh_quality", quality_run))

    selected: list[SelectedPilotCase] = []
    seen_ids: set[str] = set()
    for reason, run_row in choices:
        design_id = run_row["design_id"]
        if design_id in seen_ids:
            continue
        seen_ids.add(design_id)
        selected.append(
            SelectedPilotCase(
                reason=reason,
                run_row=run_row,
                diagnostic_row=diagnostics_by_id[design_id],
            )
        )

    return tuple(selected)


def case_from_run_row(row: dict[str, str]) -> BracketCaseInput:
    """Recreate a validated solver input from one contract Run row."""

    baseline = make_baseline_case()
    updates: dict[str, object] = {
        "design_id": row["design_id"],
        "load_force_n": _float(row, "load_force_n"),
    }
    updates.update({name: _float(row, name) for name in GEOMETRY_COLUMNS})
    copied = baseline.model_copy(update=updates)
    return BracketCaseInput.model_validate(copied.model_dump())


def compare_mesh_result(
    selected: SelectedPilotCase,
    verification: SolverResult,
) -> MeshVerificationRow:
    """Compare one extra-fine solve with its saved production result."""

    run = selected.run_row
    diagnostic = selected.diagnostic_row
    production_stress = _float(run, "max_stress_mpa")
    production_displacement = _float(run, "max_disp_mm")
    stress_change = percent_difference(
        verification.max_stress_mpa,
        production_stress,
    )
    displacement_change = percent_difference(
        verification.max_disp_mm,
        production_displacement,
    )

    old_location = (
        _float(diagnostic, "max_stress_x_mm"),
        _float(diagnostic, "max_stress_y_mm"),
        _float(diagnostic, "max_stress_z_mm"),
    )
    location_shift = math.dist(
        old_location,
        verification.max_stress_location_mm,
    )

    stress_pass = stress_change <= STRESS_CHANGE_LIMIT_PCT
    displacement_pass = (
        displacement_change <= DISPLACEMENT_CHANGE_LIMIT_PCT
    )
    quality_pass = (
        verification.minimum_element_quality
        > MINIMUM_ELEMENT_QUALITY_LIMIT
    )
    reaction_pass = (
        verification.reaction_relative_imbalance
        <= REACTION_IMBALANCE_LIMIT
    )

    return MeshVerificationRow(
        design_id=run["design_id"],
        selection_reason=selected.reason,
        production_mesh_rule=run["mesh_rule"],
        verification_mesh_rule=verification.mesh_rule,
        production_max_stress_mpa=production_stress,
        verification_max_stress_mpa=verification.max_stress_mpa,
        stress_change_pct=stress_change,
        production_max_disp_mm=production_displacement,
        verification_max_disp_mm=verification.max_disp_mm,
        displacement_change_pct=displacement_change,
        production_minimum_element_quality=_float(
            diagnostic,
            "minimum_element_quality",
        ),
        verification_minimum_element_quality=(
            verification.minimum_element_quality
        ),
        verification_reaction_relative_imbalance=(
            verification.reaction_relative_imbalance
        ),
        stress_location_shift_mm=location_shift,
        stress_change_pass=stress_pass,
        displacement_change_pass=displacement_pass,
        element_quality_pass=quality_pass,
        reaction_balance_pass=reaction_pass,
        all_checks_pass=(
            stress_pass
            and displacement_pass
            and quality_pass
            and reaction_pass
        ),
    )


def run_pilot_mesh_validation(
    pilot_directory: str | Path,
    output_directory: str | Path | None = None,
    timeout_seconds: float = DEFAULT_SOLVER_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Run extra-fine solves for representative completed pilot cases."""

    pilot_dir = Path(pilot_directory)
    output_dir = (
        Path(output_directory)
        if output_directory is not None
        else pilot_dir / "mesh_validation"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    if timeout_seconds <= 0.0:
        raise ValueError("timeout_seconds must be positive.")

    run_rows = _read_csv(pilot_dir / "runs.csv")
    diagnostic_rows = _read_csv(pilot_dir / "run_diagnostics.csv")
    selected_cases = select_validation_cases(run_rows, diagnostic_rows)

    result_rows: list[MeshVerificationRow] = []
    for index, selected in enumerate(selected_cases, start=1):
        case = case_from_run_row(selected.run_row)
        verification_settings = verification_mesh_settings(case)
        print(
            f"[{index}/{len(selected_cases)}] Verifying {case.design_id} "
            f"({selected.reason})...",
            flush=True,
        )
        result = solve_case(
            case,
            output_dir / "cases" / case.design_id,
            mesh_settings=verification_settings,
            timeout_seconds=timeout_seconds,
        )
        comparison = compare_mesh_result(selected, result)
        result_rows.append(comparison)
        print(
            f"  stress change={comparison.stress_change_pct:.3f}%, "
            f"displacement change="
            f"{comparison.displacement_change_pct:.3f}%, "
            f"pass={comparison.all_checks_pass}",
            flush=True,
        )

    csv_path = output_dir / "pilot_mesh_validation.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(asdict(result_rows[0])),
        )
        writer.writeheader()
        writer.writerows(asdict(row) for row in result_rows)

    all_pass = all(row.all_checks_pass for row in result_rows)
    report = {
        "pilot_directory": str(pilot_dir),
        "verification_mesh_strategy": (
            "two-thirds of the adaptive production element sizes"
        ),
        "solver_timeout_seconds": timeout_seconds,
        "selected_case_count": len(result_rows),
        "acceptance_limits": {
            "maximum_stress_change_pct": STRESS_CHANGE_LIMIT_PCT,
            "maximum_displacement_change_pct": (
                DISPLACEMENT_CHANGE_LIMIT_PCT
            ),
            "maximum_reaction_imbalance": (
                REACTION_IMBALANCE_LIMIT
            ),
            "minimum_element_quality": (
                MINIMUM_ELEMENT_QUALITY_LIMIT
            ),
        },
        "checks": {
            "all_selected_cases_pass": all_pass,
        },
        "results": [asdict(row) for row in result_rows],
    }
    json_path = output_dir / "pilot_mesh_validation.json"
    json_path.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote: {csv_path}")
    print(f"Wrote: {json_path}")
    print(f"All selected cases pass: {all_pass}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the production mesh on representative LHS pilot cases."
        )
    )
    parser.add_argument(
        "--pilot",
        default="data/generated/lhs_pilot_v0",
        help="Directory containing pilot runs.csv and diagnostics.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output directory (default: PILOT/mesh_validation).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_SOLVER_TIMEOUT_SECONDS,
        help="CalculiX timeout for each extra-fine case (default: 300).",
    )
    arguments = parser.parse_args()
    run_pilot_mesh_validation(
        arguments.pilot,
        arguments.output,
        timeout_seconds=arguments.timeout_seconds,
    )


if __name__ == "__main__":
    main()
