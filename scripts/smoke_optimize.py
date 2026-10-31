"""Smoke test of the optimize tool (optiSLang ARSM + ANSYS), without the LLM (no API cost).

Run from the repo root:  python -m scripts.smoke_optimize
L-bracket, 500 N down at the free end: minimise mass subject to max stress <= 150 MPa,
varying the base thickness (offset of the base top face) with a fixed 4 mm inside fillet.
Expected from the live agent run: about +0.8 mm (149.8 MPa, 0.2694 kg).
"""
import json
from pathlib import Path

from src.agent.tools import Workbench

ROOT = Path(__file__).resolve().parents[1]


def main():
    wb = Workbench(ROOT / "outputs" / "smoke_optimize")
    try:
        part = wb.load_part(ROOT / "examples" / "l_bracket.scdocx")
        a, b = part["inside_corners"][0]["face_ids"]
        faces = part["faces"]
        fixed = next(f["id"] for f in faces if f["bbox_max_mm"][0] == 0)
        load = next(f["id"] for f in faces if f["bbox_min_mm"][0] == 80)
        base_top = next(f["id"] for f in faces if f.get("normal") == [0, 1, 0] and f["bbox_max_mm"][0] > 70)
        wb.call("set_load_case", {"fixed_face_ids": [fixed], "load_face_ids": [load], "force_N": [0, -500, 0]})
        out = json.loads(wb.call("optimize", {
            "prefix": "opt",
            "variables": [{"face_id": base_top, "action": "offset", "lower": 0.0, "upper": 2.0, "start": 1.0}],
            "fixed_edits": [{"face_id": a, "other_face_id": b, "action": "fillet", "value_mm": 4}],
            "objective": "mass_kg", "limits": {"max_stress_MPa": 150}, "budget": 10}))
        for d in out["designs"]:
            print(d)
        print("stopped:", out["stopped"], "| best feasible:", out["best_feasible"])
    finally:
        wb.close()


if __name__ == "__main__":
    main()
