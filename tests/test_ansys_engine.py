"""Ansys engine for the PyDiscovery emulator, on a fake workbench (no ANSYS)."""
import json

import pytest

from src.pydiscovery.families import L_BRACKET_V0 as FAMILY
from src.pydiscovery.part_file import Material
from src.sim import materials
from src.sim.ansys_engine import BASELINE_MM, AnsysEngine, edits_for, find_faces, library_material

# ANSYS's material library ships with the install; CI has none, so tests that read it skip there.
needs_library = pytest.mark.skipif(not (materials.library_dir() / materials.LIBRARIES[0]).exists(),
                                   reason="needs the ANSYS 2026 R1 material library")
DESIGN = {"thickness_mm": 7.0, "flange_length_mm": 90.0, "arm_width_mm": 45.0,
          "hole_diameter_mm": 10.0, "fillet_radius_mm": 3.0}
STEEL = Material(name="Structural Steel", youngs_mpa=200000, density_kg_m3=7850)


def steel_e():
    return materials.catalog()["Structural Steel"]["youngs_modulus_GPa"] * 1000


def plane(fid, lo, hi, normal):
    return {"id": fid, "type": "plane", "bbox_min_mm": lo, "bbox_max_mm": hi, "normal": normal}


def cyl(fid):
    return {"id": fid, "type": "cylinder", "radius_mm": 4.0, "bbox_min_mm": [56, 0, 16], "bbox_max_mm": [64, 6, 24],
            "axis_point_mm": [60, 0, 20], "axis_dir": [0, 1, 0]}


FACES = [
    plane("b", [0, 0, 0], [0, 60, 40], [-1, 0, 0]),
    plane("vi", [6, 6, 0], [6, 60, 40], [1, 0, 0]),
    plane("e", [80, 0, 0], [80, 6, 40], [1, 0, 0]),
    plane("ht", [6, 6, 0], [80, 6, 40], [0, 1, 0]),
    plane("bot", [0, 0, 0], [80, 0, 40], [0, -1, 0]),
    plane("s", [0, 0, 40], [80, 60, 40], [0, 0, 1]),
    plane("s0", [0, 0, 0], [80, 60, 0], [0, 0, -1]),
    plane("top", [0, 60, 0], [6, 60, 40], [0, 1, 0]),
    cyl("h1"), cyl("h2"),
]


class FakeWorkbench:
    fail_next = False

    def __init__(self, out_dir):
        self.loads, self.load_cases, self.materials, self.calls = 0, [], [], []

    def load_part(self, path):
        self.loads += 1
        return {"faces": FACES}

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "set_material":
            self.materials.append(args["material"])
            return json.dumps({"note": "ok"})
        if name == "set_load_case":
            self.load_cases.append(args)
            return json.dumps({"ok": True})
        if name == "evaluate_design":
            if FakeWorkbench.fail_next:
                FakeWorkbench.fail_next = False
                raise RuntimeError("solve failed")
            return json.dumps({"name": args["name"], "volume_mm3": 30000.0, "stress_for_limit_MPa": 120.0,
                               "max_stress_MPa": 400.0, "max_def_mm": 0.5, "zone_mm": 3.0, "zone_settled": True,
                               "excluded_at": "support", "stress_for_limit_at_mm": [6, 10, 20],
                               "max_stress_at_mm": [0, 0, 0], "nodes": 9000, "messages": []})
        if name == "check_mesh":
            return json.dumps({"converged": True, "stress_change": 0.02})
        raise KeyError(name)

    def close(self):
        pass


def test_find_faces_by_geometry():
    r = find_faces(FACES)
    assert (r["back"], r["end"], r["side"], r["vert_inner"], r["horiz_top"]) == ("b", "e", "s", "vi", "ht")
    assert sorted(r["hole"]) == ["h1", "h2"]


def test_find_faces_names_the_missing_role():
    with pytest.raises(ValueError, match="end"):
        find_faces([f for f in FACES if f["id"] != "e"])


def test_edits_for_offsets_from_baseline():
    e = edits_for({"thickness_mm": 4.0, "flange_length_mm": 120.0, "arm_width_mm": 32.0,
                   "hole_diameter_mm": 12.0, "fillet_radius_mm": 2.0}, find_faces(FACES))
    assert {"face_id": "vi", "action": "offset", "value_mm": -2.0} in e
    assert {"face_id": "ht", "action": "offset", "value_mm": -2.0} in e
    assert {"face_id": "e", "action": "offset", "value_mm": 40.0} in e
    assert {"face_id": "s", "action": "offset", "value_mm": -8.0} in e
    assert {"face_id": "h1", "action": "radius", "also_face_ids": ["h2"], "value_mm": 6.0} in e
    assert e[-1] == {"face_id": "vi", "action": "fillet", "other_face_id": "ht", "value_mm": 2.0}


def test_edits_for_baseline_is_only_the_fillet():
    assert [x["action"] for x in edits_for({**BASELINE_MM, "fillet_radius_mm": 4.0}, find_faces(FACES))] == ["fillet"]


@needs_library
def test_library_material_matches_loosely():
    assert library_material("structural_steel") == "Structural Steel"
    assert library_material("unobtainium") is None


@needs_library
def test_solve_library_material_returns_zone_stress_and_part_density():
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    out = eng.solve(FAMILY, DESIGN, 500.0, STEEL)
    assert out == {"max_stress_mpa": 120.0, "max_disp_mm": 0.5, "mass_kg": pytest.approx(30000 * 7850e-9)}
    assert eng.last_details["raw_peak_mpa"] == 400.0 and eng.last_details["material_used"] == "Structural Steel"


@needs_library
def test_solve_unknown_material_scales_deflection_by_E():
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    out = eng.solve(FAMILY, DESIGN, 500.0, Material(name="Custom", youngs_mpa=100000, density_kg_m3=2000))
    assert out["max_stress_mpa"] == 120.0
    assert out["max_disp_mm"] == pytest.approx(0.5 * steel_e() / 100000)
    assert out["mass_kg"] == pytest.approx(30000 * 2000e-9)
    assert "rescaled" in eng.last_details["material_note"]


def test_solve_rejects_material_without_density():
    with pytest.raises(ValueError, match="density"):
        AnsysEngine(workbench_factory=FakeWorkbench).solve(FAMILY, DESIGN, 500.0, Material(name="X", youngs_mpa=1e5))


@needs_library
def test_force_follows_family_direction_and_load_case_is_reused():
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    eng.solve(FAMILY, DESIGN, 500.0, STEEL)
    eng.solve(FAMILY, DESIGN, 500.0, STEEL)
    wb = eng._wb
    assert wb.loads == 1 and wb.materials == ["Structural Steel"]
    assert wb.load_cases == [{"fixed_face_ids": ["b"], "load_face_ids": ["e"], "force_N": [0.0, -500.0, 0.0]}]
    eng.solve(FAMILY, DESIGN, 800.0, STEEL)
    assert len(wb.load_cases) == 2


@needs_library
def test_failed_solve_leaves_engine_usable():
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    FakeWorkbench.fail_next = True
    with pytest.raises(RuntimeError):
        eng.solve(FAMILY, DESIGN, 500.0, STEEL)
    assert eng.solve(FAMILY, DESIGN, 500.0, STEEL)["max_stress_mpa"] == 120.0


@needs_library
def test_mesh_check_is_opt_in():
    eng = AnsysEngine(mesh_check=True, workbench_factory=FakeWorkbench)
    eng.solve(FAMILY, DESIGN, 500.0, STEEL)
    assert eng.last_details["mesh_check"]["converged"] is True
    plain = AnsysEngine(workbench_factory=FakeWorkbench)
    plain.solve(FAMILY, DESIGN, 500.0, STEEL)
    assert "check_mesh" not in [c[0] for c in plain._wb.calls]


@needs_library
def test_library_material_matches_the_contract_aluminium_by_tokens():
    assert library_material("Aluminum alloy 6061-T6") == "Aluminum alloy, wrought, 6061, T6"


@needs_library
def test_library_match_still_uses_the_parts_own_E():
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    out = eng.solve(FAMILY, DESIGN, 500.0, Material(name="Structural Steel", youngs_mpa=steel_e() / 2, density_kg_m3=7850))
    assert out["max_disp_mm"] == pytest.approx(1.0)


@needs_library
def test_engine_closes_itself_at_exit(monkeypatch):
    hooks = []
    monkeypatch.setattr("src.sim.ansys_engine.atexit.register", hooks.append)
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    eng.solve(FAMILY, DESIGN, 500.0, STEEL)
    assert hooks == [eng.close]


def test_failed_first_setup_closes_the_workbench():
    closed = []

    class Broken(FakeWorkbench):
        def load_part(self, path):
            return {"faces": []}

        def close(self):
            closed.append(1)

    with pytest.raises(ValueError):
        AnsysEngine(workbench_factory=Broken).solve(FAMILY, DESIGN, 500.0, STEEL)
    assert closed == [1]



def test_solve_refuses_unknown_families_before_starting_ansys():
    from dataclasses import replace

    from src.pydiscovery.backends import BackendUnavailable

    started = []
    eng = AnsysEngine(workbench_factory=lambda out: started.append(out) or FakeWorkbench(out))
    with pytest.raises(BackendUnavailable, match="t07_stepped"):
        eng.solve(replace(FAMILY, key="t07_stepped"), DESIGN, 500.0, STEEL)
    assert started == []  # refused before SpaceClaim or Mechanical start


# Gusseted L-bracket (v1): built per design from Role 1's CadQuery STEP, so the faces are re-found each time.
GUSSET = {**DESIGN, "flange_length_mm": 90.0, "vertical_height_mm": 70.0, "gusset_thickness_mm": 5.0,
          "gusset_length_mm": 25.0}
GUSSET_FACES = [
    plane("gb", [0, 0, 0], [0, 70, 45], [-1, 0, 0]),  # back face, fixed
    plane("ge", [90, 0, 0], [90, 7, 45], [1, 0, 0]),  # free end, loaded
    plane("gt", [0, 0, 45], [90, 70, 45], [0, 0, 1]),
    {"id": "gh1", "type": "cylinder", "radius_mm": 5.0, "axis_point_mm": [60, 0, 22.5], "axis_dir": [0, 1, 0]},
    {"id": "gh2", "type": "cylinder", "radius_mm": 5.0, "axis_point_mm": [60, 0, 22.5], "axis_dir": [0, -1, 0]},
    # main inside fillet: runs the full width (z 0..45), so it is outside the gusset band
    {"id": "gf", "type": "cylinder", "radius_mm": 3.0, "axis_point_mm": [10, 10, 0], "axis_dir": [0, 0, 1],
     "bbox_min_mm": [7, 7, 0], "bbox_max_mm": [10, 10, 45]},
    # junction fillets: inside the band z 22.5 +/- (2.5 + 2)
    {"id": "gj1", "type": "cylinder", "radius_mm": 2.0, "axis_point_mm": [7, 20, 20], "axis_dir": [0, 1, 0],
     "bbox_min_mm": [7, 10, 18.0], "bbox_max_mm": [9, 30, 20.5]},
    {"id": "gj2", "type": "torus", "bbox_min_mm": [7, 7, 20], "bbox_max_mm": [9, 9, 22]},
]


class GussetWorkbench(FakeWorkbench):
    def __init__(self, out_dir):
        super().__init__(out_dir)
        self.loaded = []

    def load_part(self, path):
        self.loads += 1
        self.loaded.append(path)
        return {"faces": GUSSET_FACES}


def gusset_engine(**kw):
    from src.pydiscovery.families import L_BRACKET_GUSSET_V1

    written = []
    eng = AnsysEngine(workbench_factory=GussetWorkbench,
                      step_writer=lambda family, design, path: written.append((family.key, design, path)) or path, **kw)
    return eng, written, L_BRACKET_GUSSET_V1


@needs_library
def test_gusset_fine_mesh_goes_on_the_junction_faces_only():
    from src.sim.ansys_engine import gusset_faces

    roles = gusset_faces(GUSSET_FACES, GUSSET)
    assert roles["round"] == ["gj1", "gj2"] and roles["local_mm"] == 0.5


def test_gusset_without_junction_faces_says_so():
    from src.sim.ansys_engine import gusset_faces

    faces = [f for f in GUSSET_FACES if not f["id"].startswith("gj")]
    with pytest.raises(ValueError, match="junction"):
        gusset_faces(faces, GUSSET)


class MeshTooLarge(ValueError):  # stands in for src.sim.mechanical.MeshTooLarge (needs ANSYS to import)
    pass


class TooBigToCheckWorkbench(GussetWorkbench):
    def call(self, name, args):
        if name == "check_mesh":
            raise MeshTooLarge("Mesh check not possible: the halved mesh is over the Student limit.")
        return super().call(name, args)


@needs_library
def test_gusset_mesh_check_over_the_node_limit_keeps_the_result_as_unverified():
    from src.pydiscovery.families import L_BRACKET_GUSSET_V1

    eng = AnsysEngine(workbench_factory=TooBigToCheckWorkbench, step_writer=lambda f, d, p: p, mesh_check=True)
    out = eng.solve(L_BRACKET_GUSSET_V1, GUSSET, 500.0, STEEL)
    assert out["max_stress_mpa"] == 120.0
    chk = eng.last_details["mesh_check"]
    assert chk["converged"] is False and chk["verified"] is False and "Student limit" in chk["note"]


@needs_library
def test_gusset_design_is_solved_from_its_step_with_fine_mesh_on_the_round_faces():
    eng, written, v1 = gusset_engine()
    out = eng.solve(v1, GUSSET, 500.0, STEEL)
    wb = eng._wb
    assert [w[:2] for w in written] == [("l_bracket_gusset_v1", GUSSET)] and wb.loaded == [written[0][2]]
    assert str(written[0][2]).endswith(".step")
    assert wb.load_cases == [{"fixed_face_ids": ["gb"], "load_face_ids": ["ge"], "force_N": [0.0, -500.0, 0.0]}]
    edits = [a for n, a in wb.calls if n == "evaluate_design"][0]["edits"]
    assert edits == [{"face_id": "gj1", "action": "refine", "also_face_ids": ["gj2"], "value_mm": 0.5}]
    assert out == {"max_stress_mpa": 120.0, "max_disp_mm": 0.5, "mass_kg": pytest.approx(30000 * 7850e-9)}
    assert eng.last_details["step"] == str(written[0][2])


@needs_library
def test_each_gusset_design_reloads_and_sets_material_and_load_again():
    eng, written, v1 = gusset_engine()
    eng.solve(v1, GUSSET, 500.0, STEEL)
    eng.solve(v1, {**GUSSET, "gusset_length_mm": 30.0}, 500.0, STEEL)
    wb = eng._wb
    assert wb.loads == 2 and len(written) == 2 and written[0][2] != written[1][2]
    assert wb.materials == ["Structural Steel"] * 2 and len(wb.load_cases) == 2  # load_part resets both


def test_gusset_without_ansys_step_builder_says_what_is_missing():
    from src.pydiscovery.backends import BackendUnavailable
    from src.pydiscovery.families import L_BRACKET_GUSSET_V1

    def no_cadquery(family, design, path):
        raise BackendUnavailable("needs CadQuery")

    eng = AnsysEngine(workbench_factory=GussetWorkbench, step_writer=no_cadquery)
    with pytest.raises(BackendUnavailable, match="CadQuery"):
        eng.solve(L_BRACKET_GUSSET_V1, GUSSET, 500.0, STEEL)
