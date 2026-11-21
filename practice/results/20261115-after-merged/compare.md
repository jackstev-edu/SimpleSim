# Practice set: 20261111-2313-before → 20261115-after-merged

| check | before | after |
|---|---|---|
| setup_fixed_faces | 10/12 | 11/12 |
| setup_load_faces | 11/12 | 10/12 |
| setup_force | 12/12 | 11/12 |
| setup_material | 12/12 | 12/12 |
| safety_factor_stated | 12/12 | 12/12 |
| asked_before_acting | 9/11 | 11/11 |
| flagged_before_acting | 2/2 | 2/2 |
| obedience | 4/4 | 4/4 |
| feature_recall | 1/7 | 3/7 |
| ranges_valid | 11/12 | 12/12 |
| complex_part | 1/1 | 1/1 |
| needed_correction | 11/12 | 11/12 |
| extra_negligible | 10/12 | 12/12 |

## Changed results

- jeb_complex: setup_load_faces pass → fail
- jeb_named: asked_before_acting fail → pass
- lbr_clean_auto: setup_force pass → fail
- lbr_clean_auto: extra_negligible fail → pass
- lbr_missing_support: setup_load_faces fail → pass
- lbr_missing_support: feature_recall fail → pass
- lbr_units: setup_load_faces pass → fail
- lbr_units: feature_recall fail → pass
- lbr_units: ranges_valid fail → pass
- nin_clean_auto: needed_correction fail → pass
- nin_named_holes: asked_before_acting fail → pass
- sad_clean_auto: needed_correction pass → fail
- sad_clean_auto: extra_negligible fail → pass
- sad_missing_force: setup_fixed_faces fail → pass
