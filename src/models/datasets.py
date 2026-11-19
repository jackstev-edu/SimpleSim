"""Role 1's model datasets, one per bracket family, in one shape the models can train from.

Each family folder holds model_dataset.csv (every input, including Poisson's ratio, and the
reference targets at 500 N and E = 200,000 MPa) and summary.json, whose model_inputs lists the
columns the models take. load_model_dataset() reads both and returns a frame with the inputs
plus max_stress_mpa, max_disp_mm, mass_kg, volume_mm3, load_force_n, material_key and
mesh_rule, the names the GP and the power law already use for runs.csv. A plain contract
runs.csv loads the same way, with the five v0 dimensions as inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from contracts.models import PARAM_NAMES

V0, V1 = "l_bracket_v0", "l_bracket_gusset_v1"
FAMILY_DIRS = {V0: Path("data/processed/formal_v0"), V1: Path("data/processed/formal_v1_l_bracket_gusset_v1")}
# Geometry columns per family, in model order. PR B moves this table into contracts.FAMILY_PARAMS.
FAMILY_GEOMETRY = {V0: tuple(PARAM_NAMES), V1: (*PARAM_NAMES, "vertical_height_mm", "gusset_thickness_mm", "gusset_length_mm")}
METRICS = ("max_stress_mpa", "max_disp_mm")
REF_FORCE_N, REF_E_MPA = 500.0, 200_000.0
DEFAULT_POISSON = 0.30  # when a spec or part gives no Poisson's ratio
RENAME = {"reference_max_stress_mpa": "max_stress_mpa", "reference_max_disp_mm": "max_disp_mm",
          "reference_mass_kg": "mass_kg", "reference_load_force_n": "load_force_n"}


@dataclass
class ModelData:
    """One family's training table and what the columns mean."""

    family: str
    inputs: tuple[str, ...]  # model inputs in order, geometry first, then poissons_ratio when sampled
    frame: pd.DataFrame
    ref_force_n: float = REF_FORCE_N
    ref_e_mpa: float = REF_E_MPA
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)  # sampled ranges, from summary.json or the data
    source: str = ""

    @property
    def geometry(self) -> tuple[str, ...]:
        return tuple(n for n in self.inputs if n != "poissons_ratio")

    @property
    def has_poisson(self) -> bool:
        return "poissons_ratio" in self.inputs

    def X(self, rows: pd.DataFrame | None = None) -> np.ndarray:
        """Input matrix in self.inputs order."""
        return (self.frame if rows is None else rows)[list(self.inputs)].to_numpy(dtype=float)

    def log_targets(self, rows: pd.DataFrame | None = None) -> dict[str, np.ndarray]:
        """log(metric) at the reference load: stress scales with force, so rows at other loads normalise."""
        f = self.frame if rows is None else rows
        return {m: np.log(f[m].to_numpy(dtype=float) * self.ref_force_n / f["load_force_n"].to_numpy(dtype=float)) for m in METRICS}

    def subset(self, index) -> "ModelData":
        return ModelData(self.family, self.inputs, self.frame.iloc[index].reset_index(drop=True), self.ref_force_n, self.ref_e_mpa, self.ranges, self.source)


def resolve_source(source: str | Path) -> Path:
    """A family key, a dataset folder or a CSV path, to the CSV to read."""
    if isinstance(source, str) and source in FAMILY_DIRS:
        return FAMILY_DIRS[source] / "model_dataset.csv"
    p = Path(source)
    if p.is_dir():
        return p / "model_dataset.csv" if (p / "model_dataset.csv").exists() else p / "runs.csv"
    return p


def load_model_dataset(source: str | Path = V0, frame: pd.DataFrame | None = None, inputs: tuple[str, ...] | None = None,
                       family: str | None = None) -> ModelData:
    """Read a family's model_dataset.csv (or a runs.csv), with the inputs from summary.json when it is there."""
    path = None
    if frame is None:
        path = resolve_source(source)
        if not path.exists():
            raise FileNotFoundError(f"No training data at {path}. Copy Role 1's runs.csv or model_dataset.csv there "
                                    f"(known families: {sorted(FAMILY_DIRS)}; see docs/models.md).")
        frame = pd.read_csv(path)
    frame = frame.rename(columns=RENAME).copy()
    summary = {}
    if path is not None and (path.parent / "summary.json").exists() and path.name == "model_dataset.csv":
        summary = json.loads((path.parent / "summary.json").read_text(encoding="utf-8"))
    key = source if isinstance(source, str) and source in FAMILY_DIRS else None
    family = family or summary.get("bracket_family") or (str(frame["bracket_family"].iloc[0]) if "bracket_family" in frame else key or V0)
    if inputs is None:
        inputs = tuple(summary.get("model_inputs") or [n for n in (*FAMILY_GEOMETRY.get(family, PARAM_NAMES), "poissons_ratio") if n in frame])
    missing = [c for c in inputs if c not in frame]
    if missing:
        raise ValueError(f"{path or 'the frame'} lacks input columns {missing}")
    if "load_force_n" not in frame:
        frame["load_force_n"] = REF_FORCE_N
    if "material_key" not in frame:
        frame["material_key"] = "role1_isotropic_reference"
    if "volume_mm3" not in frame and "mass_kg" in frame:
        frame["volume_mm3"] = frame["mass_kg"] / 7850e-9  # Role 1's reference density
    ref_e = float(frame["reference_youngs_modulus_mpa"].iloc[0]) if "reference_youngs_modulus_mpa" in frame else REF_E_MPA
    ranges = {k: (float(v["low"]), float(v["high"])) for k, v in summary.get("ranges", {}).items()}
    if "poissons_ratio_range" in summary:  # the v0 summary lists only the sampled Poisson range
        ranges["poissons_ratio"] = (float(summary["poissons_ratio_range"]["low"]), float(summary["poissons_ratio_range"]["high"]))
    for n in inputs:
        ranges.setdefault(n, (float(frame[n].min()), float(frame[n].max())))
    return ModelData(family, tuple(inputs), frame.reset_index(drop=True), float(frame["load_force_n"].iloc[0]), ref_e, ranges, str(path or "frame"))


def split_indices(n: int, seed: int = 0, test_frac: float = 0.2, cal_frac: float = 0.1) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(train, calibration, test) row indices: a seeded 20% test split, then 10% of the rest for calibration."""
    perm = np.random.default_rng(seed).permutation(n)
    n_test = int(round(n * test_frac))
    test, rest = perm[:n_test], perm[n_test:]
    n_cal = int(round(len(rest) * cal_frac))
    return np.sort(rest[n_cal:]), np.sort(rest[:n_cal]), np.sort(test)


def is_v0_mesh_rule(mesh_rules) -> bool:
    """True when every label came from v0's uniform rule (tet2_<min>to<max>mm, no curvature sizing)."""
    rules = pd.Series(mesh_rules).astype(str)
    return len(rules) > 0 and bool(rules.str.fullmatch(r"tet2_[\d.]+to[\d.]+mm").all())
