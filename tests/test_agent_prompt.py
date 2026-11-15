"""The agent's instructions carry each graded behaviour (practice-set Task 10 and the 2026-11-13/15 decisions)."""
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
