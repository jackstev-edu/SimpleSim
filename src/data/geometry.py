"""Parametric CadQuery geometry for the SimpleSim L-bracket family v0."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bracket_spec import (
    BracketCaseInput,
    HOLE_CENTER_X_MM,
    HOLE_CENTER_Z_MM,
    VERTICAL_HEIGHT_MM,
)


class GeometryDependencyError(RuntimeError):
    """Raised when the optional CadQuery dependency is unavailable."""


class GeometryBuildError(RuntimeError):
    """Raised when valid inputs cannot produce valid CAD geometry."""


@dataclass(frozen=True)
class GeometrySummary:
    """Measured properties of one generated solid."""

    volume_mm3: float
    x_min_mm: float
    x_max_mm: float
    y_min_mm: float
    y_max_mm: float
    z_min_mm: float
    z_max_mm: float


def _cadquery() -> Any:
    """Import CadQuery only when geometry generation is requested."""

    try:
        import cadquery as cq
    except ImportError as exc:
        raise GeometryDependencyError(
            "CadQuery is required for geometry generation. "
            "Install it with: python -m pip install cadquery"
        ) from exc

    return cq


def _find_inside_corner_edge(
    solid: Any,
    thickness_mm: float,
    width_mm: float,
) -> Any:
    """Locate the inside-corner edge that receives the fillet."""

    candidates = []
    tolerance = 1.0e-6

    for edge in solid.Edges():
        center = edge.Center()
        tangent = edge.tangentAt(0.5)

        is_target = (
            abs(center.x - thickness_mm) < tolerance
            and abs(center.y - thickness_mm) < tolerance
            and abs(center.z - width_mm / 2.0) < tolerance
            and abs(abs(tangent.z) - 1.0) < tolerance
        )

        if is_target:
            candidates.append(edge)

    if len(candidates) != 1:
        raise GeometryBuildError(
            "Expected exactly one inside-corner edge, "
            f"but found {len(candidates)}."
        )

    return candidates[0]


def build_bracket_geometry(case: BracketCaseInput) -> Any:
    """Build one validated L-bracket as a CadQuery solid."""

    cq = _cadquery()

    length = case.flange_length_mm
    height = VERTICAL_HEIGHT_MM
    width = case.arm_width_mm
    thickness = case.thickness_mm

    horizontal_leg = cq.Workplane("XY").box(
        length,
        thickness,
        width,
        centered=(False, False, False),
    )

    vertical_leg = cq.Workplane("XY").box(
        thickness,
        height,
        width,
        centered=(False, False, False),
    )

    sharp_bracket = horizontal_leg.union(vertical_leg).val()

    try:
        inside_edge = _find_inside_corner_edge(
            sharp_bracket,
            thickness_mm=thickness,
            width_mm=width,
        )

        filleted_bracket = sharp_bracket.fillet(
            case.fillet_radius_mm,
            [inside_edge],
        )

        cut_extension_mm = 0.1

        hole = cq.Solid.makeCylinder(
            case.hole_diameter_mm / 2.0,
            thickness + 2.0 * cut_extension_mm,
            cq.Vector(
                HOLE_CENTER_X_MM,
                -cut_extension_mm,
                HOLE_CENTER_Z_MM,
            ),
            cq.Vector(0.0, 1.0, 0.0),
        )

        result = filleted_bracket.cut(hole)

    except Exception as exc:
        raise GeometryBuildError(
            f"Failed to build geometry for design "
            f"'{case.design_id}': {exc}"
        ) from exc

    if not result.isValid():
        raise GeometryBuildError(
            f"Geometry for design '{case.design_id}' "
            "is not a valid solid."
        )

    return result


def summarize_geometry(solid: Any) -> GeometrySummary:
    """Return volume and bounding-box values in millimeters."""

    bounds = solid.BoundingBox()

    return GeometrySummary(
        volume_mm3=float(solid.Volume()),
        x_min_mm=float(bounds.xmin),
        x_max_mm=float(bounds.xmax),
        y_min_mm=float(bounds.ymin),
        y_max_mm=float(bounds.ymax),
        z_min_mm=float(bounds.zmin),
        z_max_mm=float(bounds.zmax),
    )


def export_step(
    case: BracketCaseInput,
    output_path: str | Path,
) -> Path:
    """Build one case and export it as a STEP file."""

    path = Path(output_path)

    if path.suffix.lower() != ".step":
        raise ValueError("The output path must use a .step extension.")

    path.parent.mkdir(parents=True, exist_ok=True)

    solid = build_bracket_geometry(case)
    solid.exportStep(str(path))

    if not path.exists() or path.stat().st_size == 0:
        raise GeometryBuildError(
            f"STEP export failed for '{path}'."
        )

    return path