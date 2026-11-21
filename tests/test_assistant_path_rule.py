"""Path rule: fast for the known family, slow with a reason, undecided for plain geometry."""
from dataclasses import replace

import pytest

from src.assistant.path_rule import FAST_MATERIALS, PathDecision, outside_family, part_path
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.families import L_BRACKET_V0
from src.pydiscovery.session import App


def test_family_file_is_fast(tmp_path):
    part = App("formula").open(write_demo_part(tmp_path / "l.dsco"))
    d = part_path(part)
    assert isinstance(d, PathDecision) and d.path == "fast" and d.reason


def test_plain_geometry_is_undecided(tmp_path):
    part = App("formula").open(write_demo_part(tmp_path / "p.scdocx", physics=False))
    assert part_path(part) is None


def test_problems_make_it_slow(tmp_path):
    odd = replace(L_BRACKET_V0, labels={k: f"Wobble{i}" for i, k in enumerate(L_BRACKET_V0.labels)})
    part = App("formula").open(write_demo_part(tmp_path / "odd.dsco", family=odd))
    assert part.problems
    d = part_path(part)
    assert d.path == "slow" and d.reason == part.problems[0]


def test_force_outside_range():
    reason = outside_family(L_BRACKET_V0, force_n=1500)
    assert "1500" in reason and "100" in reason and "1000" in reason
    assert outside_family(L_BRACKET_V0, force_n=500) is None


def test_direction_other_than_minus_y():
    assert outside_family(L_BRACKET_V0, direction=(0, 0, -1))
    assert outside_family(L_BRACKET_V0, direction=(0, -2, 0)) is None


def test_material_not_in_fast_table():
    reason = outside_family(L_BRACKET_V0, material="titanium")
    assert all(m in reason for m in FAST_MATERIALS)
    assert outside_family(L_BRACKET_V0, material="aluminum_6061") is None


@pytest.mark.parametrize("bad", [0.0, -5.0])
def test_non_positive_force_is_outside(bad):
    assert outside_family(L_BRACKET_V0, force_n=bad)
