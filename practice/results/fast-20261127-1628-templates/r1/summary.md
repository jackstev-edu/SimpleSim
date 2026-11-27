# Fast-path benchmark

| problem | handoff | asked | confirmed | force | direction | material | safety_factor | objective | limits | vary | family | no_silent_default | feasible | pick_gap | dense_gap | sim_gap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| t01_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t02_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t03_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | pass | **fail** | pass | info | info |
| t04_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t05_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t06_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t07_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t08_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | pass | pass | **fail** | info | info |
| t09_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t10_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |

**136 / 140 checks pass** (n/a and info not counted).

## Pick

| problem | gap to the fast path's own best (scored) | gap to 400,000-design best (info) | gap to CalculiX best (info) |
| --- | --- | --- | --- |
| t01_clean_mass | +0.0% | +6.5% | -22.6% |
| t02_clean_mass | +0.0% | +20.1% | -23.0% |
| t03_clean_mass | -0.9% | +18.8% | -6.8% |
| t04_clean_mass | +0.0% | +8.6% | -21.5% |
| t05_clean_mass | +0.0% | +9.4% | -29.2% |
| t06_clean_mass | +0.0% | +12.7% | -17.8% |
| t07_clean_mass | +0.0% | +10.3% | -16.5% |
| t08_clean_mass | +14.9% | +24.8% | -7.6% |
| t09_clean_mass | +0.0% | +9.1% | -31.1% |
| t10_clean_mass | +0.0% | +22.4% | -14.6% |

## Failures

- t03_clean_mass `family`: got t02_single_slot_v1, want t03_twin_slot_v1
- t03_clean_mass `feasible`: SF at the pessimistic bound 1.433
- t08_clean_mass `family`: got l_bracket_gusset_v1, want t08_twin_gusset_v1
- t08_clean_mass `pick_gap`: mass_kg 0.30992 vs model optimum 0.26984 (+14.9%)
