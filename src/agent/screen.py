"""Pure logic for the first-look screen: vary one feature at a time, rank by effect.

No ANSYS here, so it can be unit-tested. The Workbench runs the designs (2n + 1 solves)
and hands the results to `summarize`.
"""
from __future__ import annotations

from numbers import Real


def reference_value(current: float | None, low: float, high: float) -> float:
    """Current value if it lies inside [low, high], else the midpoint (a fillet has no current value)."""
    if current is not None and low <= current <= high:
        return current
    return (low + high) / 2


def screen_plan(features: list[dict], currents: list[float | None]) -> list[tuple[str, list[float]]]:
    """(design name, value per feature) in run order: look_ref, then low/high per feature (k from 1)."""
    ref = [reference_value(c, f["low"], f["high"]) for f, c in zip(features, currents)]
    plan = [("look_ref", list(ref))]
    for k, f in enumerate(features, start=1):
        for end in ("low", "high"):
            vals = list(ref)
            vals[k - 1] = f[end]
            plan.append((f"look_{k}_{end}", vals))
    return plan


def _num(x) -> bool:
    return isinstance(x, Real) and not isinstance(x, bool)


def feasible(responses: dict, limits: list[dict]) -> bool:
    """True if every limit holds. A missing/non-numeric response or an error counts as infeasible."""
    if responses.get("error"):
        return False
    for lim in limits:
        v = responses.get(lim["response"])
        if not _num(v):
            return False
        if not (v <= lim["value"] if lim["op"] == "<=" else v >= lim["value"]):
            return False
    return True


def _rel(value, ref):
    return (value - ref) / abs(ref) if ref else 0.0


def _swing(lo, hi, ref) -> float:
    """Relative effect of a feature on one response; an errored end falls back to the other end vs. the reference."""
    if lo is not None and hi is not None:
        return abs(hi - lo) / abs(ref) if ref else 0.0
    end = lo if lo is not None else hi
    return abs(_rel(end, ref)) if end is not None else 0.0


def summarize(features: list[dict], rows: dict[str, dict], objective: str, sense: str,
              limits: list[dict]) -> dict:
    """Per-feature effects, a ranking (1-based feature indices, biggest objective swing first) and the best feasible design."""
    ref_resp = rows.get("look_ref", {}).get("responses", {})
    ref_vals = rows.get("look_ref", {}).get("values", [])
    out_features, keys, unranked = [], [], []
    for k, f in enumerate(features, start=1):
        ends = {e: rows.get(f"look_{k}_{e}", {}) for e in ("low", "high")}
        resp_out = {}
        for r, rv in ref_resp.items():
            if not _num(rv):
                continue
            lo = ends["low"].get("responses", {}).get(r)
            hi = ends["high"].get("responses", {}).get(r)
            lo, hi = (lo if _num(lo) else None), (hi if _num(hi) else None)
            # change = high - low when both ends ran, else the surviving end minus the reference
            if lo is not None and hi is not None:
                change, basis = hi - lo, "high_vs_low"
            elif lo is not None or hi is not None:
                change, basis = (lo if lo is not None else hi) - rv, "low_vs_ref" if lo is not None else "high_vs_ref"
            else:
                change, basis = None, None
            resp_out[r] = {"low": lo, "ref": rv, "high": hi, "change": change, "basis": basis,
                           "change_rel": None if change is None or not rv else change / abs(rv)}
        out_features.append({
            "index": k, "label": f.get("label", f"feature {k}"),
            "low": ends["low"].get("values", [None] * k)[k - 1] if ends["low"] else f.get("low"),
            "ref": ref_vals[k - 1] if len(ref_vals) >= k else None,
            "high": ends["high"].get("values", [None] * k)[k - 1] if ends["high"] else f.get("high"),
            "responses": resp_out,
            "feasible": {e: bool(ends[e].get("responses")) and feasible(ends[e]["responses"], limits)
                         for e in ("low", "high")}})
        obj = resp_out.get(objective, {})
        primary = _swing(obj.get("low"), obj.get("high"), obj.get("ref", 0.0))
        tie = max((_swing(resp_out[l["response"]]["low"], resp_out[l["response"]]["high"],
                          resp_out[l["response"]]["ref"])
                   for l in limits if l["response"] in resp_out), default=0.0)
        if any(_num(ends[e].get("responses", {}).get(objective)) for e in ("low", "high")):
            keys.append((-primary, -tie, k))
        else:
            unranked.append(k)
    errors = {n: r["error"] for n, r in rows.items() if r.get("error")}
    notes = []
    if not _num(ref_resp.get(objective)):
        # Without the reference there is nothing to measure the effects against.
        keys, unranked = [], [k for k in range(1, len(features) + 1)]
        notes.append("look_ref failed, so no effects or ranking can be given; see errors.")
    elif unranked:
        notes.append("No ranking for " + ", ".join(f"feature {k}" for k in unranked)
                     + ": both of its designs failed; see errors.")
    ranking = [k for *_, k in sorted(keys)]
    # The objective ranking can hide which feature controls a limit (a feature may move mass most and
    # stress hardly at all), so rank the features per limited response too.
    limit_ranking = {}
    for r in dict.fromkeys(l["response"] for l in limits):
        if not _num(ref_resp.get(r)):
            continue
        rk = [(-_swing(f["responses"][r]["low"], f["responses"][r]["high"], f["responses"][r]["ref"]), f["index"])
              for f in out_features if r in f["responses"]
              and (f["responses"][r]["low"] is not None or f["responses"][r]["high"] is not None)]
        limit_ranking[r] = [k for _, k in sorted(rk)]
    sign = -1 if sense == "max" else 1
    ok = [(n, r["responses"][objective]) for n, r in rows.items()
          if r.get("responses") and _num(r["responses"].get(objective)) and feasible(r["responses"], limits)]
    best = min(ok, key=lambda t: sign * t[1])[0] if ok else None
    if best is None and limit_ranking:
        notes.append("No design meets the limits. To reach them, change the features that move the limited "
                     "response most (limit_ranking), not the ones ranked first for the objective.")
    out = {"reference": {"values": ref_vals, "responses": ref_resp},
           "features": out_features, "ranking": ranking, "limit_ranking": limit_ranking, "best_feasible": best,
           "errors": errors, "failed": len(errors)}
    if unranked:
        out["unranked"] = unranked
    if notes:
        out["note"] = " ".join(notes)
    return out


# Mesh-size default for thin parts. The node estimate is calibrated on the saddle bracket
# (16,369 nodes at 1 mm for 4,991 mm3, quadratic tets); check_mesh halves the size, and the fine
# mesh must stay under the Student limit of 128K nodes, so we budget 110K for it.
NODES_PER_MM3_AT_1MM = 3.3
FINE_NODE_BUDGET = 110_000
MAX_ELEMENT_SIZE_MM = 2.0


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def min_wall_mm(faces: list[dict]) -> float | None:
    """Thinnest wall: the smallest gap between two opposite-facing planar faces with material between them."""
    planes = [f for f in faces if f.get("type") == "plane" and f.get("normal")]
    best = None
    for i, a in enumerate(planes):
        ca = [(lo + hi) / 2 for lo, hi in zip(a["bbox_min_mm"], a["bbox_max_mm"])]
        for b in planes[i + 1:]:
            if _dot(a["normal"], b["normal"]) > -0.999:
                continue
            cb = [(lo + hi) / 2 for lo, hi in zip(b["bbox_min_mm"], b["bbox_max_mm"])]
            d = _dot(a["normal"], [p - q for p, q in zip(ca, cb)])  # > 0: the faces point away from each other
            if d <= 1e-6:
                continue
            # the faces must overlap when seen along the normal: their boxes, grown by d, intersect
            if all(a["bbox_min_mm"][k] - d <= b["bbox_max_mm"][k] and b["bbox_min_mm"][k] - d <= a["bbox_max_mm"][k]
                   for k in range(3)):
                best = d if best is None else min(best, d)
    return best


def default_element_size(min_wall: float | None, volume_mm3: float) -> float:
    """About two elements through the thinnest wall and at most 2 mm, but never finer than the size at which
    check_mesh's halved mesh fits the node budget (that floor wins, even above 2 mm, for large parts)."""
    budget = (NODES_PER_MM3_AT_1MM * volume_mm3 * 8 / FINE_NODE_BUDGET) ** (1 / 3)
    half_wall = min_wall / 2 if min_wall else MAX_ELEMENT_SIZE_MM
    return round(max(budget, min(MAX_ELEMENT_SIZE_MM, half_wall)), 2)
