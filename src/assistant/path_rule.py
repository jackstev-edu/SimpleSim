"""Which search path a part takes: Jack's fast tools for a known family, the ANSYS agent otherwise.

Decided in code from Jack's Part, never by the LLM (spec 2026-11-18-assistant-front-layer-design.md).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from contracts.fakes import MATERIALS

FAST_MATERIALS = tuple(MATERIALS)  # the only materials the fast predictor knows


@dataclass(frozen=True)
class PathDecision:
    path: Literal["fast", "slow"]
    reason: str


def part_path(part) -> PathDecision | None:
    """fast for a known family, slow when Jack's emulator cannot model the part, None to ask the user."""
    if part.family is not None:
        return PathDecision("fast", f"this is the {part.family.title}, which the team's predictor knows")
    if part.problems:
        return PathDecision("slow", part.problems[0])
    return None


def _unit(v: Sequence[float]) -> tuple[float, ...]:
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v) if n else tuple(v)


def outside_family(family, *, force_n: float | None = None, direction: Sequence[float] | None = None,
                   material: str | None = None) -> str | None:
    """Plain-words reason why an input is outside what the fast path models, or None."""
    if force_n is not None:
        lo, hi = family.force_range_n
        if not lo <= force_n <= hi:
            return f"{force_n:g} N is outside {lo:g} to {hi:g} N, the range the {family.title} model covers"
    if direction is not None:
        want = _unit(family.load_direction)
        if any(abs(a - b) > 1e-6 for a, b in zip(_unit(direction), want)):
            return f"the {family.title} model only takes loads along {list(family.load_direction)}"
    if material is not None and material not in FAST_MATERIALS:
        return f"{material} is not one of the fast path's materials ({', '.join(FAST_MATERIALS)})"
    return None
