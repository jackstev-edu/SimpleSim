import json
import pytest

pytest.importorskip('scipy')
from src.data.multifamily_v1 import batch_v1 as b, pilot_repair as repair
from tests.test_multifamily_v1_batch import plan, prepare, sim

@pytest.mark.parametrize('family', ['T02','T10'])
@pytest.mark.parametrize('passes', [True,False])
def test_migrated_peak(plan,tmp_path,family,passes):
    risk,_=b.select_pilot(plan);row=prepare(plan[risk[family]])
    def first(c,folder,mesh,timeout,threads):
        r=sim(c,folder,mesh,timeout,threads)
        if 'base' in mesh:
            r.max_stress_location_mm=(25.,5.,5.)
        return r
    lo=b.production(row,'fine',tmp_path,10,2,first)
    old=b.compare(row,'fine',lo,tmp_path,10,2,first)
    assert old['convergence_status']=='REVIEW' and not old['peak_in_refined_core']
    path=tmp_path/'cases'/row['design_id']/'comparison.json';b.g.atomic_json(path,old)
    old_prod=(path.parent/'production'/'result.json').read_bytes()
    old_target=(path.parent/'targeted'/'result.json').read_bytes()
    calls=[]
    def follow(c,folder,mesh,timeout,threads):
        calls.append(mesh)
        r=first(c,folder,mesh,timeout,threads)
        if not passes:
            r.max_stress_mpa=110.;r.factor_of_safety=250/110.
        return r
    new=repair.retry_one(row,tmp_path,10,2,follow)
    assert new['peak_in_refined_core'] and len(calls)==1
    assert new['convergence_status']==('PASS' if passes else 'REVIEW')
    assert [25.,5.,5.] in calls[0]['centers_mm']
    assert [10.,5.,5.] in calls[0]['centers_mm']
    assert (path.parent/'production'/'result.json').read_bytes()==old_prod
    assert (path.parent/'targeted'/'result.json').read_bytes()==old_target
    assert b.read_json(path.parent/'comparison_before_second_peak.json')==old
    assert repair.retry_one(row,tmp_path,10,2,follow)==new
    assert len(calls)==1
