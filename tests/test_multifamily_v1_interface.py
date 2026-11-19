from collections import Counter, defaultdict
import math
import pytest
from src.data.bracket_spec import make_baseline_case
from src.data.geometry import build_bracket_geometry
from src.data.multifamily_v1.templates import FAMILIES, baseline, sample_plan, build_template
from src.data.multifamily_v1.features import extract_features, FEATURE_NAMES
from src.data.multifamily_v1.interface_gate import split_manifest, OLD, HOLDOUT, unique

@pytest.mark.parametrize('key',['v0',*FAMILIES])
def test_feature_schema(key):
    if key=='v0':
        case=make_baseline_case(); solid=build_bracket_geometry(case)
    else:
        case=baseline(key); solid=build_template(case)
    result=extract_features(case,solid)
    assert tuple(result)==FEATURE_NAMES
    assert all(math.isfinite(v) for v in result.values())
    assert result['cad_volume_mm3']==pytest.approx(solid.Volume())
    assert result['fixed_face_area_mm2']>0 and result['load_face_area_mm2']>0
    assert result['inertia_xx_mm5']>0


def test_split_groups_and_counts():
    rows=[dict(design_id=f'legacy_{i}',bracket_family=OLD,anchor_id=f'legacy_{i}') for i in range(200)]
    for key,family in FAMILIES.items():
        rows += [dict(design_id=c.design_id,bracket_family=family.key,anchor_id=c.anchor_id) for c in sample_plan(key)]
    split=split_manifest(rows)
    assert split==split_manifest(list(reversed(rows)))
    assert Counter(r['split'] for r in split)=={'train':736,'validation':184,'heldout_test':80}
    assert {r['bracket_family'] for r in split if r['split']=='heldout_test'}=={HOLDOUT}
    by_group=defaultdict(set)
    for r in split: by_group[(r['bracket_family'],r['anchor_id'])].add(r['split'])
    assert all(len(v)==1 for v in by_group.values())
    for family in FAMILIES.values():
        if family.key!=HOLDOUT:
            assert sum(r['bracket_family']==family.key and r['split']=='validation' for r in split)==16


def test_duplicate_rejected():
    with pytest.raises(ValueError,match='Duplicate'):
        unique([{'design_id':'a'},{'design_id':'a'}])
