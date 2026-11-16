"""Bracket shapes the emulator can rebuild, and how file parameter names map to contract names.

A real Discovery backend can resize any part. The emulator only knows families that Role 1's
solver can regenerate, so a part is solvable here only when its parameters match a family.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from contracts.models import PARAM_NAMES


@dataclass(frozen=True)
class Family:
    key: str  # StudySpec.bracket_family
    title: str
    labels: dict[str, str]  # contract name -> name used in Discovery files and on screen
    ranges_mm: dict[str, tuple[float, float]]  # where Role 1 has solved designs
    baseline_mm: dict[str, float]
    force_range_n: tuple[float, float]
    load_direction: tuple[float, float, float]  # the only direction the family models
    fixed_face: str
    load_face: str

    def contract_name(self, name: str) -> str | None:
        """Contract name for a file or contract parameter name, ignoring case, spaces and units."""
        key = _norm(name)
        for contract, label in self.labels.items():
            if key in (_norm(contract), _norm(label)):
                return contract
        return None


# Role 1's v0 family, from docs/bracket_family_v0.md on branch Jin(role--data).
L_BRACKET_V0 = Family(
    key="l_bracket_v0",
    title="L-bracket v0",
    labels={
        "thickness_mm": "Thickness",
        "flange_length_mm": "Flange length",
        "arm_width_mm": "Arm width",
        "hole_diameter_mm": "Hole diameter",
        "fillet_radius_mm": "Fillet radius",
    },
    ranges_mm={
        "thickness_mm": (4.0, 10.0),
        "flange_length_mm": (75.0, 120.0),
        "arm_width_mm": (32.0, 60.0),
        "hole_diameter_mm": (6.0, 12.0),
        "fillet_radius_mm": (2.0, 8.0),
    },
    baseline_mm={
        "thickness_mm": 6.0,
        "flange_length_mm": 80.0,
        "arm_width_mm": 40.0,
        "hole_diameter_mm": 8.0,
        "fillet_radius_mm": 4.0,
    },
    force_range_n=(100.0, 1000.0),
    load_direction=(0.0, -1.0, 0.0),
    fixed_face="back face at X = 0",
    load_face="free-end face at max X",
)

FAMILIES = {L_BRACKET_V0.key: L_BRACKET_V0}
assert set(L_BRACKET_V0.labels) == set(PARAM_NAMES)  # stays in step with the contracts


def match_family(parameter_names: list[str]) -> tuple[Family, dict[str, str]] | None:
    """The family whose every parameter appears in the file, with file name -> contract name."""
    for family in FAMILIES.values():
        mapping = {}
        for name in parameter_names:
            contract = family.contract_name(name)
            if contract:
                mapping[name] = contract
        if set(mapping.values()) == set(family.labels):
            return family, mapping
    return None


def _norm(name: str) -> str:
    """'Flange Length (mm)', 'flange_length_mm' and 'Flange length' all become 'flangelength'."""
    name = re.sub(r"\(.*?\)|\[.*?\]", "", name.lower())
    name = re.sub(r"_mm$|\bmm\b", "", name.strip())
    return re.sub(r"[^a-z]", "", name)
