"""CalculiX linear-static solver adapter for the L-bracket family v0."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .bracket_spec import BracketCaseInput
from .geometry import build_bracket_geometry, summarize_geometry
from .mesh import MeshSettings, MeshSummary, generate_mesh


class SolverDependencyError(RuntimeError):
    """Raised when a CalculiX executable cannot be found."""


class SolverExecutionError(RuntimeError):
    """Raised when deck creation, solver execution, or parsing fails."""


@dataclass(frozen=True)
class DeckSummary:
    """Information recorded while creating one CalculiX input deck."""

    deck_path: Path
    node_count: int
    volume_element_count: int
    fixed_node_count: int
    loaded_node_count: int
    loaded_area_mm2: float
    applied_force_vector_n: tuple[float, float, float]


@dataclass(frozen=True)
class ResultExtrema:
    """Maximum result values and the nodes where they occur."""

    max_displacement_mm: float
    max_displacement_node_id: int
    max_stress_mpa: float
    max_stress_node_id: int


@dataclass(frozen=True)
class SolverResult:
    """Verified outputs from one completed CalculiX analysis."""

    design_id: str
    source: str
    mesh_rule: str
    max_stress_mpa: float
    max_stress_node_id: int
    max_stress_location_mm: tuple[float, float, float]
    max_disp_mm: float
    max_disp_node_id: int
    max_disp_location_mm: tuple[float, float, float]
    factor_of_safety: float
    volume_mm3: float
    mass_kg: float
    reaction_force_n: tuple[float, float, float]
    reaction_relative_imbalance: float
    node_count: int
    volume_element_count: int
    minimum_element_quality: float
    mean_element_quality: float
    deck_path: Path
    frd_path: Path
    dat_path: Path
    log_path: Path


def find_ccx(executable: str | Path | None = None) -> str:
    """Resolve a CalculiX executable from an argument, environment, or PATH."""

    candidates: list[str] = []

    if executable is not None:
        candidates.append(str(executable))

    environment_path = os.environ.get("CALCULIX_CCX")
    if environment_path:
        candidates.append(environment_path)

    candidates.extend(["ccx", "ccx.exe", "ccx_2.23", "ccx_2.23.exe"])

    for candidate in candidates:
        candidate_path = Path(candidate)
        if candidate_path.is_file():
            return str(candidate_path.resolve())

        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    raise SolverDependencyError(
        "CalculiX was not found. Install the local prototype launcher with "
        "'python -m pip install cad-ccx', place ccx on PATH, or set the "
        "CALCULIX_CCX environment variable."
    )


def _gmsh() -> Any:
    try:
        import gmsh
    except ImportError as exc:
        raise SolverDependencyError(
            "Gmsh is required to read the generated mesh."
        ) from exc

    return gmsh


def _chunks(values: list[int], size: int = 16) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _physical_entities(gmsh: Any, name: str, dimension: int) -> list[int]:
    for group_dimension, group_tag in gmsh.model.getPhysicalGroups(dimension):
        physical_name = gmsh.model.getPhysicalName(
            group_dimension,
            group_tag,
        )

        if physical_name == name:
            return [
                int(tag)
                for tag in gmsh.model.getEntitiesForPhysicalGroup(
                    group_dimension,
                    group_tag,
                )
            ]

    raise SolverExecutionError(
        f"Required physical group '{name}' was not found in the mesh."
    )


def _triangle_area(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
    third: tuple[float, float, float],
) -> float:
    ab = tuple(second[i] - first[i] for i in range(3))
    ac = tuple(third[i] - first[i] for i in range(3))

    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )

    return 0.5 * math.sqrt(sum(component**2 for component in cross))


def _surface_area_weights(
    gmsh: Any,
    surface_entities: list[int],
    node_coordinates: dict[int, tuple[float, float, float]],
) -> tuple[dict[int, float], float]:
    """Calculate consistent nodal area weights on the loaded surface."""

    node_area_weights: dict[int, float] = {}
    total_area = 0.0

    for surface_tag in surface_entities:
        element_types, _, element_node_groups = gmsh.model.mesh.getElements(
            2,
            surface_tag,
        )

        for element_type, raw_nodes in zip(
            element_types,
            element_node_groups,
        ):
            properties = gmsh.model.mesh.getElementProperties(
                int(element_type)
            )
            element_name = properties[0]
            nodes_per_element = int(properties[3])
            nodes = [int(node) for node in raw_nodes]

            if element_name == "Triangle 3":
                load_node_indices = (0, 1, 2)
            elif element_name == "Triangle 6":
                # Consistent uniform traction for a quadratic triangle gives
                # zero integrated weight at corner nodes and A/3 at midsides.
                load_node_indices = (3, 4, 5)
            else:
                raise SolverExecutionError(
                    f"Unsupported loaded-surface element: {element_name}."
                )

            for start in range(0, len(nodes), nodes_per_element):
                connectivity = nodes[start : start + nodes_per_element]

                area = _triangle_area(
                    node_coordinates[connectivity[0]],
                    node_coordinates[connectivity[1]],
                    node_coordinates[connectivity[2]],
                )

                total_area += area

                for local_index in load_node_indices:
                    node_id = connectivity[local_index]
                    node_area_weights[node_id] = (
                        node_area_weights.get(node_id, 0.0) + area / 3.0
                    )

    if total_area <= 0.0:
        raise SolverExecutionError("The loaded surface has zero area.")

    return node_area_weights, total_area


def build_solver_deck(
    case: BracketCaseInput,
    mesh_path: str | Path,
    deck_path: str | Path,
) -> DeckSummary:
    """Convert a named Gmsh mesh into one CalculiX static-analysis deck."""

    gmsh = _gmsh()
    source_mesh_path = Path(mesh_path)
    output_deck_path = Path(deck_path)
    output_deck_path.parent.mkdir(parents=True, exist_ok=True)

    gmsh.initialize()

    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.open(str(source_mesh_path))

        node_tags, raw_coordinates, _ = gmsh.model.mesh.getNodes()

        node_coordinates = {
            int(node_tag): (
                float(raw_coordinates[3 * index]),
                float(raw_coordinates[3 * index + 1]),
                float(raw_coordinates[3 * index + 2]),
            )
            for index, node_tag in enumerate(node_tags)
        }

        volume_entities = _physical_entities(
            gmsh,
            "bracket_volume",
            3,
        )
        fixed_entities = _physical_entities(gmsh, "fixed_face", 2)
        load_entities = _physical_entities(gmsh, "load_face", 2)

        if len(volume_entities) != 1:
            raise SolverExecutionError(
                "The mesh must contain exactly one bracket volume."
            )

        element_types, element_tag_groups, element_node_groups = (
            gmsh.model.mesh.getElements(3, volume_entities[0])
        )

        volume_elements: list[tuple[int, str, list[int]]] = []

        for element_type, element_tags, raw_nodes in zip(
            element_types,
            element_tag_groups,
            element_node_groups,
        ):
            properties = gmsh.model.mesh.getElementProperties(
                int(element_type)
            )
            element_name = properties[0]
            nodes_per_element = int(properties[3])
            nodes = [int(node) for node in raw_nodes]

            if element_name == "Tetrahedron 4":
                calculix_type = "C3D4"
            elif element_name == "Tetrahedron 10":
                calculix_type = "C3D10"
            else:
                raise SolverExecutionError(
                    f"Unsupported volume element: {element_name}."
                )

            for index, element_tag in enumerate(element_tags):
                start = index * nodes_per_element
                connectivity = nodes[start : start + nodes_per_element]

                if calculix_type == "C3D10":
                    # Convert Gmsh's final two midside nodes to the
                    # Abaqus/CalculiX C3D10 ordering.
                    connectivity = (
                        connectivity[:8]
                        + [connectivity[9], connectivity[8]]
                    )

                volume_elements.append(
                    (int(element_tag), calculix_type, connectivity)
                )

        element_type_names = {item[1] for item in volume_elements}
        if len(element_type_names) != 1:
            raise SolverExecutionError(
                "Mixed volume-element types are not supported."
            )

        fixed_nodes: set[int] = set()
        for fixed_entity in fixed_entities:
            fixed_nodes.update(
                int(node)
                for node in gmsh.model.mesh.getNodes(
                    2,
                    fixed_entity,
                    True,
                    False,
                )[0]
            )

        area_weights, loaded_area_mm2 = _surface_area_weights(
            gmsh,
            load_entities,
            node_coordinates,
        )

    finally:
        gmsh.finalize()

    applied_force = case.load_vector_n

    nodal_forces = {
        node_id: tuple(
            component * area_weight / loaded_area_mm2
            for component in applied_force
        )
        for node_id, area_weight in area_weights.items()
    }

    with output_deck_path.open("w", encoding="utf-8", newline="\n") as deck:
        deck.write("*HEADING\n")
        deck.write(f"SimpleSim L-bracket: {case.design_id}\n")

        deck.write("*NODE\n")
        for node_id in sorted(node_coordinates):
            x, y, z = node_coordinates[node_id]
            deck.write(f"{node_id}, {x:.12g}, {y:.12g}, {z:.12g}\n")

        calculix_type = next(iter(element_type_names))
        deck.write(f"*ELEMENT, TYPE={calculix_type}, ELSET=EALL\n")
        for element_id, _, connectivity in volume_elements:
            deck.write(
                f"{element_id}, "
                + ", ".join(str(node) for node in connectivity)
                + "\n"
            )

        deck.write("*NSET, NSET=FIXED\n")
        for group in _chunks(sorted(fixed_nodes)):
            deck.write(", ".join(str(node) for node in group) + "\n")

        deck.write("*MATERIAL, NAME=BRACKET_MATERIAL\n")
        deck.write("*ELASTIC\n")
        deck.write(
            f"{case.material.youngs_modulus_mpa}, "
            f"{case.material.poissons_ratio}\n"
        )

        deck.write(
            "*SOLID SECTION, ELSET=EALL, MATERIAL=BRACKET_MATERIAL\n"
        )
        deck.write("*BOUNDARY\n")
        deck.write("FIXED, 1, 3, 0.0\n")

        deck.write("*STEP\n")
        deck.write("*STATIC\n")
        deck.write("*CLOAD\n")

        for node_id in sorted(nodal_forces):
            force_vector = nodal_forces[node_id]
            for direction, force_value in enumerate(force_vector, start=1):
                if abs(force_value) > 1.0e-15:
                    deck.write(
                        f"{node_id}, {direction}, {force_value:.12g}\n"
                    )

        deck.write("*NODE FILE\n")
        deck.write("U, RF\n")
        deck.write("*EL FILE\n")
        deck.write("S\n")
        deck.write("*NODE PRINT, NSET=FIXED\n")
        deck.write("RF\n")
        deck.write("*END STEP\n")

    return DeckSummary(
        deck_path=output_deck_path,
        node_count=len(node_coordinates),
        volume_element_count=len(volume_elements),
        fixed_node_count=len(fixed_nodes),
        loaded_node_count=len(nodal_forces),
        loaded_area_mm2=loaded_area_mm2,
        applied_force_vector_n=applied_force,
    )


def _parse_frd_values(
    line: str,
    value_count: int,
) -> tuple[int, list[float]]:
    node_id = int(line[3:13])
    values = [
        float(line[13 + 12 * index : 25 + 12 * index].replace("D", "E"))
        for index in range(value_count)
    ]
    return node_id, values


def parse_frd_extrema(frd_path: str | Path) -> ResultExtrema:
    """Return maximum displacement/stress and their result-node IDs."""

    displacements: dict[int, list[float]] = {}
    stresses: dict[int, list[float]] = {}
    current_section: str | None = None

    for line in Path(frd_path).read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        if line.startswith(" -4  DISP"):
            current_section = "displacement"
            continue

        if line.startswith(" -4  STRESS"):
            current_section = "stress"
            continue

        if current_section and line.startswith(" -3"):
            current_section = None
            continue

        if current_section and line.startswith(" -1"):
            value_count = 3 if current_section == "displacement" else 6
            node_id, values = _parse_frd_values(line, value_count)

            if current_section == "displacement":
                displacements[node_id] = values
            else:
                stresses[node_id] = values

    if not displacements:
        raise SolverExecutionError("No displacement results were found.")

    if not stresses:
        raise SolverExecutionError("No stress results were found.")

    displacement_values = {
        node_id: math.sqrt(sum(component**2 for component in values))
        for node_id, values in displacements.items()
    }
    max_displacement_node_id = max(
        displacement_values,
        key=displacement_values.get,
    )
    max_displacement = displacement_values[max_displacement_node_id]

    def von_mises(values: list[float]) -> float:
        sxx, syy, szz, sxy, syz, szx = values
        return math.sqrt(
            0.5
            * (
                (sxx - syy) ** 2
                + (syy - szz) ** 2
                + (szz - sxx) ** 2
            )
            + 3.0 * (sxy**2 + syz**2 + szx**2)
        )

    stress_values = {
        node_id: von_mises(values)
        for node_id, values in stresses.items()
    }
    max_stress_node_id = max(stress_values, key=stress_values.get)
    max_stress = stress_values[max_stress_node_id]

    return ResultExtrema(
        max_displacement_mm=max_displacement,
        max_displacement_node_id=max_displacement_node_id,
        max_stress_mpa=max_stress,
        max_stress_node_id=max_stress_node_id,
    )


def parse_frd_results(frd_path: str | Path) -> tuple[float, float]:
    """Return maximum displacement and stress for compatibility."""

    extrema = parse_frd_extrema(frd_path)
    return extrema.max_displacement_mm, extrema.max_stress_mpa


def parse_deck_node_coordinates(
    deck_path: str | Path,
) -> dict[int, tuple[float, float, float]]:
    """Read node coordinates from the generated CalculiX input deck."""

    coordinates: dict[int, tuple[float, float, float]] = {}
    reading_nodes = False

    for raw_line in Path(deck_path).read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        line = raw_line.strip()

        if line.upper() == "*NODE":
            reading_nodes = True
            continue

        if reading_nodes and line.startswith("*"):
            break

        if not reading_nodes or not line:
            continue

        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 4:
            raise SolverExecutionError(
                f"Invalid node row in {deck_path}: {raw_line}"
            )

        coordinates[int(fields[0])] = (
            float(fields[1]),
            float(fields[2]),
            float(fields[3]),
        )

    if not coordinates:
        raise SolverExecutionError(
            f"No node coordinates were found in {deck_path}."
        )

    return coordinates


def parse_reaction_force(dat_path: str | Path) -> tuple[float, float, float]:
    """Sum reaction-force components printed for the FIXED node set."""

    reaction_rows: list[tuple[float, float, float]] = []
    reading_reactions = False
    found_data = False

    for line in Path(dat_path).read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        if "forces (fx,fy,fz) for set FIXED" in line:
            reading_reactions = True
            continue

        if not reading_reactions:
            continue

        fields = line.split()

        if len(fields) == 4:
            try:
                int(fields[0])
                reaction_rows.append(
                    (
                        float(fields[1]),
                        float(fields[2]),
                        float(fields[3]),
                    )
                )
                found_data = True
                continue
            except ValueError:
                pass

        if found_data and not fields:
            break

    if not reaction_rows:
        raise SolverExecutionError("No fixed-support reactions were found.")

    return tuple(
        sum(row[index] for row in reaction_rows)
        for index in range(3)
    )


def solve_case(
    case: BracketCaseInput,
    output_directory: str | Path,
    mesh_settings: MeshSettings | None = None,
    ccx_executable: str | Path | None = None,
    timeout_seconds: float = 120.0,
) -> SolverResult:
    """Generate, solve, validate, and summarize one bracket analysis."""

    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    mesh_summary: MeshSummary = generate_mesh(
        case,
        output_dir,
        settings=mesh_settings,
    )

    job_stem = f"{mesh_summary.msh_path.stem}_ccx"
    deck_path = output_dir / f"{job_stem}.inp"

    build_solver_deck(
        case,
        mesh_summary.msh_path,
        deck_path,
    )

    ccx_path = find_ccx(ccx_executable)

    completed = subprocess.run(
        [ccx_path, job_stem],
        cwd=output_dir,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )

    log_path = output_dir / f"{job_stem}.log"
    log_path.write_text(
        completed.stdout + "\n" + completed.stderr,
        encoding="utf-8",
    )

    if completed.returncode != 0:
        raise SolverExecutionError(
            f"CalculiX failed with return code {completed.returncode}. "
            f"See {log_path}."
        )

    frd_path = output_dir / f"{job_stem}.frd"
    dat_path = output_dir / f"{job_stem}.dat"

    if not frd_path.exists() or not dat_path.exists():
        raise SolverExecutionError(
            "CalculiX finished without creating the required result files."
        )

    extrema = parse_frd_extrema(frd_path)
    node_coordinates = parse_deck_node_coordinates(deck_path)

    try:
        max_stress_location = node_coordinates[
            extrema.max_stress_node_id
        ]
        max_displacement_location = node_coordinates[
            extrema.max_displacement_node_id
        ]
    except KeyError as exc:
        raise SolverExecutionError(
            f"Result node {exc.args[0]} was not found in the input deck."
        ) from exc

    reaction_force = parse_reaction_force(dat_path)

    force_residual = tuple(
        reaction_force[index] + case.load_vector_n[index]
        for index in range(3)
    )

    residual_magnitude = math.sqrt(
        sum(component**2 for component in force_residual)
    )
    applied_magnitude = math.sqrt(
        sum(component**2 for component in case.load_vector_n)
    )

    reaction_relative_imbalance = residual_magnitude / applied_magnitude

    solid = build_bracket_geometry(case)
    geometry_summary = summarize_geometry(solid)
    mass_kg = (
        geometry_summary.volume_mm3
        * case.material.density_kg_m3
        * 1.0e-9
    )

    return SolverResult(
        design_id=case.design_id,
        source="calculix_2.23",
        mesh_rule=mesh_summary.mesh_rule,
        max_stress_mpa=extrema.max_stress_mpa,
        max_stress_node_id=extrema.max_stress_node_id,
        max_stress_location_mm=max_stress_location,
        max_disp_mm=extrema.max_displacement_mm,
        max_disp_node_id=extrema.max_displacement_node_id,
        max_disp_location_mm=max_displacement_location,
        factor_of_safety=(
            case.material.yield_strength_mpa / extrema.max_stress_mpa
        ),
        volume_mm3=geometry_summary.volume_mm3,
        mass_kg=mass_kg,
        reaction_force_n=reaction_force,
        reaction_relative_imbalance=reaction_relative_imbalance,
        node_count=mesh_summary.node_count,
        volume_element_count=mesh_summary.volume_element_count,
        minimum_element_quality=mesh_summary.minimum_element_quality,
        mean_element_quality=mesh_summary.mean_element_quality,
        deck_path=deck_path,
        frd_path=frd_path,
        dat_path=dat_path,
        log_path=log_path,
    )
