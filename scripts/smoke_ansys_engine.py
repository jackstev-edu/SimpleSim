"""Ansys engine check in real ANSYS (no LLM): two of Can's CalculiX runs re-solved through Jack's emulator.

Run from the repo root:  ..\\.venv\\Scripts\\python.exe -u -m scripts.smoke_ansys_engine
Reference values: data/processed/formal_v0/runs.csv on branch Jin(role--data), 500 N, structural steel.
Pass: mass within 1 %, deflection within 10 %, zone stress within 15 % of CalculiX, mesh check converged.
"""
import sys
import time

from src.pydiscovery.families import L_BRACKET_V0
from src.pydiscovery.session import table_material
from src.sim.ansys_engine import AnsysEngine

CASES = {
    "formal_20261111_085": ({"thickness_mm": 6.347747135119783, "flange_length_mm": 81.57748854959381,
                             "arm_width_mm": 38.28145814934185, "hole_diameter_mm": 9.281828861374336,
                             "fillet_radius_mm": 3.647431728482555}, (171.69, 0.4582, 0.25544)),
    "formal_20261111_145": ({"thickness_mm": 7.609197160950782, "flange_length_mm": 87.74770382265461,
                             "arm_width_mm": 39.5535328591916, "hole_diameter_mm": 7.371207621505333,
                             "fillet_radius_mm": 3.753980266308204}, (128.75, 0.3171, 0.32948)),
}
LIMITS = {"stress": 0.15, "disp": 0.10, "mass": 0.01}


def main() -> int:
    material = table_material("structural_steel")
    engine = AnsysEngine(mesh_check=True)
    failed = False
    try:
        for run_id, (design, (s_ref, d_ref, m_ref)) in CASES.items():
            t0 = time.time()
            out = engine.solve(L_BRACKET_V0, design, 500.0, material)
            det = engine.last_details
            diffs = {"stress": out["max_stress_mpa"] / s_ref - 1, "disp": out["max_disp_mm"] / d_ref - 1,
                     "mass": out["mass_kg"] / m_ref - 1}
            ok = all(abs(diffs[k]) <= LIMITS[k] for k in LIMITS) and det["mesh_check"]["converged"]
            failed |= not ok
            print(f"\n== {run_id} ({time.time() - t0:.0f} s) {'PASS' if ok else 'FAIL'}")
            print(f"  zone stress {out['max_stress_mpa']:.1f} MPa vs CalculiX {s_ref} ({diffs['stress']:+.1%}); "
                  f"raw peak {det['raw_peak_mpa']:.1f} MPa")
            print(f"  deflection {out['max_disp_mm']:.4f} mm vs {d_ref} ({diffs['disp']:+.1%})")
            print(f"  mass {out['mass_kg']:.5f} kg vs {m_ref} ({diffs['mass']:+.2%})")
            print(f"  zone {det['zone_mm']} mm, settled {det['zone_settled']}, excluded_at {det['excluded_at']}; "
                  f"limit stress at {det['stress_for_limit_at_mm']}, raw peak at {det['max_stress_at_mm']}")
            print(f"  nodes {det['nodes']}; mesh check {det['mesh_check']}; {det['material_note']}")
            for m in det["messages"]:
                print(f"  message: {m}")
    finally:
        engine.close()
    print("\nOVERALL", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
