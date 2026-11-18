"""Executable input specification for the SimpleSim L-bracket family v0.

This module belongs to the Role 1 simulation-data pipeline. It defines
the supported inputs, fixed geometry values, and validation rules without
modifying the shared hand-off contracts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Fixed geometry verified from the baseline l_bracket.scdocx file.
VERTICAL_HEIGHT_MM = 60.0
HOLE_CENTER_X_MM = 60.0
HOLE_CENTER_Z_MM = 20.0

# Fixed v0 load convention.
LOAD_ANGLE_DEG = -90.0

# Initial Role 1 pilot ranges.
THICKNESS_RANGE_MM = (4.0, 10.0)
FLANGE_LENGTH_RANGE_MM = (75.0, 120.0)
ARM_WIDTH_RANGE_MM = (32.0, 60.0)
HOLE_DIAMETER_RANGE_MM = (6.0, 12.0)
FILLET_RADIUS_RANGE_MM = (2.0, 8.0)
LOAD_FORCE_RANGE_N = (100.0, 1000.0)


class MaterialProperties(BaseModel):
    """User-supplied isotropic linear-elastic material properties."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )

    material_key: str = Field(min_length=1)
    youngs_modulus_mpa: float = Field(gt=0.0)
    poissons_ratio: float = Field(gt=0.0, lt=0.5)
    yield_strength_mpa: float = Field(gt=0.0)
    density_kg_m3: float = Field(gt=0.0)


class BracketCaseInput(BaseModel):
    """Validated inputs for one v0 L-bracket analysis case."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
    )

    design_id: str = Field(min_length=1)

    thickness_mm: float = Field(
        ge=THICKNESS_RANGE_MM[0],
        le=THICKNESS_RANGE_MM[1],
    )
    flange_length_mm: float = Field(
        ge=FLANGE_LENGTH_RANGE_MM[0],
        le=FLANGE_LENGTH_RANGE_MM[1],
    )
    arm_width_mm: float = Field(
        ge=ARM_WIDTH_RANGE_MM[0],
        le=ARM_WIDTH_RANGE_MM[1],
    )
    hole_diameter_mm: float = Field(
        ge=HOLE_DIAMETER_RANGE_MM[0],
        le=HOLE_DIAMETER_RANGE_MM[1],
    )
    fillet_radius_mm: float = Field(
        ge=FILLET_RADIUS_RANGE_MM[0],
        le=FILLET_RADIUS_RANGE_MM[1],
    )

    load_force_n: float = Field(
        ge=LOAD_FORCE_RANGE_N[0],
        le=LOAD_FORCE_RANGE_N[1],
    )

    material: MaterialProperties

    @model_validator(mode="after")
    def validate_hole_location(self) -> "BracketCaseInput":
        """Confirm that the fixed hole remains inside the horizontal leg."""

        radius = self.hole_diameter_mm / 2.0

        if HOLE_CENTER_X_MM - radius <= 0.0:
            raise ValueError("The hole intersects the X = 0 boundary.")

        if HOLE_CENTER_X_MM + radius >= self.flange_length_mm:
            raise ValueError("The hole intersects the horizontal free-end face.")

        if HOLE_CENTER_Z_MM - radius <= 0.0:
            raise ValueError("The hole intersects the Z = 0 side face.")

        if HOLE_CENTER_Z_MM + radius >= self.arm_width_mm:
            raise ValueError("The hole intersects the opposite side face.")

        return self

    @property
    def load_vector_n(self) -> tuple[float, float, float]:
        """Return the fixed downward v0 load vector."""

        return (0.0, -self.load_force_n, 0.0)

    def to_solver_payload(self) -> dict[str, Any]:
        """Return one complete input payload for a future solver adapter."""

        return {
            "design_id": self.design_id,
            "geometry": {
                "vertical_height_mm": VERTICAL_HEIGHT_MM,
                "thickness_mm": self.thickness_mm,
                "flange_length_mm": self.flange_length_mm,
                "arm_width_mm": self.arm_width_mm,
                "hole_diameter_mm": self.hole_diameter_mm,
                "hole_center_x_mm": HOLE_CENTER_X_MM,
                "hole_center_z_mm": HOLE_CENTER_Z_MM,
                "fillet_radius_mm": self.fillet_radius_mm,
            },
            "load": {
                "force_n": self.load_force_n,
                "angle_deg": LOAD_ANGLE_DEG,
                "vector_n": self.load_vector_n,
                "surface": "horizontal_free_end",
            },
            "support": {
                "surface": "vertical_back_face",
                "x_mm": 0.0,
                "constraint": "fully_fixed",
            },
            "material": self.material.model_dump(),
        }


# Working baseline values for Structural Steel.
# These values must later be confirmed against the selected solver material card.
WORKING_STRUCTURAL_STEEL = MaterialProperties(
    material_key="structural_steel_working",
    youngs_modulus_mpa=200000.0,
    poissons_ratio=0.30,
    yield_strength_mpa=250.0,
    density_kg_m3=7850.0,
)


def make_baseline_case() -> BracketCaseInput:
    """Create the verified baseline geometry and loading case."""

    return BracketCaseInput(
        design_id="baseline_l_bracket_v0",
        thickness_mm=6.0,
        flange_length_mm=80.0,
        arm_width_mm=40.0,
        hole_diameter_mm=8.0,
        fillet_radius_mm=4.0,
        load_force_n=500.0,
        material=WORKING_STRUCTURAL_STEEL,
    )
    