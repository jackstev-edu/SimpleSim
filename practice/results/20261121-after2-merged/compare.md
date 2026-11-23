# Practice set: 20261115-after-merged → 20261121-after2-merged

| check | before | after |
|---|---|---|
| setup_fixed_faces | 11/12 | 12/12 |
| setup_load_faces | 10/12 | 12/12 |
| setup_force | 11/12 | 12/12 |
| setup_material | 12/12 | 12/12 |
| safety_factor_stated | 12/12 | 12/12 |
| asked_before_acting | 11/11 | 11/11 |
| flagged_before_acting | 2/2 | 2/2 |
| obedience | 4/4 | 4/4 |
| feature_recall | 4/7 | 4/7 |
| ranges_valid | 12/12 | 12/12 |
| complex_part | 1/1 | 1/1 |
| needed_correction | 11/12 | 12/12 |
| extra_negligible | 11/12 | 11/12 |

## Changed results

- jeb_complex: setup_load_faces fail → pass
- lbr_clean_auto: setup_force fail → pass
- lbr_units: setup_load_faces fail → pass
- sad_clean_auto: setup_fixed_faces fail → pass
- sad_clean_auto: feature_recall pass → fail
- sad_clean_auto: needed_correction fail → pass
- sad_missing_force: feature_recall fail → pass
