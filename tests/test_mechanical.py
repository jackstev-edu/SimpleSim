"""Mesh-size guard: an over-limit mesh must be refused before the solver runs (no ANSYS needed)."""
import pytest

from src.sim.mechanical import STUDENT_NODE_LIMIT, MeshTooLarge, check_mesh_size


def test_within_limit_passes():
    check_mesh_size(STUDENT_NODE_LIMIT, 60_000, 1.0, None)


def test_too_many_nodes_refused_with_advice():
    with pytest.raises(MeshTooLarge) as e:
        check_mesh_size(140_512, 90_000, 0.5, ("NS_FILLETS", 0.3125))
    msg = str(e.value)
    assert "140,512" in msg and "128,000" in msg and "NOT solved" in msg
    assert "0.3125 mm on the fillets" in msg and "coarser" in msg


def test_too_many_elements_refused():
    with pytest.raises(MeshTooLarge):
        check_mesh_size(100_000, 130_000, 1.0, None)


def test_is_a_value_error():
    assert issubclass(MeshTooLarge, ValueError)


def test_unfinished_solve_is_refused_with_the_solver_messages():
    from src.sim.mechanical import SolveFailed, check_solved
    check_solved("Done", [], None)  # a finished solve passes
    with pytest.raises(SolveFailed) as e:
        check_solved("SolveRequired", ["An unknown error occurred during solution."], "*** ERROR ***\nlicense lost")
    text = str(e.value)
    assert "SolveRequired" in text and "unknown error" in text and "license lost" in text and "NOT solved" in text


def test_zone_fields_convert_units():
    from src.sim.mechanical import _zone_fields
    # Nodes 1..3 on a line, 10 mm apart; support at node 1, stress peak (300 MPa) beside it.
    coords_m = {1: (0.0, 0.0, 0.0), 2: (0.01, 0.0, 0.0), 3: (0.02, 0.0, 0.0)}
    out = _zone_fields([1, 2, 3], [300e6, 100e6, 80e6], coords_m, {1}, set(), set(),
                       step_mm=5.0, zone_mm=5.0, max_stress_mpa=300.0)
    assert out["stress_for_limit_MPa"] == pytest.approx(100.0)
    assert out["stress_for_limit_at_mm"] == [10.0, 0.0, 0.0]
    assert out["excluded_peak_MPa"] == pytest.approx(300.0)
    assert out["excluded_peak_at_mm"] == [0.0, 0.0, 0.0]


def test_zone_fields_without_plotdata_fall_back_to_raw_peak():
    from src.sim.mechanical import _zone_fields
    out = _zone_fields([], [], {}, set(), set(), set(), 5.0, None, 123.0)
    assert out["stress_for_limit_MPa"] == 123.0
    assert out["zone_mm"] is None and out["excluded_peak_MPa"] is None


def test_refinement_check_compares_zone_stress_and_reuses_zone(monkeypatch):
    from src.sim import mechanical
    calls = []

    def fake_solve(*args, **kwargs):
        calls.append(kwargs)
        n = len(calls)
        return {"max_stress_MPa": 100.0 * n, "stress_for_limit_MPa": 49.0 + n, "zone_mm": 3.0, "nodes": 1}

    monkeypatch.setattr(mechanical, "solve", fake_solve)
    out = mechanical.refinement_check("p.pmdb", "F", "L", (0, 0, 1), guard="G", min_wall_mm=4.0)
    assert out["converged"] is True
    assert out["stress_change"] == pytest.approx(0.02)
    assert calls[1]["zone_mm"] == 3.0
    assert calls[1]["guard"] == "G" and calls[1]["min_wall_mm"] == 4.0
    assert out["basis"] == "stress_for_limit_MPa" and "note" not in out


def test_refinement_check_compares_raw_peaks_when_one_zone_fell_back(monkeypatch):
    from src.sim import mechanical
    results = iter([
        {"max_stress_MPa": 100.0, "stress_for_limit_MPa": 60.0, "zone_mm": 3.0, "nodes": 1},
        {"max_stress_MPa": 105.0, "stress_for_limit_MPa": 105.0, "zone_mm": None, "nodes": 1},
    ])
    monkeypatch.setattr(mechanical, "solve", lambda *a, **k: next(results))
    out = mechanical.refinement_check("p.pmdb", "F", "L", (0, 0, 1))
    assert out["basis"] == "max_stress_MPa"
    assert out["stress_change"] == pytest.approx(0.05)
    assert "raw peak" in out["note"]


def test_refinement_check_zero_coarse_stress_does_not_divide_by_zero(monkeypatch):
    from src.sim import mechanical
    zero = {"max_stress_MPa": 0.0, "stress_for_limit_MPa": 0.0, "zone_mm": 3.0, "nodes": 1}
    monkeypatch.setattr(mechanical, "solve", lambda *a, **k: dict(zero))
    out = mechanical.refinement_check("p.pmdb", "F", "L", (0, 0, 1))
    assert out["converged"] is False and "zero" in out["note"]


def test_mesh_too_large_on_fine_solve_quotes_the_limit_stress(monkeypatch):
    from src.sim import mechanical
    coarse = {"max_stress_MPa": 400.0, "stress_for_limit_MPa": 120.0, "zone_mm": 3.0, "nodes": 1000}
    calls = iter([coarse])

    def fake_solve(*a, **k):
        try:
            return next(calls)
        except StopIteration:
            raise mechanical.MeshTooLarge("too many nodes.")

    monkeypatch.setattr(mechanical, "solve", fake_solve)
    with pytest.raises(mechanical.MeshTooLarge, match="120.0 MPa"):
        mechanical.refinement_check("p.pmdb", "F", "L", (0, 0, 1))


def test_zone_fallback_message_does_not_repeat_the_plotdata_message():
    from src.sim.mechanical import _zone_from_mesh
    fields, msg = _zone_from_mesh(None, [None, None, None], [], [], 3.0, None, 50.0)
    assert fields["stress_for_limit_MPa"] == 50.0
    assert "PlotData" not in msg and "raw peak" in msg
