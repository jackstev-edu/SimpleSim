"""Gmsh mesh generation for the SimpleSim L-bracket family v0."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bracket_spec import BracketCaseInput
from .geometry import export_step


class MeshDependencyError(RuntimeError):
    """Raised when Gmsh is not installed."""


class MeshGenerationError(RuntimeError):
    """Raised when a valid geometry cannot produce a usable mesh."""


@dataclass(frozen=True)
class MeshSettings:
    """Settings for one three-dimensional tetrahedral mesh."""

    minimum_size_mm: float = 1.5
    maximum_size_mm: float = 3.0
    element_order: int = 2

    def __post_init__(self) -> None:
        if self.minimum_size_mm <= 0.0:
            raise ValueError("minimum_size_mm must be positive.")

        if self.maximum_size_mm <= 0.0:
            raise ValueError("maximum_size_mm must be positive.")

        if self.minimum_size_mm > self.maximum_size_mm:
            raise ValueError(
                "minimum_size_mm cannot exceed maximum_size_mm."
            )

        if self.element_order not in {1, 2}:
            raise ValueError("element_order must be 1 or 2.")

    @property
    def mesh_rule(self) -> str:
        return (
            f"tet{self.element_order}_"
            f"{self.minimum_size_mm:g}to"
            f"{self.maximum_size_mm:g}mm"
        )


@dataclass(frozen=True)
class MeshSummary:
    """Summary of one completed mesh-generation run."""

    mesh_rule: str
    step_path: Path
    msh_path: Path
    inp_path: Path
    node_count: int
    volume_element_count: int
    fixed_node_count: int
    load_node_count: int
    element_type_names: tuple[str, ...]
    minimum_element_quality: float
    mean_element_quality: float


def _gmsh() -> Any:
    try:
        import gmsh
    except ImportError as exc:
        raise MeshDependencyError(
            "Gmsh is required for mesh generation. "
            "Install it with: python -m pip install gmsh"
        ) from exc

    return gmsh


def _safe_file_stem(design_id: str) -> str:
    safe_name = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        design_id.strip(),
    )

    if not safe_name:
        raise ValueError(
            "design_id does not contain a valid filename character."
        )

    return safe_name


def _find_x_surface(
    gmsh: Any,
    surface_entities: list[tuple[int, int]],
    target_x_mm: float,
    tolerance_mm: float = 1.0e-5,
) -> int:
    """Find one planar surface located at a requested X coordinate."""

    matching_tags = []

    for dimension, tag in surface_entities:
        bounds = gmsh.model.getBoundingBox(dimension, tag)
        x_min = bounds[0]
        x_max = bounds[3]

        if (
            abs(x_min - target_x_mm) <= tolerance_mm
            and abs(x_max - target_x_mm) <= tolerance_mm
        ):
            matching_tags.append(tag)

    if len(matching_tags) != 1:
        raise MeshGenerationError(
            f"Expected one surface at X = {target_x_mm} mm, "
            f"but found {len(matching_tags)}."
        )

    return matching_tags[0]


def generate_mesh(
    case: BracketCaseInput,
    output_directory: str | Path,
    settings: MeshSettings | None = None,
    verbose: bool = False,
) -> MeshSummary:
    """Generate STEP, Gmsh, and CalculiX-compatible input files."""

    gmsh = _gmsh()
    mesh_settings = settings or MeshSettings()

    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_stem = _safe_file_stem(case.design_id)

    step_path = output_dir / f"{file_stem}.step"
    msh_path = output_dir / f"{file_stem}.msh"
    inp_path = output_dir / f"{file_stem}.inp"

    export_step(case, step_path)

    gmsh.initialize()

    try:
        gmsh.option.setNumber(
            "General.Terminal",
            1 if verbose else 0,
        )

        gmsh.model.add(file_stem)

        imported_entities = gmsh.model.occ.importShapes(
            str(step_path)
        )
        gmsh.model.occ.synchronize()

        volume_tags = [
            tag
            for dimension, tag in imported_entities
            if dimension == 3
        ]

        if len(volume_tags) != 1:
            raise MeshGenerationError(
                "Expected exactly one imported solid volume, "
                f"but found {len(volume_tags)}."
            )

        volume_tag = volume_tags[0]
        surface_entities = gmsh.model.getEntities(2)

        fixed_surface_tag = _find_x_surface(
            gmsh,
            surface_entities,
            target_x_mm=0.0,
        )

        load_surface_tag = _find_x_surface(
            gmsh,
            surface_entities,
            target_x_mm=case.flange_length_mm,
        )

        volume_group = gmsh.model.addPhysicalGroup(
            3,
            [volume_tag],
        )
        gmsh.model.setPhysicalName(
            3,
            volume_group,
            "bracket_volume",
        )

        fixed_group = gmsh.model.addPhysicalGroup(
            2,
            [fixed_surface_tag],
        )
        gmsh.model.setPhysicalName(
            2,
            fixed_group,
            "fixed_face",
        )

        load_group = gmsh.model.addPhysicalGroup(
            2,
            [load_surface_tag],
        )
        gmsh.model.setPhysicalName(
            2,
            load_group,
            "load_face",
        )

        gmsh.option.setNumber(
            "Mesh.MeshSizeMin",
            mesh_settings.minimum_size_mm,
        )
        gmsh.option.setNumber(
            "Mesh.MeshSizeMax",
            mesh_settings.maximum_size_mm,
        )
        gmsh.option.setNumber(
            "Mesh.ElementOrder",
            mesh_settings.element_order,
        )

        gmsh.model.mesh.generate(3)

        node_tags = gmsh.model.mesh.getNodes()[0]

        element_types, element_tag_groups, _ = (
            gmsh.model.mesh.getElements(3, volume_tag)
        )

        all_volume_element_tags = [
            int(tag)
            for group in element_tag_groups
            for tag in group
        ]

        if not all_volume_element_tags:
            raise MeshGenerationError(
                "The generated mesh contains no volume elements."
            )

        element_type_names = tuple(
            gmsh.model.mesh.getElementProperties(
                int(element_type)
            )[0]
            for element_type in element_types
        )

        quality_values = gmsh.model.mesh.getElementQualities(
            all_volume_element_tags,
            "minSICN",
        )

        fixed_node_tags = gmsh.model.mesh.getNodes(
            2,
            fixed_surface_tag,
            True,
            False,
        )[0]

        load_node_tags = gmsh.model.mesh.getNodes(
            2,
            load_surface_tag,
            True,
            False,
        )[0]

        fixed_node_count = len(
            {int(tag) for tag in fixed_node_tags}
        )
        load_node_count = len(
            {int(tag) for tag in load_node_tags}
        )

        if fixed_node_count == 0:
            raise MeshGenerationError(
                "The fixed surface contains no mesh nodes."
            )

        if load_node_count == 0:
            raise MeshGenerationError(
                "The load surface contains no mesh nodes."
            )

        gmsh.write(str(msh_path))
        gmsh.write(str(inp_path))

        summary = MeshSummary(
            mesh_rule=mesh_settings.mesh_rule,
            step_path=step_path,
            msh_path=msh_path,
            inp_path=inp_path,
            node_count=len(node_tags),
            volume_element_count=len(
                all_volume_element_tags
            ),
            fixed_node_count=fixed_node_count,
            load_node_count=load_node_count,
            element_type_names=element_type_names,
            minimum_element_quality=float(
                min(quality_values)
            ),
            mean_element_quality=float(
                sum(quality_values) / len(quality_values)
            ),
        )

    finally:
        gmsh.finalize()

    for required_path in (step_path, msh_path, inp_path):
        if (
            not required_path.exists()
            or required_path.stat().st_size == 0
        ):
            raise MeshGenerationError(
                f"Required mesh file was not created: "
                f"{required_path}"
            )

    return summary