"""Gusseted L-bracket v1: spec rules, Sobol plan, the parallel pipeline (fake solver), shards and merge, geometry, mesh.

Geometry and mesh tests skip without CadQuery or Gmsh; the plan and pipeline tests skip without scipy.
The pipeline tests swap the worker for a fake that returns the same dict shape as run_one_case.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import RUN_COLUMNS
from src.data.gusset_v1 import spec
from src.data.gusset_v1.spec import (
    BASELINE_MM,
    GUSSET_JUNCTION_FILLET_MM,
    HOLE_CLEARANCE_MM,
    RULES,
    GussetCaseInput,
    failed_rules,
    make_baseline_case,
    make_case,
)

scipy = pytest.importorskip("scipy")  # the Sobol plan; Role 1's requirements, not the root ones

from src.data.gusset_v1 import pipeline  # noqa: E402
from src.data.gusset_v1.merge import merge_shards  # noqa: E402
from src.data.gusset_v1.pipeline import MODEL_DATASET_COLUMNS, RUNS_V1_COLUMNS, evaluate_quality, generate_dataset  # noqa: E402
from src.data.gusset_v1.plan import make_plan, plan_row_to_case, validity_report  # noqa: E402

GEOMETRY_RULE_NAMES = {name for name, _ in RULES}


# ------------------------------------------------------------------ spec

def test_baseline_validates_and_keeps_v0_values() -> None:
    case = make_baseline_case()
    assert case.thickness_mm == 6.0 and case.flange_length_mm == 80.0 and case.arm_width_mm == 40.0
    assert case.vertical_height_mm == 60.0 and case.gusset_length_mm == 20.0 and case.gusset_thickness_mm == 6.0
    assert failed_rules(case) == []
    assert case.load_vector_n == (0.0, -500.0, 0.0)


def test_ranges_include_every_v0_baseline_value() -> None:
    for name, value in BASELINE_MM.items():
        low, high = spec.GEOMETRY_RANGES_MM[name]
        assert low <= value <= high, name


@pytest.mark.parametrize(
    "rule, change",
    [
        ("hole_inside_flange", {"flange_length_mm": 75.0, "hole_diameter_mm": 12.0, "arm_width_mm": 32.0}),  # not reachable: 60 + 6 < 75
        ("gusset_clears_hole", {"thickness_mm": 10.0, "gusset_length_mm": 35.0, "hole_diameter_mm": 12.0}),
        ("gusset_clears_vertical_top", {"thickness_mm": 10.0, "gusset_length_mm": 35.0, "vertical_height_mm": 50.0}),
        ("gusset_within_width", {"arm_width_mm": 32.0, "gusset_thickness_mm": 10.0}),
        ("gusset_past_fillet", {"fillet_radius_mm": 8.0, "gusset_length_mm": 12.0}),
    ],
)
def test_each_rule_rejects_a_known_bad_case(rule: str, change: dict[str, float]) -> None:
    """Rules are checked on plain geometry objects, outside the pydantic ranges, so the bad cases can be explicit."""

    class G:  # a bare geometry holder the rules can read
        pass

    good = G()
    for k, v in BASELINE_MM.items():
        setattr(good, k, v)
    assert failed_rules(good) == []
    bad = G()
    for k, v in {**BASELINE_MM, **change}.items():
        setattr(bad, k, v)
    if rule == "hole_inside_flange":
        bad.hole_diameter_mm = 42.0  # push the hole through the free end
    elif rule == "gusset_clears_hole":
        bad.gusset_length_mm = 60.0 - 6.0 - 10.0 - GUSSET_JUNCTION_FILLET_MM - HOLE_CLEARANCE_MM + 0.5
    elif rule == "gusset_clears_vertical_top":
        bad.vertical_height_mm = 40.0
    elif rule == "gusset_within_width":
        bad.gusset_thickness_mm = 24.0
    elif rule == "gusset_past_fillet":
        bad.gusset_length_mm = 9.0
    names = {msg.split(":")[0].replace("rule ", "") for msg in failed_rules(bad)}
    assert rule in names


def test_model_validator_names_the_broken_rule() -> None:
    # Inside the documented ranges no rule can fail (that is how the ranges were chosen), so bypass
    # the range checks with model_construct and run the rule validator directly.
    bad = GussetCaseInput.model_construct(design_id="bad", load_force_n=500.0, material=spec.reference_material(),
                                          **{**BASELINE_MM, "vertical_height_mm": 30.0})  # reach 28 mm leaves no 3 mm margin
    with pytest.raises(ValueError, match="gusset_clears_vertical_top"):
        bad.validate_geometry_rules()
    with pytest.raises(ValidationError):
        make_case("out_of_range", {**BASELINE_MM, "gusset_length_mm": 40.0})  # above the range, never clamped


# ------------------------------------------------------------------ plan

def test_plan_is_deterministic_balanced_and_valid() -> None:
    first, second = make_plan(256, seed=7), make_plan(256, seed=7)
    assert first == second
    assert [r["design_id"] for r in first[:2]] == ["v1_7_0000", "v1_7_0001"]
    assert len({r["design_id"] for r in first}) == 256
    report = validity_report(first)
    assert report["valid_fraction"] >= 0.99, report["failed"]
    for name, (low, high) in spec.SAMPLED_RANGES.items():
        assert all(low <= float(r[name]) <= high for r in first)
    balance = [c for c in evaluate_quality(first, [], [], [], 0) if c.check_name.startswith("sobol_balance_")]
    assert len(balance) == len(spec.SAMPLED_RANGES) and all(c.passed for c in balance)
    assert make_plan(64, seed=8) != make_plan(64, seed=9)


# ------------------------------------------------------------------ pipeline with a fake worker

def fake_solve(job):
    """Same return shape as pipeline.run_one_case, from a closed-form stand-in. Design 0005 fails once."""
    row, case_dir, timeout, attempt, keep = job
    case = plan_row_to_case(row)
    Path(case_dir).mkdir(parents=True, exist_ok=True)
    (Path(case_dir) / "fake_ccx.log").write_text("This is Version 2.23\n", encoding="utf-8")
    if row["design_id"].endswith("0005") and attempt == 1:
        return {"design_id": row["design_id"], "attempt_number": attempt, "ok": False,
                "failure": pipeline._failure_row(row, case, "solve", RuntimeError("fake nonpositive jacobian"), attempt, time.perf_counter())}
    volume = case.thickness_mm * case.arm_width_mm * (case.flange_length_mm + case.vertical_height_mm) + 0.5 * case.gusset_length_mm**2 * case.gusset_thickness_mm
    stress = 100.0 * (6.0 / case.thickness_mm) ** 2 * case.flange_length_mm / 80.0
    from contracts import Run

    run = Run(design_id=case.design_id, source="calculix_fake", mesh_rule="fake", material_key=case.material.material_key,
              load_force_n=case.load_force_n, load_angle_deg=-90.0, thickness_mm=case.thickness_mm, flange_length_mm=case.flange_length_mm,
              arm_width_mm=case.arm_width_mm, hole_diameter_mm=case.hole_diameter_mm, fillet_radius_mm=case.fillet_radius_mm,
              max_stress_mpa=stress, max_disp_mm=stress / 1000.0, mass_kg=volume * 7850e-9)
    run_row = {**run.model_dump(), **{n: getattr(case, n) for n in spec.NEW_GEOMETRY_NAMES}}
    diagnostic = {c: 0 for c in pipeline.DIAGNOSTIC_V1_COLUMNS}
    diagnostic.update({"design_id": case.design_id, "schema_version": "0.1.0", "reaction_relative_imbalance": 1e-8, "minimum_element_quality": 0.3,
                       "elapsed_total_s": 0.01, "platform": "fake", "ccx_version": "2.23", "attempt_number": attempt, "mesh_level": "coarse"})
    model_row = {"dataset_schema_version": spec.DATASET_SCHEMA_VERSION, "design_id": case.design_id, "bracket_family": spec.FAMILY_KEY,
                 "source": "calculix_fake", "mesh_rule": "fake", **case.geometry_mm(), "poissons_ratio": case.material.poissons_ratio,
                 "reference_load_force_n": case.load_force_n, "reference_youngs_modulus_mpa": case.material.youngs_modulus_mpa,
                 "reference_max_stress_mpa": stress, "reference_max_disp_mm": stress / 1000.0, "volume_mm3": volume, "reference_mass_kg": volume * 7850e-9}
    return {"design_id": case.design_id, "attempt_number": attempt, "ok": True, "run": run_row, "diagnostic": diagnostic, "model_row": model_row}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_waves_resume_and_log_failures(tmp_path: Path) -> None:
    plan = make_plan(32, seed=11)
    first = generate_dataset(tmp_path, plan=plan, limit=16, workers=2, solve=fake_solve)
    assert first.attempted_this_run == 16 and first.completed_cases == 15 and first.unresolved_failures == 1
    failures = _rows(tmp_path / "failures.csv")
    assert [f["design_id"] for f in failures] == ["v1_11_0005"] and failures[0]["failure_stage"] == "solve"
    assert _rows(tmp_path / "runs.csv")[0].keys() == set(RUNS_V1_COLUMNS) and tuple(RUNS_V1_COLUMNS[: len(RUN_COLUMNS)]) == RUN_COLUMNS
    # The second wave runs the rest of the plan plus the failed case, and skips everything done.
    second = generate_dataset(tmp_path, plan=plan, workers=2, solve=fake_solve)
    assert second.attempted_this_run == 17 and second.completed_cases == 32 and second.unresolved_failures == 0
    assert second.all_quality_checks_passed
    attempts = _rows(tmp_path / "failure_attempts.csv")
    assert len(attempts) == 1 and attempts[0]["attempt_number"] == "1"
    assert _rows(tmp_path / "failures.csv") == []
    model = _rows(tmp_path / "model_dataset.csv")
    assert list(model[0].keys()) == list(MODEL_DATASET_COLUMNS) and model[0]["dataset_schema_version"] == "role1_model_1.0.0"
    assert [m["design_id"] for m in model] == [r["design_id"] for r in plan]  # plan order
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["successful_cases"] == 32 and summary["bracket_family"] == "l_bracket_gusset_v1"
    third = generate_dataset(tmp_path, plan=plan, workers=1, solve=fake_solve)
    assert third.attempted_this_run == 0  # nothing left


def test_frozen_plan_rejects_another_plan(tmp_path: Path) -> None:
    generate_dataset(tmp_path, plan=make_plan(8, seed=1), limit=2, workers=1, solve=fake_solve)
    with pytest.raises(ValueError, match="sample_plan"):
        generate_dataset(tmp_path, plan=make_plan(8, seed=2), workers=1, solve=fake_solve)


def test_shards_split_the_plan_and_merge_back(tmp_path: Path) -> None:
    plan = make_plan(16, seed=3)
    a = generate_dataset(tmp_path / "s0", plan=plan, shard=0, num_shards=2, workers=1, solve=fake_solve)
    b = generate_dataset(tmp_path / "s1", plan=plan, shard=1, num_shards=2, workers=1, solve=fake_solve)
    assert a.selected_cases == b.selected_cases == 8
    ids_a = {r["design_id"] for r in _rows(tmp_path / "s0" / "runs.csv")}
    ids_b = {r["design_id"] for r in _rows(tmp_path / "s1" / "runs.csv")}
    assert not ids_a & ids_b and ids_a | ids_b == {r["design_id"] for r in plan} - {"v1_3_0005"}
    partial = merge_shards(tmp_path / "merged", [tmp_path / "s0", tmp_path / "s1"])
    assert partial["missing"] == ["v1_3_0005"] and not partial["ok"]
    generate_dataset(tmp_path / "s1", plan=plan, shard=1, num_shards=2, workers=1, solve=fake_solve)  # retries the failure
    full = merge_shards(tmp_path / "merged", [tmp_path / "s0", tmp_path / "s1"])
    assert full["ok"] and full["merged_runs"] == 16 and full["all_quality_checks_passed"]
    twice = merge_shards(tmp_path / "merged_dup", [tmp_path / "s0", tmp_path / "s0", tmp_path / "s1"])
    assert twice["duplicates"] and not twice["ok"]


# ------------------------------------------------------------------ geometry and mesh (need CadQuery and Gmsh)

def test_baseline_gusset_bracket_builds() -> None:
    pytest.importorskip("cadquery")
    from src.data.geometry import summarize_geometry
    from src.data.gusset_v1.geometry import build_gusset_bracket

    solid = build_gusset_bracket(make_baseline_case())
    assert solid.isValid()
    summary = summarize_geometry(solid)
    assert summary.volume_mm3 > 32000.0  # the v0 baseline plate, plus the gusset
    assert (summary.x_max_mm, summary.y_max_mm, summary.z_max_mm) == pytest.approx((80.0, 60.0, 40.0))
    assert len(solid.Faces()) == 22


def test_extreme_corners_build() -> None:
    pytest.importorskip("cadquery")
    from src.data.gusset_v1.geometry import build_gusset_bracket

    for name, geometry in (("thin", spec.THINNEST_LONGEST_MM), ("thick", spec.THICKEST_SHORTEST_MM)):
        assert build_gusset_bracket(make_case(name, geometry)).isValid()


def test_baseline_mesh_has_fixed_and_load_nodes(tmp_path: Path) -> None:
    pytest.importorskip("cadquery")
    pytest.importorskip("gmsh")
    from src.data.gusset_v1.geometry import L_BRACKET_GUSSET_V1
    from src.data.gusset_v1.meshing import production_mesh_settings
    from src.data.mesh import generate_mesh

    case = make_baseline_case()
    summary = generate_mesh(case, tmp_path, settings=production_mesh_settings(case), family=L_BRACKET_GUSSET_V1)
    assert summary.fixed_node_count > 0 and summary.load_node_count > 0
    assert summary.volume_element_count > 0 and summary.minimum_element_quality > 0.1
    assert "curv" in summary.mesh_rule
