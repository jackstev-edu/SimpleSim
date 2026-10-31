"""Workbench first_look / progress / stop, with a fake _evaluate (no ANSYS started)."""
import json

import pytest

from src.agent.tools import Workbench

FEATURES = [
    {"face_id": "F1", "action": "offset", "low": 0.0, "high": 2.0, "label": "base"},
    {"face_id": "F2", "action": "radius", "low": 5.0, "high": 7.0},
]


@pytest.fixture
def wb(tmp_path):
    w = Workbench(tmp_path)
    w.part = tmp_path / "original.scdocx"
    w.load_case = {"fixed_face_ids": ["F0"], "load_face_ids": ["F9"], "force_N": [0, 0, -500]}
    w.faces = [{"id": "F1", "type": "plane"}, {"id": "F2", "type": "cylinder", "radius_mm": 4.0}]
    w.calls = []

    def fake(name, edits, size):
        w.calls.append((name, edits))
        if name == "look_1_high" and getattr(w, "fail_high", False):
            raise RuntimeError("boom")
        r = {"name": name, "mass_kg": 1.0 + sum(e["value_mm"] for e in edits) * 0.01,
             "max_stress_MPa": 100.0, "max_def_mm": 0.1, "safety_factor": 2.0}
        w.designs[name] = {"name": name}
        return r

    w._evaluate = fake
    yield w
    w._pool.shutdown()


def args(**kw):
    return {"features": FEATURES, "objective": "mass_kg", "limits": [], **kw}


def drain(wb):
    out = []
    while not wb.progress.empty():
        out.append(wb.progress.get())
    return out


def test_first_look_runs_2n_plus_1_and_reports(wb):
    out = json.loads(wb.call("first_look", args()))
    assert [c[0] for c in wb.calls] == ["look_ref", "look_1_low", "look_1_high", "look_2_low", "look_2_high"]
    assert out["stopped"] == "done" and out["solves"] == 5
    ev = drain(wb)
    assert [(e["k"], e["n"]) for e in ev] == [(i, 5) for i in range(1, 6)]
    assert all(e["tool"] == "first_look" for e in ev)


def test_first_look_stops_between_designs(wb):
    orig = wb._evaluate

    def stopper(name, edits, size):
        r = orig(name, edits, size)
        if name == "look_1_low":
            wb.request_stop()
        return r

    wb._evaluate = stopper
    out = json.loads(wb.call("first_look", args()))
    assert len(wb.calls) == 2 and out["stopped"] == "user" and out["solves"] == 2
    assert set(wb.designs) == {"look_ref", "look_1_low"}


def test_stop_flag_cleared_at_batch_start(wb):
    wb.request_stop()
    out = json.loads(wb.call("first_look", args()))
    assert len(wb.calls) == 5 and out["stopped"] == "done"


def test_first_look_records_failed_design(wb):
    wb.fail_high = True
    out = json.loads(wb.call("first_look", args()))
    assert out["stopped"] == "done" and len(wb.calls) == 5
    assert out["features"][0]["feasible"]["high"] is False
    assert any("error" in e for e in drain(wb))


def test_first_look_rejects_more_than_4_features(wb):
    with pytest.raises(ValueError):
        wb.call("first_look", args(features=FEATURES * 3))


def test_first_look_rejects_bad_range_and_cost_without_price(wb):
    with pytest.raises(ValueError):
        wb.call("first_look", args(features=[{**FEATURES[0], "low": 3.0, "high": 1.0}]))
    with pytest.raises(ValueError):
        wb.call("first_look", args(objective="cost_usd"))


def test_reference_uses_face_radius(wb):
    wb.call("first_look", args())
    ref_edits = dict(wb.calls)["look_ref"]
    assert ref_edits[1]["value_mm"] == 6.0  # radius 4 is outside 5-7 -> midpoint
    assert ref_edits[0]["value_mm"] == 0.0  # offset reference is 0


def test_seconds_per_design_default(wb):
    assert wb.seconds_per_design() == 30.0
