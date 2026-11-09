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

SCHEMA_VERSION = "0.1.0"

# v0 bracket family: the five dimensions from the MVP L-bracket. Confirm with Role 1.
PARAM_NAMES = ("thickness_mm", "flange_length_mm", "arm_width_mm", "hole_diameter_mm", "fillet_radius_mm")

MetricName = Literal["mass_kg", "max_stress_mpa", "max_disp_mm", "safety_factor"]
ParamName = Literal["thickness_mm", "flange_length_mm", "arm_width_mm", "hole_diameter_mm", "fillet_radius_mm"]


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
    """One static load at the bolt hole; -90 deg is straight down."""

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
        return self


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
