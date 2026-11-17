"""The v1 mesh rule and its three study levels.

v0 sized elements uniformly (max = min(2, t/3), min = max/2). On the gusseted bracket the peak
stress sits on the 2 mm junction fillet, which a uniform 1 to 2 mm mesh under-resolves by about
13%, so v1 adds Gmsh's curvature sizing: elements on fillets and the hole follow their radius.
The study levels scale the same rule, so the Gate 2 choice is one constant: PRODUCTION_LEVEL.
"""

from __future__ import annotations

from ..mesh import MeshSettings
from .spec import GussetCaseInput

MAX_SIZE_CAP_MM = 3.0
THICKNESS_DIVISOR = 2.0  # at least two quadratic elements through the plate thickness
MIN_SIZE_RATIO = 4.0  # minimum size = maximum size / 4, the floor for curvature refinement
CURVATURE_ELEMENTS = 16  # elements per full circle on curved faces at scale 1

# Level name -> scale on the element sizes. Curvature elements scale the other way.
MESH_LEVELS: dict[str, float] = {"coarse": 1.5, "medium": 1.0, "fine": 2.0 / 3.0}
PRODUCTION_LEVEL = "medium"  # chosen at Gate 2 from the mesh study; see the family doc
NONPOSITIVE_JACOBIAN_RETRY_SCALE = 0.75  # v0's one-step refinement after an inverted tet10


def mesh_settings_for(case: GussetCaseInput, scale: float) -> MeshSettings:
    """The rule at one scale: max = scale * min(3, t/2, gusset thickness), min = max/4, curvature 16/scale."""
    base_max = min(MAX_SIZE_CAP_MM, case.thickness_mm / THICKNESS_DIVISOR, case.gusset_thickness_mm)
    maximum = base_max * scale
    return MeshSettings(
        minimum_size_mm=maximum / MIN_SIZE_RATIO,
        maximum_size_mm=maximum,
        element_order=2,
        curvature_elements=max(4, round(CURVATURE_ELEMENTS / scale)),
        algorithm_3d=10,  # HXT: Delaunay left inverted curved tet10s on about 1 design in 10
        high_order_optimize=2,
    )


def level_mesh_settings(case: GussetCaseInput, level: str = PRODUCTION_LEVEL) -> MeshSettings:
    if level not in MESH_LEVELS:
        raise ValueError(f"Unknown mesh level {level!r}. Choose from {list(MESH_LEVELS)}.")
    return mesh_settings_for(case, MESH_LEVELS[level])


def production_mesh_settings(case: GussetCaseInput) -> MeshSettings:
    return level_mesh_settings(case, PRODUCTION_LEVEL)


def retry_mesh_settings(case: GussetCaseInput) -> MeshSettings:
    """One step finer than production, used once after a nonpositive Jacobian (v0 policy)."""
    return mesh_settings_for(case, MESH_LEVELS[PRODUCTION_LEVEL] * NONPOSITIVE_JACOBIAN_RETRY_SCALE)


def describe_rule(level: str = PRODUCTION_LEVEL) -> str:
    scale = MESH_LEVELS[level]
    return (f"tet2; maximum_size_mm = {scale:g} * min({MAX_SIZE_CAP_MM:g}, thickness_mm/{THICKNESS_DIVISOR:g}, gusset_thickness_mm); "
            f"minimum_size_mm = maximum_size_mm/{MIN_SIZE_RATIO:g}; Mesh.MeshSizeFromCurvature = {max(4, round(CURVATURE_ELEMENTS / scale))}; "
            f"Mesh.Algorithm3D = 10 (HXT); Mesh.HighOrderOptimize = 2 (level {level})")
