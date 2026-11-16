"""Input specification for the gusseted L-bracket family v1 (l_bracket_gusset_v1).

The v0 L-bracket plus one triangular 45 degree gusset rib at the inside corner, centred across
the width. Three variables join the five v0 ones: the vertical-leg height (fixed at 60 mm in
v0), the gusset thickness and the gusset leg length. Every geometry rule has a name, so a
rejected design says which rule it broke. See docs/bracket_family_v1_l_bracket_gusset_v1.md.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..bracket_spec import (
    ARM_WIDTH_RANGE_MM,
    FILLET_RADIUS_RANGE_MM,
    FLANGE_LENGTH_RANGE_MM,
    HOLE_CENTER_X_MM,
    HOLE_CENTER_Z_MM,
    HOLE_DIAMETER_RANGE_MM,
    LOAD_ANGLE_DEG,
    LOAD_FORCE_RANGE_N,
    THICKNESS_RANGE_MM,
    MaterialProperties,
    WORKING_STRUCTURAL_STEEL,
)

FAMILY_KEY = "l_bracket_gusset_v1"
DATASET_SCHEMA_VERSION = "role1_model_1.0.0"

# Fixed analysis values. The junction fillet rounds every edge where the gusset meets the legs,
# for the same reason v0 rounds the inside corner: a sharp re-entrant edge has no converged stress.
GUSSET_JUNCTION_FILLET_MM = 2.0
HOLE_CLEARANCE_MM = 3.0  # gap between the gusset footprint (fillet included) and the hole edge
TOP_CLEARANCE_MM = 3.0  # gap between the gusset tip and the top of the vertical leg
SIDE_CLEARANCE_MM = 3.0  # gap between the gusset (fillet included) and the bracket side faces
FILLET_CLEARANCE_MM = 2.0  # how far the gusset must reach past the inside fillet

# v0 ranges are kept as they are; the three new variables get ranges that include the baseline.
VERTICAL_HEIGHT_RANGE_MM = (50.0, 90.0)
GUSSET_THICKNESS_RANGE_MM = (3.0, 10.0)
GUSSET_LENGTH_RANGE_MM = (12.0, 35.0)
POISSONS_RATIO_RANGE = (0.25, 0.35)

GEOMETRY_RANGES_MM: dict[str, tuple[float, float]] = {
    "thickness_mm": THICKNESS_RANGE_MM,
    "flange_length_mm": FLANGE_LENGTH_RANGE_MM,
    "arm_width_mm": ARM_WIDTH_RANGE_MM,
    "hole_diameter_mm": HOLE_DIAMETER_RANGE_MM,
    "fillet_radius_mm": FILLET_RADIUS_RANGE_MM,
    "vertical_height_mm": VERTICAL_HEIGHT_RANGE_MM,
    "gusset_thickness_mm": GUSSET_THICKNESS_RANGE_MM,
    "gusset_length_mm": GUSSET_LENGTH_RANGE_MM,
}
GEOMETRY_NAMES = tuple(GEOMETRY_RANGES_MM)
NEW_GEOMETRY_NAMES = ("vertical_height_mm", "gusset_thickness_mm", "gusset_length_mm")
SAMPLED_RANGES: dict[str, tuple[float, float]] = {**GEOMETRY_RANGES_MM, "poissons_ratio": POISSONS_RATIO_RANGE}

BASELINE_MM: dict[str, float] = {
    "thickness_mm": 6.0,
    "flange_length_mm": 80.0,
    "arm_width_mm": 40.0,
    "hole_diameter_mm": 8.0,
    "fillet_radius_mm": 4.0,
    "vertical_height_mm": 60.0,
    "gusset_thickness_mm": 6.0,
    "gusset_length_mm": 20.0,
}


class GussetCaseInput(BaseModel):
    """Validated inputs for one v1 gusseted L-bracket analysis case."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    design_id: str = Field(min_length=1)
    thickness_mm: float = Field(ge=THICKNESS_RANGE_MM[0], le=THICKNESS_RANGE_MM[1])
    flange_length_mm: float = Field(ge=FLANGE_LENGTH_RANGE_MM[0], le=FLANGE_LENGTH_RANGE_MM[1])
    arm_width_mm: float = Field(ge=ARM_WIDTH_RANGE_MM[0], le=ARM_WIDTH_RANGE_MM[1])
    hole_diameter_mm: float = Field(ge=HOLE_DIAMETER_RANGE_MM[0], le=HOLE_DIAMETER_RANGE_MM[1])
    fillet_radius_mm: float = Field(ge=FILLET_RADIUS_RANGE_MM[0], le=FILLET_RADIUS_RANGE_MM[1])
    vertical_height_mm: float = Field(ge=VERTICAL_HEIGHT_RANGE_MM[0], le=VERTICAL_HEIGHT_RANGE_MM[1])
    gusset_thickness_mm: float = Field(ge=GUSSET_THICKNESS_RANGE_MM[0], le=GUSSET_THICKNESS_RANGE_MM[1])
    gusset_length_mm: float = Field(ge=GUSSET_LENGTH_RANGE_MM[0], le=GUSSET_LENGTH_RANGE_MM[1])
    load_force_n: float = Field(ge=LOAD_FORCE_RANGE_N[0], le=LOAD_FORCE_RANGE_N[1])
    material: MaterialProperties

    @model_validator(mode="after")
    def validate_geometry_rules(self) -> "GussetCaseInput":
        problems = failed_rules(self)
        if problems:
            raise ValueError(" ".join(problems))
        return self

    @property
    def load_vector_n(self) -> tuple[float, float, float]:
        """The fixed -Y load, as in v0."""
        return (0.0, -self.load_force_n, 0.0)

    @property
    def gusset_reach_mm(self) -> float:
        """How far from the back face (or the base) the gusset footprint extends, fillet included."""
        return self.thickness_mm + self.gusset_length_mm + GUSSET_JUNCTION_FILLET_MM

    def geometry_mm(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in GEOMETRY_NAMES}


# ------------------------------------------------------------------ named validity rules
# Each rule takes anything with the geometry attributes and returns None, or the reason it fails.

def rule_hole_inside_flange(g: Any) -> str | None:
    radius = g.hole_diameter_mm / 2.0
    if HOLE_CENTER_X_MM + radius >= g.flange_length_mm:
        return "the hole intersects the free-end face"
    if HOLE_CENTER_Z_MM - radius <= 0.0 or HOLE_CENTER_Z_MM + radius >= g.arm_width_mm:
        return "the hole intersects a side face"
    return None


def rule_gusset_clears_hole(g: Any) -> str | None:
    """Gusset footprint plus junction fillet stays HOLE_CLEARANCE_MM short of the hole edge."""
    reach = g.thickness_mm + g.gusset_length_mm + GUSSET_JUNCTION_FILLET_MM
    hole_edge = HOLE_CENTER_X_MM - g.hole_diameter_mm / 2.0
    gap = hole_edge - reach
    if gap < HOLE_CLEARANCE_MM:
        return f"the gusset ends {gap:.2f} mm from the hole edge; at least {HOLE_CLEARANCE_MM:g} mm is required"
    return None


def rule_gusset_clears_vertical_top(g: Any) -> str | None:
    reach = g.thickness_mm + g.gusset_length_mm + GUSSET_JUNCTION_FILLET_MM
    gap = g.vertical_height_mm - reach
    if gap < TOP_CLEARANCE_MM:
        return f"the gusset ends {gap:.2f} mm below the top of the vertical leg; at least {TOP_CLEARANCE_MM:g} mm is required"
    return None


def rule_gusset_within_width(g: Any) -> str | None:
    half_span = g.gusset_thickness_mm / 2.0 + GUSSET_JUNCTION_FILLET_MM
    gap = g.arm_width_mm / 2.0 - half_span
    if gap < SIDE_CLEARANCE_MM:
        return f"the gusset and its fillet come within {gap:.2f} mm of the side faces; at least {SIDE_CLEARANCE_MM:g} mm is required"
    return None


def rule_gusset_past_fillet(g: Any) -> str | None:
    needed = g.fillet_radius_mm + GUSSET_JUNCTION_FILLET_MM + FILLET_CLEARANCE_MM
    if g.gusset_length_mm < needed:
        return f"the gusset leg of {g.gusset_length_mm:.2f} mm must reach at least {needed:.2f} mm past the inside corner to clear the fillet"
    return None


RULES: tuple[tuple[str, Callable[[Any], str | None]], ...] = (
    ("hole_inside_flange", rule_hole_inside_flange),
    ("gusset_clears_hole", rule_gusset_clears_hole),
    ("gusset_clears_vertical_top", rule_gusset_clears_vertical_top),
    ("gusset_within_width", rule_gusset_within_width),
    ("gusset_past_fillet", rule_gusset_past_fillet),
)


def failed_rules(geometry: Any) -> list[str]:
    """Names and reasons of every geometry rule this design breaks."""
    out = []
    for name, rule in RULES:
        reason = rule(geometry)
        if reason:
            out.append(f"rule {name}: {reason}.")
    return out


# ------------------------------------------------------------------ reference cases

REFERENCE_MATERIAL_KEY = "role1_isotropic_reference"


def reference_material(poissons_ratio: float = 0.30) -> MaterialProperties:
    """E 200,000 MPa, Sy 250 MPa, rho 7,850 kg/m3 with the sampled Poisson's ratio."""
    return MaterialProperties(
        material_key=REFERENCE_MATERIAL_KEY,
        youngs_modulus_mpa=WORKING_STRUCTURAL_STEEL.youngs_modulus_mpa,
        poissons_ratio=poissons_ratio,
        yield_strength_mpa=WORKING_STRUCTURAL_STEEL.yield_strength_mpa,
        density_kg_m3=WORKING_STRUCTURAL_STEEL.density_kg_m3,
    )


def make_case(design_id: str, geometry_mm: dict[str, float], poissons_ratio: float = 0.30,
              load_force_n: float = 500.0) -> GussetCaseInput:
    return GussetCaseInput(design_id=design_id, load_force_n=load_force_n,
                           material=reference_material(poissons_ratio), **geometry_mm)


def make_baseline_case() -> GussetCaseInput:
    """The v0 baseline bracket with a 20 mm by 6 mm gusset."""
    return make_case("baseline_l_bracket_gusset_v1", BASELINE_MM)


# The two most extreme corners of the design space, for the mesh study.
THINNEST_LONGEST_MM: dict[str, float] = {
    "thickness_mm": 4.0, "flange_length_mm": 120.0, "arm_width_mm": 32.0, "hole_diameter_mm": 12.0,
    "fillet_radius_mm": 2.0, "vertical_height_mm": 90.0, "gusset_thickness_mm": 3.0, "gusset_length_mm": 35.0,
}
THICKEST_SHORTEST_MM: dict[str, float] = {
    "thickness_mm": 10.0, "flange_length_mm": 75.0, "arm_width_mm": 60.0, "hole_diameter_mm": 6.0,
    "fillet_radius_mm": 8.0, "vertical_height_mm": 50.0, "gusset_thickness_mm": 10.0, "gusset_length_mm": 12.0,
}

__all__ = [
    "FAMILY_KEY", "DATASET_SCHEMA_VERSION", "GUSSET_JUNCTION_FILLET_MM", "HOLE_CLEARANCE_MM", "TOP_CLEARANCE_MM",
    "SIDE_CLEARANCE_MM", "FILLET_CLEARANCE_MM", "GEOMETRY_RANGES_MM", "GEOMETRY_NAMES", "NEW_GEOMETRY_NAMES",
    "SAMPLED_RANGES", "BASELINE_MM", "POISSONS_RATIO_RANGE", "LOAD_ANGLE_DEG", "HOLE_CENTER_X_MM", "HOLE_CENTER_Z_MM",
    "GussetCaseInput", "RULES", "failed_rules", "reference_material", "make_case", "make_baseline_case",
    "THINNEST_LONGEST_MM", "THICKEST_SHORTEST_MM", "REFERENCE_MATERIAL_KEY",
]
