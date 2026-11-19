from collections import Counter
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from src.data.multifamily_v1 import batch_v1 as b, fea_gate as g
from src.data.multifamily_v1.templates import FAMILIES, sample_plan, build_template, check_faces
from tests.test_multifamily_v1_fea_gate import fake_result

@pytest.fixture(scope='module')
def plan():
    by={}
    for key,family in FAMILIES.items():
        for c in sample_plan(key):
            by[c.design_id]=dict(design_id=c.design_id,bracket_family=family.key,sampling_method=c.sampling_method,anchor_id=c.anchor_id,changed_parameter=c.changed_parameter,params_json=json.dumps(c.params),poissons_ratio=c.material.poissons_ratio,volume_mm3='',fixed_face_area_mm2='',load_face_area_mm2='')
    return by

def prepare(row):
    r=dict(row);c=b.new_case(r);s=build_template(c);f,l=check_faces(c,s)
    r.update(volume_mm3=s.Volume(),fixed_face_area_mm2=f,load_face_area_mm2=l);return r

def sim(c,folder,mesh,timeout,threads):
    m=mesh.get('base',mesh); r=fake_result(c,folder,m,timeout)
    if 'base' in mesh:r.node_count=300;r.volume_element_count=150;r.max_stress_mpa=101.5;r.factor_of_safety=250/101.5
    r.mesh_rule='fixture';return r

def test_pilot_ids(plan):
    a,t=b.select_pilot(plan);assert len(set(a.values())|set(t.values()))==20
    assert all(x in plan for x in (*a.values(),*t.values()))
    assert b.select_pilot(dict(reversed(list(plan.items()))))==(a,t)

def test_cached_qa_and_cleanup(plan,tmp_path):
    row=prepare(plan['T01_lhs_000']);r=b.production(row,'fine',tmp_path,10,2,sim)
    assert r['status']=='PASS';folder=tmp_path/'cases'/r['design_id']/'production'/r['attempt']
    assert sorted(p.name for p in folder.iterdir())==['attempt.json','log_path']
    def stop(*a):raise AssertionError('must resume')
    assert b.production(row,'fine',tmp_path,10,4,stop)==r
    with pytest.raises(ValueError,match='Checkpoint'):b.production(row,'medium',tmp_path,10,2,stop)
    check=b.compare(row,'fine',r,tmp_path,10,2,sim);assert check['convergence_status']=='PASS' and check['approach']=='targeted_fine'

def test_bad_reaction_not_accepted(plan,tmp_path):
    row=prepare(plan['T03_lhs_000'])
    def bad(c,folder,m,t,n):
        r=sim(c,folder,m,t,n);r.reaction_force_n=(0,100,0);return r
    result=b.production(row,'medium',tmp_path,10,2,bad)
    assert result['status']=='FAIL' and 'Reaction imbalance' in result['error']
    assert b.production(row,'medium',tmp_path,10,2,sim)['status']=='PASS'

def test_failed_pilot_blocks_labels(plan,tmp_path):
    risk,typical=b.select_pilot(plan); key='T01'
    for k in (risk[key],typical[key]):b.production(prepare(plan[k]),'fine',tmp_path,10,2,sim)
    levels={k:'fine' for k in FAMILIES};s=b.export(tmp_path,plan,levels,risk,typical)
    assert s['accepted_labels']==0 and s['successful_solver_cases']==2
    b.g.atomic_json(tmp_path/'cases'/risk[key]/'comparison.json',dict(convergence_status='PASS'))
    s=b.export(tmp_path,plan,levels,risk,typical);assert s['accepted_labels']==2 and s['pilot_passed']==[key]

def test_dispatch_unique_tasks(plan,tmp_path,monkeypatch):
    risk,typical=b.select_pilot(plan);rows=[prepare(plan[risk['T01']]),prepare(plan[typical['T01']])];calls=[]
    def fake_job(p):
        row,level,root,timeout,threads,verify=p;calls.append(row['design_id']);r=b.production(row,level,root,timeout,threads,sim)
        return dict(record=r)
    monkeypatch.setattr(b,'job',fake_job);monkeypatch.setattr(b,'available_gb',lambda:30.)
    levels={k:'fine' for k in FAMILIES};tasks=[(r,'fine',str(tmp_path),10,2,False) for r in rows]
    res=b.dispatch(tasks,2,1,8,tmp_path,plan,levels,risk,typical,executor=ThreadPoolExecutor)
    assert res['tasks']==2 and len(set(calls))==2

def test_available_memory():
    assert b.available_gb() is None or b.available_gb()>0

# Use an explicitly spawned process (Windows uses spawn, not fork). No FEA in test.
def spawned_echo(payload):
    import os
    return payload, os.getpid()

def test_spawn_process_serialization(plan):
    import os, multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    row=plan['T08_lhs_000']; payload=(row,'fine','output',1800,2,True)
    with ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn')) as pool:
        value,pid=pool.submit(spawned_echo,payload).result(timeout=30)
    assert value==payload and pid!=os.getpid()

def test_frozen_manifest_inputs(plan,tmp_path):
    from src.data.multifamily_v1 import interface_gate as i
    from src.data.multifamily_v1.templates import baseline
    rows=list(plan.values())
    for key,f in FAMILIES.items():
        c=baseline(key);rows.append(dict(design_id=c.design_id,bracket_family=f.key,sampling_method='baseline',anchor_id='',changed_parameter='',params_json=json.dumps(c.params),poissons_ratio=c.material.poissons_ratio,volume_mm3='100',fixed_face_area_mm2='20',load_face_area_mm2='10'))
    rows=[dict(r,volume_mm3='100',fixed_face_area_mm2='20',load_face_area_mm2='10') for r in rows]
    cad=tmp_path/'cad_checks.csv';i.write_csv(cad,rows)
    ir=tmp_path/'interface';ir.mkdir();ip=ir/'interface_summary.json';g.atomic_json(ip,dict(designs=1000,pending_fea=800,inputs_sha256={'cad_checks':g.sha(cad)}))
    manifest=[i.manifest_row(b.new_case(r),r['bracket_family'],r['sampling_method'],r['anchor_id'],r['changed_parameter'],json.loads(r['params_json']),'pending_fea') for r in rows if r['sampling_method']!='baseline']
    manifest.extend(dict(manifest[0],design_id=f'legacy_{n}',anchor_id=f'legacy_{n}',bracket_family=i.OLD,label_source='formal_v0') for n in range(200))
    i.write_csv(ir/'design_manifest.csv',manifest)
    i.write_csv(ir/'geometry_features.csv',[dict(design_id=r['design_id'],cad_volume_mm3=100,fixed_face_area_mm2=20,load_face_area_mm2=10) for r in manifest])
    i.write_csv(ir/'split_manifest.csv',i.split_manifest(manifest));g.atomic_json(ir/'feature_schema.json',{})
    gate=tmp_path/'fea';gate.mkdir();g.atomic_json(gate/'gate_config.json',dict(policy=g.POLICY,cad_sha256=g.sha(cad),interface_sha256=g.sha(ip)))
    g.atomic_json(gate/'fast_gate_summary.json',dict(baseline_gate_pass=True,convergence_pass=10,provisional_production_levels={k:'fine' for k in FAMILIES},threshold_stress_pct=3.,threshold_displacement_pct=1.))
    by,levels,config=b.inputs(cad,ip,gate);assert len(by)==800 and len(levels)==10 and len(config['input_hashes'])==7
    manifest[0]['poissons_ratio']=0.499;i.write_csv(ir/'design_manifest.csv',manifest)
    with pytest.raises(ValueError,match='Frozen parameters differ'):b.inputs(cad,ip,gate)
