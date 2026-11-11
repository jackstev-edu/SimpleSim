"""Practice-set problem files: schema validation, part paths, effects sidecars."""
import pytest
import yaml

from src.eval.problems import (REPO, Problem, load_effects, load_problem, load_problems, resolve_part)

EXAMPLE = {
    "id": "lbr_clean_auto",
    "part": "examples/l_bracket.scdocx",
    "kind": "clean_auto",
    "request": "I have this L bracket and want it lighter.",
    "hidden_facts": "Back face bolted to a wall. 500 N down on the end. Steel. SF 1.5.",
    "user_named_features": [],
    "key": {
        "setup": {"fixed_face_ids": ["0:12"], "load_face_ids": ["0:7"], "force_N": [0, -500, 0],
                  "material": "Structural Steel", "safety_factor": 1.5},
        "must_ask": [], "must_flag": [],
        "objective": "mass_kg",
        "limits": [{"response": "safety_factor", "op": ">=", "value": 1.5}],
        "features": [{"id": "base_thickness", "label": "thickness of the horizontal leg", "face_id": "0:3",
                      "action": "offset", "other_face_id": None, "alt_face_ids": ["0:4"],
                      "key_low": -2.0, "key_high": 2.0, "min_value": None, "max_value": None}],
    },
}


def write(tmp_path, data, name=None):
    path = tmp_path / f"{name or data['id']}.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def variant(**changes):
    data = yaml.safe_load(yaml.safe_dump(EXAMPLE))
    data.update(changes)
    return data


def prob_with_part(part):
    return Problem(id="x", part=part, kind="clean_auto", request="r", hidden_facts="h",
                   user_named_features=[], key=EXAMPLE["key"])


def test_load_problem_roundtrip(tmp_path):
    p = load_problem(write(tmp_path, EXAMPLE))
    assert p.id == "lbr_clean_auto" and p.key["setup"]["force_N"] == [0, -500, 0]
    assert p.key["features"][0]["alt_face_ids"] == ["0:4"]


def test_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError, match="kind"):
        load_problem(write(tmp_path, variant(kind="foo")))


def test_rejects_named_feature_not_in_key(tmp_path):
    with pytest.raises(ValueError, match="user_named_features"):
        load_problem(write(tmp_path, variant(user_named_features=["nope"])))


def test_rejects_id_not_matching_filename(tmp_path):
    with pytest.raises(ValueError, match="id"):
        load_problem(write(tmp_path, EXAMPLE, name="other"))


def test_rejects_bad_must_flag(tmp_path):
    data = variant()
    data["key"]["must_flag"] = ["vibes"]
    with pytest.raises(ValueError, match="must_flag"):
        load_problem(write(tmp_path, data))


def test_rejects_bad_action(tmp_path):
    data = variant()
    data["key"]["features"][0]["action"] = "twist"
    with pytest.raises(ValueError, match="action"):
        load_problem(write(tmp_path, data))


def test_external_missing_resolves_none():
    assert resolve_part(prob_with_part("external:external/none.stp")) is None


def test_repo_part_resolves():
    assert resolve_part(prob_with_part("examples/l_bracket.scdocx")) == REPO / "examples/l_bracket.scdocx"


def test_load_problems_skips_effects_files(tmp_path):
    write(tmp_path, EXAMPLE)
    (tmp_path / "lbr_clean_auto.effects.yaml").write_text("problem: lbr_clean_auto\nreviewed: false\n")
    assert [p.id for p in load_problems(tmp_path)] == ["lbr_clean_auto"]


def test_load_effects_missing_returns_none(tmp_path):
    assert load_effects(prob_with_part("examples/l_bracket.scdocx"), tmp_path) is None


def test_load_effects_reads_sidecar(tmp_path):
    (tmp_path / "x.effects.yaml").write_text("problem: x\nreviewed: true\neffects: {a: negligible}\n")
    assert load_effects(prob_with_part("p"), tmp_path)["effects"] == {"a": "negligible"}
