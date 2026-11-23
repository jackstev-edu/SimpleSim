"""'Why this design won' in plain words, written by the LLM from result.json (Role 3 backlog item 3).

The text may only show numbers that are in the result (see numbers.py). A text with an invented
number gets one retry that names the number; if the retry fails too, Jack's template explanation
from run_pipeline is kept and a warning says so (decision 2026-11-15).
"""
from __future__ import annotations

import json

from src.assistant.contract_adapter import load_result
from src.assistant.numbers import unknown_numbers
from src.llm import MODEL

INSTRUCTIONS = """\
You explain a bracket design result to a beginner engineering student in 4-6 short sentences.
Cover: which design is recommended and what it gains over the baseline; the binding constraint
(the limit the design sits on) and what that means; the trade-off it made; and the exact values
to enter in Ansys Discovery (discovery_values), with units.
If baseline.feasible is false, say first that the baseline does not meet the limits and name
them (baseline.violated); then a heavier or costlier recommended design is the price of meeting
them, not a trade-off.
If nothing feasible was found (recommended is null), say so plainly and name the limit that
could not be met.
Use only numbers that appear in the result, rounded for reading, or the percent change of the
recommended design against the baseline. Never compute or invent any other number.
Plain text, no headings."""


def explain(result, client, model: str = MODEL) -> tuple[str, list[str]]:
    """(explanation, warnings). Falls back to result['explanation'] when the numbers do not check out.

    `result` is a contracts Result, a dict or a path to result.json; it is checked against the contract
    before any API call (ResultContractError if it does not match)."""
    result = load_result(result)
    messages = [{"role": "user", "content": "The result:\n" + json.dumps(result, indent=1)}]
    bad: list[str] = []
    for attempt in range(2):
        r = client.responses.create(model=model, instructions=INSTRUCTIONS, input=messages,
                                    max_output_tokens=1500, reasoning={"effort": "low"})
        text = (r.output_text or "").strip()
        bad = unknown_numbers(text, result)
        if text and not bad:
            return text, []
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": f"These numbers are not in the result: {', '.join(bad) or '(empty reply)'}. "
                                                 "Rewrite the explanation using only numbers from the result."}]
    return result["explanation"], [f"Explanation used the template: the generated text twice contained numbers "
                                   f"not in the result ({', '.join(bad) or 'empty reply'})."]
