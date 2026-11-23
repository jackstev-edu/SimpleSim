"""Answer-key labels: which key features measurably matter, from a first-look summary.

controls_limit: moves a limited response by >= 5 % or ranks first in its limit_ranking.
moves_objective: moves the objective by >= 5 %. Otherwise negligible. A person reviews every label.
"""
from __future__ import annotations

THRESHOLD = 0.05


def _rel(feature: dict, response: str) -> float | None:
    v = feature.get("responses", {}).get(response, {}).get("change_rel")
    return None if v is None else abs(v)


def label_effects(summary: dict, objective: str, limits: list[dict]) -> list[str]:
    ranking = summary.get("limit_ranking", {})
    out = []
    for f in summary["features"]:
        controls = False
        for lim in limits:
            r = lim["response"]
            rel = _rel(f, r)
            if (rel is not None and rel >= THRESHOLD) or (ranking.get(r) and ranking[r][0] == f["index"]
                                                           and rel is not None):
                controls = True
        obj = _rel(f, objective)
        out.append("controls_limit" if controls else
                   "moves_objective" if obj is not None and obj >= THRESHOLD else "negligible")
    return out
