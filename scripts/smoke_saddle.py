"""Robustness smoke test on a new shape: the PyAnsys saddle bracket (real ANSYS, no LLM).

Run from the repo root:
  python -u -m scripts.smoke_saddle describe   # load the part, print faces and inside corners
  python -u -m scripts.smoke_saddle run        # load case, baseline, mesh check, small first_look
"""
import json
import sys
import time
from pathlib import Path

from src.agent.tools import Workbench

ROOT = Path(__file__).resolve().parents[1]
PART = ROOT / "examples" / "pyansys" / "saddle_bracket.scdoc"
LIMITS = [{"response": "safety_factor", "op": ">=", "value": 1.5}]


def show(part: dict) -> None:
    print(f"volume {part['volume_mm3']} mm3, mass {part['mass_kg']} kg, {len(part['faces'])} faces", flush=True)
    for f in part["faces"]:
        print(" ", json.dumps(f), flush=True)
    print("inside corners:", json.dumps(part["inside_corners"]), flush=True)


def main(mode: str) -> None:
    wb = Workbench(ROOT / "outputs" / "smoke_saddle")
    try:
        t0 = time.time()
        part = wb.load_part(PART)
        print(f"load_part took {time.time() - t0:.0f} s", flush=True)
        show(part)
        if mode == "describe":
            return
        # Test load case (ours, not from PyAnsys): both left bolt holes fixed, 20 N down on the big right hole.
        cyl = sorted((f for f in part["faces"] if f["type"] == "cylinder"), key=lambda f: f["radius_mm"])
        fixed, load = [f["id"] for f in cyl[:2]], [cyl[-1]["id"]]
        print(wb.call("set_load_case", {"fixed_face_ids": fixed, "load_face_ids": load, "force_N": [0, -20, 0]}))

        t0 = time.time()
        base = json.loads(wb.call("evaluate_design", {"name": "baseline", "edits": []}))
        print(f"baseline ({time.time() - t0:.0f} s):", json.dumps(base), flush=True)
        t0 = time.time()
        print(f"check_mesh:", wb.call("check_mesh", {"name": "baseline"}), f"({time.time() - t0:.0f} s)", flush=True)

        # Features: the inside corner nearest the peak (fillet) and the two flange thicknesses (offset).
        peak = base.get("max_stress_at_mm") or [0, 0, 0]
        corner = min(part["inside_corners"],
                     key=lambda c: sum((p - q) ** 2 for p, q in zip(c["midpoint_mm"], peak)))
        a, b = corner["face_ids"]
        left_top = next(f["id"] for f in part["faces"] if f.get("normal") == [0.0, 1.0, 0.0] and f["bbox_max_mm"][1] < 2)
        right_top = next(f["id"] for f in part["faces"] if f.get("normal") == [0.0, 1.0, 0.0] and 8 < f["bbox_max_mm"][1] < 9)
        print("corner nearest peak:", corner, flush=True)
        t0 = time.time()
        out = json.loads(wb.call("first_look", {
            "features": [
                {"face_id": a, "other_face_id": b, "action": "fillet", "low": 0.5, "high": 2.0, "label": "corner fillet"},
                {"face_id": left_top, "action": "offset", "low": -0.3, "high": 1.0, "label": "left flange thickness"},
                {"face_id": right_top, "action": "offset", "low": -0.3, "high": 1.0, "label": "right flange thickness"}],
            "objective": "mass_kg", "limits": LIMITS}))
        print(f"first_look ({time.time() - t0:.0f} s):", json.dumps(out, indent=1), flush=True)
        while not wb.progress.empty():
            ev = wb.progress.get_nowait()
            if "error" in ev:
                print("  FAILED design:", ev, flush=True)
    finally:
        wb.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "describe")
