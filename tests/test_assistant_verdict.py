"""The judgement call after an optimization (src/assistant/verdict.py): LLM text under the number check, template otherwise."""
import json
from types import SimpleNamespace as NS

import pytest

from src.assistant.contract_adapter import ResultContractError, load_result
from src.assistant.numbers import unknown_numbers
from src.assistant.verdict import INSTRUCTIONS, template_verdict, trust, verdict
from tests.test_assistant_contract_adapter import EXAMPLE

R = load_result(EXAMPLE)
P = json.loads((EXAMPLE.parent / "prediction.json").read_text(encoding="utf-8"))


class ScriptClient:
    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []
        self.responses = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return NS(output_text=self.replies.pop(0), status="completed")


def sentences(text):
    return [s for s in text.split(". ") if s.strip()]


def test_template_passes_the_number_check_and_covers_the_five_points():
    t = template_verdict(R, P)
    assert unknown_numbers(t, R) == []
    assert 3 <= len(sentences(t)) <= 6
    assert t.startswith("Adopt it") and "45% lighter" in t  # the decision and the main gain
    assert "binding requirement is safety_factor >= 2" in t and "2.27" in t  # the binding limit
    assert "formula demo value, not a solve" in t  # trust: fake_formula is not a solver
    assert "Next step:" in t


def test_template_names_the_trade_off_when_something_got_worse():
    r = json.loads(json.dumps(R))
    r["recommended"]["max_disp_mm"] = r["baseline"]["max_disp_mm"] * 1.5  # lighter but less stiff
    t = template_verdict(r, P)
    assert "it costs 50% more deflection" in t and "lower peak stress" in t
    assert unknown_numbers(t, r) == []


def test_template_when_nothing_feasible_was_found():
    r = {**R, "recommended": None, "binding_constraint": None}
    t = template_verdict(r, P)
    assert t.startswith("Do not adopt") and "safety_factor >= 2" in t and "Next step" in t
    assert unknown_numbers(t, r) == []


@pytest.mark.parametrize("source,phrase", [("calculix", "solver checked in CalculiX"), ("ansys_mechanical", "solver checked in ANSYS Mechanical"),
                                           ("power_law_v0", "fitted model only"), ("fake_formula", "formula demo")])
def test_trust_follows_the_source(source, phrase):
    r = json.loads(json.dumps(R))
    r["recommended"]["source"] = source
    pred = {"shortlist": [{"design_id": r["recommended"]["design_id"], "in_training_range": False}]}
    how, in_range = trust(r, pred)
    assert phrase in how and in_range is False
    assert "outside the training range" in template_verdict(r, pred)
    assert trust(r, P)[1] is None  # the example's recommended design is not in its shortlist


def test_model_text_is_used_when_its_numbers_check_out():
    good = "Adopt fake-0-00228: it is 45% lighter at 0.066 kg. Trust it as a formula demo only. Next, solve it in Ansys."
    c = ScriptClient(good)
    text, source, warnings = verdict(R, c, prediction=P)
    assert (text, source, warnings) == (good, "model", []) and len(c.sent) == 1
    assert c.sent[0]["instructions"] == INSTRUCTIONS and '"finalist_numbers"' in c.sent[0]["input"][0]["content"]
    assert "solver checked" in INSTRUCTIONS and "3 to 5" in INSTRUCTIONS and "Never compute or invent" in INSTRUCTIONS


def test_invented_number_gets_one_retry_then_the_template():
    c = ScriptClient("It is 60% lighter.", "It is 45% lighter.")
    assert verdict(R, c)[:2] == ("It is 45% lighter.", "model")
    assert "60%" in c.sent[1]["input"][-1]["content"]
    c = ScriptClient("It is 60% lighter.", "It holds 900 N.")
    text, source, warnings = verdict(R, c)
    assert source == "template" and text == template_verdict(R) and "900" in warnings[0]


def test_no_client_means_the_template_and_a_bad_result_is_refused():
    assert verdict(R, None) == (template_verdict(R), "template", [])
    with pytest.raises(ResultContractError):
        verdict({k: v for k, v in R.items() if k != "explanation"}, ScriptClient("unused"))


# ---------------------------------------------------------------- review fixes (PR #14 follow-up, 2026-11-26)

def _copy():
    return json.loads(json.dumps(R))


def test_template_keeps_the_baseline_when_it_passes_and_the_pick_is_worse():
    r = _copy()
    r["baseline"]["feasible"], r["baseline"]["violated"] = True, []
    r["recommended"]["mass_kg"] = r["baseline"]["mass_kg"] * 1.08  # heavier than a baseline that already passes
    t = template_verdict(r, P)
    assert t.startswith("Keep the baseline") and "Adopt it" not in t
    assert unknown_numbers(t, r) == []


def test_mass_shows_up_as_a_trade_off_when_it_is_not_the_objective():
    r = _copy()
    r["spec"]["objective"] = {"metric": "safety_factor", "direction": "maximize"}
    r["recommended"]["mass_kg"] = r["baseline"]["mass_kg"] * 1.3
    r["recommended"]["safety_factor"] = r["baseline"]["safety_factor"] * 1.2
    t = template_verdict(r, P)
    assert "30% more mass" in t and "gives nothing up" not in t
    assert unknown_numbers(t, r) == []


@pytest.mark.parametrize("factor", [0.9993, 0.99637, 1.00412])
def test_small_changes_pass_the_number_check(factor):
    r = _copy()
    r["recommended"]["max_stress_mpa"] = r["baseline"]["max_stress_mpa"] * factor  # -0.07 %, -0.4 %, +0.4 %
    t = template_verdict(r, P)
    assert unknown_numbers(t, r) == [], t


def test_a_value_that_rounds_to_one_figure_still_passes():
    r = _copy()
    r["recommended"]["mass_kg"] = 0.29981  # .3g would print "0.3", which the check only accepts as exact
    t = template_verdict(r, P)
    assert unknown_numbers(t, r) == [], t


def test_discovery_is_a_solver_and_an_unknown_source_is_not_called_a_formula():
    r = _copy()
    r["recommended"]["source"] = "ansys_discovery"
    assert "solver checked in Ansys Discovery" in trust(r)[0]
    r["recommended"]["source"] = "something_new"
    how = trust(r)[0]
    assert "formula" not in how and "unknown" in how


def test_nothing_feasible_names_the_limit_the_finalists_failed():
    r = _copy()
    r["baseline"]["feasible"], r["baseline"]["violated"] = True, []
    r["recommended"], r["binding_constraint"] = None, None
    for f in r["finalists"]:
        f["feasible"], f["violated"] = False, ["max_disp_mm <= 1"]
    t = template_verdict(r, P)
    assert "max_disp_mm <= 1" in t and t.startswith("Do not adopt")
