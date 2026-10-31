"""Classical optimizer: optiSLang ARSM driven through PyOptiSLang's proxy solver.

optiSLang runs the algorithm (Adaptive Response Surface Method: fits a local response
surface to a few designs per iteration, moves and shrinks the region toward the
optimum; meant for few variables and expensive solvers). Each design it asks for
is evaluated here in Python by `evaluate` (SpaceClaim edit + Mechanical solve), so the
ANSYS sessions stay in this process.

Student licence (seen 2026-10-31): only a few optiSLang sessions may run at once; an
orphaned session holds a seat and the next start fails with "Maximum licensed number
of demo users already reached". So the session is always disposed in `finally`.
"""
from __future__ import annotations

import time
from collections.abc import Callable

import ansys.optislang.core.node_types as nt
from ansys.optislang.core import Optislang
from ansys.optislang.core.nodes import DesignFlow
from ansys.optislang.core.project_parametric import (
    ComparisonType,
    ConstraintCriterion,
    ObjectiveCriterion,
    OptimizationParameter,
)

# Penalty responses for a design whose geometry edit or solve failed. ARSM needs a value
# for every design; a clearly infeasible one keeps it away from that region.
FAILED = 1e9


def arsm(
    variables: list[dict],
    evaluate: Callable[[dict[str, float]], dict[str, float]],
    objective: str,
    limits: dict[str, float],
    budget: int,
    timeout_s: float = 3600,
) -> dict:
    """Minimise response `objective` subject to response <= limit for each item of `limits`.

    variables: [{"name", "lower", "upper", "start"}]. evaluate(values) returns a dict
    with the objective and every limited response, or raises on failure.
    Returns {"evaluations": [...], "stopped": reason}.
    """
    responses = sorted({objective, *limits})
    evaluations: list[dict] = []
    osl = Optislang(ini_timeout=180)
    try:
        system = osl.application.project.root_system.create_node(type_=nt.ARSM, name="ARSM")
        settings = system.get_property("OptimizerSettings")
        # Each iteration evaluates a small DOE (about n + 1 designs for n variables).
        settings["settings"]["max_iteration"] = max(2, budget // (len(variables) + 1))
        system.set_property("OptimizerSettings", settings)
        system.set_property("AutoSaveMode", "no_auto_save")
        system.set_property("UpdateResultFile", "never")

        proxy = system.create_node(type_=nt.ProxySolver, name="ansys", design_flow=DesignFlow.RECEIVE_SEND)
        proxy.set_property("MultiDesignLaunchNum", 1)
        proxy.load(args={
            "parameters": [{"dir": {"value": "input"}, "name": v["name"], "value": v["start"]} for v in variables],
            "responses": [{"dir": {"value": "output"}, "name": r, "value": 0.0} for r in responses],
        })
        proxy.register_locations_as_parameter()
        proxy.register_locations_as_response()
        for v in variables:
            system.parameter_manager.modify_parameter(
                OptimizationParameter(name=v["name"], reference_value=v["start"], range=(v["lower"], v["upper"])))
        system.criteria_manager.add_criterion(
            ObjectiveCriterion(name="objective", expression=objective, criterion=ComparisonType.MIN))
        for r, limit in limits.items():
            system.criteria_manager.add_criterion(ConstraintCriterion(
                name=f"limit_{r}", expression=r, criterion=ComparisonType.LESSEQUAL, limit_expression=repr(limit)))

        project = osl.application.project
        project.start(wait_for_finished=False)
        t0, stopped = time.time(), "converged"
        while project.root_system.get_status() != "Processing done":
            if len(evaluations) >= budget:
                stopped = "budget"
                break
            if time.time() - t0 > timeout_s:
                stopped = "timeout"
                break
            done = []
            for d in proxy.get_designs():
                values = {p["name"]: p["value"] for p in d["parameters"]}
                try:
                    out, error = evaluate(values), None
                except Exception as e:  # noqa: BLE001 - a failed design must not stop the study
                    out, error = {r: FAILED for r in responses}, f"{type(e).__name__}: {e}"
                evaluations.append({"values": values, "responses": {r: out[r] for r in responses}, "error": error})
                done.append({"hid": d["hid"], "responses": [{"name": r, "value": out[r]} for r in responses]})
            if done:
                proxy.set_designs(done)
            else:
                time.sleep(0.2)
        if stopped != "converged":
            project.stop(wait_for_finished=True)
        return {"evaluations": evaluations, "stopped": stopped}
    finally:
        osl.dispose()
