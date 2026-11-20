"""Geometry families: what the shared mesh and solver code needs to know about one bracket shape.

v0 code built the L-bracket directly. A family bundles the CadQuery builder and the free-end
face location, so generate_mesh and solve_case work for the v0 L-bracket (the default) and for
the gusseted v1 bracket without touching the deck builder, parsers or QA.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class GeometryFamily:
    """One parametric bracket shape the pipeline can build, mesh and solve."""

    key: str
    build: Callable[[Any], Any]  # case -> CadQuery solid
    load_face_x_mm: Callable[[Any], float]  # X of the free-end face that takes the load
    fixed_face_x_mm: float = 0.0  # the fully fixed back face
