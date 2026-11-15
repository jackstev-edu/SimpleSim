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

import numpy as np

from src.sim import materials, zones

# PyMechanical's embedding injects Model, Quantity, enums etc. into these globals.
_app: App | None = None

# Ansys Student size limit (structural). Above it MAPDL stops with "The version size limits (NODES)
# have been exceeded", and its distributed worker processes never exit, so sol.Solve hangs forever
# (seen 2026-11-01). So the mesh is checked here and an oversized design is never sent to the solver.
STUDENT_NODE_LIMIT = 128_000


class MeshTooLarge(ValueError):
    """The mesh exceeds the Ansys Student node/element limit; the design was not solved."""


class SolveFailed(RuntimeError):
    """Mechanical did not finish the solve; there are no results for this design."""


def check_solved(status: str, messages: list[str], solver_output: str | None) -> None:
    """Raise SolveFailed unless the solution status is Done.

    A failed solve leaves stress, deflection and reaction at 0.0. Returned as a result, that became
    a ZeroDivisionError in the safety factor far from the cause (seen 2026-11-11 to 2026-11-13).
    """
    if status == "Done":
        return
    raise SolveFailed(f"Design NOT solved: Mechanical solution status is {status}, not Done. "
                      f"Messages: {messages or 'none'}. Solver output (last lines): {solver_output or 'not available'}")


def _solver_output_tail(analysis, lines: int = 25) -> str | None:
    """The end of MAPDL's solve.out for this analysis, where it states why it stopped."""
    try:
        out = Path(str(analysis.WorkingDir)) / "solve.out"
        text = out.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(ln for ln in text[-lines:] if ln.strip())
    except Exception:  # noqa: BLE001 - diagnostics only; the SolveFailed message says "not available"
        return None


def check_mesh_size(nodes: int, elements: int, element_size_mm: float,
                    local_size: tuple[str, float] | None, limit: int = STUDENT_NODE_LIMIT) -> None:
    """Raise MeshTooLarge, with what to change, when the mesh is over the Student limit."""
    if nodes <= limit and elements <= limit:
        return
    local = f" and {local_size[1]} mm on the fillets" if local_size else ""
    raise MeshTooLarge(
        f"Mesh too large for the Ansys Student licence: {nodes:,} nodes / {elements:,} elements "
        f"(limit {limit:,}) at {element_size_mm} mm{local}. The design was NOT solved. "
        "Use a coarser element size (or a larger fillet radius, which sets a coarser fillet mesh).")


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
    save_to: str | Path | None = None,
    guard: str | None = None,
    min_wall_mm: float | None = None,
    zone_mm: float | None = None,
) -> dict:
    """Fix the faces in named selection `fixed`, apply `force_N` on `load`, solve.

    local_size = (named selection, size in mm) refines the mesh on those faces only,
    e.g. fillets, which a global mesh fine enough to resolve would exceed the node limit.
    material = an entry from materials.get(); None keeps Mechanical's default Structural Steel.
    save_to = a .mechdb path: save the solved analysis there so it can be opened in Mechanical.
    guard = optional named selection whose stress must never be excluded (may be absent from the part).
    min_wall_mm sets the zone step (half of it); zone_mm fixes the zone size (no search)."""
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
    check_mesh_size(model.Mesh.Nodes, model.Mesh.Elements, element_size_mm, local_size)

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
    check_solved(str(sol.Status), [m.DisplayString for m in ExtAPI.Application.Messages],  # noqa: F821
                 _solver_output_tail(analysis))

    peak_at, node_ids, values_pa = _peak_location(analysis, stress)
    zone, zone_msg = _zone_from_mesh(analysis, [ns[fixed], ns[load], ns.get(guard) if guard else None],
                                     node_ids, values_pa, zones.step_for(min_wall_mm, element_size_mm),
                                     zone_mm, stress.Maximum.Value)
    reaction_N = [reaction.XAxis.Value, reaction.YAxis.Value, reaction.ZAxis.Value]
    # The reaction should equal minus the applied load. Mechanical reports reactions
    # with the sign convention "force the support exerts", so compare magnitudes per axis.
    applied = sum(f * f for f in force_N) ** 0.5
    residual = sum((abs(r) - abs(f)) ** 2 for r, f in zip(reaction_N, force_N)) ** 0.5
    if save_to is not None:  # the full analysis (mesh, supports, load, results) for the user to open in Mechanical
        app.save_as(str(save_to), overwrite=True)
    return {
        "status": str(sol.Status),
        "material": material["name"] if material else materials.DEFAULT,
        "nodes": model.Mesh.Nodes,
        "element_size_mm": element_size_mm,
        "local_size_mm": local_size[1] if local_size else None,
        "max_stress_MPa": stress.Maximum.Value,
        "max_stress_at_mm": peak_at,
        "max_def_mm": deformation.Maximum.Value,
        "reaction_N": reaction_N,
        "balance_error": residual / applied if applied else 0.0,
        "messages": [m.DisplayString for m in ExtAPI.Application.Messages]  # noqa: F821
                    + ([] if peak_at else ["Peak-stress location could not be read (max_stress_at_mm is null); "
                                           "the stress and deflection values are valid."])
                    + ([zone_msg] if zone_msg else []),
        **zone,
        "solve_s": round(time.time() - t0, 1),
    }


def _peak_location(analysis, stress):
    """(peak coords in mm or None, node ids, stress values in Pa) from one PlotData read.

    PlotData and MeshData are in solver SI units (Pa, m) regardless of the active unit
    system (seen 2026-10-31), so the peak value is cross-checked against stress.Maximum
    (MPa) to catch a unit change in a future release.
    Reading PlotData fails now and then with "PlotData is not supported for Equivalent
    Stress" on a design that reads fine on a re-run (seen 2026-11-11, cause unknown). The
    location is extra information, so try twice and then give None and empty arrays."""
    for attempt in (1, 2):
        try:
            pd = stress.PlotData
            nodes, vals = list(pd["Node"]), list(pd["Values"])
            break
        except Exception:  # .NET NotImplementedException from Mechanical
            if attempt == 2:
                return None, [], []
            time.sleep(1)
    i = max(range(len(vals)), key=vals.__getitem__)
    if abs(vals[i] / 1e6 - stress.Maximum.Value) > 1e-3 * abs(stress.Maximum.Value):
        raise RuntimeError(f"Peak-location units look wrong: PlotData max {vals[i]} vs "
                           f"Maximum {stress.Maximum.Value} MPa")
    node = analysis.MeshData.NodeById(nodes[i])
    return [round(c * 1000, 2) for c in (node.X, node.Y, node.Z)], nodes, vals


def _zone_fields(nodes: list[int], values_pa: list[float], coords_m: dict[int, tuple],
                 support_ids: set[int], load_ids: set[int], guard_ids: set[int],
                 step_mm: float, zone_mm: float | None, max_stress_mpa: float) -> dict:
    """Exclusion-zone fields from SI node data (Pa, m); pure, no ANSYS."""
    if not nodes:
        # PlotData unreadable: judge the limit on the raw peak and say no zone was used.
        return {"stress_for_limit_MPa": max_stress_mpa, "stress_for_limit_at_mm": None,
                "excluded_peak_MPa": None, "excluded_peak_at_mm": None, "excluded_at": None,
                "zone_mm": None, "zone_settled": None}
    coords_mm = np.array([[c * 1000 for c in coords_m[n]] for n in nodes])
    stress_mpa = np.asarray(values_pa, dtype=float) / 1e6

    def idx(ids: set[int]) -> np.ndarray:
        return np.array([k for k, n in enumerate(nodes) if n in ids], dtype=int)

    return zones.exclusion_zone(coords_mm, stress_mpa, idx(support_ids), idx(load_ids),
                                idx(guard_ids), step_mm, zone_mm=zone_mm)


def _zone_from_mesh(analysis, selections, nodes, values_pa, step_mm, zone_mm, max_stress_mpa):
    """Gather coordinates and face node ids from Mechanical, then call _zone_fields.

    Returns (fields, message or None). Any ANSYS API failure falls back to the raw peak."""
    try:
        if not nodes:
            raise RuntimeError("stress PlotData was not readable")
        mesh = analysis.MeshData
        coords = {}
        for n in nodes:  # per-node lookup; Task 5 measures the time at 120K nodes
            nd = mesh.NodeById(n)
            coords[n] = (nd.X, nd.Y, nd.Z)
        id_sets = []
        for sel in selections:
            ids: set[int] = set()
            for geo_id in (sel.Location.Ids if sel is not None else []):
                ids.update(int(i) for i in mesh.MeshRegionById(geo_id).NodeIds)
            id_sets.append(ids)
        return _zone_fields(nodes, values_pa, coords, id_sets[0], id_sets[1], id_sets[2],
                            step_mm, zone_mm, max_stress_mpa), None
    except Exception as e:  # noqa: BLE001 - the zone is extra; keep the solve
        fields = _zone_fields([], [], {}, set(), set(), set(), step_mm, zone_mm, max_stress_mpa)
        return fields, f"Exclusion zone could not be computed ({type(e).__name__}: {e}); the limit uses the raw peak."


def refinement_check(pmdb: str | Path, fixed: str, load: str, force_N, element_size_mm: float = 2.0,
                     tolerance: float = 0.10, local_size: tuple[str, float] | None = None,
                     material: dict | None = None, on_solve=None, *,
                     guard: str | None = None, min_wall_mm: float | None = None) -> dict:
    """Re-solve with half the element size (global and local). A max stress change above
    `tolerance` means the peak is mesh-dependent, typically a singularity at a sharp corner.
    The comparison uses stress_for_limit_MPa (outside the exclusion zone); the fine solve reuses the
    coarse zone_mm. `on_solve("coarse" | "fine", result)` is called after each solve."""
    coarse = solve(pmdb, fixed, load, force_N, element_size_mm, local_size, material,
                   guard=guard, min_wall_mm=min_wall_mm)
    if on_solve:
        on_solve("coarse", coarse)
    fine_local = (local_size[0], local_size[1] / 2) if local_size else None
    try:
        fine = solve(pmdb, fixed, load, force_N, element_size_mm / 2, fine_local, material,
                     guard=guard, min_wall_mm=min_wall_mm, zone_mm=coarse["zone_mm"])
    except MeshTooLarge as e:
        raise MeshTooLarge(
            f"Mesh check not possible: the halved mesh is over the Student limit. {e} The coarse result "
            f"stands but is NOT mesh-verified: {coarse['max_stress_MPa']:.1f} MPa peak, "
            f"{coarse['nodes']:,} nodes at {element_size_mm} mm.") from e
    if on_solve:
        on_solve("fine", fine)
    change = abs(fine["stress_for_limit_MPa"] - coarse["stress_for_limit_MPa"]) / coarse["stress_for_limit_MPa"]
    return {"coarse": coarse, "fine": fine, "stress_change": change, "converged": change <= tolerance}
