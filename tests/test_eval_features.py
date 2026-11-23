"""Matching the agent's proposed features to the answer key's features."""
from src.eval.features import match_all, matches, proposal_features, unmatched


def cyl(fid, x, r=4.0):
    return {"id": fid, "type": "cylinder", "radius_mm": r, "axis_point_mm": [x, 0.0, 0.0], "axis_dir": [0.0, 0.0, 1.0]}


FACES = [cyl("h1a", 10), cyl("h1b", 10), cyl("h2a", 50), cyl("h2b", 50),
         {"id": "w1", "type": "plane"}, {"id": "w2", "type": "plane"}, {"id": "w3", "type": "plane"}]
KEY_HOLE1 = {"id": "hole1", "face_id": "h1a", "action": "radius", "alt_face_ids": []}
KEY_HOLE2 = {"id": "hole2", "face_id": "h2a", "action": "radius", "alt_face_ids": []}
KEY_WALL = {"id": "wall", "face_id": "w1", "action": "offset", "alt_face_ids": ["w2"]}
KEY_FILLET = {"id": "corner", "face_id": "w1", "other_face_id": "w3", "action": "fillet", "alt_face_ids": []}


def test_split_hole_other_half_matches():
    assert matches(KEY_HOLE1, {"face_id": "h1b", "action": "radius"}, FACES)


def test_other_hole_does_not_match():
    assert not matches(KEY_HOLE1, {"face_id": "h2b", "action": "radius"}, FACES)


def test_grouped_holes_match_each_key_hole():
    prop = {"face_id": "h1a", "action": "radius", "also_face_ids": ["h2a"]}
    assert matches(KEY_HOLE1, prop, FACES) and matches(KEY_HOLE2, prop, FACES)


def test_fillet_pair_order_ignored():
    assert matches(KEY_FILLET, {"face_id": "w3", "other_face_id": "w1", "action": "fillet"}, FACES)


def test_offset_alt_face_matches():
    assert matches(KEY_WALL, {"face_id": "w2", "action": "offset"}, FACES)
    assert not matches(KEY_WALL, {"face_id": "w3", "action": "offset"}, FACES)


def test_action_mismatch_fails():
    assert not matches(KEY_WALL, {"face_id": "w1", "action": "fillet", "other_face_id": "w3"}, FACES)


def test_optimize_lower_upper_normalized():
    f = proposal_features({"tool": "optimize", "input": {"features": [
        {"face_id": "a", "action": "offset", "lower": -1, "upper": 2, "start": 0}]}})
    assert f[0]["low"] == -1 and f[0]["high"] == 2 and f[0]["also_face_ids"] == []


def test_first_look_low_high_kept():
    f = proposal_features({"tool": "first_look", "input": {"features": [
        {"face_id": "a", "action": "radius", "low": 3, "high": 5}]}})
    assert (f[0]["low"], f[0]["high"]) == (3, 5)


def test_no_proposal_gives_no_features():
    assert proposal_features(None) == []


def test_match_all_and_unmatched():
    props = [{"face_id": "h1b", "action": "radius"}, {"face_id": "w3", "action": "offset"}]
    keys = [KEY_HOLE1, KEY_HOLE2, KEY_WALL]
    assert match_all(keys, props, FACES) == {"hole1": [0], "hole2": [], "wall": []}
    assert unmatched(keys, props, FACES) == [1]


def test_grouped_key_feature_needs_all_its_holes():
    key = {"id": "mounts", "face_id": "h1a", "action": "radius", "also_face_ids": ["h2a"], "alt_face_ids": []}
    assert matches(key, {"face_id": "h2b", "action": "radius", "also_face_ids": ["h1b"]}, FACES)
    assert not matches(key, {"face_id": "h1a", "action": "radius"}, FACES)
