"""Ansys engine for the PyDiscovery emulator (Role 3): L-bracket v0 designs solved with SpaceClaim + Mechanical.

Can's v0 family (docs/bracket_family_v0.md) is built from examples/l_bracket.scdocx, so each of the five
dimensions maps to a workbench edit on that file: offsets from the baseline faces, a hole radius and the
inside-corner fillet. Faces are found by geometry on the baseline part, never by id.
Stress for the safety factor is the peak outside the support and load zones (stress_for_limit_MPa).
"""
from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

from src.sim import materials

BASELINE_MM = {"thickness_mm": 6.0, "flange_length_mm": 80.0, "arm_width_mm": 40.0, "hole_diameter_mm": 8.0}
PART = Path(__file__).resolve().parents[2] / "examples" / "l_bracket.scdocx"
FALLBACK = "Structural Steel"
TOL_MM, TOL_DOT = 0.01, 0.999


def _along(face: dict, axis: int, sign: int) -> bool:
    # Planar face whose outward normal points along +/- axis.
    return face["type"] == "plane" and sign * face["normal"][axis] > TOL_DOT


def find_faces(faces: list[dict]) -> dict:
    """Roles of the baseline L-bracket's faces, from normals and bounding boxes."""
    roles: dict = {}
    back = [f for f in faces if _along(f, 0, -1) and abs(f["bbox_max_mm"][0]) < TOL_MM]
    plus_x = sorted((f for f in faces if _along(f, 0, 1)), key=lambda f: f["bbox_max_mm"][0])
    plus_z = sorted((f for f in faces if _along(f, 2, 1)), key=lambda f: f["bbox_max_mm"][2])
    tops = sorted((f for f in faces if _along(f, 1, 1) and f["bbox_min_mm"][1] > TOL_MM),
                  key=lambda f: f["bbox_min_mm"][1])
    if back:
        roles["back"] = back[0]["id"]
    x_max = max(f["bbox_max_mm"][0] for f in faces)
    end = [f for f in plus_x if abs(f["bbox_max_mm"][0] - x_max) < TOL_MM]  # the free end is the part's max X
    inner = [f for f in plus_x if f["bbox_max_mm"][0] < x_max - TOL_MM]
    if end:
        roles["end"] = end[0]["id"]
    if inner:
        roles["vert_inner"] = inner[0]["id"]
    if plus_z:
        roles["side"] = plus_z[-1]["id"]
    if tops:
        roles["horiz_top"] = tops[0]["id"]
    hole = [f["id"] for f in faces if f["type"] == "cylinder"
            and abs(f["radius_mm"] - BASELINE_MM["hole_diameter_mm"] / 2) < TOL_MM]
    if hole:
        roles["hole"] = hole
    missing = [r for r in ("back", "end", "vert_inner", "side", "horiz_top", "hole") if r not in roles]
    if missing:
        raise ValueError(f"Not an L-bracket v0 baseline: no face found for {', '.join(missing)}.")
    return roles


def edits_for(design_mm: dict[str, float], roles: dict) -> list[dict]:
    """Workbench edits that turn the baseline part into this design; the fillet goes last."""
    edits = []
    dt = design_mm["thickness_mm"] - BASELINE_MM["thickness_mm"]
    for face, delta in ((roles["vert_inner"], dt), (roles["horiz_top"], dt),
                        (roles["end"], design_mm["flange_length_mm"] - BASELINE_MM["flange_length_mm"]),
                        (roles["side"], design_mm["arm_width_mm"] - BASELINE_MM["arm_width_mm"])):
        if abs(delta) > 1e-9:
            edits.append({"face_id": face, "action": "offset", "value_mm": round(delta, 6)})
    if abs(design_mm["hole_diameter_mm"] - BASELINE_MM["hole_diameter_mm"]) > 1e-9:
        first, *rest = roles["hole"]
        edit = {"face_id": first, "action": "radius", "value_mm": round(design_mm["hole_diameter_mm"] / 2, 6)}
        if rest:
            edit["also_face_ids"] = rest
        edits.append(edit)
    edits.append({"face_id": roles["vert_inner"], "action": "fillet", "other_face_id": roles["horiz_top"],
                  "value_mm": round(design_mm["fillet_radius_mm"], 6)})
    return edits


def _key(name: str) -> str:
    return re.sub(r"[\s_\-]", "", name.lower())


def library_material(name: str) -> str | None:
    """The ANSYS library material with this name, ignoring case, spaces, '_' and '-'."""
    return next((n for n in materials.catalog() if _key(n) == _key(name)), None)


def _default_workbench(out_dir):
    from src.agent.tools import Workbench  # imports SpaceClaim/Mechanical clients only when used
    return Workbench(out_dir)


class AnsysEngine:
    """One SpaceClaim + one embedded Mechanical, started on the first solve; close() shuts them down."""

    name = "ansys_mechanical"

    def __init__(self, out_dir: Path = Path("data/processed/ansys_engine"), mesh_check: bool = False,
                 workbench_factory=None):
        self.out_dir = Path(out_dir)
        self.mesh_check = mesh_check
        self._factory = workbench_factory or _default_workbench
        self._wb = None
        self._roles: dict | None = None
        self._material: str | None = None  # library name set in the workbench
        self._force: list[float] | None = None
        self._ids = itertools.count(1)
        self.last_details: dict = {}

    def solve(self, family, design_mm: dict[str, float], force_n: float, material) -> dict[str, float]:
        """max_stress_mpa (zone stress), max_disp_mm and mass_kg for one design."""
        for field in ("youngs_mpa", "density_kg_m3"):
            if getattr(material, field, None) is None:
                raise ValueError(f"The Ansys engine needs the material's {field.replace('_', ' ')}; set the material first.")
        if self._wb is None:
            wb = self._factory(self.out_dir)
            self._roles = find_faces(wb.load_part(PART)["faces"])
            self._wb = wb
        lib = library_material(material.name)
        used = lib or FALLBACK
        if used != self._material:
            self._wb.call("set_material", {"material": used})
            self._material, self._force = used, None  # the workbench clears its load case results on a change
        force = [round(force_n * c, 9) + 0.0 for c in family.load_direction]
        if force != self._force:
            self._wb.call("set_load_case", {"fixed_face_ids": [self._roles["back"]],
                                            "load_face_ids": [self._roles["end"]], "force_N": force})
            self._force = force
        name = f"pyd_{next(self._ids):04d}"
        edits = edits_for(design_mm, self._roles)
        r = json.loads(self._wb.call("evaluate_design", {"name": name, "edits": edits}))
        disp = r["max_def_mm"]
        note = f"Solved with the ANSYS library material {used}."
        if lib is None:
            e_lib = materials.catalog()[FALLBACK]["youngs_modulus_GPa"] * 1000
            disp *= e_lib / material.youngs_mpa  # linear: deflection scales with 1/E, stress does not
            note = (f"{material.name} is not in the ANSYS library: solved as {FALLBACK}, deflection rescaled by E, "
                    "stress unchanged (Poisson's ratio effect ignored), mass from the part's density.")
        self.last_details = {
            "design": name, "edits": edits, "raw_peak_mpa": r["max_stress_MPa"],
            "stress_for_limit_mpa": r["stress_for_limit_MPa"], "zone_mm": r.get("zone_mm"),
            "zone_settled": r.get("zone_settled"), "excluded_at": r.get("excluded_at"),
            "stress_for_limit_at_mm": r.get("stress_for_limit_at_mm"), "max_stress_at_mm": r.get("max_stress_at_mm"),
            "nodes": r.get("nodes"), "messages": r.get("messages", []), "volume_mm3": r["volume_mm3"],
            "material_used": used, "material_note": note, "mesh_check": None,
        }
        if self.mesh_check:
            self.last_details["mesh_check"] = json.loads(self._wb.call("check_mesh", {"name": name}))
        return {"max_stress_mpa": r["stress_for_limit_MPa"], "max_disp_mm": disp,
                "mass_kg": r["volume_mm3"] * material.density_kg_m3 * 1e-9}

    def close(self) -> None:
        if self._wb is not None:
            self._wb.close()
            self._wb = None
