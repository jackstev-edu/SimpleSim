from collections import Counter
from copy import deepcopy
import pytest
from src.data.multifamily_v1 import finalize_v1 as f

# Synthetic tables using the already delivered schema and exact group counts.
COLS='bbox_x_mm bbox_y_mm bbox_z_mm plate_thickness_mm poissons_ratio cad_volume_mm3 surface_area_mm2 center_x_mm center_y_mm center_z_mm inertia_xx_mm5 inertia_yy_mm5 inertia_zz_mm5 inertia_xy_mm5 inertia_xz_mm5 inertia_yz_mm5 fixed_face_area_mm2 load_face_area_mm2 load_center_y_mm load_center_z_mm'.split()

@pytest.fixture
def tables():
    m,x,s,l,d,n=[],[],[],[],[],[]
    for i in range(11):
        family='l_bracket_v0' if i==0 else f'family_{i:02}'
        for j in range(200 if i==0 else 80):
            k=f'{i:02}_{j:03}'; anchor=f'{i:02}_{(j-60)//2:03}' if i and j>=60 else k
            method='legacy_lhs' if not i else 'paired' if j>=60 else 'lhs'
            split='heldout_test' if i==7 else ('validation' if (j<40 if not i else j<2 or 10<=j<20 or 60<=j<64) else 'train')
            vol=100 if method!='paired' else 90
            m.append(dict(design_id=k,bracket_family=family,anchor_id=anchor,sampling_method=method,changed_parameter='thickness_mm' if method=='paired' else '',label_source='formal_v0' if not i else 'pending_fea',poissons_ratio=.3,reference_load_force_n=500,reference_youngs_modulus_mpa=200000,reference_yield_strength_mpa=250,reference_density_kg_m3=7850))
            x.append(dict(design_id=k,**{c:1 for c in COLS}));x[-1].update(poissons_ratio=.3,cad_volume_mm3=vol)
            s.append(dict(design_id=k,bracket_family=family,anchor_id=anchor,split=split))
            z=dict(design_id=k,source='calculix_2.23',mesh_rule='sample',reference_max_stress_mpa=300,reference_max_disp_mm=.5,volume_mm3=vol,reference_mass_kg=vol*7850e-9)
            q=dict(reaction_relative_imbalance=1e-7,minimum_element_quality=.2,node_count=100,volume_element_count=50)
            if not i:
                l.append(dict(z,reference_load_force_n=500,reference_youngs_modulus_mpa=200000,poissons_ratio=.3));d.append(dict(q,design_id=k,youngs_modulus_mpa=200000,poissons_ratio=.3,yield_strength_mpa=250,density_kg_m3=7850,factor_of_safety=250/300,volume_mm3=vol))
            else:n.append(dict(z,**q,bracket_family=family,production_level='fine',reference_factor_of_safety=250/300))
    return m,x,s,l,d,n,dict(schema_version='role1_multifamily_1.0.0',feature_columns=COLS,label_columns=list(f.Y),heldout_family='family_07')

def test_full_merge(tables):
    rows,qa,pairs=f.merge(*tables)
    assert len(rows)==len(qa)==1000 and len(pairs)==200
    assert Counter(r['split'] for r in rows)==f.EXPECTED_SPLITS
    assert all(r['mass_change_pct']<0 for r in pairs)
    assert all(r['reference_factor_of_safety']<1 for r in rows) # valid failed designs retained

def test_duplicate(tables):
    tables[5][-1]=deepcopy(tables[5][0])
    with pytest.raises(ValueError,match='Duplicate'):f.merge(*tables)

def test_nonfinite(tables):
    tables[5][0]['reference_max_stress_mpa']='nan'
    with pytest.raises(ValueError,match='Nonfinite'):f.merge(*tables)

def test_quality(tables):
    tables[4][0]['minimum_element_quality']=.1
    with pytest.raises(ValueError,match='numerical QA'):f.merge(*tables)

def test_mass(tables):
    tables[5][0]['reference_mass_kg']=999
    with pytest.raises(ValueError,match='mass'):f.merge(*tables)

def test_holdout(tables):
    a=next(r for r in tables[2] if r['split']=='heldout_test' and r['design_id'].endswith('_050'))
    b=next(r for r in tables[2] if r['split']=='train' and r['design_id'].endswith('_050'))
    a['split'],b['split']=b['split'],a['split']
    with pytest.raises(ValueError,match='holdout'):f.merge(*tables)

def test_complete():
    keys=['T%02d'%i for i in range(1,11)]
    s=dict(complete=True,planned=800,successful_solver_cases=800,accepted_labels=800,pilot_review=[],unresolved_production_failures=0,pilot_passed=keys,family_accepted={k:80 for k in keys})
    f.validate_complete(s,keys);s['unresolved_production_failures']=1
    with pytest.raises(ValueError,match='failure'):f.validate_complete(s,keys)

def test_checkpoint_crosscheck(tmp_path):
    from types import SimpleNamespace
    import json
    k='T02_lhs_001'; row={'design_id':k}; mesh={'element_order':2}
    metrics=dict(max_stress_mpa=100,max_disp_mm=.2,volume_mm3=100,mass_kg=.000785,factor_of_safety=2.5,reaction_relative_imbalance=0,minimum_element_quality=.2,node_count=100,volume_element_count=50,source='calculix_2.23',mesh_rule='test',max_stress_location_mm=[1,2,3],reaction_force_n=[0,500,0])
    r=dict(status='PASS',template_id='T02',level='fine',config=dict(policy='test',row=row,level='fine',kind='production',mesh=mesh),metrics=metrics,wall_s=1.5,attempt='attempt_001')
    folder=tmp_path/'cases'/k/'production'/r['attempt'];folder.mkdir(parents=True);(folder/'attempt.json').write_text(json.dumps(r))
    b=SimpleNamespace(POLICY='test',checkpoint=lambda p,key:r,new_case=lambda x:x,g=SimpleNamespace(mesh_parameters=lambda c,l:mesh))
    z=dict(design_id=k,production_level='fine',source=metrics['source'],mesh_rule='test',reference_max_stress_mpa='100',reference_max_disp_mm='.2',volume_mm3='100',reference_mass_kg='.000785',reference_factor_of_safety='2.5',reaction_relative_imbalance='0',minimum_element_quality='.2',node_count='100',volume_element_count='50',wall_s='1.5',max_stress_location_mm='[1,2,3]')
    f.verify_checkpoints(b,tmp_path,{k:row},{'T02':'fine'},[z])
    z['reference_max_stress_mpa']='101'
    with pytest.raises(ValueError,match='CSV/checkpoint mismatch'):f.verify_checkpoints(b,tmp_path,{k:row},{'T02':'fine'},[z])
