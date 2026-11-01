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
SENSES = {"min": ComparisonType.MIN, "max": ComparisonType.MAX}
OPS = {"<=": ComparisonType.LESSEQUAL, ">=": ComparisonType.GREATEREQUAL}
FAILED_STATES = {"Check failed", "Execution failed"}


def _log_messages(osl) -> list[dict]:
    """Every actor log message in the project status (best effort, for error reports)."""
    try:
        info = osl.osl_server.get_full_project_status_info(include_designs=False, include_log_messages=True)
    except Exception:  # noqa: BLE001
        return []
    out, stack = [], [info]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            out += [m for m in node.get("log_messages", []) if isinstance(m, dict)]
            stack += [v for v in node.values() if isinstance(v, (dict, list))]
        elif isinstance(node, list):
            stack += node
    return out


def arsm(
    variables: list[dict],
    evaluate: Callable[[dict[str, float]], dict[str, float]],
    objective: str,
    limits: list[dict],
    budget: int,
    timeout_s: float = 3600,
    sense: str = "min",
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Minimise (sense="min") or maximise ("max") response `objective` subject to every
    limit {"response", "op": "<=" | ">=", "value"}.

    variables: [{"name", "lower", "upper", "start"}]. evaluate(values) returns a dict
    with the objective and every limited response, or raises on failure.
    should_stop() is checked before each design; when true the run ends with stopped = "user".
    Returns {"evaluations": [...], "stopped": reason}.
    """
    if sense not in SENSES:
        raise ValueError(f"sense must be one of {sorted(SENSES)}")
    for lim in limits:
        if lim["op"] not in OPS:
            raise ValueError(f"limit op must be one of {sorted(OPS)}, got {lim['op']!r}")
    responses = sorted({objective, *(lim["response"] for lim in limits)})
    # A failed design gets the worst value in the direction that matters for each response.
    low_is_bad = {lim["response"] for lim in limits if lim["op"] == ">="} | ({objective} if sense == "max" else set())
    failed = {r: -FAILED if r in low_is_bad else FAILED for r in responses}
    evaluations: list[dict] = []
    osl = Optislang(ini_timeout=180)
    try:
        system = osl.application.project.root_system.create_node(type_=nt.ARSM, name="ARSM")
        settings = system.get_property("OptimizerSettings")
        # Each iteration evaluates a small DOE (about n + 1 designs for n variables).
        max_iter = max(2, budget // (len(variables) + 1))
        settings["settings"]["max_iteration"] = max_iter
        # ARSM's default minimum is 5 iterations; a smaller maximum fails optiSLang's check
        # ("Min. number of iterations larger than max. number") and no design is ever sent.
        settings["settings"]["min_iteration_test"] = min(settings["settings"].get("min_iteration_test", 5), max_iter)
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
            ObjectiveCriterion(name="objective", expression=objective, criterion=SENSES[sense]))
        for k, lim in enumerate(limits):
            system.criteria_manager.add_criterion(ConstraintCriterion(
                name=f"limit_{k}_{lim['response']}", expression=lim["response"], criterion=OPS[lim["op"]],
                limit_expression=repr(float(lim["value"]))))

        project = osl.application.project
        project.start(wait_for_finished=False)
        t0, stopped = time.time(), "converged"
        while (status := project.root_system.get_status()) != "Processing done":
            if status in FAILED_STATES:  # optiSLang rejected the setup: report it instead of waiting forever
                msgs = [m["message"] for m in _log_messages(osl) if m.get("level") == "ERROR"]
                raise RuntimeError(f"optiSLang ARSM {status.lower()}: {'; '.join(msgs) or 'no message'}")
            if len(evaluations) >= budget:
                stopped = "budget"
                break
            if time.time() - t0 > timeout_s:
                stopped = "timeout"
                break
            if should_stop and should_stop():  # also while ARSM is idle and no design is waiting
                stopped = "user"
                break
            done = []
            for d in proxy.get_designs():
                if should_stop and should_stop():
                    stopped = "user"
                    break
                values = {p["name"]: p["value"] for p in d["parameters"]}
                try:
                    out, error = evaluate(values), None
                except Exception as e:  # noqa: BLE001 - a failed design must not stop the study
                    out, error = failed, f"{type(e).__name__}: {e}"
                evaluations.append({"values": values, "responses": {r: out[r] for r in responses}, "error": error})
                done.append({"hid": d["hid"], "responses": [{"name": r, "value": out[r]} for r in responses]})
            if stopped == "user":
                break
            if done:
                proxy.set_designs(done)
            else:
                time.sleep(0.2)
        if stopped != "converged":
            project.stop(wait_for_finished=True)
        return {"evaluations": evaluations, "stopped": stopped}
    finally:
        osl.dispose()
