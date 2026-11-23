"""Number check for the "why this won" explanation (Role 3 backlog item 3).

Every number the explanation shows must be in result.json, at the precision the text shows it,
or be a percent change of the recommended design against the baseline. Anything else is an
invented number and the explanation is rejected (decision 2026-11-15).
"""
from __future__ import annotations

import re

# Words that start with a letter are identifiers or units (fake-0-00228, l_bracket_v0, p95, mm):
# blank them out whole (same length, so positions still match the text) so their digits are not numbers.
_WORD = re.compile(r"[A-Za-z_][\w\-]*")
_NUMBER = re.compile(r"(?<![\d.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s?%)?")
METRICS = ("mass_kg", "max_stress_mpa", "max_disp_mm", "safety_factor")
# Direction words after a percent: (sign of the change, metric it must be, or None for any metric).
_DIRECTION = [
    (re.compile(r"lighter"), -1, "mass_kg"), (re.compile(r"heavier"), +1, "mass_kg"),
    (re.compile(r"(less|lower|smaller|fewer|reduc\w*|decreas\w*|drop\w*|sav\w*|down)"), -1, None),
    (re.compile(r"(more|higher|larger|greater|increas\w*|rise|rises|rose|gain\w*|up)"), +1, None),
]


def _matches(text: str):
    blanked = _WORD.sub(lambda m: " " * len(m.group(0)), text)
    return list(_NUMBER.finditer(blanked))


def _tokens(text: str) -> list[str]:
    return [m.group(0) for m in _matches(text)]


def _value(token: str) -> tuple[float, int, bool]:
    """(absolute value, decimals shown, is a percent)."""
    is_pct = token.endswith("%")
    digits = token.rstrip("% ").replace(",", "").lstrip("-+")
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return float(digits), decimals, is_pct


def _sig_figs(token: str) -> int:
    digits = token.rstrip("% ").replace(",", "").lstrip("-+").replace(".", "").lstrip("0")
    return len(digits)


def _direction(text: str, end: int) -> tuple[int, str | None] | None:
    """Sign and metric implied by the first direction word within a few words after a percent."""
    after = re.split(r"[.;:!?]", text[end:end + 40].lower())[0]  # stay within the clause
    words = re.findall(r"[a-z]+", after)[:3]
    for w in words:
        for pattern, sign, metric in _DIRECTION:
            if pattern.fullmatch(w):
                return sign, metric
    return None


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


def percent_changes(result: dict) -> dict[str, float]:
    """Percent change of the recommended design against the baseline, per metric (signed)."""
    base, rec = result.get("baseline") or {}, result.get("recommended") or {}
    return {m: 100.0 * (rec[m] - base[m]) / base[m] for m in METRICS
            if isinstance(base.get(m), (int, float)) and isinstance(rec.get(m), (int, float)) and base[m]}


def _found(x: float, decimals: int, sig: int, pool: list[float]) -> bool:
    """x matches a pool value at the shown precision; with one significant figure only an exact value counts."""
    for v in pool:
        if abs(round(v, decimals) - x) < 1e-9 and (sig >= 2 or abs(v - x) < 1e-9):
            return True
    return False


def unknown_numbers(text: str, result: dict) -> list[str]:
    """Numbers in `text` that match nothing in the result at the precision shown; [] means the text is clean.

    Plain numbers are compared by size. A percent keeps its direction: an explicit sign, or a direction
    word after it (lighter, higher ...), must agree with the sign of the change, and lighter/heavier
    only match the mass change."""
    plain = [abs(v) for v in allowed_numbers(result)]
    changes = percent_changes(result)
    bad = []
    for m in _matches(text):
        token = m.group(0)
        x, d, is_pct = _value(token)
        sig = _sig_figs(token)
        if not is_pct:
            ok = _found(x, d, sig, plain)
        else:
            sign = -1 if token.startswith("-") else +1 if token.startswith("+") else None
            metric = None
            implied = _direction(text, m.end())
            if implied:
                sign = sign or implied[0]
                metric = implied[1]
            pool = [v for k, v in changes.items() if metric in (None, k)]
            if sign is None:
                ok = _found(x, d, sig, [abs(v) for v in pool])
            else:
                ok = _found(sign * x, d, sig, pool)
        if not ok:
            bad.append(token.replace(" ", ""))
    return bad
