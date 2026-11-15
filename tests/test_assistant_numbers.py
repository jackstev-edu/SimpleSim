"""Item 3 number check: every number in the explanation must come from result.json (or a % change vs baseline)."""
from src.assistant.numbers import allowed_numbers, unknown_numbers

RESULT = {
    "spec": {"constraints": [{"metric": "safety_factor", "op": ">=", "value": 2.0}], "n_verify": 5,
             "bracket_family": "l_bracket_v0"},
    "baseline": {"design_id": "baseline", "mass_kg": 0.12042900807076046, "max_stress_mpa": 159.34844569714872,
                 "safety_factor": 1.5688888517628845, "max_disp_mm": 0.0580008},
    "recommended": {"design_id": "fake-0-00228", "mass_kg": 0.06638908682050484, "max_stress_mpa": 110.26071626711466,
                    "safety_factor": 2.2673533100796903, "max_disp_mm": 0.026103438329740547,
                    "design": {"thickness_mm": 2.756151831517622}},
    "binding_constraint": "safety_factor >= 2",
    "discovery_values": {"thickness_mm": 2.756151831517622},
}


def test_rounded_values_from_the_result_pass():
    text = ("Design fake-0-00228 weighs 0.066 kg against 0.120 kg for the baseline, with a safety factor of 2.27 "
            "(limit 2) and a peak stress of 110 MPa. Enter a thickness of 2.76 mm in Discovery.")
    assert unknown_numbers(text, RESULT) == []


def test_percent_change_against_baseline_passes_with_or_without_sign():
    # mass: (0.0664 - 0.1204) / 0.1204 = -44.9 %
    assert unknown_numbers("It is 45% lighter (-44.9 %).", RESULT) == []


def test_invented_numbers_are_caught():
    assert unknown_numbers("It is 60% lighter and holds 900 N.", RESULT) == ["60%", "900"]


def test_identifiers_and_units_are_not_numbers():
    assert unknown_numbers("Family l_bracket_v0, design fake-0-00228, p95 band, mm^2.", RESULT) == []


def test_numbers_inside_strings_of_the_result_count():
    assert 2.0 in allowed_numbers(RESULT)  # from "safety_factor >= 2"


def test_wrong_rounding_is_caught():
    assert unknown_numbers("safety factor 2.30", RESULT) == ["2.30"]
