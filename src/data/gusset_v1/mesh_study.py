"""Mesh convergence for l_bracket_gusset_v1: three designs at three levels, then the Gate 2 choice.

    python -m src.data.gusset_v1.mesh_study --output data/generated/gusset_v1_mesh_study --workers 3

Designs: the baseline and the two most extreme corners (thinnest/longest, thickest/shortest).
Rule (from the overnight brief): pick the coarsest level whose max stress is within 3% of the
next finer level on all three designs; if none, the finest level within 5%; if none, stop.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .geometry import L_BRACKET_GUSSET_V1
from .meshing import MESH_LEVELS, mesh_settings_for
from .spec import BASELINE_MM, THICKEST_SHORTEST_MM, THINNEST_LONGEST_MM, make_case

STUDY_DESIGNS: dict[str, dict[str, float]] = {
    "baseline": BASELINE_MM,
    "thinnest_longest": THINNEST_LONGEST_MM,
    "thickest_shortest": THICKEST_SHORTEST_MM,
}
LEVEL_ORDER = ("coarse", "medium", "fine")
TIGHT_LIMIT_PCT = 3.0
LOOSE_LIMIT_PCT = 5.0
THREAD_VARIABLES = ("OMP_NUM_THREADS", "CCX_NPROC_EQUATION_SOLVER", "CCX_NPROC_STIFFNESS", "CCX_NPROC_RESULTS")


def _single_thread() -> None:
    for name in THREAD_VARIABLES:
        os.environ[name] = "1"


def _solve_one(job: tuple[str, str, str, float]) -> dict[str, object]:
    """One design at one level, in a worker process. Returns plain data for the parent."""
    from ..solver import solve_case

    design_name, level, output_dir, timeout_s = job
    case = make_case(f"study_{design_name}", STUDY_DESIGNS[design_name])
    settings = mesh_settings_for(case, MESH_LEVELS[level])
    started = time.perf_counter()
    try:
        result = solve_case(case, Path(output_dir) / design_name / level, mesh_settings=settings,
                            timeout_seconds=timeout_s, family=L_BRACKET_GUSSET_V1)
    except Exception as exc:  # noqa: BLE001  the study reports failures instead of dying
        return {"design": design_name, "level": level, "mesh_rule": settings.mesh_rule, "ok": False,
                "error": f"{type(exc).__name__}: {exc}", "elapsed_s": time.perf_counter() - started}
    return {
        "design": design_name, "level": level, "mesh_rule": result.mesh_rule, "ok": True, "error": "",
        "max_stress_mpa": result.max_stress_mpa, "max_stress_x_mm": result.max_stress_location_mm[0],
        "max_stress_y_mm": result.max_stress_location_mm[1], "max_stress_z_mm": result.max_stress_location_mm[2],
        "max_disp_mm": result.max_disp_mm, "mass_kg": result.mass_kg, "node_count": result.node_count,
        "volume_element_count": result.volume_element_count, "minimum_element_quality": result.minimum_element_quality,
        "reaction_relative_imbalance": result.reaction_relative_imbalance, "elapsed_s": time.perf_counter() - started,
    }


def percent_change(value: float, reference: float) -> float:
    return abs(value - reference) / abs(reference) * 100.0


def choose_level(rows: list[dict[str, object]]) -> dict[str, object]:
    """Apply the Gate 2 rule to the finished table."""
    by_key = {(str(r["design"]), str(r["level"])): r for r in rows if r.get("ok")}
    changes: dict[str, dict[str, float]] = {}  # level -> design -> % change to the next finer level
    for i, level in enumerate(LEVEL_ORDER[:-1]):
        finer = LEVEL_ORDER[i + 1]
        changes[level] = {}
        for design in STUDY_DESIGNS:
            a, b = by_key.get((design, level)), by_key.get((design, finer))
            if a and b:
                changes[level][design] = percent_change(float(a["max_stress_mpa"]), float(b["max_stress_mpa"]))
    complete = {lvl: d for lvl, d in changes.items() if len(d) == len(STUDY_DESIGNS)}
    within = lambda lvl, limit: lvl in complete and all(v <= limit for v in complete[lvl].values())  # noqa: E731
    for level in LEVEL_ORDER[:-1]:  # coarsest first
        if within(level, TIGHT_LIMIT_PCT):
            return {"chosen_level": level, "basis": f"coarsest level within {TIGHT_LIMIT_PCT:g}% of the next finer level on all designs",
                    "stress_changes_pct": changes, "passed": True}
    for level in reversed(LEVEL_ORDER[:-1]):  # finest first
        if within(level, LOOSE_LIMIT_PCT):
            return {"chosen_level": level, "basis": f"no level within {TIGHT_LIMIT_PCT:g}%; finest level within {LOOSE_LIMIT_PCT:g}%",
                    "stress_changes_pct": changes, "passed": True}
    return {"chosen_level": None, "basis": f"no level within {LOOSE_LIMIT_PCT:g}% of the next finer level on all designs; stop before the full run",
            "stress_changes_pct": changes, "passed": False}


def run_study(output_directory: str | Path, workers: int = 3, timeout_s: float = 1800.0) -> dict[str, object]:
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(design, level, str(output_dir), timeout_s) for level in LEVEL_ORDER for design in STUDY_DESIGNS]
    rows: list[dict[str, object]] = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_single_thread) as pool:
        futures = {pool.submit(_solve_one, job): job for job in jobs}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            if row["ok"]:
                print(f"  {row['design']:>18} {row['level']:>6} {row['mesh_rule']:<28} stress {row['max_stress_mpa']:8.2f} MPa "
                      f"disp {row['max_disp_mm']:.4f} mm nodes {row['node_count']:>7} {row['elapsed_s']:6.1f} s", flush=True)
            else:
                print(f"  {row['design']:>18} {row['level']:>6} FAILED {row['error']}", flush=True)
    order = {(d, l): (LEVEL_ORDER.index(l), list(STUDY_DESIGNS).index(d)) for d in STUDY_DESIGNS for l in LEVEL_ORDER}
    rows.sort(key=lambda r: order[(str(r["design"]), str(r["level"]))])
    columns = ["design", "level", "mesh_rule", "ok", "error", "max_stress_mpa", "max_stress_x_mm", "max_stress_y_mm", "max_stress_z_mm",
               "max_disp_mm", "mass_kg", "node_count", "volume_element_count", "minimum_element_quality", "reaction_relative_imbalance", "elapsed_s"]
    with (output_dir / "mesh_study.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({c: r.get(c, "") for c in columns} for r in rows)
    choice = choose_level(rows)
    seconds = {lvl: [float(r["elapsed_s"]) for r in rows if r["level"] == lvl and r["ok"]] for lvl in LEVEL_ORDER}
    report = {
        "family": "l_bracket_gusset_v1", "platform": platform.platform(), "workers": workers,
        "designs": STUDY_DESIGNS, "levels": {lvl: MESH_LEVELS[lvl] for lvl in LEVEL_ORDER},
        "limits_pct": {"tight": TIGHT_LIMIT_PCT, "loose": LOOSE_LIMIT_PCT},
        "seconds_per_solve_by_level": {lvl: {"mean": sum(v) / len(v) if v else None, "max": max(v) if v else None} for lvl, v in seconds.items()},
        **choice, "rows": rows,
    }
    (output_dir / "mesh_study.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Chosen level: {choice['chosen_level']} ({choice['basis']})")
    for level, per_design in choice["stress_changes_pct"].items():
        print(f"  {level} vs next finer: " + ", ".join(f"{d} {v:.2f}%" for d, v in per_design.items()))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Mesh convergence study for the gusseted bracket.")
    parser.add_argument("--output", default="data/generated/gusset_v1_mesh_study")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    report = run_study(args.output, workers=args.workers, timeout_s=args.timeout_seconds)
    raise SystemExit(0 if report["passed"] else 2)


if __name__ == "__main__":
    main()
