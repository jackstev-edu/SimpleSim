"""SimJEB comparison, design 148, vertical load case (real ANSYS, no LLM).

SimJEB (Whalen et al., Harvard Dataverse doi:10.7910/DVN/XFUWJG, ODC-By; CAD from the GrabCAD GE bracket
challenge, non-commercial). The part file stays outside the repo: Project2/external/simjeb/samples/148.stp.
Load case from 148.fem (OptiStruct, mm/N/t): the four bolt holes fixed (RBE2 to SPC 123456), 35,585.77 N in +z
on an RBE3 at the pin centre (-21.04, -75.06, 44.80). SimJEB results for 148 (all_bracket_metadata.tab):
max displacement 2.490718 mm, max von Mises stress 1719.315 MPa, on a 640,197-tet mesh. Ti-6Al-4V, E 113.8 GPa.

Run from the repo root:  python -u -m scripts.smoke_simjeb describe | run
"""
import json
import math
import sys
import time
from pathlib import Path

from src.agent.tools import Workbench

ROOT = Path(__file__).resolve().parents[1]
PART = ROOT.parent / "external" / "simjeb" / "samples" / "148.stp"
BOLTS = [(51.94775, 1.551532, 4.274734), (0.042367, -0.269911, 4.318873),
         (-0.201863, -148.094, 4.245224), (38.14242, -146.964, 4.240045)]  # RBE2 reference nodes 129261-4
PIN = (-21.0359, -75.0647, 44.80101)  # RBE3 reference node 129265
FORCE = [0.0, 0.0, 35585.77]
REF = {"max_def_mm": 2.490718, "max_stress_MPa": 1719.315, "E_GPa": 113.8}
MATERIAL = "Titanium alloy, Ti-6Al-4V, annealed"  # Granta library entry, E 111.2 GPa


def centre(f):
    return [(a + b) / 2 for a, b in zip(f["bbox_min_mm"], f["bbox_max_mm"])]


def main(mode: str) -> None:
    wb = Workbench(ROOT / "outputs" / "smoke_simjeb")
    try:
        t0 = time.time()
        part = wb.load_part(PART)
        faces = part["faces"]
        cyl = [f for f in faces if f["type"] == "cylinder"]
        print(f"load_part {time.time() - t0:.0f} s: volume {part['volume_mm3']} mm3, {len(faces)} faces "
              f"({len(cyl)} cylinders), min wall {part['min_wall_mm']}, default mesh {part['default_element_size_mm']} mm",
              flush=True)
        # Bolt holes: cylinders whose centre lies near a bolt reference node in x-y (holes run along z).
        fixed = [f["id"] for f in cyl if abs(f["radius_mm"] - 5.156) < 0.1
                 and min(math.dist(centre(f)[:2], b[:2]) for b in BOLTS) < 8]
        # Pin hole (3/4 in pin): cylinders of radius 8-11 mm near the pin reference node.
        load = [f["id"] for f in cyl if 8 < f["radius_mm"] < 11 and math.dist(centre(f), PIN) < 30]
        if not load:
            near = sorted(cyl, key=lambda f: math.dist(centre(f), PIN))[:8]
            raise SystemExit("No pin faces found; nearest cylinders: " + "; ".join(
                f"{f['id']} r={f['radius_mm']} c={[round(c, 1) for c in centre(f)]}" for f in near))
        for f in cyl:
            tag = "FIXED" if f["id"] in fixed else "LOAD" if f["id"] in load else ""
            if tag or mode == "describe":
                print(f"  {tag:5s} {f['id']} r={f.get('radius_mm')} centre={[round(c, 2) for c in centre(f)]}", flush=True)
        if mode == "describe":
            return
        print(wb.call("set_material", {"material": MATERIAL}), flush=True)
        print(wb.call("set_load_case", {"fixed_face_ids": fixed, "load_face_ids": load, "force_N": FORCE}), flush=True)
        t0 = time.time()
        base = json.loads(wb.call("evaluate_design", {"name": "baseline", "edits": []}))
        print(f"baseline ({time.time() - t0:.0f} s):", json.dumps(base), flush=True)
        t0 = time.time()
        chk = json.loads(wb.call("check_mesh", {"name": "baseline"}))
        print(f"check_mesh ({time.time() - t0:.0f} s):", json.dumps(chk), flush=True)
        e_corr = 111.2 / REF["E_GPa"]  # our material is stiffer, so scale our deflection to SimJEB's E
        for label, r in (("coarse", chk["coarse"]), ("fine", chk["fine"])):
            d = r["max_def_mm"] * e_corr
            print(f"{label} {r['element_size_mm']} mm, {r['nodes']} nodes: deflection {d:.3f} mm (E-corrected) vs "
                  f"{REF['max_def_mm']} ({(d / REF['max_def_mm'] - 1) * 100:+.1f}%), stress {r['max_stress_MPa']:.0f} vs "
                  f"{REF['max_stress_MPa']} MPa ({(r['max_stress_MPa'] / REF['max_stress_MPa'] - 1) * 100:+.1f}%), "
                  f"peak at {r['max_stress_at_mm']}", flush=True)
    finally:
        wb.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "describe")
