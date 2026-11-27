"""The judgement call after an optimization: is the recommended design worth adopting, in 3 to 5 sentences.

Written by the LLM from result.json, like explain.py, and held to the same number check: every number in
the text must be in the result or be a percent change against the baseline. A text that fails gets one
retry that names the numbers; if that fails too (or the model is unavailable) the verdict is a template
built from result.json, which passes the same check by construction.
"""
from __future__ import annotations

import json

from src.assistant.contract_adapter import load_result
from src.assistant.numbers import percent_changes, unknown_numbers
from src.llm import MODEL

INSTRUCTIONS = """\
You give a beginner engineering student a verdict on a bracket optimization result, in 3 to 5 plain
sentences and one paragraph. Cover, in this order:
1. Whether the recommended design is worth adopting (yes, yes with a caveat, or no), with the one
   number that decides it.
2. The main trade-off: what got better and what it cost (for example lighter but less stiff).
3. Which requirement is binding (result.binding_constraint) and what that means for the design.
4. How much to trust it: say "solver checked" only when the source is calculix or ansys_mechanical;
   a power_law, fake_formula or formula_emulator source is a model estimate, not a solve. Say whether
   the design sits inside the training range when that is given.
5. One concrete next step.
If recommended is null, say that no design met every limit, name the limit, and make the next step
about relaxing it or widening the search.
Use only numbers that appear in the result, rounded for reading, or the percent change of the
recommended design against the baseline. Never compute or invent any other number. No headings,
no bullet points, no bold."""

SOLVERS = {"calculix": "CalculiX", "ansys_mechanical": "ANSYS Mechanical"}
METRIC_WORDS = {"mass_kg": "mass", "max_stress_mpa": "peak stress", "max_disp_mm": "deflection", "safety_factor": "safety factor"}
UNITS = {"mass_kg": " kg", "max_stress_mpa": " MPa", "max_disp_mm": " mm", "safety_factor": ""}


def _fmt(metric: str, value: float) -> str:
    """A reading of the value that still matches the result under the number check (3 significant figures)."""
    text = f"{value:.3g}"
    if "e" in text:  # very small or large: plain decimals, enough digits that the check sees at least two
        text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text + UNITS[metric]


BETTER_WHEN_LOWER = {"mass_kg": True, "max_stress_mpa": True, "max_disp_mm": True, "safety_factor": False}


def _pct(changes: dict, metric: str) -> tuple[str, bool] | None:
    """('45% lighter', improved?) for one metric, or None when there is no change to report."""
    v = changes.get(metric)
    if v is None or abs(v) < 0.05:
        return None
    words = {"mass_kg": ("lighter", "heavier"), "max_stress_mpa": ("lower", "higher"),
             "max_disp_mm": ("less", "more"), "safety_factor": ("lower", "higher")}[metric]
    size = abs(v)
    shown = f"{size:.1f}" if size < 10 else f"{size:.0f}"  # one significant figure needs an exact match
    noun = {"mass_kg": "", "max_stress_mpa": " peak stress", "max_disp_mm": " deflection", "safety_factor": " safety factor"}[metric]
    return f"{shown}% {words[0] if v < 0 else words[1]}{noun}", (v < 0) == BETTER_WHEN_LOWER[metric]


def trust(result: dict, prediction: dict | None = None) -> tuple[str, bool | None]:
    """(how the finalist numbers were obtained, whether the recommended design is in the training range)."""
    rec = result.get("recommended") or result.get("baseline") or {}
    source = rec.get("source", "")
    if source in SOLVERS:
        how = f"solver checked in {SOLVERS[source]}"
    elif source.startswith("power_law"):
        how = "estimated by the fitted model only, not solved"
    else:
        how = "a formula demo value, not a solve"
    in_range = None
    if prediction and result.get("recommended"):
        hit = next((c for c in prediction.get("shortlist", []) if c.get("design_id") == rec.get("design_id")), None)
        if hit is not None and isinstance(hit.get("in_training_range"), bool):
            in_range = hit["in_training_range"]
    return how, in_range


def template_verdict(result: dict, prediction: dict | None = None) -> str:
    """A verdict built from result.json alone; every number it shows is in the result or a percent change."""
    result = load_result(result)
    spec, base, rec = result["spec"], result["baseline"], result.get("recommended")
    obj = spec["objective"]["metric"]
    how, in_range = trust(result, prediction)
    if rec is None:
        limits = ", ".join(base.get("violated") or [f"{c['metric']} {c['op']} {c['value']}" for c in spec["constraints"]])
        return ("Do not adopt a design from this search: none of the finalists met every limit. "
                f"The limit that could not be met is {limits}. The finalist numbers are {how}. "
                "Next step: relax that limit a little or widen the dimension ranges, then search again.")
    changes = percent_changes(result)
    gain = _pct(changes, obj)
    verdict = ("Adopt it" if base.get("feasible") else "Adopt it: the baseline fails a limit, so this is the price of meeting it")
    s1 = f"{verdict}. {rec['design_id']} has a {METRIC_WORDS[obj]} of {_fmt(obj, rec[obj])}" + (f", {gain[0]} than the baseline" if gain else "") + "."
    others = [p for m in ("max_disp_mm", "max_stress_mpa", "safety_factor") if m != obj for p in [_pct(changes, m)] if p]
    worse = [t for t, ok in others if not ok]
    better = [t for t, ok in others if ok]
    if worse:
        s2 = "The trade-off: it costs " + " and ".join(worse[:2]) + (f" for {better[0]}" if better else "") + "."
    elif better:
        s2 = "It gives nothing up against the baseline: " + " and ".join(better[:2]) + "."
    else:
        s2 = "The other outputs stay where the baseline had them."
    bind = result.get("binding_constraint")
    if bind:
        metric = bind.split(" ")[0]
        s3 = f"The binding requirement is {bind}: the design sits at {_fmt(metric, rec[metric])} {METRIC_WORDS.get(metric, metric)}, so there is no room to go further on it."
    else:
        s3 = "No requirement is binding, so the search range, not a limit, set the result."
    s4 = f"Trust: the finalist numbers are {how}" + ("" if in_range is None else (", and the design is inside the training range" if in_range else ", and the design is outside the training range, so its prediction was extrapolated")) + "."
    s5 = ("Next step: enter the Discovery values and run one Ansys solve to confirm the numbers before building it."
          if not how.startswith("solver checked") else "Next step: enter the Discovery values and check the part against the real load case and mounting.")
    return " ".join([s1, s2, s3, s4, s5])


def verdict(result, client, model: str = MODEL, prediction: dict | None = None) -> tuple[str, str, list[str]]:
    """(text, source, warnings): source is "model" when the LLM's text passed the number check, else "template".

    `result` is a contracts Result, a dict or a path to result.json; it is checked against the contract before any
    API call. `client` may be None, which goes straight to the template."""
    result = load_result(result)
    fallback = template_verdict(result, prediction)
    if client is None:
        return fallback, "template", []
    how, in_range = trust(result, prediction)
    context = {"result": result, "finalist_numbers": how,
               "recommended_in_training_range": in_range if in_range is not None else "not given"}
    messages = [{"role": "user", "content": "The result and how its numbers were obtained:\n" + json.dumps(context, indent=1)}]
    bad: list[str] = []
    for _ in range(2):
        r = client.responses.create(model=model, instructions=INSTRUCTIONS, input=messages,
                                    max_output_tokens=900, reasoning={"effort": "low"})
        text = (r.output_text or "").strip()
        bad = unknown_numbers(text, result)
        if text and not bad:
            return text, "model", []
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"These numbers are not in the result: {', '.join(bad) or '(empty reply)'}. "
                                                 "Rewrite the verdict using only numbers from the result."}]
    return fallback, "template", [f"Verdict used the template: the generated text twice contained numbers "
                                  f"not in the result ({', '.join(bad) or 'empty reply'})."]
