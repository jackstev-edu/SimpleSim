"""Item 3: the LLM writes "why this won" from result.json; invented numbers get one retry, then the template."""
from types import SimpleNamespace as NS

import pytest

from src.assistant.contract_adapter import ResultContractError, load_result
from src.assistant.explain import explain
from tests.test_assistant_contract_adapter import EXAMPLE

R = load_result(EXAMPLE)  # Jack's contract example, checked against contracts.models.Result
TEMPLATE = R["explanation"]


class ScriptClient:
    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []
        self.responses = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return NS(output_text=self.replies.pop(0), status="completed")


def test_clean_text_is_used():
    good = "fake-0-00228 is 45% lighter (0.066 kg) and keeps a safety factor of 2.27 against the limit of 2."
    c = ScriptClient(good)
    text, warnings = explain(R, c)
    assert text == good and warnings == [] and len(c.sent) == 1
    assert '"mass_kg"' in c.sent[0]["input"][0]["content"]  # the model sees the result itself


def test_invented_number_gets_one_retry_that_names_it():
    c = ScriptClient("It is 60% lighter.", "It is 45% lighter.")
    text, warnings = explain(R, c)
    assert text == "It is 45% lighter." and warnings == []
    retry = c.sent[1]["input"][-1]["content"]
    assert "60%" in retry and "not in the result" in retry


def test_second_failure_falls_back_to_the_template_with_a_warning():
    c = ScriptClient("It is 60% lighter.", "It holds 900 N.")
    text, warnings = explain(R, c)
    assert text == TEMPLATE and len(c.sent) == 2
    assert warnings and "900" in warnings[0] and "template" in warnings[0]


def test_instructions_forbid_new_numbers_and_ask_for_the_discovery_values():
    c = ScriptClient("ok")
    explain(R, c)
    ins = c.sent[0]["instructions"]
    assert "only numbers that appear in the result" in ins and "Discovery" in ins and "binding" in ins


def test_result_that_breaks_the_contract_is_refused_before_any_api_call():
    c = ScriptClient("unused")
    bad = {k: v for k, v in R.items() if k != "explanation"}
    with pytest.raises(ResultContractError):
        explain(bad, c)
    assert c.sent == []
