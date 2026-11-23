# Fast-path benchmark

| problem | force | direction | material | safety_factor | objective | limits | vary | asked | confirmed | no_silent_default | feasible | pick_gap | sim_gap | handoff |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fast_refuse_titanium | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | pass |
| v0_alu_vague | pass | pass | pass | pass | pass | pass | pass | **fail** | pass | pass | pass | **fail** | n/a | pass |
| v0_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | info | pass |
| v0_kn_units | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | n/a | pass |
| v0_missing_force | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | n/a | pass |
| v0_missing_sf | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | info | pass |
| v0_stiff | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | n/a | pass |
| v1_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | info | pass |
| v1_named_dims | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | n/a | pass |
| v1_sf2_lbf | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | n/a | pass |

**109 / 118 checks pass** (n/a and info not counted).

## Pick

| problem | gap to model optimum | gap to CalculiX best (info) |
| --- | --- | --- |
| fast_refuse_titanium |  |  |
| v0_alu_vague | +11.9% |  |
| v0_clean_mass | +9.8% | -16.4% |
| v0_kn_units | +10.6% |  |
| v0_missing_force | +14.0% |  |
| v0_missing_sf | +9.8% | -16.4% |
| v0_stiff | +19.4% |  |
| v1_clean_mass | +16.3% | -0.7% |
| v1_named_dims | +0.2% |  |
| v1_sf2_lbf | +27.3% |  |

## Failures

- v0_alu_vague `asked`: not asked before use: material
- v0_alu_vague `pick_gap`: mass_kg 0.068555 vs model optimum 0.061289 (+11.9%)
- v0_clean_mass `pick_gap`: mass_kg 0.23072 vs model optimum 0.21021 (+9.8%)
- v0_kn_units `pick_gap`: mass_kg 0.29328 vs model optimum 0.26523 (+10.6%)
- v0_missing_force `pick_gap`: mass_kg 0.19932 vs model optimum 0.17487 (+14.0%)
- v0_missing_sf `pick_gap`: mass_kg 0.23072 vs model optimum 0.21021 (+9.8%)
- v0_stiff `pick_gap`: max_disp_mm 0.25088 vs model optimum 0.21014 (+19.4%)
- v1_clean_mass `pick_gap`: mass_kg 0.29027 vs model optimum 0.24962 (+16.3%)
- v1_sf2_lbf `pick_gap`: mass_kg 0.45079 vs model optimum 0.35414 (+27.3%)
