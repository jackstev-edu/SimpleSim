"""Baseline checks and mesh-convergence study for L-bracket family v0."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .bracket_spec import BracketCaseInput, make_baseline_case
from .mesh import MeshSettings
from .solver import SolverResult, solve_case


@dataclass(frozen=True)
class MeshLevel:
    """One mesh size used in the convergence study."""

    name: str
    minimum_size_mm: float
    maximum_size_mm: float


@dataclass(frozen=True)
class HandCalculation:
    """Simple cantilever reference that intentionally ignores 3-D details."""

    stress_mpa: float
    displacement_mm: float
    factor_of_safety: float


DEFAULT_MESH_LEVELS = (
    MeshLevel("coarse", 2.0, 4.0),
    MeshLevel("medium", 1.5, 3.0),
    MeshLevel("fine", 1.0, 2.0),
    MeshLevel("extra_fine", 0.75, 1.5),
)


def percent_difference(value: float, reference: float) -> float:
    """Return the absolute percentage difference relative to reference."""

    if reference == 0.0:
        raise ValueError("reference must be nonzero")

    return abs(value - reference) / abs(reference) * 100.0


def cantilever_reference(case: BracketCaseInput) -> HandCalculation:
    """Estimate root stress and tip deflection with elementary beam theory.

    The bracket horizontal leg is treated as a rectangular cantilever. The
    hole, vertical-leg flexibility, internal fillet, and 3-D stress state are
    omitted, so this is a scale/trend check rather than an exact FEA target.
    """

    force_n = case.load_force_n
    length_mm = case.flange_length_mm
    width_mm = case.arm_width_mm
    thickness_mm = case.thickness_mm
    elastic_modulus_mpa = case.material.youngs_modulus_mpa

    second_moment_mm4 = width_mm * thickness_mm**3 / 12.0
    bending_moment_n_mm = force_n * length_mm
    stress_mpa = (
        bending_moment_n_mm
        * (thickness_mm / 2.0)
        / second_moment_mm4
    )
    displacement_mm = (
        force_n
        * length_mm**3
        / (3.0 * elastic_modulus_mpa * second_moment_mm4)
    )

    return HandCalculation(
        stress_mpa=stress_mpa,
        displacement_mm=displacement_mm,
        factor_of_safety=(
            case.material.yield_strength_mpa / stress_mpa
        ),
    )


def _result_row(
    level: MeshLevel,
    result: SolverResult,
    previous: SolverResult | None,
) -> dict[str, object]:
    return {
        "mesh_level": level.name,
        "minimum_size_mm": level.minimum_size_mm,
        "maximum_size_mm": level.maximum_size_mm,
        "mesh_rule": result.mesh_rule,
        "node_count": result.node_count,
        "volume_element_count": result.volume_element_count,
        "minimum_element_quality": result.minimum_element_quality,
        "mean_element_quality": result.mean_element_quality,
        "max_stress_mpa": result.max_stress_mpa,
        "max_stress_node_id": result.max_stress_node_id,
        "max_stress_x_mm": result.max_stress_location_mm[0],
        "max_stress_y_mm": result.max_stress_location_mm[1],
        "max_stress_z_mm": result.max_stress_location_mm[2],
        "max_disp_mm": result.max_disp_mm,
        "max_disp_node_id": result.max_disp_node_id,
        "max_disp_x_mm": result.max_disp_location_mm[0],
        "max_disp_y_mm": result.max_disp_location_mm[1],
        "max_disp_z_mm": result.max_disp_location_mm[2],
        "factor_of_safety": result.factor_of_safety,
        "reaction_x_n": result.reaction_force_n[0],
        "reaction_y_n": result.reaction_force_n[1],
        "reaction_z_n": result.reaction_force_n[2],
        "reaction_relative_imbalance": (
            result.reaction_relative_imbalance
        ),
        "stress_change_from_previous_pct": (
            None
            if previous is None
            else percent_difference(
                result.max_stress_mpa,
                previous.max_stress_mpa,
            )
        ),
        "displacement_change_from_previous_pct": (
            None
            if previous is None
            else percent_difference(
                result.max_disp_mm,
                previous.max_disp_mm,
            )
        ),
    }


def run_baseline_validation(
    output_directory: str | Path,
    levels: tuple[MeshLevel, ...] = DEFAULT_MESH_LEVELS,
) -> dict[str, object]:
    """Run the baseline mesh study and save CSV/JSON evidence."""

    if len(levels) < 3:
        raise ValueError("At least three mesh levels are required.")

    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    case = make_baseline_case()
    hand = cantilever_reference(case)
    results: list[SolverResult] = []
    rows: list[dict[str, object]] = []

    for level in levels:
        result = solve_case(
            case,
            output_dir / level.name,
            mesh_settings=MeshSettings(
                minimum_size_mm=level.minimum_size_mm,
                maximum_size_mm=level.maximum_size_mm,
            ),
        )
        rows.append(
            _result_row(
                level,
                result,
                results[-1] if results else None,
            )
        )
        results.append(result)

    csv_path = output_dir / "mesh_convergence.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    final_two_stress_change = percent_difference(
        results[-1].max_stress_mpa,
        results[-2].max_stress_mpa,
    )
    final_two_displacement_change = percent_difference(
        results[-1].max_disp_mm,
        results[-2].max_disp_mm,
    )

    reaction_balance_pass = all(
        result.reaction_relative_imbalance <= 0.01
        for result in results
    )
    # The coarse mesh is intentionally exploratory. Quality acceptance is
    # applied to the final two meshes that could be selected for production.
    accepted_quality_results = results[-2:]
    element_quality_pass = all(
        result.minimum_element_quality > 0.1
        for result in accepted_quality_results
    )
    mesh_convergence_pass = (
        final_two_stress_change <= 5.0
        and final_two_displacement_change <= 5.0
    )

    fine = results[-1]
    report: dict[str, object] = {
        "design_id": case.design_id,
        "source": fine.source,
        "hand_calculation": asdict(hand),
        "fine_mesh_comparison_to_hand_calculation": {
            "stress_percent_difference": percent_difference(
                fine.max_stress_mpa,
                hand.stress_mpa,
            ),
            "displacement_percent_difference": percent_difference(
                fine.max_disp_mm,
                hand.displacement_mm,
            ),
        },
        "final_two_mesh_levels": {
            "stress_percent_change": final_two_stress_change,
            "displacement_percent_change": (
                final_two_displacement_change
            ),
        },
        "acceptance_limits": {
            "maximum_reaction_imbalance": 0.01,
            "minimum_element_quality": 0.1,
            "maximum_final_mesh_change_pct": 5.0,
        },
        "checks": {
            "reaction_balance_pass": reaction_balance_pass,
            "element_quality_pass": element_quality_pass,
            "mesh_convergence_pass": mesh_convergence_pass,
            "all_required_checks_pass": (
                reaction_balance_pass
                and element_quality_pass
                and mesh_convergence_pass
            ),
        },
        "mesh_results": rows,
        "notes": [
            "The hand calculation is a scale/trend check, not an exact "
            "three-dimensional benchmark.",
            "The reported FEA stress is the maximum nodal von Mises stress.",
            "If stress does not converge, inspect its location before "
            "generating training data.",
            "The coarse mesh is exploratory; element-quality acceptance "
            "is applied to the final two mesh levels.",
        ],
    }

    json_path = output_dir / "baseline_validation.json"
    json_path.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(f"Wrote: {csv_path}")
    print(f"Wrote: {json_path}")
    print(
        "Final mesh stress change (%): "
        f"{final_two_stress_change:.3f}"
    )
    print(
        "Final mesh displacement change (%): "
        f"{final_two_displacement_change:.3f}"
    )
    print(
        "Reaction balance pass: "
        f"{reaction_balance_pass}"
    )
    print(f"Element quality pass: {element_quality_pass}")
    print(f"Mesh convergence pass: {mesh_convergence_pass}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the SimpleSim baseline L-bracket solver."
    )
    parser.add_argument(
        "--output",
        default="data/generated/validation",
        help="Directory for solver outputs and validation reports.",
    )
    arguments = parser.parse_args()
    run_baseline_validation(arguments.output)


if __name__ == "__main__":
    main()
