# Fast-path benchmark, 3 repeats

A check passes only when all repeats pass it; `fail (k/N)` gives the passing repeats. A repeat that never reached a problem counts as not passing.

| problem | handoff | asked | confirmed | force | direction | material | safety_factor | objective | limits | vary | family | no_silent_default | feasible | pick_gap | dense_gap | sim_gap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| t01_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t02_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t03_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** (0/3) | pass | **fail** (0/3) | pass | info | info |
| t04_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t05_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t06_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t07_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t08_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** (0/3) | pass | pass | **fail** (0/3) | info | info |
| t09_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |
| t10_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info |

**136 / 140 combined checks pass** (n/a and info not counted).

## Pick (gap to the fast path's own best, per repeat)

| problem | r1 | r2 | r3 |
| --- | --- | --- | --- |
| t01_clean_mass | +0.0% | +0.0% | +0.0% |
| t02_clean_mass | +0.0% | +0.0% | +0.0% |
| t03_clean_mass | -0.9% | -0.9% | -0.9% |
| t04_clean_mass | +0.0% | +0.0% | +0.0% |
| t05_clean_mass | +0.0% | +0.0% | +0.0% |
| t06_clean_mass | +0.0% | +0.0% | +0.0% |
| t07_clean_mass | +0.0% | +0.0% | +0.0% |
| t08_clean_mass | +14.9% | +14.9% | +14.9% |
| t09_clean_mass | +0.0% | +0.0% | +0.0% |
| t10_clean_mass | +0.0% | +0.0% | +0.0% |

## Failures

- r1 t03_clean_mass `family`: got t02_single_slot_v1, want t03_twin_slot_v1
- r1 t03_clean_mass `feasible`: SF at the pessimistic bound 1.433
- r1 t08_clean_mass `family`: got l_bracket_gusset_v1, want t08_twin_gusset_v1
- r1 t08_clean_mass `pick_gap`: mass_kg 0.30992 vs model optimum 0.26984 (+14.9%)
- r2 t03_clean_mass `family`: got t02_single_slot_v1, want t03_twin_slot_v1
- r2 t03_clean_mass `feasible`: SF at the pessimistic bound 1.433
- r2 t08_clean_mass `family`: got l_bracket_gusset_v1, want t08_twin_gusset_v1
- r2 t08_clean_mass `pick_gap`: mass_kg 0.30992 vs model optimum 0.26984 (+14.9%)
- r3 t03_clean_mass `family`: got t02_single_slot_v1, want t03_twin_slot_v1
- r3 t03_clean_mass `feasible`: SF at the pessimistic bound 1.433
- r3 t08_clean_mass `family`: got l_bracket_gusset_v1, want t08_twin_gusset_v1
- r3 t08_clean_mass `pick_gap`: mass_kg 0.30992 vs model optimum 0.26984 (+14.9%)
