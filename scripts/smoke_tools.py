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
        print(f"loaded: {len(faces)} faces, volume {part['volume_mm3']} mm^3")

        def face(rule):
            return next(f["id"] for f in faces if rule(f))

        fixed = face(lambda f: f["bbox_max_mm"][0] == 0)
        load = face(lambda f: f["bbox_min_mm"][0] == 80)
        base_top = face(lambda f: f.get("normal") == [0, 1, 0] and f["bbox_min_mm"][1] > 1
                        and f["bbox_max_mm"][0] > 70)
        hole = face(lambda f: f["type"] == "cylinder")

        print(wb.call("set_load_case", {"fixed_face_ids": [fixed], "load_face_ids": [load],
                                        "force_N": [0, -500, 0]}))
        for name, edits in [
            ("baseline", []),
            ("t8_r6", [{"face_id": base_top, "action": "offset", "value_mm": 2},
                       {"face_id": hole, "action": "radius", "value_mm": 6}]),
        ]:
            r = json.loads(wb.call("evaluate_design", {"name": name, "edits": edits}))
            print(f"{name}: volume {r['volume_mm3']}, stress {r['max_stress_MPa']:.1f} MPa, "
                  f"deflection {r['max_def_mm']:.4f} mm, balance error {r['balance_error']:.2%}, {r['solve_s']} s")
        try:
            wb.call("evaluate_design", {"name": "bad", "edits": [{"face_id": hole, "action": "offset",
                                                                   "value_mm": 1}]})
        except ValueError as e:
            print("bad edit rejected as expected:", e)
        print(wb.call("finalize_design", {"name": "t8_r6"}))
        print("table:", wb.table())
    finally:
        wb.close()


if __name__ == "__main__":
    main()
