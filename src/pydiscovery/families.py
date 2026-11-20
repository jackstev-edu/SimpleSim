"""Bracket shapes the emulator can rebuild, and how file parameter names map to contract names.

A real Discovery backend can resize any part. The emulator only knows families that Role 1's
solver can regenerate, so a part is solvable here only when its parameters match a family.
Two families: the v0 L-bracket and the v1 gusseted bracket (three more dimensions and named
geometry rules from Role 1's spec, so a design that cannot be built is refused by name).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from contracts.models import FAMILY_PARAMS, PARAM_NAMES


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
    rules: Callable[[dict[str, float]], list[str]] = field(default=lambda design: [])  # geometry rules a design must pass

    def contract_name(self, name: str) -> str | None:
        """Contract name for a file or contract parameter name, ignoring case, spaces and units."""
        key = _norm(name)
        for contract, label in self.labels.items():
            if key in (_norm(contract), _norm(label)):
                return contract
        return None

    def failed_rules(self, design_mm: dict[str, float]) -> list[str]:
        """Why this design cannot be built, or an empty list."""
        return self.rules(design_mm)

    def valid(self, design_mm: dict[str, float]) -> bool:
        return not self.rules(design_mm)


# Role 1's v0 family, from docs/bracket_family_v0.md.
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


def _gusset_rules(design_mm: dict[str, float]) -> list[str]:
    """Role 1's named v1 geometry rules (src/data/gusset_v1/spec.py), which import nothing beyond pydantic."""
    from src.data.gusset_v1.spec import failed_rules

    return failed_rules(_Geometry(design_mm))


class _Geometry:
    """Attribute access over a design dict, the shape the rules read."""

    def __init__(self, design_mm: dict[str, float]):
        self.__dict__.update(design_mm)


# Role 1's v1 family, from docs/bracket_family_v1_l_bracket_gusset_v1.md and the dataset's summary.json.
L_BRACKET_GUSSET_V1 = Family(
    key="l_bracket_gusset_v1",
    title="Gusseted L-bracket v1",
    labels={
        **L_BRACKET_V0.labels,
        "vertical_height_mm": "Vertical height",
        "gusset_thickness_mm": "Gusset thickness",
        "gusset_length_mm": "Gusset length",
    },
    ranges_mm={
        **L_BRACKET_V0.ranges_mm,
        "fillet_radius_mm": (2.5, 8.0),  # kept apart from the 2 mm junction fillet
        "vertical_height_mm": (50.0, 90.0),
        "gusset_thickness_mm": (3.0, 10.0),
        "gusset_length_mm": (12.0, 35.0),
    },
    baseline_mm={
        **L_BRACKET_V0.baseline_mm,
        "vertical_height_mm": 60.0,
        "gusset_thickness_mm": 6.0,
        "gusset_length_mm": 20.0,
    },
    force_range_n=(100.0, 1000.0),
    load_direction=(0.0, -1.0, 0.0),
    fixed_face="back face at X = 0",
    load_face="free-end face at max X",
    rules=_gusset_rules,
)

FAMILIES = {L_BRACKET_V0.key: L_BRACKET_V0, L_BRACKET_GUSSET_V1.key: L_BRACKET_GUSSET_V1}
for _family in FAMILIES.values():  # stays in step with the contracts
    assert tuple(_family.labels) == FAMILY_PARAMS[_family.key], _family.key
assert set(L_BRACKET_V0.labels) == set(PARAM_NAMES)


def match_family(parameter_names: list[str]) -> tuple[Family, dict[str, str]] | None:
    """The largest family whose every parameter appears in the file, with file name -> contract name."""
    for family in sorted(FAMILIES.values(), key=lambda f: -len(f.labels)):  # a gusset file also names the five v0 dimensions
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
