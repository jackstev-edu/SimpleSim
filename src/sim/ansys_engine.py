"""Ansys engine for the PyDiscovery emulator (Role 3): L-bracket designs solved with SpaceClaim + Mechanical.

Gusseted L-bracket v1 (docs/bracket_family_v1_l_bracket_gusset_v1.md): each design is built by Role 1's
CadQuery builder and exported as STEP, so ANSYS solves exactly the part the predictor learned from. The
2 mm gusset junction fillets, where the peak sits, get a local mesh of r/4 (0.5 mm). The main inside fillet
keeps the global size, which keeps the mesh check under the Student node limit. A design still too large
to mesh-check keeps its result, marked not mesh-verified.

Can's v0 family (docs/bracket_family_v0.md) is built from examples/l_bracket.scdocx, so each of the five
dimensions maps to a workbench edit on that file: offsets from the baseline faces, a hole radius and the
inside-corner fillet. Faces are found by geometry on the baseline part, never by id.
Stress for the safety factor is the peak outside the support and load zones (stress_for_limit_MPa).
"""
from __future__ import annotations

import atexit
import itertools
import json
import re
from pathlib import Path

from src.sim import materials

BASELINE_MM = {"thickness_mm": 6.0, "flange_length_mm": 80.0, "arm_width_mm": 40.0, "hole_diameter_mm": 8.0}
PART = Path(__file__).resolve().parents[2] / "examples" / "l_bracket.scdocx"
FALLBACK = "Structural Steel"
TOL_MM, TOL_DOT = 0.01, 0.999
FAMILIES = ("l_bracket_v0", "l_bracket_gusset_v1")
ELEMENTS_PER_RADIUS = 4  # same rule as the workbench's fillet tool: local size r/4


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


def _tokens(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.lower()))


def library_material(name: str) -> str | None:
    """The ANSYS library material with this name: exact up to case, spaces, '_' and '-', else the one
    library name containing every word of it ("Aluminum alloy 6061-T6" -> "Aluminum alloy, wrought, 6061, T6")."""
    names = list(materials.catalog())
    exact = next((n for n in names if _key(n) == _key(name)), None)
    if exact:
        return exact
    words = _tokens(name)
    hits = [n for n in names if words and words <= _tokens(n)]
    return hits[0] if len(hits) == 1 else None  # ambiguous or none: fall back to steel


def gusset_faces(faces: list[dict], design_mm: dict[str, float]) -> dict:
    """Support, load and round faces of one gusset STEP, from normals, bounding boxes and the hole size."""
    def one(role, hits):
        if len(hits) != 1:
            raise ValueError(f"Could not identify the {role} face of the gusset part ({len(hits)} candidates)")
        return hits[0]["id"]

    flange = design_mm["flange_length_mm"]
    back = one("back", [f for f in faces if _along(f, 0, -1) and abs(f["bbox_max_mm"][0]) < TOL_MM])
    end = one("end", [f for f in faces if _along(f, 0, 1) and abs(f["bbox_min_mm"][0] - flange) < TOL_MM])
    r_hole = design_mm["hole_diameter_mm"] / 2

    def is_hole(f):  # the bolt hole runs along Y; fillets run along X or Z
        return (f["type"] == "cylinder" and abs(f["radius_mm"] - r_hole) < TOL_MM
                and abs(f.get("axis_dir", [0, 0, 0])[1]) > TOL_DOT)

    from src.data.gusset_v1.spec import GUSSET_JUNCTION_FILLET_MM  # pydantic only, no CadQuery

    # The gusset is centred across the width; its junction fillets stay within this band in z,
    # while the main inside fillet runs the full width. Only the junction (where the peak sits) is refined.
    mid = design_mm["arm_width_mm"] / 2
    half = design_mm["gusset_thickness_mm"] / 2 + GUSSET_JUNCTION_FILLET_MM + 0.5  # bbox slack; arm >= 30 mm wide

    def in_band(f):
        return f["bbox_min_mm"][2] >= mid - half and f["bbox_max_mm"][2] <= mid + half

    junction = [f for f in faces if f["type"] != "plane" and not is_hole(f) and in_band(f)]
    if not junction:
        raise ValueError("Found no gusset junction fillet faces on the part, so the junction could not be meshed finely")
    return {"back": back, "end": end, "round": [f["id"] for f in junction],
            "local_mm": round(GUSSET_JUNCTION_FILLET_MM / ELEMENTS_PER_RADIUS, 4)}


def write_gusset_step(family, design_mm: dict[str, float], path: Path) -> Path:
    """One v1 design as STEP from Role 1's CadQuery builder (needs CadQuery, src/sim/requirements.txt)."""
    from src.pydiscovery.backends import BackendUnavailable

    try:
        from src.data.bracket_spec import MaterialProperties
        from src.data.geometry import GeometryDependencyError, export_step
        from src.data.gusset_v1.geometry import L_BRACKET_GUSSET_V1
        from src.data.gusset_v1.spec import GussetCaseInput
    except ImportError as exc:
        raise BackendUnavailable(f"The Ansys engine builds the gusseted bracket with Role 1's builder: {exc}") from exc
    # the shape depends on the dimensions only; load and material are in-range placeholders the case model needs
    case = GussetCaseInput(design_id=path.stem, **design_mm, load_force_n=500.0, material=MaterialProperties(
        material_key="geometry_only", youngs_modulus_mpa=200000.0, poissons_ratio=0.3, yield_strength_mpa=250.0,
        density_kg_m3=7850.0))
    try:
        return export_step(case, path, L_BRACKET_GUSSET_V1)
    except GeometryDependencyError as exc:
        raise BackendUnavailable("The Ansys engine needs CadQuery for the gusseted bracket "
                                 f"(pip install -r src/sim/requirements.txt): {exc}") from exc


def _default_workbench(out_dir):
    from src.agent.tools import Workbench  # imports SpaceClaim/Mechanical clients only when used
    return Workbench(out_dir)


class AnsysEngine:
    """One SpaceClaim + one embedded Mechanical, started on the first solve; close() shuts them down."""

    name = "ansys_mechanical"

    def __init__(self, out_dir: Path = Path("data/processed/ansys_engine"), mesh_check: bool = False,
                 workbench_factory=None, step_writer=None):
        self.out_dir = Path(out_dir)
        self.mesh_check = mesh_check
        self._factory = workbench_factory or _default_workbench
        self._write_step = step_writer or write_gusset_step
        self._loaded = None  # the part the workbench has open: PART or a gusset STEP
        self._wb = None
        self._roles: dict | None = None
        self._material: str | None = None  # library name set in the workbench
        self._force: list[float] | None = None
        self._ids = itertools.count(1)
        self.last_details: dict = {}

    def solve(self, family, design_mm: dict[str, float], force_n: float, material) -> dict[str, float]:
        """max_stress_mpa (zone stress), max_disp_mm and mass_kg for one design."""
        if family.key not in FAMILIES:  # never solve an unknown shape as one of ours
            from src.pydiscovery.backends import BackendUnavailable

            raise BackendUnavailable(
                f"The Ansys engine builds {' and '.join(FAMILIES)} only, not {family.key}. "
                "Use the fitted or CalculiX engine for this family.")
        for field in ("youngs_mpa", "density_kg_m3"):
            if getattr(material, field, None) is None:
                raise ValueError(f"The Ansys engine needs the material's {field.replace('_', ' ')}; set the material first.")
        name = f"pyd_{next(self._ids):04d}"
        step = None
        if family.key == "l_bracket_v0":
            if self._loaded != PART:
                self._roles = self._load(PART, find_faces)
            edits = edits_for(design_mm, self._roles)
            fixed, loaded = self._roles["back"], self._roles["end"]
        else:  # gusset v1: a fresh STEP per design, so its faces are found again every time
            self.out_dir.mkdir(parents=True, exist_ok=True)
            step = Path(self._write_step(family, design_mm, self.out_dir / f"{name}.step"))
            roles = self._load(step, lambda faces: gusset_faces(faces, design_mm))
            first, *rest = roles["round"]
            edits = [{"face_id": first, "action": "refine", "also_face_ids": rest, "value_mm": roles["local_mm"]}]
            fixed, loaded = roles["back"], roles["end"]
        lib = library_material(material.name)
        used = lib or FALLBACK
        if used != self._material:
            self._wb.call("set_material", {"material": used})
            self._material, self._force = used, None  # the workbench clears its load case results on a change
        force = [round(force_n * c, 9) + 0.0 for c in family.load_direction]
        if force != self._force:
            self._wb.call("set_load_case", {"fixed_face_ids": [fixed], "load_face_ids": [loaded], "force_N": force})
            self._force = force
        r = json.loads(self._wb.call("evaluate_design", {"name": name, "edits": edits}))
        e_used = materials.catalog()[used]["youngs_modulus_GPa"] * 1000
        ratio = e_used / material.youngs_mpa
        disp = r["max_def_mm"] * ratio  # linear: deflection scales with 1/E, stress does not
        note = f"Solved with the ANSYS library material {used}."
        if lib is None:
            note = (f"{material.name} is not in the ANSYS library: solved as {FALLBACK}, deflection rescaled by E, "
                    "stress unchanged (Poisson's ratio effect ignored), mass from the part's density.")
        elif abs(ratio - 1) > 1e-3:
            note += f" Deflection rescaled to the part's E ({material.youngs_mpa:g} MPa vs library {e_used:g} MPa)."
        self.last_details = {
            "design": name, "edits": edits, "raw_peak_mpa": r["max_stress_MPa"],
            "stress_for_limit_mpa": r["stress_for_limit_MPa"], "zone_mm": r.get("zone_mm"),
            "zone_settled": r.get("zone_settled"), "excluded_at": r.get("excluded_at"),
            "stress_for_limit_at_mm": r.get("stress_for_limit_at_mm"), "max_stress_at_mm": r.get("max_stress_at_mm"),
            "nodes": r.get("nodes"), "messages": r.get("messages", []), "volume_mm3": r["volume_mm3"],
            "material_used": used, "material_note": note, "mesh_check": None,
            "step": None if step is None else str(step),
        }
        if self.mesh_check:
            try:
                chk = json.loads(self._wb.call("check_mesh", {"name": name}))
                self.last_details["mesh_check"] = {**chk, "verified": True}
            except ValueError as e:
                # MeshTooLarge (a ValueError; matched by name so this module needs no ANSYS import):
                # keep the solved result and say it is not mesh-verified
                if type(e).__name__ != "MeshTooLarge":
                    raise
                self.last_details["mesh_check"] = {"converged": False, "verified": False, "note": str(e)}
        return {"max_stress_mpa": r["stress_for_limit_MPa"], "max_disp_mm": disp,
                "mass_kg": r["volume_mm3"] * material.density_kg_m3 * 1e-9}

    def _load(self, part: Path, find) -> dict:
        """Open a part in the workbench (starting it on first use) and find its face roles."""
        fresh = self._wb is None
        if fresh:
            self._wb = self._factory(self.out_dir)
        try:
            roles = find(self._wb.load_part(part)["faces"])
        except Exception:
            if fresh:  # never leak a SpaceClaim session: the Student licence allows only a few
                self._wb.close()
                self._wb = None
            self._loaded = None
            raise
        if fresh:  # Mechanical lives on the workbench thread; disposing it from the main thread at exit deadlocks.
            atexit.register(self.close)
        # load_part resets the workbench's material and load case
        self._loaded, self._material, self._force = part, None, None
        return roles

    def close(self) -> None:
        if self._wb is not None:
            self._wb.close()
            self._wb = None
