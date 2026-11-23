"""Answer-key labels from a first-look summary."""
from src.eval.keys import label_effects

LIMITS = [{"response": "safety_factor", "op": ">=", "value": 1.5}]


def feat(index, sf_rel, mass_rel):
    return {"index": index, "responses": {"safety_factor": {"change_rel": sf_rel}, "mass_kg": {"change_rel": mass_rel}}}


def summary(*features, first=None):
    order = sorted(features, key=lambda f: -abs(f["responses"]["safety_factor"]["change_rel"] or 0))
    return {"features": list(features), "limit_ranking": {"safety_factor": [first] if first else [f["index"] for f in order]}}


def test_limit_mover_is_controls_limit():
    assert label_effects(summary(feat(1, 0.12, 0.01), feat(2, 0.06, 0.0)), "mass_kg", LIMITS) == \
        ["controls_limit", "controls_limit"]


def test_top_of_limit_ranking_is_controls_limit_even_if_small():
    assert label_effects(summary(feat(1, 0.01, 0.0), feat(2, 0.02, 0.0)), "mass_kg", LIMITS) == \
        ["negligible", "controls_limit"]


def test_objective_only_mover():
    s = summary(feat(1, 0.30, 0.0), feat(2, -0.01, 0.08))
    assert label_effects(s, "mass_kg", LIMITS) == ["controls_limit", "moves_objective"]


def test_small_everything_negligible():
    s = summary(feat(1, 0.30, 0.0), feat(2, 0.01, 0.02), feat(3, None, None))
    assert label_effects(s, "mass_kg", LIMITS)[1:] == ["negligible", "negligible"]
