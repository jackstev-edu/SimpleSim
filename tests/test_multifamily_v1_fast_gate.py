from unittest.mock import MagicMock
from src.data.multifamily_v1 import fea_gate as g, refine_gate as r, fast_gate as f
from src.data.multifamily_v1.templates import baseline
from tests.test_multifamily_v1_fea_gate import frozen_inputs,fake_result

def test_fields():
    c=baseline('T08');a={'metrics':{'max_stress_location_mm':[10.,5.,10.]}};b={'metrics':{'max_stress_location_mm':[11.,5.,11.]}}
    p=f.plan_for(c,a,b,4)
    assert len(p['centers_mm'])==2
    assert p['base']['maximum_size_mm']==g.mesh_parameters(c,'fine')['maximum_size_mm']
    assert p['base']['minimum_size_mm']<g.mesh_parameters(c,'fine')['minimum_size_mm']
    gm=MagicMock();gm.model.mesh.field.add.side_effect=[1,2,3]
    f.apply_fields(gm,p);gm.model.mesh.field.setNumbers.assert_called_once_with(3,'FieldsList',[1,2]);gm.model.mesh.field.setAsBackgroundMesh.assert_called_once_with(3)

def test_moved_peak():
    c=baseline('T08');a=dict(status='PASS',metrics=dict(max_stress_location_mm=[10.,5.,10.],max_stress_mpa=100,max_disp_mm=.5,node_count=100,volume_element_count=50))
    b=dict(status='PASS',metrics=dict(max_stress_location_mm=[70.,5.,10.],max_stress_mpa=101,max_disp_mm=.5,node_count=200,volume_element_count=100))
    check,covered=f.local_check(a,b,f.plan_for(c,a,a,4));assert not covered and check['convergence_status']=='REVIEW'

def test_pending_and_resume(frozen_inputs,tmp_path):
    review={'T01','T02','T04','T07','T08','T09','T10'}
    def solver(c,folder,mesh,timeout):
        n=mesh['curvature_elements']
        if n==36 and c.template_id in {'T08','T09','T10'}:raise TimeoutError('test')
        v=fake_result(c,folder,mesh,timeout);v.max_stress_mpa=100 if n==16 else (105 if c.template_id in review else 101)
        if n==36:v.max_stress_mpa=106;v.node_count=300;v.volume_element_count=150
        v.factor_of_safety=250/v.max_stress_mpa;return v
    g.generate(*frozen_inputs,tmp_path,['medium','fine'],10,solver);r.generate(*frozen_inputs,tmp_path,10,solver)
    path=tmp_path/'cases/T08/extra_fine/result.json';old=path.read_bytes();calls=[]
    def targeted(c,folder,plan,timeout):
        calls.append(c.template_id);v=fake_result(c,folder,plan['base'],timeout);v.max_stress_mpa=106.;v.factor_of_safety=250/106.;v.node_count=250;v.volume_element_count=125;return v
    s=f.generate(*frozen_inputs,tmp_path,10,4,targeted)
    assert s['baseline_gate_pass'] and calls==['T08','T09','T10'] and path.read_bytes()==old and s['formal_labels_added']==0
    calls.clear();assert f.generate(*frozen_inputs,tmp_path,10,4,targeted)==s and not calls

def test_real_wrapper_installs_and_restores(monkeypatch,tmp_path):
    import sys
    from dataclasses import make_dataclass
    gm=MagicMock();gm.model.mesh.field.add.side_effect=[1,2,3]
    original=gm.model.mesh.generate
    monkeypatch.setitem(sys.modules,'gmsh',gm)
    case=baseline('T08');a={'metrics':{'max_stress_location_mm':[10.,5.,10.]}};p=f.plan_for(case,a,a,4)
    Result=make_dataclass('Result',[('mesh_rule',str)])
    def run(c,folder,mesh,timeout):
        gm.model.mesh.generate(3)
        assert mesh==p['base']
        return Result('test')
    monkeypatch.setattr(g,'real_solve',run)
    # The wrapper changes environment only for the CLI process. Restore it here.
    for name in ('OMP_NUM_THREADS','CCX_NPROC_STIFFNESS','CCX_NPROC_RESULTS','CCX_NPROC_EQUATION_SOLVER','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
        monkeypatch.setenv(name,'1')
    assert f.targeted_solve(case,tmp_path,p,1).mesh_rule=='test_targeted1'
    assert gm.model.mesh.generate is original
    original.assert_called_once_with(3)
