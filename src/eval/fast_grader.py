"""Grade one fast-path benchmark run log against its problem's key.

Run log: {"id", "events": [{"seq", "type": "student"|"text"|"tool"|"tool_result"|"path"|"handoff"|"error", ...}],
"result": contract Result as a dict or None, "stopped": str | None}. Each check is pass / fail / n/a / info.
"""
from __future__ import annotations

import math
import re

from src.eval.fast_keys import FastProblem, meets, model_eval, objective_value
from src.pydiscovery.families import FAMILIES

PICK_GAP_PASS = 0.05  # provisional pass line (user decision 2026-11-22), recalibrated after the first run
FORCE_REL = 0.005
ANGLE_DEG = 1.0
STUDY_CHECKS = ("force", "direction", "material", "safety_factor", "objective", "limits", "vary",
                "asked", "confirmed", "no_silent_default", "feasible", "pick_gap", "sim_gap")
ASK_PATTERNS = {
    "support": re.compile(r"support|fixed|bolt|wall|back face|mount", re.I),
    "load_face": re.compile(r"free end|end face|load(ed)? face|where .*load|tip", re.I),
    "safety_factor": re.compile(r"safety factor|\bSF\b", re.I),
    "force": re.compile(r"force|newton|\bN\b|load|how much", re.I),
    "material": re.compile(r"material|steel|alumin|alloy|6061", re.I),
}
AGREE = re.compile(r"\b(yes|yeah|correct|right|confirm|looks good|ok(ay)?|go ahead|approved?)\b", re.I)
NEGATED = re.compile(r"\b(no|not|wrong|incorrect)\b|n't\b", re.I)
QUESTION = re.compile(r"[^.!?\n]*\?")  # one question sentence
CARD_ITEMS = ("support", "load_face")  # fixed by the family; a confirmed setup card counts as asking (spec)
CARD_QUESTION = re.compile(r"correct|right|confirm|look(s)? good|ok(ay)?|accurate", re.I)


def _r(result: str, detail: str = "") -> dict:
    return {"result": result, "detail": detail}


def _first(events, pred) -> float:
    return next((e["seq"] for e in events if pred(e)), math.inf)


def _tool(name: str):
    return lambda e: e["type"] == "tool" and e.get("name") == name


def _first_use(events, item: str) -> float:
    """Seq at which the assistant first acted on an item; a value must be asked before this point."""
    sf_use = lambda e: (e["type"] == "tool" and e.get("name") == "record_study_inputs"
                        and "safety_factor" in (e.get("input") or {}))
    uses = {"support": [_tool("set_support"), _tool("confirm_setup")], "load_face": [_tool("confirm_setup")],
            "safety_factor": [sf_use, _tool("optimize_design")], "force": [_tool("set_load"), _tool("confirm_setup")],
            "material": [_tool("set_material"), _tool("confirm_setup")]}
    return min(_first(events, u) for u in uses.get(item, [_tool("confirm_setup")]))


def _asked(events, item: str) -> bool:
    """A question about the item, then a student reply, both before the item was first used."""
    before = _first_use(events, item)
    if before == math.inf:
        before = _first(events, _tool("optimize_design"))
    pat = ASK_PATTERNS.get(item, re.compile(re.escape(item), re.I))
    # the keyword must sit in a question sentence, not anywhere in a message that also asks something
    asks = [e["seq"] for e in events if e["type"] == "text" and e["seq"] < before
            and (any(pat.search(q) for q in QUESTION.findall(e["text"]))
                 or (item in CARD_ITEMS and pat.search(e["text"])  # a setup card the student is asked to confirm
                     and any(CARD_QUESTION.search(q) for q in QUESTION.findall(e["text"]))))]
    return bool(asks) and any(e["type"] == "student" and min(asks) < e["seq"] < before for e in events)


def _confirmed(events) -> tuple[bool, str]:
    at = _first(events, _tool("confirm_setup"))
    if at == math.inf:
        return False, "confirm_setup was never called"
    # agreement after the last question before confirm_setup; plain remarks in between are fine
    last_q = max((e["seq"] for e in events if e["type"] == "text" and e["seq"] < at and "?" in e["text"]), default=-1)
    agreed = any(e["type"] == "student" and last_q < e["seq"] < at and AGREE.search(e["text"])
                 and not NEGATED.search(e["text"]) for e in events)
    return agreed, "student agreed before confirm_setup" if agreed else "confirm_setup without the student's agreement"


def _vary_names(family: str, vary: list[str]) -> set[str]:
    fam = FAMILIES[family]
    if "all" in vary:
        return set(fam.ranges_mm)
    return {fam.contract_name(v) or v for v in vary}


def grade_fast(problem: FastProblem, log: dict) -> dict:
    key, events, result = problem.key, log.get("events", []), log.get("result")
    checks: dict[str, dict] = {}
    pick = {"gap": None, "sim_gap": None}
    went_slow = any(e["type"] in ("path", "handoff") and e.get("path", "slow") == "slow" for e in events)
    optimized = any(_tool("optimize_design")(e) for e in events)

    if key.get("expect_handoff"):
        ok = went_slow and not optimized
        checks = {c: _r("n/a") for c in STUDY_CHECKS}
        checks["handoff"] = _r("pass" if ok else "fail", "handed off to ANSYS" if ok else "stayed on the fast path")
        return {"id": problem.id, "checks": checks, "pick": pick}
    checks["handoff"] = _r("fail", "handed off to ANSYS on a fast-path problem") if went_slow else _r("pass", "stayed on the fast path")

    study = key["study"]
    checks["asked"] = (_r("pass") if all(_asked(events, i) for i in key.get("must_ask", []))
                       else _r("fail", "not asked before use: " + ", ".join(i for i in key["must_ask"] if not _asked(events, i))))
    ok, why = _confirmed(events)
    checks["confirmed"] = _r("pass" if ok else "fail", why)

    if not result:
        why = f"no optimize result ({log.get('stopped') or 'run ended'})"
        for c in ("force", "direction", "material", "safety_factor", "objective", "limits", "vary",
                  "no_silent_default", "feasible", "pick_gap"):
            checks[c] = _r("fail", why)
        checks["sim_gap"] = _r("n/a", why)
        return {"id": problem.id, "checks": checks, "pick": pick}

    spec = result["spec"]
    cons = spec.get("constraints", [])
    sf = next((c["value"] for c in cons if c["metric"] == "safety_factor" and c["op"] == ">="), None)
    lims = sorted((c["metric"], c["op"], round(c["value"], 6)) for c in cons if not (c["metric"] == "safety_factor" and c["op"] == ">="))
    want_lims = sorted((x["metric"], x["op"], round(x["value"], 6)) for x in study.get("limits") or [])
    varied = {p["name"] for p in spec.get("parameters", []) if p["high_mm"] > p["low_mm"]}
    load = spec.get("load") or {}
    f, angle = load.get("force_n"), load.get("angle_deg", -90.0)

    def cmp(name, ok, got, want):
        checks[name] = _r("pass" if ok else "fail", f"got {got}, want {want}")

    cmp("force", f is not None and abs(f - study["force_n"]) <= FORCE_REL * study["force_n"], f, study["force_n"])
    cmp("direction", abs(angle - study["angle_deg"]) <= ANGLE_DEG, angle, study["angle_deg"])
    cmp("material", spec.get("material_key") == study["material_key"], spec.get("material_key"), study["material_key"])
    cmp("safety_factor", sf is not None and math.isclose(sf, study["safety_factor"]), sf, study["safety_factor"])
    cmp("objective", spec.get("objective") == study["objective"], spec.get("objective"), study["objective"])
    cmp("limits", lims == want_lims, lims, want_lims)
    want_vary = _vary_names(problem.family, study.get("vary") or ["all"])
    cmp("vary", varied == want_vary, sorted(varied), sorted(want_vary))
    cmp("no_silent_default", sf is not None and math.isclose(sf, study["safety_factor"]), sf, study["safety_factor"])

    rec = (result.get("recommended") or {}).get("design")
    if not rec:
        checks["feasible"] = _r("fail", "no recommendation")
        checks["pick_gap"] = _r("fail", "no recommendation")
        checks["sim_gap"] = _r("n/a", "no recommendation")
        return {"id": problem.id, "checks": checks, "pick": pick}

    # judge the pick with the problem's true load and material, at the model's pessimistic bound
    ev = model_eval(problem.family, study["material_key"], study["force_n"], rec)
    feas = meets(ev, study["safety_factor"], study.get("limits") or [], at="worst")
    checks["feasible"] = _r("pass" if feas else "fail", f"SF at the pessimistic bound {ev['safety_factor_worst']:.3f}")
    metric, sign = study["objective"]["metric"], 1 if study["objective"]["direction"] == "minimize" else -1
    val = objective_value(ev, metric)
    opt = key["model_optimum"]["objective_value"]
    pick["gap"] = sign * (val - opt) / opt
    checks["pick_gap"] = _r("pass" if pick["gap"] <= PICK_GAP_PASS else "fail",
                            f"{metric} {val:.5g} vs model optimum {opt:.5g} ({pick['gap']:+.1%})")
    sim = key.get("sim_optimum") or {}
    if sim.get("design_id"):
        pick["sim_gap"] = sign * (val - sim["objective_value"]) / sim["objective_value"]
        checks["sim_gap"] = _r("info", f"vs CalculiX best {sim['objective_value']:.5g} ({pick['sim_gap']:+.1%})")
    else:
        checks["sim_gap"] = _r("n/a", sim.get("reason", "no simulation optimum"))
    return {"id": problem.id, "checks": checks, "pick": pick}
