"""Agent tools over the emulator: JSON in, JSON out, and one short message for the user each time.

Role 3 registers TOOL_SPECS with the agent loop (OpenAI: wrap each as {"type": "function", **spec};
Anthropic: rename "parameters" to "input_schema") and routes calls to PyDiscoveryTools.call().
Errors come back as {"ok": False, "message": ...} so the agent relays them instead of crashing.
The flow the agent should follow: open_part, ask for anything in "missing", show describe_setup,
call confirm_setup only after the user says yes, then optimize_design and save_design.
"""

from __future__ import annotations

from .backends import BackendUnavailable
from .part_file import UnsupportedPartError
from .session import App, Part, SetupError

_DIMS = {"type": "object", "description": "Dimension name (e.g. Thickness) to value in mm", "additionalProperties": {"type": "number"}}
_CONSTRAINT = {"type": "object", "properties": {
    "metric": {"type": "string", "enum": ["mass_kg", "max_stress_mpa", "max_disp_mm", "safety_factor"]},
    "op": {"type": "string", "enum": [">=", "<="]}, "value": {"type": "number"}}, "required": ["metric", "op", "value"]}


def _spec(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": required or []}}


TOOL_SPECS = [
    _spec("open_part", "Open a part file (.dsco, .scdocx, .scdoc, .step). Returns the setup card and what is missing.",
          {"path": {"type": "string"}}, ["path"]),
    _spec("describe_setup", "Show the current setup card: supports, load, material, dimensions, what is missing."),
    _spec("use_family", "For files with no parameters: after the user confirms which known bracket it is.",
          {"family": {"type": "string", "enum": ["l_bracket_v0"]}}, ["family"]),
    _spec("set_support", "Fix the part at a face. Only after the user says where it is held.", {"face": {"type": "string"}}),
    _spec("set_load", "Set the load in newtons, and optionally a direction vector. Only values the user gave.",
          {"force_n": {"type": "number"}, "direction": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}},
          ["force_n"]),
    _spec("set_material", "Set the material when the file has none.",
          {"material": {"type": "string", "enum": ["structural_steel", "aluminum_6061"]}}, ["material"]),
    _spec("confirm_setup", "Record that the user said the setup card is right. Never call without their yes."),
    _spec("set_dimensions", "Change dimensions of the current design (mm).", {"values": _DIMS}, ["values"]),
    _spec("solve_design", "Solve the current design, or the given values, with the chosen engine.", {"values": _DIMS}),
    _spec("optimize_design", "Search for the best design: the predictor shortlists, the engine verifies finalists.",
          {"objective": {"type": "string", "enum": ["mass_kg", "max_stress_mpa", "max_disp_mm", "safety_factor"]},
           "direction": {"type": "string", "enum": ["minimize", "maximize"]},
           "constraints": {"type": "array", "items": _CONSTRAINT},
           "vary": {"type": "array", "items": {"type": "string"}, "description": "Dimensions allowed to change; default all"},
           "n_verify": {"type": "integer", "minimum": 1, "maximum": 20}}),
    _spec("save_design", "Save the recommended (or given) values: a .dsco copy for Discovery parts, else a .json of values.",
          {"path": {"type": "string"}, "values": _DIMS}, ["path"]),
]


class PyDiscoveryTools:
    """One conversation's tool state: the engine and the currently open part."""

    def __init__(self, backend: str = "fitted", predictor: str = "power_law", runs=None):
        self.app = App(backend, predictor, runs)  # same arguments as App, so the GUI server and the agent share one path
        self.part: Part | None = None

    def call(self, name: str, args: dict | None = None) -> dict:
        handler = getattr(self, f"_{name}", None)
        if handler is None or name not in {s["name"] for s in TOOL_SPECS}:
            return {"ok": False, "message": f"Unknown tool {name!r}."}
        try:
            return {"ok": True, **handler(**(args or {}))}
        except (SetupError, ValueError, KeyError, UnsupportedPartError, BackendUnavailable, FileNotFoundError, NotImplementedError) as exc:
            return {"ok": False, "message": str(exc)}

    def _need_part(self) -> Part:
        if self.part is None:
            raise SetupError("No part is open. Ask the user for a part file and call open_part.")
        return self.part

    def _card(self, lead: str = "") -> dict:
        part = self._need_part()
        return {"message": (lead + "\n" if lead else "") + part.describe(), "state": part.state()}

    def _open_part(self, path: str) -> dict:
        self.part = self.app.open(path)
        return self._card()

    def _describe_setup(self) -> dict:
        return self._card()

    def _use_family(self, family: str) -> dict:
        self._need_part().use_family(family)
        return self._card("Using the baseline dimensions of that bracket.")

    def _set_support(self, face: str | None = None) -> dict:
        self._need_part().set_support(face)
        return self._card()

    def _set_load(self, force_n: float, direction: list[float] | None = None) -> dict:
        self._need_part().set_load(force_n, tuple(direction) if direction else None)
        return self._card()

    def _set_material(self, material: str) -> dict:
        self._need_part().set_material(material)
        return self._card()

    def _confirm_setup(self) -> dict:
        self._need_part().confirm()
        return self._card("Setup confirmed.")

    def _set_dimensions(self, values: dict) -> dict:
        self._need_part().set_dimensions(values)
        return self._card()

    def _solve_design(self, values: dict | None = None) -> dict:
        v = self._need_part().solve(values)
        msg = (f"Mass {v.mass_kg:.3f} kg, peak stress {v.max_stress_mpa:.1f} MPa, safety factor {v.safety_factor:.2f}, "
               f"deflection {v.max_disp_mm:.3f} mm ({v.source}).")
        return {"message": msg, "verified": v.model_dump(mode="json")}

    def _optimize_design(self, objective: str = "mass_kg", direction: str = "minimize", constraints: list | None = None,
                         vary: list[str] | None = None, n_verify: int = 5) -> dict:
        part = self._need_part()
        r = part.optimize(objective, direction, constraints, vary, n_verify=n_verify)
        # study_spec and prediction let the GUI show the run with the same three files the fixtures use
        return {"message": r.explanation, "result": r.model_dump(mode="json"),
                "study_spec": r.spec.model_dump(mode="json"), "prediction": part.last_prediction.model_dump(mode="json")}

    def _save_design(self, path: str, values: dict | None = None) -> dict:
        part = self._need_part()
        if values is None and part.last_result and part.last_result.recommended:
            values = {part.family.labels[c]: v for c, v in part.last_result.recommended.design.items()}
        out = part.save(path, values)
        return {"message": f"Saved to {out}.", "path": str(out)}
