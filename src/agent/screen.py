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
    out_features, keys = [], []
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
            change = hi - lo if lo is not None and hi is not None else (
                (lo if lo is not None else hi) - rv if (lo is not None or hi is not None) else None)
            resp_out[r] = {"low": lo, "ref": rv, "high": hi, "change": change,
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
        keys.append((-primary, -tie, k))
    ranking = [k for *_, k in sorted(keys)]
    sign = -1 if sense == "max" else 1
    ok = [(n, r["responses"][objective]) for n, r in rows.items()
          if r.get("responses") and _num(r["responses"].get(objective)) and feasible(r["responses"], limits)]
    best = min(ok, key=lambda t: sign * t[1])[0] if ok else None
    return {"reference": {"values": ref_vals, "responses": ref_resp},
            "features": out_features, "ranking": ranking, "best_feasible": best}
