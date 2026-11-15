"""Role 3 reads team results only through src/assistant/contract_adapter.py, checked against contracts.models.Result."""
import json
from pathlib import Path

import pytest

from contracts.models import Result
from src.assistant.contract_adapter import ResultContractError, load_result

EXAMPLE = Path(__file__).resolve().parents[1] / "contracts" / "examples" / "result.json"


def test_example_file_loads_as_a_plain_dict():
    r = load_result(EXAMPLE)
    assert isinstance(r, dict)
    assert r["recommended"]["design_id"] == "fake-0-00228"
    assert r["binding_constraint"] == "safety_factor >= 2"


def test_dict_and_model_inputs_give_the_same_result():
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert load_result(data) == load_result(Result.model_validate(data)) == load_result(EXAMPLE)


def test_missing_field_is_refused_with_its_name():
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    del data["explanation"]
    with pytest.raises(ResultContractError, match="explanation"):
        load_result(data)


def test_other_schema_version_is_refused():
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["schema_version"] = "9.9.9"
    with pytest.raises(ResultContractError, match="schema_version"):
        load_result(data)
