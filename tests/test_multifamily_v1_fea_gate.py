"""Orchestration tests use stand-ins, not physical solver results."""
import csv
import json
from types import SimpleNamespace
import pytest
from src.data.multifamily_v1 import fea_gate as g
from src.data.multifamily_v1.templates import FAMILIES, baseline, build_template, check_faces, sample_plan

@pytest.fixture(scope='module')
def frozen_inputs(tmp_path_factory):
    pytest.importorskip('cadquery')
    root=tmp_path_factory.mktemp('baseline_input'); rows=[]
    for key,family in FAMILIES.items():
        for c in [baseline(key),*sample_plan(key)]:
            row=dict(design_id=c.design_id,bracket_family=family.key,sampling_method=c.sampling_method,anchor_id=c.anchor_id,changed_parameter=c.changed_parameter,params_json=json.dumps(c.params),poissons_ratio=c.material.poissons_ratio,volume_mm3='',fixed_face_area_mm2='',load_face_area_mm2='')
            if c.sampling_method=='baseline':
                solid=build_template(c); fixed,load=check_faces(c,solid)
                row.update(volume_mm3=solid.Volume(),fixed_face_area_mm2=fixed,load_face_area_mm2=load)
            rows.append(row)
    cad=root/'cad.csv'; interface=root/'interface.json'
    with cad.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    interface.write_text(json.dumps(dict(designs=1000,pending_fea=800,inputs_sha256={'cad_checks':g.sha(cad)})))
    return cad,interface

def fake_result(case,folder,mesh,timeout):
    volume=build_template(case).Volume(); fine=mesh['curvature_elements']==24; stress=101. if fine else 100.
    data=dict(design_id=case.design_id,source='calculix_2.23',mesh_rule='test-only',max_stress_mpa=stress,max_disp_mm=.501 if fine else .5,volume_mm3=volume,mass_kg=volume*7850e-9,factor_of_safety=250/stress,reaction_force_n=(0.,500.,0.),reaction_relative_imbalance=0.,minimum_element_quality=.25,node_count=200 if fine else 100,volume_element_count=100 if fine else 50,max_stress_location_mm=(10.,5.,5.),max_disp_location_mm=(80.,5.,10.),elapsed_geometry_s=.1,elapsed_mesh_s=.2,elapsed_solve_s=.3)
    for name in ('deck_path','frd_path','dat_path','log_path'):
        path=folder/name;path.write_text('pytest fixture only');data[name]=path
    return SimpleNamespace(**data)

@pytest.mark.parametrize('key',list(FAMILIES))
def test_mesh_levels(key):
    case=baseline(key); a=g.mesh_parameters(case,'medium'); b=g.mesh_parameters(case,'fine')
    assert a['element_order']==2 and a['algorithm_3d']==10 and a['high_order_optimize']==2
    assert 0<b['minimum_size_mm']<a['minimum_size_mm']<=a['maximum_size_mm']
    assert b['maximum_size_mm']<a['maximum_size_mm'] and b['curvature_elements']>a['curvature_elements']

def test_frozen_hash(frozen_inputs,tmp_path):
    cad,interface=frozen_inputs
    assert len(g.baseline_inputs(cad,interface))==10
    wrong=tmp_path/'bad.json';wrong.write_text(json.dumps(dict(designs=1000,pending_fea=800,inputs_sha256={'cad_checks':'bad'})))
    with pytest.raises(ValueError,match='differs'):g.baseline_inputs(cad,wrong)

def test_failure_resume_and_convergence(frozen_inputs,tmp_path):
    calls=[]
    def first(case,folder,mesh,timeout):
        calls.append(case.template_id)
        if case.template_id=='T03':raise RuntimeError('test injected failure')
        return fake_result(case,folder,mesh,timeout)
    a=g.generate(*frozen_inputs,tmp_path,['medium'],10,first)
    assert a['medium_pass']==9 and a['formal_labels_added']==0 and len(calls)==10
    calls.clear()
    def second(case,folder,mesh,timeout):
        calls.append(case.template_id);return fake_result(case,folder,mesh,timeout)
    b=g.generate(*frozen_inputs,tmp_path,['medium'],10,second)
    assert b['medium_pass']==10 and calls==['T03']
    calls.clear();c=g.generate(*frozen_inputs,tmp_path,['fine'],10,second)
    assert c['baseline_gate_pass'] and len(calls)==10 and c['formal_labels_added']==0
    calls.clear();d=g.generate(*frozen_inputs,tmp_path,['both'],10,second)
    assert d['baseline_gate_pass'] and not calls
    assert (tmp_path/'cases/T03/medium/attempt_001/attempt.json').is_file()
    assert (tmp_path/'cases/T03/medium/attempt_002/attempt.json').is_file()

def test_convergence_review():
    def rec(s,d,n):return dict(status='PASS',metrics=dict(max_stress_mpa=s,max_disp_mm=d,node_count=n,volume_element_count=n))
    assert g.convergence(rec(100,.5,10),rec(110,.5,20))['convergence_status']=='REVIEW'
    assert g.convergence(rec(100,.5,10),rec(100,.51,20))['convergence_status']=='REVIEW'
    assert g.convergence(rec(100,.5,10),rec(100,.5,10))['convergence_status']=='REVIEW'
    assert g.convergence(rec(100,.5,10),None)['convergence_status']=='PENDING_OR_FAILED'

def test_result_rejects_unbalanced_and_bad_mesh(frozen_inputs,tmp_path):
    case=baseline('T01');row=g.baseline_inputs(*frozen_inputs)['T01'];result=fake_result(case,tmp_path,g.mesh_parameters(case,'medium'),10)
    g.verify_result(case,result,row,tmp_path)
    result.reaction_force_n=(0.,400.,0.);result.reaction_relative_imbalance=.2
    with pytest.raises(ValueError,match='Reaction imbalance'):g.verify_result(case,result,row,tmp_path)
    result.reaction_force_n=(0.,500.,0.);result.reaction_relative_imbalance=0.;result.minimum_element_quality=.02
    with pytest.raises(ValueError,match='mesh quality'):g.verify_result(case,result,row,tmp_path)
