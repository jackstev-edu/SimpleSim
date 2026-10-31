"""Smoke test of the agent's tool layer, without the LLM (no API cost).

Run from the repo root:  python -m scripts.smoke_tools
Makes the same Workbench.call(...) calls the agent would, on examples/l_bracket.scdocx.
"""
import json
from pathlib import Path

from src.agent.tools import Workbench

ROOT = Path(__file__).resolve().parents[1]


def main():
    wb = Workbench(ROOT / "outputs" / "smoke_tools")
    try:
        part = wb.load_part(ROOT / "examples" / "l_bracket.scdocx")
        faces = part["faces"]
        print(f"loaded: {len(faces)} faces, volume {part['volume_mm3']} mm^3, mass {part['mass_kg']} kg")
        print("inside corners:", part["inside_corners"])
        corner = part["inside_corners"][0]["face_ids"]

        def face(rule):
            return next(f["id"] for f in faces if rule(f))

        fixed = face(lambda f: f["bbox_max_mm"][0] == 0)
        load = face(lambda f: f["bbox_min_mm"][0] == 80)
        base_top = face(lambda f: f.get("normal") == [0, 1, 0] and f["bbox_min_mm"][1] > 1
                        and f["bbox_max_mm"][0] > 70)
        hole = face(lambda f: f["type"] == "cylinder")

        print(wb.call("set_load_case", {"fixed_face_ids": [fixed], "load_face_ids": [load],
                                        "force_N": [0, -500, 0]}))
        fillet = [{"face_id": corner[0], "other_face_id": corner[1], "action": "fillet", "value_mm": 3}]
        for name, edits in [
            ("baseline", []),
            ("t8_r6", [{"face_id": base_top, "action": "offset", "value_mm": 2},
                       {"face_id": hole, "action": "radius", "value_mm": 6}]),
            ("fillet_r3", fillet),
        ]:
            r = json.loads(wb.call("evaluate_design", {"name": name, "edits": edits}))
            print(f"{name}: volume {r['volume_mm3']}, mass {r['mass_kg']} kg, stress {r['max_stress_MPa']:.1f} MPa, "
                  f"deflection {r['max_def_mm']:.4f} mm, balance error {r['balance_error']:.2%}, {r['solve_s']} s")
        for name in ("baseline", "fillet_r3"):
            m = json.loads(wb.call("check_mesh", {"name": name}))
            print(f"mesh check {name}: {m['coarse']['max_stress_MPa']:.1f} -> {m['fine']['max_stress_MPa']:.1f} MPa "
                  f"({m['stress_change']:+.1%}), converged {m['converged']}")
        try:
            wb.call("evaluate_design", {"name": "bad", "edits": [{"face_id": hole, "action": "offset",
                                                                   "value_mm": 1}]})
        except ValueError as e:
            print("bad edit rejected as expected:", e)
        print(wb.call("finalize_design", {"name": "t8_r6"}))
        print("table:", wb.table())

        # Materials: same design in aluminium. Deflection should scale with E_steel / E_al, and
        # the price below is a made-up test value, only to exercise the cost response.
        steel = json.loads(wb.call("evaluate_design", {"name": "fillet_steel", "edits": fillet}))
        print(wb.call("set_material", {"material": "Aluminum Alloy", "safety_factor": 2.0,
                                       "price_usd_per_kg": 1.0, "price_source": "smoke-test dummy"}))
        al = json.loads(wb.call("evaluate_design", {"name": "fillet_al", "edits": fillet}))
        mats = json.loads(wb.call("list_materials", {}))["materials"]
        e_ratio = mats["Structural Steel"]["youngs_modulus_GPa"] / mats["Aluminum Alloy"]["youngs_modulus_GPa"]
        print(f"aluminium: material {al['material']}, mass {al['mass_kg']} kg (steel {steel['mass_kg']}), "
              f"SF {al['safety_factor']} (required {al['required_safety_factor']}, ok {al['meets_safety_factor']}), "
              f"cost {al['cost_usd']} USD; deflection ratio {al['max_def_mm'] / steel['max_def_mm']:.3f} "
              f"vs E ratio {e_ratio:.3f}")
        m = json.loads(wb.call("check_mesh", {"name": "fillet_al"}))
        print(f"mesh check fillet_al: {m['coarse']['max_stress_MPa']:.1f} -> {m['fine']['max_stress_MPa']:.1f} MPa")
        print("table:", wb.table())
    finally:
        wb.close()


if __name__ == "__main__":
    main()
