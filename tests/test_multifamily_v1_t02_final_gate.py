import pytest
from src.data.multifamily_v1 import batch_v1 as b, t02_final_gate as final
from tests.test_multifamily_v1_batch import plan, prepare, sim

@pytest.mark.parametrize('scenario', ['pass', 'stress', 'nodes', 'failure'])
def test_global_review(plan,tmp_path,scenario):
    risk,typical=b.select_pilot(plan)
    row=prepare(plan[risk['T02']]); lo=b.production(row,'fine',tmp_path,10,2,sim)
    b.production(prepare(plan[typical['T02']]),'fine',tmp_path,10,2,sim)
    folder=tmp_path/'cases'/row['design_id']; path=folder/'comparison.json'
    prior=dict(convergence_status='REVIEW',stress_change_pct=3.168)
    b.g.atomic_json(path,prior)
    before=(folder/'production'/'result.json').read_bytes(); calls=[]
    def upper(c,f,m,t,n):
        calls.append(m)
        if scenario=='failure':raise TimeoutError('test only')
        r=sim(c,f,m,t,n);r.max_stress_mpa=102 if scenario!='stress' else 110; r.factor_of_safety=250/r.max_stress_mpa
        r.node_count=400 if scenario!='nodes' else lo['metrics']['node_count'];r.volume_element_count=200
        return r
    report=final.verify(row,tmp_path,10,2,upper)
    assert report['convergence_status']=={'pass':'PASS','stress':'REVIEW','nodes':'REVIEW','failure':'PENDING_OR_FAILED'}[scenario]
    assert (folder/'production'/'result.json').read_bytes()==before
    assert b.read_json(folder/'comparison_before_t02_global_reference.json')==prior
    assert b.read_json(path)==(report if scenario=='pass' else prior)
    levels={k:'fine' for k in b.FAMILIES}
    s=b.export(tmp_path,plan,levels,risk,typical)
    assert s['accepted_labels']==(2 if scenario=='pass' else 0)
    if scenario=='pass':
        assert final.verify(row,tmp_path,10,2,upper)==report and len(calls)==1


def test_other_family_rejected(plan,tmp_path):
    with pytest.raises(ValueError,match='T02 only'):
        final.verify(plan['T01_lhs_000'],tmp_path,10,2,sim)
