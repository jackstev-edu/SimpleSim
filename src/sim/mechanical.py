"""Solve a part with ANSYS Mechanical (PyMechanical, embedding mode).

Input is a .pmdb from GeometrySession.save with named selections for the
supports and loads. Units are N, mm, MPa throughout.

Every result carries plausibility checks: the support reaction must balance the
applied load, and `refinement_check` re-solves on a finer mesh to flag
stress values that don't converge (singularities at sharp corners).
"""
from __future__ import annotations

import time
from pathlib import Path

from ansys.mechanical.core import App

from src.sim import materials

# PyMechanical's embedding injects Model, Quantity, enums etc. into these globals.
_app: App | None = None


def _get_app(version: int = 261) -> App:
    global _app
    if _app is None:
        _app = App(version=version, globals=globals())
    return _app


def shutdown() -> None:
    """Dispose the embedded Mechanical. Call it on the thread that started it:
    PyMechanical's own atexit hook disposes from the main thread, which deadlocks
    when Mechanical was started on a worker thread (seen 2026-10-30)."""
    global _app
    if _app is not None:
        _app._dispose()  # private, but it is exactly what PyMechanical's atexit hook calls
        _app = None


def solve(
    pmdb: str | Path,
    fixed: str,
    load: str,
    force_N: tuple[float, float, float],
    element_size_mm: float = 2.0,
    local_size: tuple[str, float] | None = None,
    material: dict | None = None,
) -> dict:
    """Fix the faces in named selection `fixed`, apply `force_N` on `load`, solve.

    local_size = (named selection, size in mm) refines the mesh on those faces only,
    e.g. fillets, which a global mesh fine enough to resolve would exceed the node limit.
    material = an entry from materials.get(); None keeps Mechanical's default Structural Steel."""
    app = _get_app()
    t0 = time.time()
    app.new()
    model = app.Model
    geometry_import = model.GeometryImportGroup.AddGeometryImport()
    prefs = Ansys.ACT.Mechanical.Utilities.GeometryImportPreferences()  # noqa: F821
    prefs.ProcessNamedSelections = True
    geometry_import.Import(
        str(pmdb), Ansys.Mechanical.DataModel.Enums.GeometryImportPreference.Format.Automatic, prefs  # noqa: F821
    )
    ExtAPI.Application.ActiveUnitSystem = MechanicalUnitSystem.StandardNMM  # noqa: F821
    ns = {n.Name: n for n in model.NamedSelections.Children}
    missing = {fixed, load, *([local_size[0]] if local_size else [])} - ns.keys()
    if missing:
        raise KeyError(f"Named selections {sorted(missing)} not in {pmdb} (found {sorted(ns)})")

    if material is not None and material["name"] != materials.DEFAULT:
        model.Materials.Import(str(materials.library_dir() / material["library"]))
        body = model.Geometry.GetChildren(Ansys.Mechanical.DataModel.Enums.DataModelObjectCategory.Body, True)[0]  # noqa: F821
        body.Material = material["name"]
        if body.Material != material["name"]:
            raise RuntimeError(f"Mechanical did not assign material {material['name']!r} (got {body.Material!r})")

    analysis = model.AddStaticStructuralAnalysis()
    model.Mesh.ElementSize = Quantity(f"{element_size_mm} [mm]")  # noqa: F821
    if local_size:
        sizing = model.Mesh.AddSizing()
        sizing.Location = ns[local_size[0]]
        sizing.ElementSize = Quantity(f"{local_size[1]} [mm]")  # noqa: F821
    model.Mesh.GenerateMesh()

    support = analysis.AddFixedSupport()
    support.Location = ns[fixed]
    force = analysis.AddForce()
    force.Location = ns[load]
    force.DefineBy = LoadDefineBy.Components  # noqa: F821
    for comp, value in zip((force.XComponent, force.YComponent, force.ZComponent), force_N):
        comp.Output.DiscreteValues = [Quantity(f"{value} [N]")]  # noqa: F821

    sol = analysis.Solution
    deformation = sol.AddTotalDeformation()
    stress = sol.AddEquivalentStress()
    reaction = sol.AddForceReaction()
    reaction.BoundaryConditionSelection = support
    sol.Solve(True)

    reaction_N = [reaction.XAxis.Value, reaction.YAxis.Value, reaction.ZAxis.Value]
    # The reaction should equal minus the applied load. Mechanical reports reactions
    # with the sign convention "force the support exerts", so compare magnitudes per axis.
    applied = sum(f * f for f in force_N) ** 0.5
    residual = sum((abs(r) - abs(f)) ** 2 for r, f in zip(reaction_N, force_N)) ** 0.5
    return {
        "status": str(sol.Status),
        "material": material["name"] if material else materials.DEFAULT,
        "nodes": model.Mesh.Nodes,
        "element_size_mm": element_size_mm,
        "local_size_mm": local_size[1] if local_size else None,
        "max_stress_MPa": stress.Maximum.Value,
        "max_stress_at_mm": _peak_location(analysis, stress),
        "max_def_mm": deformation.Maximum.Value,
        "reaction_N": reaction_N,
        "balance_error": residual / applied if applied else 0.0,
        "messages": [m.DisplayString for m in ExtAPI.Application.Messages],  # noqa: F821
        "solve_s": round(time.time() - t0, 1),
    }


def _peak_location(analysis, stress) -> list[float]:
    """Coordinates (mm) of the node with the highest equivalent stress.

    PlotData and MeshData are in solver SI units (Pa, m) regardless of the active unit
    system (seen 2026-10-31), so the peak value is cross-checked against stress.Maximum
    (MPa) to catch a unit change in a future release."""
    pd = stress.PlotData
    nodes, vals = list(pd["Node"]), list(pd["Values"])
    i = max(range(len(vals)), key=vals.__getitem__)
    if abs(vals[i] / 1e6 - stress.Maximum.Value) > 1e-3 * abs(stress.Maximum.Value):
        raise RuntimeError(f"Peak-location units look wrong: PlotData max {vals[i]} vs "
                           f"Maximum {stress.Maximum.Value} MPa")
    node = analysis.MeshData.NodeById(nodes[i])
    return [round(c * 1000, 2) for c in (node.X, node.Y, node.Z)]


def refinement_check(pmdb: str | Path, fixed: str, load: str, force_N, element_size_mm: float = 2.0,
                     tolerance: float = 0.10, local_size: tuple[str, float] | None = None,
                     material: dict | None = None, on_solve=None) -> dict:
    """Re-solve with half the element size (global and local). A max stress change above
    `tolerance` means the peak is mesh-dependent, typically a singularity at a sharp corner.
    `on_solve("coarse" | "fine", result)` is called after each solve (progress reporting)."""
    coarse = solve(pmdb, fixed, load, force_N, element_size_mm, local_size, material)
    if on_solve:
        on_solve("coarse", coarse)
    fine_local = (local_size[0], local_size[1] / 2) if local_size else None
    fine = solve(pmdb, fixed, load, force_N, element_size_mm / 2, fine_local, material)
    if on_solve:
        on_solve("fine", fine)
    change = abs(fine["max_stress_MPa"] - coarse["max_stress_MPa"]) / coarse["max_stress_MPa"]
    return {"coarse": coarse, "fine": fine, "stress_change": change, "converged": change <= tolerance}
