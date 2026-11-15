"""Exclusion zone around supports and loads (pure numpy/scipy, no ANSYS).

A fully fixed face or a small loaded face gives a singular stress spike at its
edge. The safety factor is judged on the peak outside a zone grown around the
support and load nodes until that outside peak settles. When in doubt the
smaller zone (higher stress) wins, so a real stress is never hidden.
Units: mm and MPa.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree


def step_for(min_wall_mm: float | None, element_size_mm: float) -> float:
    """Zone growth step: half the thinnest wall, or the element size if unknown."""
    return min_wall_mm / 2 if min_wall_mm is not None else element_size_mm


def _dist(coords_mm: np.ndarray, idx: np.ndarray) -> np.ndarray:
    # Distance of every node to the nearest node in idx (inf when idx is empty).
    if len(idx) == 0:
        return np.full(len(coords_mm), np.inf)
    return cKDTree(coords_mm[idx]).query(coords_mm)[0]


def _xyz(coords_mm: np.ndarray, i: int) -> list[float]:
    return [round(float(v), 2) for v in coords_mm[i]]


def exclusion_zone(coords_mm: np.ndarray, stress_mpa: np.ndarray,
                   support_idx: np.ndarray, load_idx: np.ndarray,
                   guard_idx: np.ndarray, step_mm: float, n_steps: int = 3,
                   tol: float = 0.05, zone_mm: float | None = None) -> dict:
    """Pick the zone size and the stress to judge the limit on.

    guard_idx are nodes whose stress must never be excluded (they cap the zone).
    With zone_mm given, that one size is used with no search (zone_settled None).
    """
    coords_mm = np.asarray(coords_mm, dtype=float)
    stress_mpa = np.asarray(stress_mpa, dtype=float)
    d_s = _dist(coords_mm, np.asarray(support_idx, dtype=int))
    d_l = _dist(coords_mm, np.asarray(load_idx, dtype=int))
    d = np.minimum(d_s, d_l)
    raw_i = int(np.argmax(stress_mpa))

    def peak_outside(size: float) -> tuple[float, int]:
        # Max stress over nodes farther than `size` from every source.
        outside = np.flatnonzero(d > size)
        if len(outside) == 0:
            return float(stress_mpa[raw_i]), raw_i
        i = int(outside[np.argmax(stress_mpa[outside])])
        return float(stress_mpa[i]), i

    def build(size: float, settled: bool | None) -> dict:
        value, at = peak_outside(size)
        inside = d[raw_i] <= size
        return {
            "stress_for_limit_MPa": value,
            "stress_for_limit_at_mm": _xyz(coords_mm, at),
            "excluded_peak_MPa": float(stress_mpa[raw_i]) if inside else None,
            "excluded_peak_at_mm": _xyz(coords_mm, raw_i) if inside else None,
            "excluded_at": (("support" if d_s[raw_i] <= d_l[raw_i] else "load")
                            if inside else None),
            "zone_mm": size,
            "zone_settled": settled,
        }

    if zone_mm is not None:
        return build(float(zone_mm), None)
    # The raw peak is already outside the first zone: nothing to exclude.
    if d[raw_i] > step_mm:
        return build(step_mm, True)

    # Zones must stay smaller than the distance from any source to a guard node.
    d_guard = float(d[np.asarray(guard_idx, dtype=int)].min()) if len(guard_idx) else np.inf
    sizes = [step_mm * k for k in range(1, n_steps + 1) if step_mm * k < d_guard]
    if not sizes:
        # No zone fits: nothing is excluded and the raw peak is the answer.
        return {
            "stress_for_limit_MPa": float(stress_mpa[raw_i]),
            "stress_for_limit_at_mm": _xyz(coords_mm, raw_i),
            "excluded_peak_MPa": None,
            "excluded_peak_at_mm": None,
            "excluded_at": None,
            "zone_mm": 0,
            "zone_settled": False,
        }
    peaks = [peak_outside(s)[0] for s in sizes]
    for k in range(1, len(sizes)):
        # Settled when growing the zone no longer changes the outside peak.
        if abs(peaks[k] - peaks[k - 1]) <= tol * abs(peaks[k - 1]):
            return build(sizes[k - 1], True)
    return build(sizes[0], False)
