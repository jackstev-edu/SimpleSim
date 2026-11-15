"""Regenerate contracts/examples/ from the fakes. Run after any contract change.

    python -m contracts.make_examples
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .fakes import FakePredictor, beam_formula, run_pipeline
from .models import RUN_COLUMNS, Constraint, Load, Objective, ParameterRange, Run, StudySpec

HERE = Path(__file__).parent / "examples"

# The MVP's small steel bracket: baseline values and default ranges in mm.
BASELINE = {"thickness_mm": 5.0, "flange_length_mm": 90.0, "arm_width_mm": 25.0, "hole_diameter_mm": 8.5, "fillet_radius_mm": 4.0}
RANGES = {"thickness_mm": (2.5, 10.0), "flange_length_mm": (72.0, 108.0), "arm_width_mm": (18.75, 31.25), "hole_diameter_mm": (5.95, 11.05), "fillet_radius_mm": (1.0, 12.5)}


def example_spec() -> StudySpec:
    return StudySpec(
        objective=Objective(metric="mass_kg", direction="minimize"),
        constraints=[Constraint(metric="safety_factor", op=">=", value=2.0), Constraint(metric="max_disp_mm", op="<=", value=0.3)],
        parameters=[ParameterRange(name=k, low_mm=lo, high_mm=hi) for k, (lo, hi) in RANGES.items()],
        load=Load(force_n=500.0),
        assumptions=["Load acts straight down at the hole", "Mounting face fully fixed"],
    )


def example_runs(n: int = 40) -> list[Run]:
    """Formula-labelled rows in the runs.csv shape; real rows come from Role 1."""
    from .fakes import sample_designs

    rows = []
    for i, design in enumerate(sample_designs(example_spec(), n, seed=1)):
        s, d, m = beam_formula(design, 500.0, "structural_steel")
        rows.append(Run(design_id=f"ex-{i:04d}", source="fake_formula", mesh_rule="none (formula)", material_key="structural_steel", load_force_n=500.0, load_angle_deg=-90.0, **design, max_stress_mpa=s, max_disp_mm=d, mass_kg=m))
    return rows


def main() -> None:
    HERE.mkdir(exist_ok=True)
    spec = example_spec()
    (HERE / "study_spec.json").write_text(spec.model_dump_json(indent=2) + "\n")
    with (HERE / "runs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RUN_COLUMNS)
        w.writeheader()
        for r in example_runs():
            w.writerow(r.model_dump())
    (HERE / "prediction.json").write_text(FakePredictor().predict(spec, n=200).model_dump_json(indent=2) + "\n")
    (HERE / "result.json").write_text(run_pipeline(spec, BASELINE).model_dump_json(indent=2) + "\n")
    print("wrote", sorted(p.name for p in HERE.iterdir()))


if __name__ == "__main__":
    main()
