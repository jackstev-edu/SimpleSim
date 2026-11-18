"""Study inputs: the user's safety factor always reaches optimize, the guard, the hand-off text."""
import re

import pytest

from src.assistant.study import StudyInputs, build_constraints, handoff_message, optimize_blocker
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.session import App

SF15 = {"metric": "safety_factor", "op": ">=", "value": 1.5}


def _bare_part(tmp_path):
    part = App("formula").open(write_demo_part(tmp_path / "l.scdocx", physics=False))
    part.use_family("l_bracket_v0")
    return part


def test_constraints_always_carry_user_sf():
    disp = {"metric": "max_disp_mm", "op": "<=", "value": 0.5}
    assert build_constraints(StudyInputs(safety_factor=1.5, limits=[disp])) == [SF15, disp]


def test_sf_in_limits_is_replaced():
    sf2 = {"metric": "safety_factor", "op": ">=", "value": 2.0}
    assert build_constraints(StudyInputs(safety_factor=1.5, limits=[sf2])) == [SF15]


def test_no_sf_raises():
    with pytest.raises(ValueError):
        build_constraints(StudyInputs())


def test_blocker_lists_all_missing(tmp_path):
    part = _bare_part(tmp_path)
    msg = optimize_blocker(part, StudyInputs())
    for phrase in ("the user's confirmation of the setup card", "a safety factor", "an objective"):
        assert phrase in msg
    part.set_support()
    part.set_load(500.0)
    part.set_material("structural_steel")
    part.confirm()
    assert optimize_blocker(part, StudyInputs(safety_factor=1.5, objective="mass_kg", direction="minimize")) is None


def test_handoff_lists_confirmed_values_only(tmp_path):
    part = _bare_part(tmp_path)
    part.set_load(500.0, (0.0, -1.0, 0.0))
    part.set_material("structural_steel")
    inputs = StudyInputs(safety_factor=1.5, objective="mass_kg", direction="minimize")
    text = handoff_message("l.scdocx", part, inputs, "the user asked for ANSYS")
    for want in ("500 N", "-Y", "Structural Steel", "1.5", "minimise mass", "the user asked for ANSYS",
                 "confirm the faces with the user"):
        assert want in text


def test_handoff_skips_unconfirmed_file_values(tmp_path):
    part = App("formula").open(write_demo_part(tmp_path / "l.dsco", force_n=700.0))
    text = handoff_message("l.dsco", part, StudyInputs(), "the user asked for ANSYS")
    assert "700" not in text


def test_handoff_with_nothing_confirmed():
    text = handoff_message("saddle.scdoc", None, StudyInputs(), "its shape is not a known bracket")
    assert "saddle.scdoc" in text and "Nothing is confirmed yet" in text
    assert not re.search(r"\d", text)
