"""OpenAI client helpers: token prices, a hard budget cap per run, and a spend ledger.

The course key has a fixed budget ($20) that must last until the deployed demo on the larger
model (decision 2026-11-15), so every scripted run counts its tokens and stops at a cap.
The API key comes from the OPENAI_API_KEY environment variable, never from code.
"""
from __future__ import annotations

import csv
import os
import time
from pathlib import Path

# SIMPLESIM_MODE picks the model for every assistant: "debug" (default) and "demo" use gpt-6-luna
# (cheap, priced like Haiku 5.5); "deploy" uses gpt-6.1-sol (priced like Sonnet 5.5, 20x luna).
MODELS = {"debug": "gpt-6-luna", "demo": "gpt-6-luna", "deploy": "gpt-6.1-sol"}
MODE = os.environ.get("SIMPLESIM_MODE", "debug")
if MODE not in MODELS:
    raise ValueError(f"SIMPLESIM_MODE must be one of {sorted(MODELS)}, got {MODE!r}")
MODEL = MODELS[MODE]

# USD per million tokens: input, cached input, output.
# Source: developers.openai.com/api/docs/pricing, read 2026-11-15.
PRICES = {"gpt-6-luna": (0.10, 0.01, 0.50),
          "gpt-6.1-sol": (2.00, 0.10, 10.00)}
USAGE_KEYS = ("input_tokens", "cached_tokens", "output_tokens")
LEDGER = Path(__file__).resolve().parents[1] / "practice" / "spend.csv"
# Cap for one interactive session (the GUI, smoke scripts), USD; SIMPLESIM_SESSION_BUDGET_USD overrides it.
SESSION_BUDGET_USD = {"debug": 0.20, "demo": 0.20, "deploy": 2.00}
LEDGER_FIELDS = ("date", "label", "problems", "models", "cost_usd", "total_usd")


class BudgetExceeded(RuntimeError):
    """The run's spending cap is reached; no further API calls are made."""


def cost(usage: dict) -> float:
    """USD for {model: {input_tokens, cached_tokens, output_tokens}}; cached tokens are part of input."""
    total = 0.0
    for model, u in usage.items():
        if model not in PRICES:
            raise ValueError(f"no price for model {model!r}; add it to src/llm.py PRICES")
        p_in, p_cached, p_out = PRICES[model]
        total += ((u["input_tokens"] - u["cached_tokens"]) * p_in + u["cached_tokens"] * p_cached
                  + u["output_tokens"] * p_out) / 1e6
    return total


def counted_client(usage: dict, budget_usd: float, client=None):
    """An OpenAI client whose responses.create adds to `usage` and refuses calls once the cap is reached.

    The check runs before each call, so a run can overshoot the cap by at most one call.
    """
    if client is None:
        from openai import OpenAI
        client = OpenAI()
    create = client.responses.create

    def counted(**kwargs):
        spent = cost(usage)
        if spent >= budget_usd:
            raise BudgetExceeded(f"budget cap reached: ${spent:.2f} spent of ${budget_usd:.2f}")
        r = create(**kwargs)
        u = usage.setdefault(kwargs["model"], dict.fromkeys(USAGE_KEYS, 0))
        details = getattr(r.usage, "input_tokens_details", None)
        u["input_tokens"] += r.usage.input_tokens or 0
        u["cached_tokens"] += (getattr(details, "cached_tokens", 0) or 0) if details else 0
        u["output_tokens"] += r.usage.output_tokens or 0
        return r

    client.responses.create = counted
    return client


def add_usage(total: dict, more: dict) -> None:
    for model, u in more.items():
        t = total.setdefault(model, dict.fromkeys(USAGE_KEYS, 0))
        for k in USAGE_KEYS:
            t[k] += u.get(k, 0)


def append_spend(path: Path, label: str, problems: int, usage: dict) -> float:
    """Add one row per run to the ledger; returns the running total of all rows."""
    path = Path(path)
    previous = 0.0
    if path.is_file():
        previous = sum(float(r["cost_usd"]) for r in csv.DictReader(path.open(encoding="utf-8")))
    run = cost(usage)
    new = not path.is_file()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"date": time.strftime("%Y-%m-%d %H:%M"), "label": label, "problems": problems,
                    "models": " ".join(sorted(usage)), "cost_usd": f"{run:.4f}",
                    "total_usd": f"{previous + run:.4f}"})
    return previous + run


def session_budget(mode: str = MODE) -> float:
    """The spending cap for one interactive session in this mode, or the SIMPLESIM_SESSION_BUDGET_USD override."""
    raw = os.environ.get("SIMPLESIM_SESSION_BUDGET_USD")
    if raw is None:
        return SESSION_BUDGET_USD[mode]
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if value <= 0:
        raise ValueError(f"SIMPLESIM_SESSION_BUDGET_USD must be a positive number of USD, got {raw!r}")
    return value
