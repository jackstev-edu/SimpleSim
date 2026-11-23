"""The 10 fast-path benchmark problems: present, keyed, and consistent with the fast path's limits."""
from src.eval.fast_keys import load_fast_problems

IDS = ["v0_clean_mass", "v0_missing_sf", "v0_missing_force", "v0_alu_vague", "v0_kn_units", "v0_stiff",
       "v1_clean_mass", "v1_named_dims", "v1_sf2_lbf", "fast_refuse_titanium"]


def test_ten_problems_load_with_keys():
    assert sorted(p.id for p in load_fast_problems()) == sorted(IDS)


def test_unit_conversions():
    k = {p.id: p.key["study"] for p in load_fast_problems()}
    assert k["v0_kn_units"]["force_n"] == 800.0
    assert abs(k["v1_sf2_lbf"]["force_n"] - 667.233) < 0.01 and k["v1_sf2_lbf"]["safety_factor"] == 2.0


def test_forces_inside_fast_range_except_handoff():
    for p in load_fast_problems():
        if not p.key["expect_handoff"]:
            assert 100.0 <= p.key["study"]["force_n"] <= 1000.0


def test_sim_optimum_only_where_load_matches():
    for p in load_fast_problems():
        if p.key["expect_handoff"]:
            continue
        s, sim = p.key["study"], p.key["sim_optimum"]
        match = s["force_n"] == 500.0 and s["angle_deg"] == -90.0 and s["material_key"] == "structural_steel"
        if not match:
            assert sim["design_id"] is None, p.id
        else:  # a matching load gets a table row, or a stated reason why none fits
            assert sim["design_id"] is not None or sim["reason"], p.id


def test_unstated_support_and_load_face_are_asked():
    for p in load_fast_problems():
        if not p.key["expect_handoff"] and not p.key.get("states_setup"):
            assert {"support", "load_face"} <= set(p.key["must_ask"]), p.id
