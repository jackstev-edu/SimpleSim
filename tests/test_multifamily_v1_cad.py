"""CAD and sample-plan gates for the ten multifamily templates."""
import math
import pytest

pytest.importorskip('scipy')
from src.data.multifamily_v1.templates import (
    FAMILIES, baseline, sample_plan, ranges, build_template, check_faces,
    make_case,
)


def values(case):
    return {**case.params, 'poissons_ratio':case.material.poissons_ratio}


@pytest.mark.parametrize('key', sorted(FAMILIES))
def test_sample_plan(key):
    plan=sample_plan(key)
    assert plan==sample_plan(key)
    assert len(plan)==80
    assert len({c.design_id for c in plan})==80
    assert len({tuple(values(c).values()) for c in plan})==80
    anchors={c.design_id:c for c in plan if c.sampling_method=='lhs'}
    assert len(anchors)==60
    for name,(lo,hi) in ranges(key).items():
        bins={math.floor(60*(values(c)[name]-lo)/(hi-lo)) for c in anchors.values()}
        assert bins==set(range(60))
    paired=[c for c in plan if c.sampling_method=='paired']
    assert len(paired)==20 and len({c.anchor_id for c in paired})==10
    for c in paired:
        old,new=values(anchors[c.anchor_id]),values(c)
        assert [n for n in old if old[n]!=new[n]]==[c.changed_parameter]
    assert all(c.load_vector_n==(0.,-500.,0.) for c in plan)


@pytest.mark.parametrize('key', sorted(FAMILIES))
def test_baseline_step_roundtrip(key,tmp_path):
    cq=pytest.importorskip('cadquery')
    case=baseline(key)
    solid=FAMILIES[key].build(case)
    fixed,load=check_faces(case,solid)
    assert fixed>0 and load>0
    path=tmp_path/f'{key}.step'
    solid.exportStep(str(path))
    restored=cq.importers.importStep(str(path)).val()
    assert restored.isValid() and len(restored.Solids())==1
    assert restored.Volume()==pytest.approx(solid.Volume(),rel=1e-6)
    check_faces(case,restored)


@pytest.mark.parametrize('key', sorted(FAMILIES))
def test_reject_out_of_range(key):
    p=values(baseline(key))
    p['thickness_mm']=3.
    with pytest.raises(ValueError,match='thickness_mm'):
        make_case(key,'bad',p)
