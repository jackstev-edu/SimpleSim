"""Jack's AnsysBackend delegates to the Role 3 engine, imported only when used."""
import sys
import types
from pathlib import Path

import pytest

from src.pydiscovery.backends import BackendUnavailable, get_backend


def test_ansys_backend_delegates_lazily(monkeypatch):
    fake = types.ModuleType("src.sim.ansys_engine")

    class E:
        def __init__(self, mesh_check=False):
            self.last_details = {"k": 1}

        def solve(self, *a):
            return {"max_stress_mpa": 1.0, "max_disp_mm": 2.0, "mass_kg": 3.0}

        def close(self):
            pass

    fake.AnsysEngine = E
    monkeypatch.setitem(sys.modules, "src.sim.ansys_engine", fake)
    b = get_backend("ansys")
    assert b.last_details == {}
    assert b.solve(None, {}, 1.0, None)["mass_kg"] == 3.0 and b.last_details == {"k": 1}


def test_ansys_backend_without_ansys_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "src.sim.ansys_engine", None)  # makes the import raise ImportError
    with pytest.raises(BackendUnavailable, match="ANSYS"):
        get_backend("ansys").solve(None, {}, 1.0, None)


def test_backends_module_does_not_import_ansys():
    src = (Path(__file__).parents[1] / "src" / "pydiscovery" / "backends.py").read_text(encoding="utf-8")
    top = src.split("\nclass ")[0]
    assert "import AnsysEngine" not in top and "import ansys_engine" not in top  # only inside AnsysBackend.solve
