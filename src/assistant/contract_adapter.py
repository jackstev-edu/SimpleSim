"""The one place Role 3 code reads the team contracts (contracts/models.py).

The contracts are a draft and will change; when they do, only this file should need editing.
Everything downstream (explain.py, decision_log.py) works on the plain dict returned here.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from contracts.models import SCHEMA_VERSION, Result


class ResultContractError(ValueError):
    """The result does not match the Result contract this code was written against."""


def load_result(source: Result | dict | str | Path) -> dict:
    """A contract-checked result as a plain dict, from a Result, a dict or a path to result.json."""
    if isinstance(source, Result):
        model = source
    else:
        data = json.loads(Path(source).read_text(encoding="utf-8")) if isinstance(source, (str, Path)) else source
        version = data.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ResultContractError(f"schema_version {version!r} is not the {SCHEMA_VERSION!r} this code reads; "
                                      "update src/assistant/contract_adapter.py")
        try:
            model = Result.model_validate(data)
        except ValidationError as e:
            # Name each bad field so a contract change is easy to trace.
            problems = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])
            raise ResultContractError(f"result does not match the Result contract ({e.error_count()} problem(s)): "
                                      f"{problems}") from e
    return model.model_dump(mode="json")
