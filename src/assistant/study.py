"""What the front layer keeps beside Jack's setup card: safety factor, objective, limits.

Jack's card has no safety-factor field, so the front layer stores it and always passes it to
optimize_design itself (never Jack's silent SF >= 2.0 default).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.pydiscovery.session import material_key

LIBRARY_NAMES = {"structural_steel": "Structural Steel",
                 "aluminum_6061": "Aluminum alloy, wrought, 6061, T6"}  # ANSYS library names (src/sim/materials.py)
METRIC_WORDS = {"mass_kg": "mass", "max_stress_mpa": "peak stress", "max_disp_mm": "deflection",
                "safety_factor": "safety factor"}
AXES = {(1, 0, 0): "+X", (-1, 0, 0): "-X", (0, 1, 0): "+Y", (0, -1, 0): "-Y", (0, 0, 1): "+Z", (0, 0, -1): "-Z"}


@dataclass
class StudyInputs:
    safety_factor: float | None = None
    objective: str | None = None
    direction: str | None = None
    limits: list[dict] = field(default_factory=list)  # {"metric", "op", "value"} like Jack's constraints
    vary: list[str] | None = None


def build_constraints(inputs: StudyInputs) -> list[dict]:
    """The user's safety factor first, then their other limits; any other SF entry is dropped."""
    if inputs.safety_factor is None:
        raise ValueError("No safety factor from the user yet.")
    sf = {"metric": "safety_factor", "op": ">=", "value": inputs.safety_factor}
    return [sf] + [c for c in inputs.limits if c.get("metric") != "safety_factor"]


def optimize_blocker(part, inputs: StudyInputs) -> str | None:
    """What is still missing before a search may run, or None."""
    missing = []
    if part is None or not part.confirmed:
        missing.append("the user's confirmation of the setup card")
    if inputs.safety_factor is None:
        missing.append("a safety factor")
    if inputs.objective is None:
        missing.append("an objective")
    return ("Can't search yet. Missing: " + ", ".join(missing) + ".") if missing else None


def _given(part, key: str) -> bool:
    """A value counts when the user set it, or when the user confirmed the card it was on."""
    return key in part.sources and (part.sources[key] == "user" or part.confirmed)


def _direction_words(d) -> str:
    key = tuple(int(round(x)) for x in d)
    return AXES.get(key) if key in AXES and all(abs(a - b) < 1e-6 for a, b in zip(d, key)) else str(list(d))


def _material_name(m) -> str:
    key = material_key(m)
    return LIBRARY_NAMES.get(key, m.name) if key else m.name


def handoff_message(filename: str, part, inputs: StudyInputs, reason: str) -> str:
    """First message to the ANSYS agent: what the user already confirmed, and what is left."""
    done = []
    if part is not None:
        if _given(part, "load") and part.force_n is not None:
            way = f", direction {_direction_words(part.direction)}" if part.direction else ""
            done.append(f"load {part.force_n:g} N{way}")
        if _given(part, "material") and part.material is not None:
            done.append(f"material {_material_name(part.material)}")
    if inputs.safety_factor is not None:
        done.append(f"safety factor {inputs.safety_factor:g}")
    if inputs.objective is not None:
        verb = "maximise" if inputs.direction == "maximize" else "minimise"
        done.append(f"objective: {verb} {METRIC_WORDS.get(inputs.objective, inputs.objective)}")
    for c in inputs.limits:
        if c.get("metric") != "safety_factor":
            done.append(f"limit {METRIC_WORDS.get(c['metric'], c['metric'])} {c['op']} {c['value']:g}")
    head = f"The user uploaded '{filename}'. Switched to the ANSYS search because {reason}."
    if not done:
        return head + " Nothing is confirmed yet: ask the user for every input as usual."
    return (head + " Already confirmed by the user: " + "; ".join(done) + ". Do not ask for these again. "
            "Still to do: find the supported and loaded faces, confirm the faces with the user, "
            "and ask for anything else that is missing.")
