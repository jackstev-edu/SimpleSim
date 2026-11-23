"""Exclusion zone around supports and loads, on a synthetic 1-D bar of nodes."""
import pytest

# Role 3 packages live in src/sim/requirements.txt, not the root one, so CI skips this file.
pytest.importorskip("scipy")

import numpy as np

from src.sim.zones import exclusion_zone, step_for

X = np.arange(0.0, 101.0, 1.0)
COORDS = np.column_stack([X, np.zeros_like(X), np.zeros_like(X)])
SUPPORT = np.array([0])
LOAD = np.array([100])
NO_GUARD = np.array([], dtype=int)


def _run(stress, step=3.0, guard=NO_GUARD, **kw):
    return exclusion_zone(COORDS, stress, SUPPORT, LOAD, guard, step, **kw)


def test_peak_away_from_supports_is_kept():
    stress = np.full_like(X, 40.0)
    stress[50] = 50.0
    r = _run(stress)
    assert r["excluded_peak_MPa"] is None
    assert r["excluded_at"] is None
    assert r["stress_for_limit_MPa"] == 50.0
    assert r["stress_for_limit_at_mm"] == [50.0, 0.0, 0.0]
    assert r["zone_settled"] is True


def test_support_spike_is_excluded_and_settles():
    stress = 100.0 + 1000.0 * np.exp(-X / 1.0)
    r = _run(stress)
    assert r["excluded_at"] == "support"
    assert r["excluded_peak_MPa"] == pytest.approx(1100.0)
    assert r["excluded_peak_at_mm"] == [0.0, 0.0, 0.0]
    assert r["stress_for_limit_MPa"] == pytest.approx(100.0, rel=0.05)
    assert r["zone_settled"] is True
    # p(3) to p(6) still changes by 14.7 %, so it settles one step later
    assert r["zone_mm"] == 6.0


def test_load_spike_is_labelled_load():
    stress = 100.0 + 1000.0 * np.exp(-(100.0 - X) / 1.0)
    r = _run(stress)
    assert r["excluded_at"] == "load"
    assert r["excluded_peak_MPa"] == pytest.approx(1100.0)


def test_guard_inside_first_step_gives_unsettled_raw_peak():
    stress = 100.0 + 1000.0 * np.exp(-X / 1.0)
    r = _run(stress, guard=np.array([2]))
    assert r["zone_mm"] == 0
    assert r["stress_for_limit_MPa"] == stress.max()
    assert r["zone_settled"] is False


def test_guard_limits_growth_to_one_step():
    stress = 100.0 + 1000.0 * np.exp(-X / 1.0)
    r = _run(stress, guard=np.array([5]))
    assert r["zone_mm"] == 3.0
    assert r["zone_settled"] is False


def test_never_settling_uses_the_smallest_zone():
    stress = 1000.0 / (X + 0.1)
    r = _run(stress)
    assert r["zone_mm"] == 3.0
    assert r["stress_for_limit_MPa"] == pytest.approx(stress[X > 3.0].max())
    assert r["zone_settled"] is False


def test_fixed_zone_mm_skips_the_search():
    stress = 100.0 + 1000.0 * np.exp(-X / 1.0)
    r = _run(stress, zone_mm=6.0)
    assert r["zone_mm"] == 6.0
    assert r["stress_for_limit_MPa"] == pytest.approx(stress[X > 6.0].max())
    assert r["zone_settled"] is None


def test_step_for():
    assert step_for(6.0, 2.0) == 3.0
    assert step_for(None, 2.0) == 2.0


def test_zero_zone_excludes_nothing():
    stress = 100.0 + 1000.0 * np.exp(-X / 1.0)
    r = _run(stress, guard=np.array([2]))
    assert r["excluded_peak_MPa"] is None
    assert r["excluded_peak_at_mm"] is None
    assert r["excluded_at"] is None


def test_all_zero_stress_does_not_raise():
    r = _run(np.zeros_like(X))
    assert r["stress_for_limit_MPa"] == 0.0
    assert r["zone_settled"] is False  # spec: settled needs a change strictly below 5 %


def test_reused_zero_zone_still_excludes_nothing():
    # check_mesh reuses the coarse zone_mm; a coarse "no zone fits" (0) must not exclude face nodes.
    stress = 100.0 + 1000.0 * np.exp(-X / 1.0)
    r = _run(stress, zone_mm=0)
    assert r["stress_for_limit_MPa"] == stress.max()
    assert r["excluded_peak_MPa"] is None and r["zone_mm"] == 0


def test_non_positive_step_is_rejected():
    with pytest.raises(ValueError):
        _run(np.ones_like(X), step=0.0)
    with pytest.raises(ValueError):
        step_for(0.0, 2.0)


def test_support_load_tie_counts_as_support():
    coords = np.array([[0.0, 0, 0], [2.0, 0, 0], [4.0, 0, 0], [20.0, 0, 0], [40.0, 0, 0]])
    stress = np.array([10.0, 500.0, 10.0, 50.0, 49.0])
    r = exclusion_zone(coords, stress, np.array([0]), np.array([2]), NO_GUARD, 3.0)
    assert r["excluded_at"] == "support"
