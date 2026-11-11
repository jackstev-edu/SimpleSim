"""Scale reference FEA outputs to user material and load inputs.

The reference surrogate is trained at one load magnitude and Young's
modulus while Poisson's ratio remains a model input. Under the documented
linear-static assumptions, force, elastic modulus, yield strength, and
density can then be applied deterministically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ScaledBracketResponse:
    """User-condition outputs derived from one reference prediction."""

    max_stress_mpa: float
    max_disp_mm: float
    volume_mm3: float
    mass_kg: float
    factor_of_safety: float


def _positive_finite(name: str, value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{name} must be finite and greater than zero.")
    return numeric


def scale_reference_response(
    *,
    reference_max_stress_mpa: float,
    reference_max_disp_mm: float,
    volume_mm3: float,
    reference_load_force_n: float,
    reference_youngs_modulus_mpa: float,
    user_load_force_n: float,
    user_youngs_modulus_mpa: float,
    user_yield_strength_mpa: float,
    user_density_kg_m3: float,
) -> ScaledBracketResponse:
    """Apply linear-static scaling to a surrogate reference response.

    Poisson's ratio is intentionally absent from this function because it is
    an input to the surrogate itself. These equations are valid only for the
    same geometry, support, load direction, and linear-elastic assumptions.
    """

    reference_stress = _positive_finite(
        "reference_max_stress_mpa",
        reference_max_stress_mpa,
    )
    reference_displacement = _positive_finite(
        "reference_max_disp_mm",
        reference_max_disp_mm,
    )
    solid_volume = _positive_finite("volume_mm3", volume_mm3)
    reference_force = _positive_finite(
        "reference_load_force_n",
        reference_load_force_n,
    )
    reference_modulus = _positive_finite(
        "reference_youngs_modulus_mpa",
        reference_youngs_modulus_mpa,
    )
    user_force = _positive_finite("user_load_force_n", user_load_force_n)
    user_modulus = _positive_finite(
        "user_youngs_modulus_mpa",
        user_youngs_modulus_mpa,
    )
    user_yield = _positive_finite(
        "user_yield_strength_mpa",
        user_yield_strength_mpa,
    )
    user_density = _positive_finite(
        "user_density_kg_m3",
        user_density_kg_m3,
    )

    force_ratio = user_force / reference_force
    stress = reference_stress * force_ratio
    displacement = (
        reference_displacement
        * force_ratio
        * reference_modulus
        / user_modulus
    )
    mass = solid_volume * user_density * 1.0e-9
    factor_of_safety = user_yield / stress

    return ScaledBracketResponse(
        max_stress_mpa=stress,
        max_disp_mm=displacement,
        volume_mm3=solid_volume,
        mass_kg=mass,
        factor_of_safety=factor_of_safety,
    )
