"""App and Part: the PyDiscovery-style API that the agent, the demo and the tests call.

Shaped like PyMechanical's embedded App (App(), open, save). Nothing here imports Ansys.
Flow: open a part, fill what the file lacks, confirm with the user, then solve, optimize, save.
The emulator never assumes physics: solve and optimize refuse until the user has confirmed.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from contracts.fakes import MATERIALS, FakePredictor, run_pipeline
from contracts.models import Constraint, Load, Objective, ParameterRange, Result, StudySpec, Verified

from src.models.datasets import FAMILY_DIRS
from src.models.power_law import PowerLawPredictor

from .backends import Backend, get_backend
from .families import FAMILIES, L_BRACKET_V0, Family, match_family
from .part_file import Material, PartSetup, open_part, save_with_parameters

POISSON = {"structural_steel": 0.30, "aluminum_6061": 0.33}  # the contracts' table has no Poisson's ratio
MATERIAL_NAMES = {"structural_steel": "Structural steel", "aluminum_6061": "Aluminum alloy 6061-T6"}


class SetupError(RuntimeError):
    """The part isn't ready. The message says what to ask the user."""


def table_material(key: str) -> Material:
    """A material from the contracts' table, for parts whose file has none."""
    if key not in MATERIALS:
        raise ValueError(f"Unknown material {key!r}. Choose from {sorted(MATERIALS)}.")
    m = MATERIALS[key]
    return Material(name=MATERIAL_NAMES.get(key, key), youngs_mpa=m["E_mpa"], poisson=POISSON.get(key),
                    yield_mpa=m["yield_mpa"], density_kg_m3=m["rho_kg_mm3"] * 1e9)


def material_key(material: Material) -> str | None:
    """The contracts' key closest to a named material; the fake predictor needs one."""
    name = material.name.lower()
    if "6061" in name:
        return "aluminum_6061"
    if "steel" in name:
        return "structural_steel"
    return None


PREDICTORS = ("power_law", "gp")


class App:
    """Entry point. App(backend="fitted" | "calculix" | ..., predictor="power_law" | "gp", runs=training data for the GP).

    runs: a path (the v0 GP trains from it, as before) or {family key: path}. Families without an entry train from
    Role 1's model_dataset.csv under data/processed, cached on disk after the first fit (src/models/gp.py).
    """

    def __init__(self, backend: str | Backend = "fitted", predictor: str = "power_law", runs: str | Path | dict | None = None):
        if predictor not in PREDICTORS:
            raise ValueError(f"Unknown predictor {predictor!r}. Choose from {PREDICTORS}.")
        self.backend = get_backend(backend) if isinstance(backend, str) else backend
        self.predictor = predictor
        self.runs = runs if isinstance(runs, dict) or runs is None else {"l_bracket_v0": runs}
        self._gp: dict[str, object] = {}  # trained once per App and family: a second for v0, minutes for v1 (then cached)

    def open(self, path: str | Path) -> "Part":
        return Part(open_part(path), self.backend, self._predictor_for)

    def _predictor_for(self, m: Material, family: Family = L_BRACKET_V0):
        """The shortlisting model for a part's material and family."""
        source = self.runs.get(family.key) if self.runs else None
        if self.predictor == "gp" and (source or family.key in FAMILY_DIRS):  # the templates have no GP dataset: power law
            if family.key not in self._gp:
                from src.models.gp import GPPredictor, trained_gp  # needs scikit-learn and Role 1's data

                self._gp[family.key] = GPPredictor(source, family=family.key) if source else trained_gp(family.key)
            return self._gp[family.key].for_material(m.youngs_mpa, m.yield_mpa, m.density_kg_m3, poisson=m.poisson, valid=family.valid)
        return PowerLawPredictor(e_mpa=m.youngs_mpa, yield_mpa=m.yield_mpa, density_kg_m3=m.density_kg_m3, family=family.key,
                                 poisson=m.poisson, valid=family.valid)


class Part:
    """One open part: its setup, current dimensions and whether the user has confirmed it."""

    def __init__(self, setup: PartSetup, backend: Backend, predictor_for=None):
        self.setup = setup
        self.backend = backend
        self._predictor_for = predictor_for or (lambda m, fam=L_BRACKET_V0: PowerLawPredictor(
            e_mpa=m.youngs_mpa, yield_mpa=m.yield_mpa, density_kg_m3=m.density_kg_m3, family=fam.key, poisson=m.poisson, valid=fam.valid))
        self.last_prediction = None  # the shortlist behind last_result, for the GUI
        self.family: Family | None = None
        self.file_names: dict[str, str] = {}  # contract name -> parameter name inside the file
        self.values_mm: dict[str, float] = {}
        self.sources: dict[str, str] = {}  # where each part of the setup came from
        self.support: str | None = None
        self.force_n: float | None = None
        self.direction: tuple[float, float, float] | None = None
        self.material: Material | None = None
        self.confirmed = False
        self.last_result: Result | None = None
        match = match_family([p.name for p in setup.parameters])
        if match:
            self.family, mapping = match
            by_name = {p.name: p.value for p in setup.parameters}
            self.file_names = {contract: name for name, contract in mapping.items()}
            self.values_mm = {contract: by_name[name] for name, contract in mapping.items()}
            self.sources["dimensions"] = "file"
        if setup.supports:
            self.support = ", ".join(f"{s.name} ({s.face_count} face{'s' * (s.face_count != 1)})" for s in setup.supports)
            self.sources["supports"] = "file"
        if len(setup.forces) == 1:
            self.force_n = setup.forces[0].magnitude_n
            self.direction = _unit(setup.forces[0].direction)
            self.sources["load"] = "file"
        m = setup.material
        if m and None not in (m.youngs_mpa, m.yield_mpa, m.density_kg_m3):
            self.material = m
            self.sources["material"] = "file"

    # ------------------------------------------------------------ what the agent checks

    @property
    def missing(self) -> list[str]:
        """Setup the user must supply before confirming."""
        out = []
        if self.family is None and not self.setup.parameters:
            out.append("which bracket this is, so its dimensions are known (use_family)")
        if self.support is None:
            out.append("supports")
        if self.force_n is None and len(self.setup.forces) < 2:
            out.append("load")
        if self.material is None:
            out.append("material")
        return out

    @property
    def problems(self) -> list[str]:
        """Blockers that answering questions can't fix inside the emulator."""
        out = []
        if self.family is None and self.setup.parameters:
            names = ", ".join(p.name for p in self.setup.parameters)
            out.append(f"The emulator can't rebuild this shape: its parameters ({names}) don't match a known bracket. A Discovery or Ansys engine could.")
        if len(self.setup.forces) > 1:
            out.append(f"The file has {len(self.setup.forces)} loads; the emulator models one.")
        out += [f"The file has {c}, which the emulator can't model." for c in self.setup.other_conditions]
        if self.family and self.force_n is not None:
            lo, hi = self.family.force_range_n
            if not lo <= self.force_n <= hi:
                out.append(f"The load of {self.force_n:g} N is outside {lo:g} to {hi:g} N, where the {self.family.title} has data.")
            if self.direction and not _same(self.direction, self.family.load_direction):
                out.append(f"A load {_dir_text(self.direction)} isn't modelled; the {self.family.title} takes loads {_dir_text(self.family.load_direction)} only.")
        if self.family:
            for contract, value in self.values_mm.items():
                lo, hi = self.family.ranges_mm[contract]
                if not lo <= value <= hi:
                    out.append(f"{self.family.labels[contract]} is {value:g} mm, outside {lo:g} to {hi:g} mm.")
            if len(self.values_mm) == len(self.family.labels):
                out += [f"The dimensions break {r}" for r in self.family.failed_rules(self.values_mm)]
        return out

    def describe(self) -> str:
        """The confirmation card, in plain language."""
        fam = self.family
        src = lambda key: f" [{self.sources[key]}]" if key in self.sources else ""  # noqa: E731
        lines = [f"Part: {Path(self.setup.path).name} ({self.setup.file_type} file)"
                 + (f", {fam.title}" if fam else "") + f". Engine: {self.backend.name}."]
        if self.support:
            lines.append(f"Fixed: {self.support}{src('supports')}" + (f", treated as the {fam.fixed_face}" if fam and self.sources.get("supports") == "file" else ""))
        else:
            lines.append("Fixed: not set")
        if self.force_n is not None:
            where = f" on the {fam.load_face}" if fam else ""
            lines.append(f"Load: {self.force_n:g} N {_dir_text(self.direction)}{where}{src('load')}")
        else:
            lines.append("Load: not set")
        if self.material:
            m = self.material
            lines.append(f"Material: {m.name}, E {m.youngs_mpa:,.0f} MPa, yield {m.yield_mpa:g} MPa, density {m.density_kg_m3:g} kg/m3{src('material')}")
        else:
            lines.append("Material: not set")
        if fam:
            dims = ", ".join(f"{fam.labels[c]} {v:g} mm ({fam.ranges_mm[c][0]:g} to {fam.ranges_mm[c][1]:g})" for c, v in self.values_mm.items())
            lines.append(f"Dimensions{src('dimensions')}: {dims}")
        if self.setup.gravity:
            lines.append("Note: gravity is on in the file; the emulator ignores self-weight.")
        lines += [f"Note: {n}" for n in self.setup.notes]
        lines.append("Still needed: " + ("; ".join(self.missing) or "nothing"))
        if self.problems:
            lines.append("Can't continue: " + " ".join(self.problems))
        lines.append("Status: " + ("confirmed by the user" if self.confirmed else "waiting for the user to confirm this setup"))
        return "\n".join(lines)

    def state(self) -> dict:
        """Everything above as plain data, for the agent and the UI."""
        fam = self.family
        return {
            "part": self.setup.path,
            "file_type": self.setup.file_type,
            "family": fam.key if fam else None,
            "engine": self.backend.name,
            "dimensions": [{"name": fam.labels[c], "contract_name": c, "value_mm": v, "low_mm": fam.ranges_mm[c][0],
                            "high_mm": fam.ranges_mm[c][1]} for c, v in self.values_mm.items()] if fam else [],
            "support": self.support,
            "load": {"force_n": self.force_n, "direction": self.direction} if self.force_n is not None else None,
            "material": self.material.model_dump() if self.material else None,
            "sources": dict(self.sources),
            "missing": self.missing,
            "problems": self.problems,
            "confirmed": self.confirmed,
        }

    # ------------------------------------------------------------ filling gaps (each resets confirmation)

    def use_family(self, key: str = "l_bracket_v0") -> None:
        """For files without parameters: the user confirms which known bracket this is."""
        if self.setup.parameters:
            raise SetupError("This file defines its own parameters, so its shape comes from the file.")
        if key not in FAMILIES:
            raise ValueError(f"Unknown bracket {key!r}. Known: {sorted(FAMILIES)}.")
        self.family = FAMILIES[key]
        self.values_mm = dict(self.family.baseline_mm)
        self.sources["dimensions"] = "family baseline"
        self.confirmed = False

    def set_support(self, face: str | None = None) -> None:
        fam = self._need_family()
        face = face or fam.fixed_face
        if face != fam.fixed_face:
            raise SetupError(f"The emulator's {fam.title} can only be fixed at the {fam.fixed_face}.")
        self.support, self.sources["supports"], self.confirmed = face, "user", False

    def set_load(self, force_n: float, direction: tuple[float, float, float] | None = None) -> None:
        fam = self._need_family()
        direction = _unit(direction or fam.load_direction)
        lo, hi = fam.force_range_n
        if not lo <= force_n <= hi:
            raise SetupError(f"{force_n:g} N is outside {lo:g} to {hi:g} N, where the {fam.title} has data.")
        if not _same(direction, fam.load_direction):
            raise SetupError(f"The {fam.title} takes loads {_dir_text(fam.load_direction)} only.")
        self.force_n, self.direction = float(force_n), direction
        self.sources["load"], self.confirmed = "user", False

    def set_material(self, key: str) -> None:
        self.material, self.sources["material"], self.confirmed = table_material(key), "user", False

    def confirm(self) -> str:
        """Call only after the user has said the card is right."""
        blockers = self.missing + self.problems
        if blockers:
            raise SetupError("Can't confirm yet. " + " ".join(f"Needed: {b}." if b in self.missing else b for b in blockers))
        self.confirmed = True
        return self.describe()

    # ------------------------------------------------------------ design changes (confirmation stands)

    def set_dimensions(self, values: dict[str, float]) -> None:
        self.values_mm = self._validated(values)

    def solve(self, values: dict[str, float] | None = None, constraints: list | None = None) -> Verified:
        """Solve the current design, or the given values, with the chosen engine."""
        self._ready()
        design = self._validated(values) if values else dict(self.values_mm)
        return self._verify("current" if not values else "candidate", design, _constraints(constraints or []))

    def optimize(self, objective: str = "mass_kg", direction: str = "minimize", constraints: list | None = None,
                 vary: list[str] | None = None, n_candidates: int = 2000, n_verify: int = 5, predictor=None) -> Result:
        """The team workflow: predictor shortlists, this engine verifies finalists, contract Result out."""
        self._ready()
        fam, m = self.family, self.material
        if predictor is None:  # Role 2's model for this family, using the part's own material
            predictor = self._predictor_for(m, fam)
        key = material_key(m)
        if key is None and predictor is None:
            raise SetupError(f"The stand-in predictor knows {sorted(MATERIALS)} only; {m.name} needs a trained model.")
        cons = _constraints(constraints or [("safety_factor", ">=", 2.0)])
        varied = {fam.contract_name(n) for n in (vary or list(fam.labels))}
        if None in varied:
            raise ValueError(f"Unknown dimension in {vary}. Known: {list(fam.labels.values())}.")
        params = [ParameterRange(name=c, low_mm=lo, high_mm=hi) if c in varied else ParameterRange(name=c, low_mm=self.values_mm[c], high_mm=self.values_mm[c])
                  for c, (lo, hi) in fam.ranges_mm.items()]
        spec = StudySpec(
            bracket_family=fam.key,
            objective=Objective(metric=objective, direction=direction),
            constraints=cons,
            parameters=params,
            load=Load(force_n=self.force_n, angle_deg=math.degrees(math.atan2(self.direction[1], self.direction[0]))),
            material_key=key or m.name,
            n_candidates=n_candidates,
            n_verify=n_verify,
            assumptions=self._assumptions(),
        )
        capture = _Capture(predictor or FakePredictor())
        result = run_pipeline(spec, dict(self.values_mm), predictor=capture, solver=_PipelineSolver(self))
        if self.backend.name == "formula_emulator":
            result.warnings.append("Formula engine: stress and deflection come from beam theory for a different bracket, not FEA. Use the fitted or CalculiX engine.")
        if self.backend.name == "power_law_v0":
            result.warnings.append("Finalists were checked with the fitted power law, not a real solver. Use the CalculiX engine (Role 1) for a real check.")
        if fam.key != "l_bracket_v0":
            result.warnings.append(f"{fam.title}: the GUI's beam equations describe the v0 L-bracket; the numbers above come from the {fam.key} models and engine.")
        self.last_result, self.last_prediction = result, capture.prediction
        return result

    def save(self, dst: str | Path, values: dict[str, float] | None = None) -> Path:
        """Discovery parts: a .dsco copy with new values. Other types: a .json of values to type in."""
        fam = self._need_family()
        design = self._validated(values) if values else dict(self.values_mm)
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if self.setup.file_type == "discovery" and self.file_names:
            if dst.suffix.lower() != ".dsco":
                raise ValueError("Save a Discovery part as .dsco")
            return save_with_parameters(self.setup.path, dst, {self.file_names[c]: v for c, v in design.items()})
        if dst.suffix.lower() != ".json":
            raise ValueError("The emulator can't edit this file type; save the values as .json")
        dst.write_text(json.dumps({"part": self.setup.path, "family": fam.key,
                                  "values_mm": {fam.labels[c]: round(v, 3) for c, v in design.items()},
                                  "note": "Enter these values in Discovery; the emulator can't edit this file type."}, indent=2), encoding="utf-8")
        return dst

    # ------------------------------------------------------------ internals

    def _need_family(self) -> Family:
        if self.family is None:
            raise SetupError("The emulator doesn't know this part's shape yet. " + " ".join(self.problems or ["Ask which bracket it is (use_family)."]))
        return self.family

    def _ready(self) -> None:
        if not self.confirmed:
            todo = self.missing + self.problems
            raise SetupError("Confirm the setup with the user first. " + (" ".join(todo) if todo else "Show describe() and call confirm() once they agree."))

    def _validated(self, values: dict[str, float]) -> dict[str, float]:
        fam = self._need_family()
        new = dict(self.values_mm)
        for name, value in values.items():
            contract = fam.contract_name(name)
            if contract is None:
                raise ValueError(f"Unknown dimension {name!r}. Known: {list(fam.labels.values())}.")
            lo, hi = fam.ranges_mm[contract]
            if not lo <= value <= hi:
                raise ValueError(f"{fam.labels[contract]} {value:g} mm is outside {lo:g} to {hi:g} mm.")  # never clamp
            new[contract] = float(value)
        broken = fam.failed_rules(new)
        if broken:
            raise ValueError(f"These dimensions can't be built: {' '.join(broken)}")
        return new

    def _source(self) -> str:
        """Verified.source: engines that serve several families name the one they solved."""
        source = getattr(self.backend, "source", None)
        return source(self.family) if callable(source) else self.backend.name

    def _verify(self, design_id: str, design: dict[str, float], constraints: list[Constraint]) -> Verified:
        out = self.backend.solve(self.family, design, self.force_n, self.material)
        metrics = {**out, "safety_factor": self.material.yield_mpa / out["max_stress_mpa"]}
        violated = [f"{c.metric} {c.op} {c.value:g}" for c in constraints
                    if not (metrics[c.metric] >= c.value if c.op == ">=" else metrics[c.metric] <= c.value)]
        return Verified(design_id=design_id, design=design, source=self._source(), feasible=not violated, violated=violated, **metrics)

    def _assumptions(self) -> list[str]:
        fam, out = self.family, []
        if self.sources.get("supports") == "file":
            out.append(f"Supports from the file ({self.support}) are the {fam.fixed_face}.")
        out.append(f"Load {self.force_n:g} N {_dir_text(self.direction)} on the {fam.load_face} ({self.sources['load']}).")
        out.append(f"Material {self.material.name} ({self.sources['material']}).")
        if self.sources.get("dimensions") == "family baseline":
            out.append(f"Starting dimensions are the {fam.title} baseline, confirmed by the user.")
        if self.setup.gravity:
            out.append("Self-weight ignored.")
        out.append("Setup confirmed by the user.")
        return out


class _Capture:
    """Keeps the Prediction that run_pipeline asks for, so the GUI can show the shortlist."""

    def __init__(self, inner):
        self.inner, self.prediction = inner, None

    def predict(self, spec: StudySpec, *args, **kwargs):
        self.prediction = self.inner.predict(spec, *args, **kwargs)
        return self.prediction


class _PipelineSolver:
    """Adapts the engine to contracts.run_pipeline's solver interface."""

    def __init__(self, part: Part):
        self.part = part
        self.source = part._source()

    def solve(self, spec: StudySpec, design_id: str, design: dict[str, float]) -> Verified:
        return self.part._verify(design_id, dict(design), list(spec.constraints))


def _constraints(items: list) -> list[Constraint]:
    """Accept Constraint objects, (metric, op, value) tuples or {"metric", "op", "value"} dicts."""
    out = []
    for c in items:
        if isinstance(c, Constraint):
            out.append(c)
        elif isinstance(c, dict):
            out.append(Constraint(**c))
        else:
            out.append(Constraint(metric=c[0], op=c[1], value=c[2]))
    return out


def _unit(v) -> tuple[float, float, float]:
    n = math.sqrt(sum(float(x) ** 2 for x in v)) or 1.0
    return tuple(float(x) / n + 0.0 for x in v)  # + 0.0 turns -0.0 into 0.0


def _same(a, b, tol: float = 1e-6) -> bool:
    return all(abs(x - y) < tol for x, y in zip(_unit(a), _unit(b)))


def _dir_text(d) -> str:
    axis = [i for i, c in enumerate(d) if abs(c) > 1e-9]
    if len(axis) == 1:
        return f"along {'-' if d[axis[0]] < 0 else '+'}{'XYZ'[axis[0]]}"
    return "along (" + ", ".join(f"{c:.3g}" for c in d) + ")"
