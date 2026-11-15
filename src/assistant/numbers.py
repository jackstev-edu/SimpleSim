"""Number check for the "why this won" explanation (Role 3 backlog item 3).

Every number the explanation shows must be in result.json, at the precision the text shows it,
or be a percent change of the recommended design against the baseline. Anything else is an
invented number and the explanation is rejected (decision 2026-11-15).
"""
from __future__ import annotations

import re

# Words that start with a letter are identifiers or units (fake-0-00228, l_bracket_v0, p95, mm):
# drop them whole so the digits inside them are not read as numbers.
_WORD = re.compile(r"[A-Za-z_][\w\-]*")
_NUMBER = re.compile(r"(?<![\d.])-?\d+(?:,\d{3})*(?:\.\d+)?(?:\s?%)?")
METRICS = ("mass_kg", "max_stress_mpa", "max_disp_mm", "safety_factor")


def _tokens(text: str) -> list[str]:
    return [m.group(0) for m in _NUMBER.finditer(_WORD.sub(" ", text))]


def _value(token: str) -> tuple[float, int, bool]:
    """(absolute value, decimals shown, is a percent)."""
    is_pct = token.endswith("%")
    digits = token.rstrip("% ").replace(",", "").lstrip("-")
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return float(digits), decimals, is_pct


def _walk(node, out: list[float]) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        out.append(float(node))
    elif isinstance(node, str):
        out.extend(_value(t)[0] for t in _tokens(node))
    elif isinstance(node, dict):
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def allowed_numbers(result: dict) -> list[float]:
    """Every number in the result, including numbers inside its strings (e.g. 'safety_factor >= 2')."""
    out: list[float] = []
    _walk(result, out)
    return out


def percent_changes(result: dict) -> list[float]:
    """Percent change of the recommended design against the baseline, per metric."""
    base, rec = result.get("baseline") or {}, result.get("recommended") or {}
    return [100.0 * (rec[m] - base[m]) / base[m] for m in METRICS
            if isinstance(base.get(m), (int, float)) and isinstance(rec.get(m), (int, float)) and base[m]]


def unknown_numbers(text: str, result: dict) -> list[str]:
    """Numbers in `text` that match nothing in the result at the precision shown; [] means the text is clean."""
    plain = [abs(v) for v in allowed_numbers(result)]
    pct = [abs(v) for v in percent_changes(result)]
    bad = []
    for token in _tokens(text):
        x, d, is_pct = _value(token)
        pool = pct if is_pct else plain
        if not any(abs(round(v, d) - x) < 1e-9 for v in pool):
            bad.append(token.replace(" ", ""))
    return bad
