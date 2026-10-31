"""Material price cache: price per kg with the source it came from.

Decision 2026-10-31: ANSYS's libraries have no prices, so a price comes from (a) this
cache, (b) a live web search by the agent, stored here with its source URL and access
date, or (c) the user. Every entry keeps its source so the agent can cite it. The file
grows over time and can be filled from Granta EduPack later.

Prices are indicative raw-material prices (USD per kg), not the cost of a finished part.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlparse

PRICES = Path(__file__).resolve().parents[2] / "data" / "material_prices.json"


def _load() -> dict[str, dict]:
    if not PRICES.exists():
        return {}
    return json.loads(PRICES.read_text(encoding="utf-8"))


def get(material: str) -> dict | None:
    entry = _load().get(material)
    return None if entry is None else {"material": material, **entry}


def record(material: str, usd_per_kg: float, source_url: str, source_title: str, basis: str) -> dict:
    """Store a price with its source; today's date is the access date. Overwrites an older entry."""
    if usd_per_kg <= 0:
        raise ValueError("usd_per_kg must be positive")
    if urlparse(source_url).scheme not in ("http", "https"):
        raise ValueError(f"source_url must be an http(s) URL, got {source_url!r}")
    entry = {"usd_per_kg": float(usd_per_kg), "source_url": source_url, "source_title": source_title,
             "basis": basis, "accessed": dt.date.today().isoformat()}
    data = _load()
    data[material] = entry
    PRICES.parent.mkdir(parents=True, exist_ok=True)
    # Sorted and indented, so the file diffs cleanly in git.
    PRICES.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"material": material, **entry}
