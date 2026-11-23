# Fast-path benchmark

| problem | force | direction | material | safety_factor | objective | limits | vary | asked | confirmed | no_silent_default | feasible | pick_gap | sim_gap | handoff |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fast_refuse_titanium | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | pass |
| v0_alu_vague | pass | pass | pass | pass | pass | pass | pass | **fail** | pass | pass | pass | **fail** | n/a | pass |
| v0_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | info | pass |
| v0_kn_units | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | pass | **fail** | **fail** | **fail** | **fail** | n/a | pass |
| v0_missing_force | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | **fail** | n/a | **fail** |
| v0_missing_sf | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | info | pass |
| v0_stiff | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | n/a | pass |
| v1_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | info | pass |
| v1_named_dims | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | n/a | pass |
| v1_sf2_lbf | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | **fail** | n/a | pass |

**87 / 118 checks pass** (n/a and info not counted).

## Pick

| problem | gap to model optimum | gap to CalculiX best (info) |
| --- | --- | --- |
| fast_refuse_titanium |  |  |
| v0_alu_vague | +11.9% |  |
| v0_clean_mass | +9.8% | -16.4% |
| v0_kn_units |  |  |
| v0_missing_force |  |  |
| v0_missing_sf | +9.8% | -16.4% |
| v0_stiff | +19.4% |  |
| v1_clean_mass | +16.3% | -0.7% |
| v1_named_dims | +0.2% |  |
| v1_sf2_lbf | +27.3% |  |

## Failures

- v0_alu_vague `asked`: not asked before use: material
- v0_alu_vague `pick_gap`: mass_kg 0.068555 vs model optimum 0.061289 (+11.9%)
- v0_clean_mass `pick_gap`: mass_kg 0.23072 vs model optimum 0.21021 (+9.8%)
- v0_kn_units `confirmed`: confirm_setup was never called
- v0_kn_units `force`: no optimize result (reply cap)
- v0_kn_units `direction`: no optimize result (reply cap)
- v0_kn_units `material`: no optimize result (reply cap)
- v0_kn_units `safety_factor`: no optimize result (reply cap)
- v0_kn_units `objective`: no optimize result (reply cap)
- v0_kn_units `limits`: no optimize result (reply cap)
- v0_kn_units `vary`: no optimize result (reply cap)
- v0_kn_units `no_silent_default`: no optimize result (reply cap)
- v0_kn_units `feasible`: no optimize result (reply cap)
- v0_kn_units `pick_gap`: no optimize result (reply cap)
- v0_missing_force `handoff`: handed off to ANSYS on a fast-path problem
- v0_missing_force `asked`: not asked before use: load_face
- v0_missing_force `confirmed`: confirm_setup was never called
- v0_missing_force `force`: no optimize result (handoff)
- v0_missing_force `direction`: no optimize result (handoff)
- v0_missing_force `material`: no optimize result (handoff)
- v0_missing_force `safety_factor`: no optimize result (handoff)
- v0_missing_force `objective`: no optimize result (handoff)
- v0_missing_force `limits`: no optimize result (handoff)
- v0_missing_force `vary`: no optimize result (handoff)
- v0_missing_force `no_silent_default`: no optimize result (handoff)
- v0_missing_force `feasible`: no optimize result (handoff)
- v0_missing_force `pick_gap`: no optimize result (handoff)
- v0_missing_sf `pick_gap`: mass_kg 0.23072 vs model optimum 0.21021 (+9.8%)
- v0_stiff `pick_gap`: max_disp_mm 0.25088 vs model optimum 0.21014 (+19.4%)
- v1_clean_mass `pick_gap`: mass_kg 0.29027 vs model optimum 0.24962 (+16.3%)
- v1_sf2_lbf `pick_gap`: mass_kg 0.45079 vs model optimum 0.35414 (+27.3%)
