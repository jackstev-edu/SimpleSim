"""End-to-end smoke test: edit a part in SpaceClaim, solve it in Mechanical, check the result.

Run from the repo root:  python -m scripts.smoke_loop

Uses examples/l_bracket.scdocx: an L-bracket (80 x 60 x 40 mm, 6 mm walls, 8 mm
bolt hole), fixed on the back of the vertical leg, 500 N down on the free end.
Face picking is hard-coded for this part; on user parts the agent picks faces.
"""
from pathlib import Path

from src.sim import mechanical
from src.sim.geometry import GeometrySession

ROOT = Path(__file__).resolve().parents[1]
PART = ROOT / "examples" / "l_bracket.scdocx"
OUT = ROOT / "outputs"
FORCE = (0.0, -500.0, 0.0)


def pick(faces, rule):
    hits = [f for f in faces if rule(f)]
    assert len(hits) == 1, f"expected one face, got {len(hits)}"
    return hits[0]["id"]


def main():
    OUT.mkdir(exist_ok=True)
    with GeometrySession() as geo:
        print(f"SpaceClaim up in {geo.startup_s:.0f} s")
        geo.open(PART)
        faces = geo.describe_faces()
        print(f"{len(faces)} faces, volume {geo.volume_mm3():.0f} mm^3")

        base_top = pick(faces, lambda f: f.get("normal") == [0, 1, 0] and f["bbox_min_mm"][1] > 1
                        and f["bbox_max_mm"][0] > 70)
        geo.offset_face(base_top, 2.0, [0, 1, 0])  # base 6 -> 8 mm

        faces = geo.describe_faces()  # ids can change after an edit
        geo.name_faces("NS_FIXED", [pick(faces, lambda f: f["bbox_max_mm"][0] == 0)])
        geo.name_faces("NS_LOAD", [pick(faces, lambda f: f["bbox_min_mm"][0] == 80)])
        print(f"after edit: volume {geo.volume_mm3():.0f} mm^3 (hand calc 37678)")
        pmdb = geo.save(OUT / "l_bracket_t8.pmdb")

    check = mechanical.refinement_check(pmdb, "NS_FIXED", "NS_LOAD", FORCE)
    for label in ("coarse", "fine"):
        r = check[label]
        print(f"{label}: {r['element_size_mm']} mm mesh, {r['nodes']} nodes, {r['solve_s']} s, "
              f"stress {r['max_stress_MPa']:.1f} MPa, deflection {r['max_def_mm']:.4f} mm, "
              f"reaction {[round(v, 1) for v in r['reaction_N']]} N, balance error {r['balance_error']:.2%}")
    print(f"stress change on refinement {check['stress_change']:.1%} -> "
          f"{'converged' if check['converged'] else 'NOT converged (likely a singularity)'}")


if __name__ == "__main__":
    main()
