"""Ansys engine check on the gusseted L-bracket (v1) in real ANSYS (no LLM).

Each design is built by Role 1's CadQuery builder, exported as STEP and solved in Mechanical with a local
mesh on the rounded faces. Run from the repo root:  ..\\.venv\\Scripts\\python.exe -u -m scripts.smoke_ansys_gusset
Pass: baseline volume within 0.5 % of the family doc's 33,240 mm3, mesh check converged on every design,
and the limit stress located at the gusset (inside the gusset reach, near the inside corner).
"""
import sys
import time

from src.pydiscovery.families import L_BRACKET_GUSSET_V1
from src.pydiscovery.session import table_material
from src.sim.ansys_engine import AnsysEngine

BASE = {"thickness_mm": 6.0, "flange_length_mm": 80.0, "arm_width_mm": 40.0, "hole_diameter_mm": 8.0,
        "fillet_radius_mm": 4.0, "vertical_height_mm": 60.0, "gusset_thickness_mm": 6.0, "gusset_length_mm": 20.0}
CASES = {"baseline": BASE,
         "long_thin_gusset": {**BASE, "gusset_thickness_mm": 4.0, "gusset_length_mm": 30.0, "vertical_height_mm": 75.0}}
BASE_VOLUME_MM3 = 33240.0  # docs/bracket_family_v1_l_bracket_gusset_v1.md
REACH_MARGIN_MM = 4.0  # junction fillet (2 mm) plus mesh-node slack around the gusset reach


def at_gusset(design: dict, xyz: list | None) -> bool:
    """True when a point lies within the gusset's reach from the inside corner (x, y <= t + g + margin)."""
    if not xyz:
        return False
    reach = design["thickness_mm"] + design["gusset_length_mm"] + REACH_MARGIN_MM
    return xyz[0] <= reach and xyz[1] <= reach


def main() -> int:
    material = table_material("structural_steel")
    engine = AnsysEngine(mesh_check=True)
    failed = False
    try:
        for name, design in CASES.items():
            t0 = time.time()
            out = engine.solve(L_BRACKET_GUSSET_V1, design, 500.0, material)
            det = engine.last_details
            ok_mesh = bool(det["mesh_check"] and det["mesh_check"].get("converged"))
            ok_where = at_gusset(design, det["stress_for_limit_at_mm"])
            ok_vol = name != "baseline" or abs(det["volume_mm3"] / BASE_VOLUME_MM3 - 1) <= 0.005
            ok = ok_mesh and ok_where and ok_vol
            failed |= not ok
            print(f"\n== {name} ({time.time() - t0:.0f} s) {'PASS' if ok else 'FAIL'}")
            print(f"  limit stress {out['max_stress_mpa']:.1f} MPa at {det['stress_for_limit_at_mm']} "
                  f"(at the gusset: {ok_where}); raw peak {det['raw_peak_mpa']:.1f} MPa at {det['max_stress_at_mm']}")
            print(f"  deflection {out['max_disp_mm']:.4f} mm; volume {det['volume_mm3']:.1f} mm3 "
                  f"(volume check {ok_vol}); mass {out['mass_kg']:.5f} kg")
            print(f"  zone {det['zone_mm']} mm, settled {det['zone_settled']}, excluded_at {det['excluded_at']}")
            print(f"  nodes {det['nodes']}; mesh check {det['mesh_check']}")
            print(f"  STEP {det['step']}; {det['material_note']}")
            for m in det["messages"]:
                print(f"  message: {m}")
        print("\nFor information only: Role 1's CalculiX baseline at the medium mesh read 273.7 MPa (family doc).")
    finally:
        engine.close()
    print("\nOVERALL", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
