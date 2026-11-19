"""Re-solve a seeded spread of formal_v0 designs on the v1 production mesh, to measure the v0 stress bias.

    python -m src.models.v0_mesh_bias --count 48 --workers 3 --case-dir <scratch folder off OneDrive>

The v1 work found that v0's uniform 1 to 2 mm mesh under-reads peak stress at the inside fillet
(240.7 MPa against a converged 273.7 MPa on the v1 baseline). This script keeps the v0 geometry
and Role 1's family registry, swaps only the mesh rule for the v1 one (max = min(3, t/2), min = max/4,
curvature sizing at 16 per circle, HXT, high-order optimization), and writes one row per design to
docs/model_data/v0_mesh_bias.csv with both results side by side. Role 1's dataset is never touched.
Needs src/data/requirements.txt (CadQuery, Gmsh, CalculiX); nothing here runs in the tests.
"""

from __future__ import annotations

import argparse
import csv
import os
import platform
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

V0_DIR = Path("data/processed/formal_v0")
OUT_CSV = Path("docs/model_data/v0_mesh_bias.csv")
SEED, COUNT = 0, 48
GEOMETRY = ("thickness_mm", "flange_length_mm", "arm_width_mm", "hole_diameter_mm", "fillet_radius_mm")
COLUMNS = ("design_id", *GEOMETRY, "poissons_ratio", "v0_mesh_rule", "v0_max_stress_mpa", "v0_max_disp_mm",
           "v1_mesh_rule", "v1_max_stress_mpa", "v1_max_disp_mm", "stress_ratio", "disp_ratio",
           "v1_node_count", "v1_min_element_quality", "v1_reaction_imbalance", "v1_elapsed_s", "ok", "error")
THREAD_VARIABLES = ("OMP_NUM_THREADS", "CCX_NPROC_EQUATION_SOLVER", "CCX_NPROC_STIFFNESS", "CCX_NPROC_RESULTS")


def pick_designs(model_dataset: pd.DataFrame, count: int = COUNT, seed: int = SEED) -> pd.DataFrame:
    """A seeded random spread of the v0 plan, in plan order."""
    rows = np.sort(np.random.default_rng(seed).choice(len(model_dataset), count, replace=False))
    return model_dataset.iloc[rows].reset_index(drop=True)


def v1_rule_mesh_settings(thickness_mm: float):
    """The v1 production rule (meshing.mesh_settings_for at scale 1) with no gusset term."""
    from src.data.mesh import MeshSettings

    maximum = min(3.0, thickness_mm / 2.0)
    return MeshSettings(minimum_size_mm=maximum / 4.0, maximum_size_mm=maximum, element_order=2,
                        curvature_elements=16, algorithm_3d=10, high_order_optimize=2)


def solve_one(job: tuple[dict, str, float]) -> dict:
    """Worker: one v0 design on the v1 mesh rule through Role 1's solver and family registry."""
    row, case_dir, timeout_s = job
    for name in THREAD_VARIABLES:  # one thread per CalculiX process, as Role 1's pipeline does
        os.environ[name] = "1"
    from src.data.bracket_spec import BracketCaseInput, MaterialProperties
    from src.data.formal_pipeline import cleanup_successful_case_files
    from src.data.geometry import L_BRACKET_V0
    from src.data.solver import solve_case

    out = {"design_id": row["design_id"], **{g: row[g] for g in GEOMETRY}, "poissons_ratio": row["poissons_ratio"],
           "v0_mesh_rule": row["mesh_rule"], "v0_max_stress_mpa": row["reference_max_stress_mpa"],
           "v0_max_disp_mm": row["reference_max_disp_mm"]}
    started = time.perf_counter()
    try:
        case = BracketCaseInput(
            design_id=str(row["design_id"]), **{g: float(row[g]) for g in GEOMETRY},
            load_force_n=float(row["reference_load_force_n"]),
            material=MaterialProperties(material_key="role1_isotropic_reference",
                                        youngs_modulus_mpa=float(row["reference_youngs_modulus_mpa"]),
                                        poissons_ratio=float(row["poissons_ratio"]), yield_strength_mpa=250.0, density_kg_m3=7850.0))
        folder = Path(case_dir) / case.design_id
        result = solve_case(case, folder, mesh_settings=v1_rule_mesh_settings(case.thickness_mm),
                            timeout_seconds=timeout_s, family=L_BRACKET_V0)
        out.update({"v1_mesh_rule": result.mesh_rule, "v1_max_stress_mpa": result.max_stress_mpa, "v1_max_disp_mm": result.max_disp_mm,
                    "stress_ratio": result.max_stress_mpa / float(row["reference_max_stress_mpa"]),
                    "disp_ratio": result.max_disp_mm / float(row["reference_max_disp_mm"]),
                    "v1_node_count": result.node_count, "v1_min_element_quality": result.minimum_element_quality,
                    "v1_reaction_imbalance": result.reaction_relative_imbalance, "ok": True, "error": ""})
        try:
            cleanup_successful_case_files(folder)
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001  the row records the failure; the rest of the batch continues
        out.update({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
    out["v1_elapsed_s"] = time.perf_counter() - started
    return out


def summarise(frame: pd.DataFrame) -> dict:
    """Median ratios and spread, the numbers docs/models.md quotes."""
    ok = frame[frame.ok == True]  # noqa: E712  CSV round trips keep booleans as strings sometimes
    out = {"solved": int(len(ok)), "failed": int(len(frame) - len(ok))}
    for name in ("stress_ratio", "disp_ratio"):
        r = ok[name].astype(float)
        out[name] = {"median": float(r.median()), "mean": float(r.mean()), "min": float(r.min()), "max": float(r.max()),
                     "p05": float(r.quantile(0.05)), "p95": float(r.quantile(0.95)), "std_pct": float(r.std() * 100)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=COUNT)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--timeout-seconds", type=float, default=900.0)
    ap.add_argument("--case-dir", default="data/generated/v0_mesh_bias", help="scratch folder for solver files, keep it off OneDrive")
    ap.add_argument("--out", type=Path, default=OUT_CSV)
    args = ap.parse_args(argv)
    designs = pick_designs(pd.read_csv(V0_DIR / "model_dataset.csv"), args.count, args.seed)
    done = {}
    if args.out.exists():  # resume: keep rows that already solved
        for r in csv.DictReader(args.out.open(encoding="utf-8")):
            if r["ok"] == "True":
                done[r["design_id"]] = r
    todo = [r for r in designs.to_dict("records") if r["design_id"] not in done]
    print(f"{len(designs)} designs picked (seed {args.seed}); {len(done)} already solved; {len(todo)} to run on {args.workers} workers; {platform.platform()}", flush=True)
    rows = dict(done)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(solve_one, (r, args.case_dir, args.timeout_seconds)) for r in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            rows[r["design_id"]] = r
            status = f"stress x{r['stress_ratio']:.3f} disp x{r['disp_ratio']:.4f}" if r["ok"] else r["error"]
            print(f"[{i}/{len(todo)}] {r['design_id']} {'OK' if r['ok'] else 'FAIL'} {status} ({r['v1_elapsed_s']:.0f} s)", flush=True)
            write_rows(args.out, [rows[d] for d in designs.design_id if d in rows])
    frame = pd.read_csv(args.out)
    print("summary:", summarise(frame), flush=True)
    print("=== v0_mesh_bias done", flush=True)
    return 0


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
