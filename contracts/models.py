"""The four hand-off contracts every role imports.

Agent -> Model:  StudySpec        (study_spec.json)
Data  -> Model:  Run rows          (runs.csv, one row per solve)
Model -> Agent:  Prediction        (prediction.json)
Agent -> UI:     Result            (result.json)

Units live in the field names (mm, N, MPa, kg). Changing a field here is a
contract change and needs both sides of that hand-off to approve the PR.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "0.2.0"  # 0.2.0: FAMILY_PARAMS, the v1 names in ParamName, family checks on StudySpec and Result

# v0 bracket family: the five dimensions from the MVP L-bracket (docs/bracket_family_v0.md).
PARAM_NAMES = ("thickness_mm", "flange_length_mm", "arm_width_mm", "hole_diameter_mm", "fillet_radius_mm")

# Every family's dimensions, in model order. A StudySpec's parameters and every design in a Result
# use exactly its family's set. v1 is the gusseted bracket (docs/bracket_family_v1_l_bracket_gusset_v1.md).
# T01 to T10 are Role 1's templates (docs/role1_multifamily_handoff.md): the v0 frame with a variable
# leg height, a hole only where the template has one, then the template's own cut or rib dimensions.
_PLATE = ("thickness_mm", "flange_length_mm", "arm_width_mm")
_HOLED = PARAM_NAMES + ("vertical_height_mm",)  # the five v0 names plus the leg height
_SLOTTED = _PLATE + ("fillet_radius_mm", "vertical_height_mm")  # no plain hole
FAMILY_PARAMS: dict[str, tuple[str, ...]] = {
    "l_bracket_v0": PARAM_NAMES,
    "l_bracket_gusset_v1": PARAM_NAMES + ("vertical_height_mm", "gusset_thickness_mm", "gusset_length_mm"),
    "t01_double_hole_v1": _HOLED,
    "t02_single_slot_v1": _SLOTTED + ("slot_width_mm", "slot_straight_length_mm"),
    "t03_twin_slot_v1": _SLOTTED + ("slot_width_mm", "slot_straight_length_mm"),
    "t04_rounded_window_v1": _SLOTTED + ("window_length_mm", "window_width_mm", "window_radius_mm"),
    "t05_side_notches_v1": _HOLED + ("notch_radius_mm",),
    "t06_tapered_v1": _HOLED + ("tip_trim_mm",),
    "t07_stepped_v1": _HOLED + ("step_trim_mm",),
    "t08_twin_gusset_v1": _HOLED + ("gusset_thickness_mm", "gusset_length_mm"),
    "t09_perforated_gusset_v1": _HOLED + ("gusset_thickness_mm", "gusset_length_mm", "rib_hole_diameter_mm"),
    "t10_offset_planform_v1": _HOLED + ("offset_trim_mm",),
}

MetricName = Literal["mass_kg", "max_stress_mpa", "max_disp_mm", "safety_factor"]
ParamName = Literal["thickness_mm", "flange_length_mm", "arm_width_mm", "hole_diameter_mm", "fillet_radius_mm",
                    "vertical_height_mm", "gusset_thickness_mm", "gusset_length_mm",
                    "slot_width_mm", "slot_straight_length_mm", "window_length_mm", "window_width_mm", "window_radius_mm",
                    "notch_radius_mm", "tip_trim_mm", "step_trim_mm", "rib_hole_diameter_mm", "offset_trim_mm"]


def family_params(bracket_family: str) -> tuple[str, ...]:
    """The dimension names of a family, or a ValueError naming the known families."""
    if bracket_family not in FAMILY_PARAMS:
        raise ValueError(f"unknown bracket_family {bracket_family!r}; known: {sorted(FAMILY_PARAMS)}")
    return FAMILY_PARAMS[bracket_family]


def _check_design(design: dict, expected: tuple[str, ...], what: str) -> None:
    if set(design) != set(expected):
        raise ValueError(f"{what} must use exactly the keys {list(expected)}, got {sorted(design)}")


class Versioned(BaseModel):
    """Every file carries the schema version it was written against."""

    schema_version: str = SCHEMA_VERSION


# ---------------------------------------------------------------- Agent -> Model


class Objective(BaseModel):
    metric: MetricName
    direction: Literal["minimize", "maximize"]


class Constraint(BaseModel):
    metric: MetricName
    op: Literal[">=", "<="]
    value: float


class ParameterRange(BaseModel):
    """low == high means the parameter is held fixed."""

    name: ParamName
    low_mm: float = Field(gt=0)
    high_mm: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> "ParameterRange":
        if self.high_mm < self.low_mm:
            raise ValueError(f"{self.name}: high_mm below low_mm")
        return self


class Load(BaseModel):
    """One static load on the free-end face of the flange (not the bolt hole); -90 deg is straight down."""

    force_n: float = Field(gt=0)
    angle_deg: float = -90.0


class StudySpec(Versioned):
    """What the agent asks the model to search. Written by Role 3, read by Role 2."""

    bracket_family: str = "l_bracket_v0"
    objective: Objective
    constraints: list[Constraint] = Field(min_length=1)
    parameters: list[ParameterRange] = Field(min_length=1)
    load: Load
    material_key: str = "structural_steel"
    n_candidates: int = Field(default=50_000, ge=100, le=500_000)
    n_verify: int = Field(default=5, ge=1, le=20)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_params(self) -> "StudySpec":
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("duplicate parameter ranges")
        expected = family_params(self.bracket_family)
        if set(names) != set(expected):  # a fixed dimension is a range with low == high, never a missing one
            raise ValueError(f"parameters of a {self.bracket_family} spec must be exactly {list(expected)}, got {names}")
        return self

    def param_names(self) -> tuple[str, ...]:
        """The family's dimensions, in model order."""
        return family_params(self.bracket_family)


# ---------------------------------------------------------------- Data -> Model


class Run(Versioned):
    """One solved design: the row format of runs.csv. Written by Role 1, read by Role 2."""

    design_id: str
    source: str  # fem2d, ansys_mechanical, ansys_discovery, calculix ...
    mesh_rule: str  # e.g. "edge=0.1*arm_width, fillet refined x2"
    material_key: str
    load_force_n: float = Field(gt=0)
    load_angle_deg: float
    thickness_mm: float = Field(gt=0)
    flange_length_mm: float = Field(gt=0)
    arm_width_mm: float = Field(gt=0)
    hole_diameter_mm: float = Field(gt=0)
    fillet_radius_mm: float = Field(gt=0)
    max_stress_mpa: float = Field(gt=0)
    max_disp_mm: float = Field(gt=0)
    mass_kg: float = Field(gt=0)

    def design(self) -> dict[str, float]:
        """Just the geometry, in contract order."""
        return {n: getattr(self, n) for n in PARAM_NAMES}


# runs.csv header, in this order, no extra columns without a contract change.
RUN_COLUMNS = tuple(Run.model_fields)


# ---------------------------------------------------------------- Model -> Agent


class Estimate(BaseModel):
    """Point estimate with a 90 percent interval."""

    value: float
    p05: float
    p95: float


class Candidate(BaseModel):
    design_id: str
    design: dict[ParamName, float]
    mass_kg: float
    max_stress_mpa: Estimate
    max_disp_mm: Estimate
    safety_factor: Estimate
    feasible_p95: bool  # constraints hold even at the pessimistic end
    in_training_range: bool


class Prediction(Versioned):
    """Shortlist the model returns for a spec. Written by Role 2, read by Role 3."""

    model_name: str  # gp, formula_gp, autogluon, fake_formula
    trained_on_runs: int
    shortlist: list[Candidate]  # ranked by objective, best first
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_family(self) -> "Prediction":
        keys = {frozenset(c.design) for c in self.shortlist}
        if len(keys) > 1:
            raise ValueError("shortlist designs mix dimension sets; every candidate must use one family's keys")
        if keys and next(iter(keys)) not in {frozenset(v) for v in FAMILY_PARAMS.values()}:
            raise ValueError(f"shortlist designs use {sorted(next(iter(keys)))}, which is no family's set")
        return self


# ---------------------------------------------------------------- Agent -> UI


class Verified(BaseModel):
    """A finalist after a real solve. Solver numbers replace the estimates."""

    design_id: str
    design: dict[ParamName, float]
    source: str
    mass_kg: float
    max_stress_mpa: float
    max_disp_mm: float
    safety_factor: float
    feasible: bool
    violated: list[str] = Field(default_factory=list)


class Result(Versioned):
    """Everything the UI shows. Written by Role 3, read by Role 4."""

    spec: StudySpec
    baseline: Verified
    recommended: Verified | None  # None when nothing feasible was found
    finalists: list[Verified]
    binding_constraint: str | None
    sensitivities: dict[ParamName, float]  # correlation with the objective
    explanation: str  # built from the numbers above, never new numbers
    discovery_values: dict[ParamName, float]  # what the student types in
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _designs_match_the_family(self) -> "Result":
        expected = self.spec.param_names()
        _check_design(self.baseline.design, expected, "baseline.design")
        if self.recommended is not None:
            _check_design(self.recommended.design, expected, "recommended.design")
        for i, f in enumerate(self.finalists):
            _check_design(f.design, expected, f"finalists[{i}].design")
        _check_design(self.discovery_values, expected, "discovery_values")
        extra = set(self.sensitivities) - set(expected)
        if extra:
            raise ValueError(f"sensitivities name dimensions outside the {self.spec.bracket_family} family: {sorted(extra)}")
        return self
