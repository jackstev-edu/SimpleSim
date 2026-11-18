"""The frozen Sobol sample plan for l_bracket_gusset_v1.

A scrambled Sobol sequence (scipy.stats.qmc.Sobol) with one fixed seed, n = 2048 = 2**11, over
the eight geometry variables and Poisson's ratio. Any prefix of the sequence stays space filling,
which is why it replaces v0's Latin hypercube: waves (--limit) and shards are plain slices of it.
Design IDs look like v1_<seed>_<0000>. The plan is written once and checked on every resume.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from .spec import (
    REFERENCE_MATERIAL_KEY,
    SAMPLED_RANGES,
    GussetCaseInput,
    make_case,
    reference_material,
)

PLAN_SAMPLE_COUNT = 2048
PLAN_SEED = 20261116
REFERENCE_LOAD_FORCE_N = 500.0

SAMPLE_PLAN_COLUMNS = (
    "design_id",
    *SAMPLED_RANGES,
    "reference_load_force_n",
    "reference_youngs_modulus_mpa",
    "reference_yield_strength_mpa",
    "reference_density_kg_m3",
    "material_key",
)


def sobol_unit_samples(count: int, dimension: int, seed: int) -> list[list[float]]:
    """Scrambled Sobol points in [0, 1)^dimension. A full power of two is balanced; a prefix still fills space."""
    from scipy.stats import qmc

    sampler = qmc.Sobol(d=dimension, scramble=True, seed=seed)
    power = int(math.log2(count))
    if 2**power == count:
        points = sampler.random_base2(power)
    else:
        points = sampler.random(count)  # scipy warns about balance; callers pass 2048 in production
    return [[float(v) for v in row] for row in points]


def make_plan(count: int = PLAN_SAMPLE_COUNT, seed: int = PLAN_SEED) -> list[dict[str, object]]:
    """Plan rows in Sobol order, every input spelled out so the CSV alone reproduces each case."""
    names = tuple(SAMPLED_RANGES)
    material = reference_material()
    rows: list[dict[str, object]] = []
    for index, unit in enumerate(sobol_unit_samples(count, len(names), seed)):
        row: dict[str, object] = {"design_id": f"v1_{seed}_{index:04d}"}
        for name, u in zip(names, unit):
            low, high = SAMPLED_RANGES[name]
            row[name] = low + u * (high - low)
        row.update({
            "reference_load_force_n": REFERENCE_LOAD_FORCE_N,
            "reference_youngs_modulus_mpa": material.youngs_modulus_mpa,
            "reference_yield_strength_mpa": material.yield_strength_mpa,
            "reference_density_kg_m3": material.density_kg_m3,
            "material_key": REFERENCE_MATERIAL_KEY,
        })
        rows.append(row)
    return rows


def plan_row_to_case(row: dict[str, object]) -> GussetCaseInput:
    """Validate one plan row. Raises pydantic.ValidationError naming the rule a bad design breaks."""
    geometry = {name: float(row[name]) for name in SAMPLED_RANGES if name != "poissons_ratio"}
    return make_case(str(row["design_id"]), geometry, poissons_ratio=float(row["poissons_ratio"]),
                     load_force_n=float(row["reference_load_force_n"]))


def validity_report(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    """How many plan rows pass the spec, and which rules the rest break."""
    failed: dict[str, str] = {}
    for row in rows:
        try:
            plan_row_to_case(row)
        except ValidationError as exc:
            failed[str(row["design_id"])] = "; ".join(str(e["msg"]) for e in exc.errors())
    return {"rows": len(rows), "valid": len(rows) - len(failed), "valid_fraction": (len(rows) - len(failed)) / max(len(rows), 1),
            "failed": failed}


def write_plan(path: Path, rows: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SAMPLE_PLAN_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def read_plan(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def check_frozen_plan(path: Path, expected: Sequence[dict[str, object]]) -> None:
    """A saved plan must match the regenerated one row for row; otherwise the output folder is for another plan."""
    if not path.exists():
        return
    saved = read_plan(path)
    if len(saved) != len(expected):
        raise ValueError(f"{path} has {len(saved)} rows but this plan has {len(expected)}. Use another output folder.")
    numeric = [c for c in SAMPLE_PLAN_COLUMNS if c not in ("design_id", "material_key")]
    for old, new in zip(saved, expected):
        if old["design_id"] != new["design_id"] or old["material_key"] != new["material_key"]:
            raise ValueError(f"{path} does not match this seed and order at {old['design_id']}.")
        for column in numeric:
            if not math.isclose(float(old[column]), float(new[column]), rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError(f"{path} differs from the requested plan at {old['design_id']}:{column}.")
