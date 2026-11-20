"""Engines behind the emulator. Swap the engine, keep the agent, UI and contracts unchanged.

fitted     default; Role 2's power law fitted to Role 1's CalculiX runs (v0 and the gusseted v1), instant
formula    the contracts' beam formula; v0 only, a different bracket's theory, far off for this one
calculix   Role 1's CalculiX solver through its family registry (v0 and v1); needs src/data's packages
ansys      Role 3's SpaceClaim + Mechanical pipeline (src/sim/ansys_engine.py); v0 only, needs a local licensed ANSYS
discovery  the real PyDiscovery, once Ansys releases it
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Protocol

from contracts.fakes import MATERIALS, beam_formula

from src.models.power_law import PowerLawBackend

from .families import Family
from .part_file import Material

REF = MATERIALS["structural_steel"]  # the beam formula's built-in material


class BackendUnavailable(RuntimeError):
    """This engine can't run here; the message says what's missing."""


class Backend(Protocol):
    name: str  # written into Verified.source

    def solve(self, family: Family, design_mm: dict[str, float], force_n: float, material: Material) -> dict[str, float]:
        """Return max_stress_mpa, max_disp_mm and mass_kg for one design."""
        ...


class FormulaBackend:
    """Contracts' beam formula, rescaled to the part's own stiffness and density. Kept for parity with the fakes."""

    name = "formula_emulator"

    def solve(self, family, design_mm, force_n, material):
        if family.key != "l_bracket_v0":
            raise BackendUnavailable(f"The formula engine knows the v0 L-bracket only, not {family.key}. Use the fitted or CalculiX engine.")
        stress, disp, mass = beam_formula(design_mm, force_n, "structural_steel")
        disp *= REF["E_mpa"] / material.youngs_mpa  # deflection scales with 1/E
        mass *= material.density_kg_m3 * 1e-9 / REF["rho_kg_mm3"]  # mass scales with density
        return {"max_stress_mpa": stress, "max_disp_mm": disp, "mass_kg": mass}


class CalculixBackend:
    """Role 1's CadQuery, gmsh and CalculiX pipeline, imported only when used."""

    name = "calculix"
    _ids = itertools.count(1)

    def __init__(self, work_dir: str | Path = "data/processed/pydiscovery_runs", timeout_s: float = 300.0):
        self.work_dir = Path(work_dir)
        self.timeout_s = timeout_s

    def solve(self, family, design_mm, force_n, material):
        try:  # lazy so CI and teammates without the solver never import it
            from src.data.bracket_spec import MaterialProperties
            from src.data.solver import solve_case
        except ImportError as exc:
            raise BackendUnavailable(
                "The CalculiX engine needs Role 1's solver (src/data) and its packages from src/data/requirements.txt. "
                f"Import failed: {exc}"
            ) from exc
        if material.poisson is None:
            raise BackendUnavailable("CalculiX needs Poisson's ratio; set the material first")
        case_type, mesh_for, shape = self._registry(family)
        design_id = f"pyd-{next(self._ids):04d}"
        case = case_type(
            design_id=design_id,
            **design_mm,
            load_force_n=force_n,
            material=MaterialProperties(
                material_key=material.name,
                youngs_modulus_mpa=material.youngs_mpa,
                poissons_ratio=material.poisson,
                yield_strength_mpa=material.yield_mpa,
                density_kg_m3=material.density_kg_m3,
            ),
        )
        result = solve_case(case, self.work_dir / design_id, mesh_settings=mesh_for(case), timeout_seconds=self.timeout_s, family=shape)
        return {"max_stress_mpa": result.max_stress_mpa, "max_disp_mm": result.max_disp_mm, "mass_kg": result.mass_kg}

    @staticmethod
    def _registry(family):
        """Role 1's case model, production mesh rule and geometry family for one bracket family."""
        if family.key == "l_bracket_v0":
            from src.data.bracket_spec import BracketCaseInput
            from src.data.geometry import L_BRACKET_V0
            from src.data.ovat_pipeline import production_mesh_settings

            return BracketCaseInput, production_mesh_settings, L_BRACKET_V0
        if family.key == "l_bracket_gusset_v1":
            from src.data.gusset_v1.geometry import L_BRACKET_GUSSET_V1
            from src.data.gusset_v1.meshing import production_mesh_settings
            from src.data.gusset_v1.spec import GussetCaseInput

            return GussetCaseInput, production_mesh_settings, L_BRACKET_GUSSET_V1
        raise BackendUnavailable(f"Role 1's solver builds l_bracket_v0 and l_bracket_gusset_v1, not {family.key}")


class AnsysBackend:
    """Role 3's SpaceClaim + Mechanical pipeline (src/sim/ansys_engine.py), imported only when used."""

    name = "ansys_mechanical"

    def __init__(self, mesh_check: bool = False):
        self.mesh_check = mesh_check
        self._engine = None

    @property
    def last_details(self) -> dict:
        """Raw peak, zone fields, locations and notes of the last solve (Verified drops extra keys)."""
        return self._engine.last_details if self._engine is not None else {}

    def solve(self, family, design_mm, force_n, material):
        if self._engine is None:
            try:  # lazy so CI and teammates without ANSYS never import it
                from src.sim.ansys_engine import AnsysEngine
            except ImportError as exc:
                raise BackendUnavailable(
                    "The Ansys engine needs a licensed ANSYS 2026 R1 with PyMechanical and PyAnsys Geometry "
                    f"(Role 3, src/sim). Import failed: {exc}"
                ) from exc
            self._engine = AnsysEngine(mesh_check=self.mesh_check)
        return self._engine.solve(family, design_mm, force_n, material)

    def close(self) -> None:
        if self._engine is not None:
            self._engine.close()
            self._engine = None


class DiscoveryBackend:
    """The only class to replace when Ansys releases PyDiscovery."""

    name = "ansys_discovery"

    def solve(self, family, design_mm, force_n, material):
        raise NotImplementedError("PyDiscovery is not released yet")


BACKENDS = {"fitted": PowerLawBackend, "formula": FormulaBackend, "calculix": CalculixBackend, "ansys": AnsysBackend, "discovery": DiscoveryBackend}


def get_backend(name: str) -> Backend:
    if name not in BACKENDS:
        raise ValueError(f"Unknown engine {name!r}. Choose from {sorted(BACKENDS)}.")
    return BACKENDS[name]()
