"""The agent's instructions carry each graded behaviour (practice-set Task 10 and the 2026-11-13/15 decisions)."""
import pytest

# Role 3 packages live in src/sim/requirements.txt, not the root one, so CI skips this file.
pytest.importorskip("ansys.geometry.core")
pytest.importorskip("ansys.mechanical.core")
pytest.importorskip("ansys.optislang.core")

import re

from src.agent.agent import SYSTEM
from src.agent.tools import TOOLS

S = " ".join(SYSTEM.split())  # one line, single spaces: phrases may wrap in the source


def tool_desc(name):
    return " ".join(next(t for t in TOOLS if t["name"] == name)["description"].split())


def test_user_named_features_are_binding_and_extras_are_labelled_suggestions():
    assert "binding" in S
    assert "suggestion" in S and "only after the user agrees" in S
    assert "If the user names no features, recommend" in S


def test_says_what_it_sees_and_waits_before_solving():
    assert "wait for the user to confirm before" in S


def test_complex_part_rule_without_never_seen_wording():
    assert "looks complex" in S
    for m in re.finditer("never seen", S):
        assert "Do not say" in S[max(0, m.start() - 40):m.start()]


def test_flags_low_safety_factor_and_mass_given_as_load():
    assert "below 1" in S and "9.81" in S


def test_offsets_are_relative_and_include_zero():
    assert "must include 0" in S
    for name in ("first_look", "optimize"):
        d = tool_desc(name)
        assert "offset from the current face" in d and "must include 0" in d


def test_no_assumed_calculation_inputs():
    assert "Never assume a calculation input" in S
    for item in ("fixed faces", "loaded faces", "force", "material", "safety factor", "objective"):
        assert item in S
    assert "1.5 is the value you offer" in S
    # the old "state the default and continue" rule is gone
    assert "1.5 unless the user sets another" not in S
    assert "default Structural Steel)" not in S


def test_material_shortlist_with_recommendation_and_user_choice():
    assert "3-4 common bracket materials" in S
    assert "yield / density" in S
    assert "strength, weight and price only" in S
    assert "recommend one" in S and "the user picks" in S


def test_deflection_limit_is_researched_never_invented():
    assert "Never invent a deflection limit" in S
    assert "with its source" in S
    assert "does not know whether the deflection is acceptable" in S
    assert "Use web_search for nothing else" not in S  # web search is now also for deflection limits


def test_dead_solver_is_not_retried():
    assert "SolveFailed" in S and "do not retry" in S.lower()


def test_prompt_explains_exclusion_zones():
    for phrase in ["stress_for_limit_MPa", "excluded_peak_MPa", "not physical",
                   "bolted joint needs its own check", "how the load is really applied", "zone_settled"]:
        assert phrase in S, phrase


def test_tool_descriptions_point_to_limit_stress():
    for name in ("optimize", "first_look"):
        assert "stress_for_limit_MPa" in tool_desc(name), name


def test_limit_stress_location_and_mesh_check_wording():
    assert "conservative" in S
    assert "stress_for_limit_at_mm" in S
    assert "stress_for_limit_MPa" in tool_desc("check_mesh")
    assert "If max stress changes" not in tool_desc("check_mesh")


def test_down_is_worked_out_from_the_part_never_defaulted_to_z():
    # after-run 11-15: lbr_clean_auto set "straight down" as -Z on a wall-mounted L-bracket
    assert "no gravity" in S and "never default to Z" in S
    assert "bolted to a wall is vertical" in S


def test_user_words_map_to_faces_literally():
    # lbr_units: "hanging from the end of the short leg" went on the hole; sad_clean_auto: flange
    # faces fixed for "the two small holes are bolted down"
    assert "end of a leg is its end face" in S
    assert "fix the bore faces of those holes" in S


def test_set_load_case_once_after_every_setup_question_is_answered():
    # sad_clean_auto: set_load_case called, then a support question asked, then called again
    assert "Call set_load_case once" in S
