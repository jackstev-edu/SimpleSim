"""Workbench first_look / progress / stop, with a fake _evaluate (no ANSYS started)."""
import pytest

# Role 3 packages live in src/sim/requirements.txt, not the root one, so CI skips this file.
pytest.importorskip("ansys.geometry.core")
pytest.importorskip("ansys.mechanical.core")
pytest.importorskip("ansys.optislang.core")

import json


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
    # the agent itself sees why the design failed, and solves counts only completed designs
    err = out["errors"]["look_1_high"]  # message plus where it was raised (file:line in function)
    assert list(out["errors"]) == ["look_1_high"] and err.startswith("RuntimeError: boom (at test_workbench.py:")
    assert err.endswith(" in fake)")
    assert out["failed"] == 1 and out["solves"] == 4


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


# --- exclusion-zone stress and guard faces ---------------------------------
class FakeGeo:
    """Records name_faces calls; describe_faces grows by one cylinder after a fillet."""

    def __init__(self):
        self.named, self.filleted = [], False

    def open(self, path): pass
    def volume_mm3(self): return 1000.0
    def save(self, path): return path
    def offset_face(self, fid, value, normal): pass
    def set_radius(self, ids, value): pass
    def name_faces(self, name, ids): self.named.append((name, list(ids)))
    def fillet_between(self, a, b, r): self.filleted = True

    def describe_faces(self):
        faces = [{"id": i, "type": "plane", "normal": [0, 0, 1]} for i in ("0:27", "0:36", "0:39", "0:40")]
        if self.filleted:
            faces.append({"id": "0:99", "type": "cylinder", "radius_mm": 3.0})
        return faces


@pytest.fixture
def gwb(tmp_path, monkeypatch):
    from src.agent import tools
    w = Workbench(tmp_path)
    w.part = tmp_path / "original.scdocx"
    w.load_case = {"fixed_face_ids": ["0:27"], "load_face_ids": ["0:36"], "force_N": [0, 0, -500]}
    w.geo = FakeGeo()
    w._session = lambda: w.geo
    w.min_wall_mm = 2.5
    monkeypatch.setattr(tools.screen, "hole_faces", lambda faces, fid: [fid], raising=False)
    yield w
    w._pool.shutdown()


def test_safety_factor_uses_zone_stress(gwb):
    d = {"volume_mm3": 1000.0, "material": {"density_kg_m3": 7850.0, "yield_MPa": 250.0},
         "results": {"max_stress_MPa": 400.0, "stress_for_limit_MPa": 100.0, "max_def_mm": 0.1}}
    r = gwb._responses(d)
    assert r["safety_factor"] == 2.5 and r["stress_for_limit_MPa"] == 100.0 and r["max_stress_MPa"] == 400.0


def test_guard_excludes_fixed_and_loaded_faces(gwb):
    gwb._build([{"face_id": "0:36", "action": "offset", "value_mm": 1.0},
                {"face_id": "0:39", "action": "offset", "value_mm": 1.0}])
    assert ("NS_GUARD", ["0:39"]) in gwb.geo.named


def test_guard_includes_new_fillet_faces(gwb):
    gwb._build([{"face_id": "0:39", "action": "fillet", "other_face_id": "0:40", "value_mm": 3.0}])
    assert ("NS_GUARD", ["0:99"]) in gwb.geo.named


def test_refine_meshes_existing_faces_finely_and_guards_them(gwb):
    from src.agent import tools
    _, local = gwb._build([{"face_id": "0:39", "action": "refine", "also_face_ids": ["0:40"], "value_mm": 0.5}])
    assert local == (tools.FILLETS, 0.5)
    assert ("NS_FILLETS", ["0:39", "0:40"]) in gwb.geo.named and ("NS_GUARD", ["0:39", "0:40"]) in gwb.geo.named


def test_refine_with_a_fillet_takes_the_finer_size(gwb):
    _, local = gwb._build([{"face_id": "0:39", "action": "fillet", "other_face_id": "0:40", "value_mm": 3.0},
                           {"face_id": "0:27", "action": "refine", "value_mm": 0.5}])
    assert local[1] == 0.5 and ("NS_FILLETS", ["0:99", "0:27"]) in gwb.geo.named


def test_refine_rejects_unknown_faces(gwb):
    with pytest.raises(KeyError):
        gwb._build([{"face_id": "0:39", "action": "refine", "also_face_ids": ["nope"], "value_mm": 0.5}])

def test_no_guard_when_set_empty(gwb):
    gwb._build([{"face_id": "0:36", "action": "offset", "value_mm": 1.0}])
    assert not any(n == "NS_GUARD" for n, _ in gwb.geo.named)


def test_limit_on_raw_peak_is_refused(gwb):
    with pytest.raises(ValueError, match="stress_for_limit_MPa"):
        gwb.call("optimize", {"prefix": "o", "objective": "mass_kg", "variables": [
            {"face_id": "F1", "action": "offset", "lower": 0, "upper": 1, "start": 0}],
            "limits": [{"response": "max_stress_MPa", "max": 150}]})
    with pytest.raises(ValueError, match="stress_for_limit_MPa"):
        gwb.call("first_look", {"features": [{"face_id": "F1", "action": "offset", "low": 0, "high": 1}],
                                "objective": "max_stress_MPa", "limits": []})


def test_check_mesh_reports_zone_fields(gwb, monkeypatch):
    from src.agent import tools
    side = {"element_size_mm": 2.0, "local_size_mm": None, "max_stress_MPa": 400.0, "max_stress_at_mm": None,
            "max_def_mm": 0.1, "nodes": 10, "stress_for_limit_MPa": 100.0, "zone_mm": 3.0,
            "zone_settled": True, "excluded_peak_MPa": 400.0, "excluded_at": "support"}
    seen = {}

    def fake_check(*a, **k):
        seen.update(k)
        return {"coarse": side, "fine": side, "stress_change": 0.0, "converged": True}

    monkeypatch.setattr(tools.mechanical, "refinement_check", fake_check)
    gwb.designs["d"] = {"results": {"element_size_mm": 2.0}, "pmdb": "p", "local": None, "material": {}}
    out = json.loads(gwb.call("check_mesh", {"name": "d"}))
    assert out["coarse"]["stress_for_limit_MPa"] == 100.0 and out["coarse"]["zone_settled"] is True
    assert out["fine"]["excluded_at"] == "support"
    assert seen["guard"] == "NS_GUARD" and seen["min_wall_mm"] == 2.5


def test_set_load_case_reports_how_many_holes_it_fixed_and_loaded(gwb, monkeypatch):
    monkeypatch.undo()  # the fixture stubs hole_faces; this test needs the real grouping
    cyl = lambda fid, p: {"id": fid, "type": "cylinder", "kind": "concave", "radius_mm": 5.0,
                          "axis_point_mm": list(p), "axis_dir": [0, 0, 1]}
    gwb.faces = [cyl("h1a", (0, 0, 0)), cyl("h1b", (0, 0, 0)), cyl("h2a", (50, 0, 0)), cyl("pin", (20, 40, 0))]
    out = json.loads(gwb.call("set_load_case", {"fixed_face_ids": ["h1a", "h1b"], "load_face_ids": ["pin"],
                                                "force_N": [0, 0, 100]}))
    assert out["fixed_holes"] == 1 and out["loaded_holes"] == 1
