"""Shared hand-off contracts. Import from here, never redefine these shapes elsewhere."""

from .models import (
    PARAM_NAMES,
    RUN_COLUMNS,
    SCHEMA_VERSION,
    Candidate,
    Constraint,
    Estimate,
    Load,
    Objective,
    ParameterRange,
    Prediction,
    Result,
    Run,
    StudySpec,
    Verified,
)
from .fakes import FakePredictor, FakeSolver, beam_formula, run_pipeline

__all__ = [
    "PARAM_NAMES",
    "RUN_COLUMNS",
    "SCHEMA_VERSION",
    "Candidate",
    "Constraint",
    "Estimate",
    "Load",
    "Objective",
    "ParameterRange",
    "Prediction",
    "Result",
    "Run",
    "StudySpec",
    "Verified",
    "FakePredictor",
    "FakeSolver",
    "beam_formula",
    "run_pipeline",
]
