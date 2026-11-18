"""Tests for the L-bracket Gmsh mesh generator."""

import pytest

pytest.importorskip("cadquery")
pytest.importorskip("gmsh")

from src.data.bracket_spec import make_baseline_case
from src.data.mesh import (
    MeshSettings,
    generate_mesh,
)


def test_baseline_mesh_and_boundary_groups(tmp_path) -> None:
    summary = generate_mesh(
        make_baseline_case(),
        tmp_path,
    )

    assert summary.step_path.exists()
    assert summary.msh_path.exists()
    assert summary.inp_path.exists()

    assert summary.node_count > 0
    assert summary.volume_element_count > 0
    assert summary.fixed_node_count > 0
    assert summary.load_node_count > 0

    assert any(
        "Tetrahedron" in element_name
        for element_name in summary.element_type_names
    )

    assert summary.minimum_element_quality > 0.1
    assert summary.mean_element_quality > 0.5

    inp_text = summary.inp_path.read_text(
        encoding="utf-8"
    )

    assert "fixed_face" in inp_text
    assert "load_face" in inp_text
    assert "bracket_volume" in inp_text


def test_invalid_mesh_settings_are_rejected() -> None:
    with pytest.raises(ValueError):
        MeshSettings(
            minimum_size_mm=5.0,
            maximum_size_mm=2.0,
        )

    with pytest.raises(ValueError):
        MeshSettings(element_order=3)