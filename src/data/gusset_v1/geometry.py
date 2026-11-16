"""CadQuery geometry for the gusseted L-bracket v1.

Build order: the two legs, the v0 inside-corner fillet, the triangular gusset fused in, a fixed
2 mm fillet along every edge where the gusset meets the legs, then the hole. The junction fillet
is part of the family definition (spec.GUSSET_JUNCTION_FILLET_MM), not a design variable.
"""

from __future__ import annotations

from typing import Any

from ..family import GeometryFamily
from ..geometry import GeometryBuildError, _cadquery, _find_inside_corner_edge
from .spec import (
    FAMILY_KEY,
    GUSSET_JUNCTION_FILLET_MM,
    HOLE_CENTER_X_MM,
    HOLE_CENTER_Z_MM,
    GussetCaseInput,
)

OVERLAP_MM = 0.5  # the gusset triangle reaches this far into the legs so the fuse has no coincident faces
TOL = 1.0e-6


def _gusset_solid(cq: Any, case: GussetCaseInput) -> Any:
    """Right triangle in the XY plane with its hypotenuse x + y = 2t + g, extruded across the gusset band."""
    t, g, w, gt = case.thickness_mm, case.gusset_length_mm, case.arm_width_mm, case.gusset_thickness_mm
    corner = t - OVERLAP_MM
    far = t + g + OVERLAP_MM  # keeps the hypotenuse on the same line while the corner overlaps the legs
    profile = [(corner, corner), (far, corner), (corner, far)]
    z0 = w / 2.0 - gt / 2.0
    return (
        cq.Workplane("XY", origin=(0.0, 0.0, z0))
        .polyline(profile)
        .close()
        .extrude(gt)
        .val()
    )


def _is_gusset_face(face: Any, case: GussetCaseInput) -> bool:
    """Faces whose centre sits above both legs inside the gusset band belong to the gusset."""
    c = face.Center()
    half = case.gusset_thickness_mm / 2.0 + TOL
    return (
        abs(c.z - case.arm_width_mm / 2.0) <= half
        and c.x > case.thickness_mm + TOL
        and c.y > case.thickness_mm + TOL
    )


def junction_edges(solid: Any, case: GussetCaseInput) -> list[Any]:
    """Edges shared by one gusset face and one leg or fillet face: the loop around the gusset footprint."""
    gusset_faces = [f for f in solid.Faces() if _is_gusset_face(f, case)]
    other_edges = [e for f in solid.Faces() if not _is_gusset_face(f, case) for e in f.Edges()]
    found: list[Any] = []
    for face in gusset_faces:
        for edge in face.Edges():
            if any(edge.isSame(other) for other in other_edges) and not any(edge.isSame(k) for k in found):
                found.append(edge)
    return found


def build_gusset_bracket(case: GussetCaseInput) -> Any:
    """Build one validated gusseted L-bracket as a CadQuery solid."""
    cq = _cadquery()
    t, length, height, width = case.thickness_mm, case.flange_length_mm, case.vertical_height_mm, case.arm_width_mm
    horizontal = cq.Workplane("XY").box(length, t, width, centered=(False, False, False))
    vertical = cq.Workplane("XY").box(t, height, width, centered=(False, False, False))
    sharp = horizontal.union(vertical).val()
    try:
        inside_edge = _find_inside_corner_edge(sharp, thickness_mm=t, width_mm=width)
        filleted = sharp.fillet(case.fillet_radius_mm, [inside_edge])
        fused = cq.Workplane("XY").add(filleted).union(_gusset_solid(cq, case)).val()
        if not fused.isValid():
            raise GeometryBuildError("the gusset fuse produced an invalid solid")
        edges = junction_edges(fused, case)
        if len(edges) != 8:  # two leg lines, two fillet arcs, two tip lines per side pair
            raise GeometryBuildError(f"expected 8 gusset junction edges, found {len(edges)}")
        rounded = fused.fillet(GUSSET_JUNCTION_FILLET_MM, edges)
        extension = 0.1
        hole = cq.Solid.makeCylinder(
            case.hole_diameter_mm / 2.0,
            t + 2.0 * extension,
            cq.Vector(HOLE_CENTER_X_MM, -extension, HOLE_CENTER_Z_MM),
            cq.Vector(0.0, 1.0, 0.0),
        )
        result = rounded.cut(hole)
    except GeometryBuildError as exc:
        raise GeometryBuildError(f"Failed to build geometry for design '{case.design_id}': {exc}") from exc
    except Exception as exc:  # OCC errors carry little text; keep the design id with them
        raise GeometryBuildError(f"Failed to build geometry for design '{case.design_id}': {type(exc).__name__}: {exc}") from exc
    if not result.isValid():
        raise GeometryBuildError(f"Geometry for design '{case.design_id}' is not a valid solid.")
    return result


L_BRACKET_GUSSET_V1 = GeometryFamily(
    key=FAMILY_KEY,
    build=build_gusset_bracket,
    load_face_x_mm=lambda case: case.flange_length_mm,
)
