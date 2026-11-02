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
