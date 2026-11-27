# Fast-path benchmark

| problem | force | direction | material | safety_factor | objective | limits | vary | asked | confirmed | no_silent_default | feasible | pick_gap | dense_gap | sim_gap | handoff |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fast_refuse_titanium | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | pass |
| v0_alu_vague | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | n/a | pass |
| v0_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info | pass |
| v0_kn_units | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | n/a | pass |
| v0_missing_force | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | n/a | pass |
| v0_missing_sf | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info | pass |
| v0_stiff | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | n/a | pass |
| v1_clean_mass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | info | pass |
| v1_named_dims | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | info | n/a | pass |
| v1_sf2_lbf | pass | pass | pass | pass | pass | pass | pass | **fail** | pass | pass | pass | pass | info | n/a | pass |

**117 / 118 checks pass** (n/a and info not counted).

## Pick

| problem | gap to the fast path's own best (scored) | gap to 400,000-design best (info) | gap to CalculiX best (info) |
| --- | --- | --- | --- |
| fast_refuse_titanium |  |  |  |
| v0_alu_vague | +0.0% | +11.9% |  |
| v0_clean_mass | +0.0% | +9.8% | -16.4% |
| v0_kn_units | +0.0% | +10.6% |  |
| v0_missing_force | +0.0% | +14.0% |  |
| v0_missing_sf | +0.0% | +9.8% | -16.4% |
| v0_stiff | +0.0% | +19.4% |  |
| v1_clean_mass | +0.0% | +16.3% | -0.7% |
| v1_named_dims | +0.0% | +0.2% |  |
| v1_sf2_lbf | +0.0% | +27.3% |  |

## Failures

- v1_sf2_lbf `asked`: not asked before use: load_face
