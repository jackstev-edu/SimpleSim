"""Read-only audit of completed Role 1 inputs; publish a separate 1000-row package.
No CAD regeneration, mesh generation or FEA. No overwrites of existing results.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, shutil, tempfile
from collections import Counter
from pathlib import Path

Y = ('reference_max_stress_mpa','reference_max_disp_mm')
EXPECTED_SPLITS = {'train':736,'validation':184,'heldout_test':80}


def require(ok, msg):
    if not ok: raise ValueError(msg)


def read(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def indexed(rows):
    out = {r['design_id']:r for r in rows}
    require(len(out)==len(rows) and '' not in out,'Duplicate or empty design ID')
    return out


def jread(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def num(v):
    x=float(v); require(math.isfinite(x),'Nonfinite value: '+str(v)); return x


def eq(a,b): return math.isclose(num(a),num(b),rel_tol=1e-6,abs_tol=1e-9)
def write(p,rows):
    require(bool(rows),'Empty output '+str(p))
    with p.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def validate_complete(s, families):
    require(s['complete'] is True and s['planned']==s['successful_solver_cases']==s['accepted_labels']==800,'Batch not complete')
    require(not s['pilot_review'] and s['unresolved_production_failures']==0 and set(s['pilot_passed'])==set(families),'Pending pilot or production failure')
    require(s['family_accepted']=={k:80 for k in families},'Unexpected family totals')


def merge(manifest, features, splits, legacy, diagnostics, labels, schema):
    m,f,s,l,d,n=map(indexed,(manifest,features,splits,legacy,diagnostics,labels))
    require(len(m)==len(f)==len(s)==1000 and m.keys()==f.keys()==s.keys(),'Manifest/feature/split mismatch')
    require(len(l)==len(d)==200 and l.keys()==d.keys() and len(n)==800 and not l.keys()&n.keys() and (l.keys()|n.keys())==m.keys(),'Legacy/new label IDs mismatch')
    require(schema['schema_version']=='role1_multifamily_1.0.0' and tuple(schema['label_columns'])==Y,'Wrong schema')
    cols=schema['feature_columns']; require(len(cols)==len(set(cols))==20 and set(cols).isdisjoint(Y),'Bad feature columns')
    require(Counter(x['split'] for x in s.values())==EXPECTED_SPLITS,'Split count mismatch')
    family_counts=Counter(x['bracket_family'] for x in m.values())
    require(len(family_counts)==11 and family_counts['l_bracket_v0']==200 and all(v==80 for k,v in family_counts.items() if k!='l_bracket_v0'),'Family count mismatch')
    require({k for k,v in m.items() if v['label_source']=='formal_v0'}==set(l),'Legacy source mismatch')
    require({k for k,v in m.items() if v['label_source']=='pending_fea'}==set(n),'New source mismatch')
    result=[]; qa=[]; pairs=[]
    for k,r in sorted(m.items()):
        a=s[k]; x=f[k]; old=k in l; z=l[k] if old else n[k]; q=d[k] if old else z
        require(r['bracket_family']==a['bracket_family'] and r['anchor_id']==a['anchor_id'] and a['split']==s[r['anchor_id']]['split'],k+': split metadata')
        require((a['split']=='heldout_test')==(r['bracket_family']==schema['heldout_family']),k+': holdout leak')
        require(z['source']=='calculix_2.23' and bool(z['mesh_rule']),k+': solver provenance')
        if old:
            require(r['bracket_family']=='l_bracket_v0' and all(eq(r[v],z[v]) for v in ('reference_load_force_n','reference_youngs_modulus_mpa','poissons_ratio')),k+': legacy input mismatch')
            require(all(eq(q[v],r[w]) for v,w in [('youngs_modulus_mpa','reference_youngs_modulus_mpa'),('poissons_ratio','poissons_ratio'),('yield_strength_mpa','reference_yield_strength_mpa'),('density_kg_m3','reference_density_kg_m3')]),k+': legacy material mismatch')
            require(eq(q['volume_mm3'],z['volume_mm3']),k+': legacy volume diagnostics')
        else: require(r['bracket_family']==z['bracket_family'],k+': new family mismatch')
        require(all(eq(r[c],v) for c,v in [('reference_load_force_n',500),('reference_youngs_modulus_mpa',200000),('reference_yield_strength_mpa',250),('reference_density_kg_m3',7850)]),k+': reference conditions')
        fx={c:num(x[c]) for c in cols}; require(eq(fx['poissons_ratio'],r['poissons_ratio']),k+': feature material mismatch')
        require(all(fx[c]>0 for c in ('cad_volume_mm3','fixed_face_area_mm2','load_face_area_mm2','plate_thickness_mm','bbox_x_mm','bbox_y_mm','bbox_z_mm')),k+': nonpositive geometry')
        stress,disp=(num(z[c]) for c in Y); vol=num(z['volume_mm3']); mass=num(z['reference_mass_kg']); sf=num(q['factor_of_safety'] if old else q['reference_factor_of_safety'])
        require(min(stress,disp,vol,mass,sf)>0,k+': nonpositive label')
        require(eq(vol,fx['cad_volume_mm3']) and eq(mass,vol*7850e-9) and eq(sf,250/stress),k+': volume/mass/safety factor mismatch')
        reaction=num(q['reaction_relative_imbalance']); quality=num(q['minimum_element_quality']); nodes=int(q['node_count']); elements=int(q['volume_element_count'])
        require(0<=reaction<=.01 and .1<quality<=1.000001 and nodes>0 and elements>0,k+': numerical QA')
        result.append(dict(design_id=k,bracket_family=r['bracket_family'],split=a['split'],anchor_id=r['anchor_id'],sampling_method=r['sampling_method'],changed_parameter=r['changed_parameter'],label_source='formal_v0' if old else 'multifamily_v1',**fx,reference_max_stress_mpa=stress,reference_max_disp_mm=disp,volume_mm3=vol,reference_mass_kg=mass,reference_factor_of_safety=sf))
        qa.append(dict(design_id=k,source=z['source'],mesh_rule=z['mesh_rule'],production_level='legacy' if old else z['production_level'],reaction_relative_imbalance=reaction,minimum_element_quality=quality,node_count=nodes,volume_element_count=elements))
    by=indexed(result)
    for k,r in by.items():
        if r['sampling_method']!='paired':continue
        a=by[r['anchor_id']]; require(a['bracket_family']==r['bracket_family'] and a['sampling_method']=='lhs' and a['split']==r['split'],k+': invalid pair')
        require(r['reference_mass_kg']<a['reference_mass_kg'],k+': mass direction')
        pairs.append(dict(design_id=k,anchor_id=a['design_id'],split=r['split'],bracket_family=r['bracket_family'],changed_parameter=r['changed_parameter'],mass_change_pct=100*(r['reference_mass_kg']/a['reference_mass_kg']-1),stress_change_pct=100*(r[Y[0]]/a[Y[0]]-1),displacement_change_pct=100*(r[Y[1]]/a[Y[1]]-1)))
    require(len(pairs)==200,'Expected 200 mass-reduction pairs')
    return result,qa,pairs


def verify_checkpoints(b,root,by,levels,labels):
    records=indexed(labels); require(records.keys()==by.keys(),'New case IDs differ from frozen plan')
    fields={'reference_max_stress_mpa':'max_stress_mpa','reference_max_disp_mm':'max_disp_mm','volume_mm3':'volume_mm3','reference_mass_kg':'mass_kg','reference_factor_of_safety':'factor_of_safety','reaction_relative_imbalance':'reaction_relative_imbalance','minimum_element_quality':'minimum_element_quality','node_count':'node_count','volume_element_count':'volume_element_count'}
    for k,row in by.items():
        r=b.checkpoint(root,k); z=records[k]; key=k.split('_')[0]; level=levels[key]
        require(r is not None and r['status']=='PASS' and r['template_id']==key and r['level']==level and z['production_level']==level,k+': checkpoint not accepted')
        require(r['config']==dict(policy=b.POLICY,row=row,level=level,kind='production',mesh=b.g.mesh_parameters(b.new_case(row),level)),k+': production config mismatch')
        m=r['metrics']; require(all(num(z[c])==num(m[v]) for c,v in fields.items()) and z['source']==m['source'] and z['mesh_rule']==m['mesh_rule'],k+': CSV/checkpoint mismatch')
        require(num(z['wall_s'])==num(r['wall_s']) and json.loads(z['max_stress_location_mm'])==m['max_stress_location_mm'],k+': diagnostic mismatch')
        require(len(m['reaction_force_n'])==3 and eq(math.sqrt(sum((num(a)+bb)**2 for a,bb in zip(m['reaction_force_n'],(0,-500,0))))/500,m['reaction_relative_imbalance']),k+': reaction vector mismatch')
        folder=root/'cases'/k/'production'/r['attempt']; require(jread(folder/'attempt.json')==r,k+': attempt checkpoint mismatch')
        # b.checkpoint already checks the retained solver log exists and is nonempty.


def run(data_root,legacy_dir,output):
    from . import batch_v1 as b
    from .features import FEATURE_NAMES
    data_root,legacy_dir,output=map(Path,(data_root,legacy_dir,output)); root=data_root/'batch'; ir=data_root/'interface_gate'; cad=data_root/'cad_gate'/'cad_checks.csv'; interface=ir/'interface_summary.json'; gate=data_root/'fea_gate'
    require(not output.exists(),'Output exists; choose a new directory')
    require(not (root/'batch.lock').exists(),'Batch running; stop before audit')
    summary=jread(root/'batch_summary.json'); validate_complete(summary,b.FAMILIES)
    by,levels,config=b.inputs(cad,interface,gate); risk,typical=b.select_pilot(by); config.update(risk=risk,typical=typical)
    require(jread(root/'batch_config.json')==config,'Frozen batch config mismatch')
    pilot,passed=b.pilot_status(root,risk,typical); require(passed==set(b.FAMILIES),'Pilot not complete')
    for r in pilot:
        require(0<=num(r['stress_change_pct'])<=3 and 0<=num(r['displacement_change_pct'])<=1,r['template_id']+': pilot tolerance')
        cmp=jread(root/'cases'/r['risk_id']/'comparison.json'); require(cmp['upper_status']=='PASS',r['template_id']+': upper solve')
        if cmp['approach'].startswith('targeted'):require(cmp['peak_in_refined_core'] is True,r['template_id']+': hotspot not covered')
    require(not read(root/'failures.csv'),'Failure CSV not empty')
    lp=legacy_dir/'model_dataset.csv'; dp=legacy_dir/'run_diagnostics.csv'; qc=legacy_dir/'quality_checks.csv'
    require(sha(lp)==jread(interface)['inputs_sha256']['legacy_model_dataset'],'Legacy data hash changed')
    old_checks=read(qc); require(bool(old_checks) and all(r['passed'].lower()=='true' for r in old_checks),'Legacy QA not passed')
    schema=jread(ir/'feature_schema.json'); require(tuple(schema['feature_columns'])==FEATURE_NAMES,'Unexpected feature schema')
    labs=read(root/'new_fea_labels.csv'); verify_checkpoints(b,root,by,levels,labs)
    print('CHECKPOINT AUDIT: PASS (800/800; retained logs and original inputs)',flush=True)
    result,qa,pairs=merge(read(ir/'design_manifest.csv'),read(ir/'geometry_features.csv'),read(ir/'split_manifest.csv'),read(lp),read(dp),labs,schema)
    print('MERGE AUDIT: PASS (200 legacy + 800 new; 1000 unique IDs; 200 pairs)',flush=True)
    audit=dict(status='PASS',rows=1000,legacy_rows=200,new_rows=800,feature_count=20,split_counts=dict(Counter(r['split'] for r in result)),family_counts=dict(Counter(r['bracket_family'] for r in result)),pair_count=len(pairs),max_reaction_imbalance=max(r['reaction_relative_imbalance'] for r in qa),min_element_quality=min(r['minimum_element_quality'] for r in qa),input_sha256={p.name:sha(p) for p in (lp,dp,qc,ir/'design_manifest.csv',ir/'geometry_features.csv',ir/'split_manifest.csv',ir/'feature_schema.json',root/'new_fea_labels.csv',root/'batch_config.json',root/'batch_summary.json',root/'pilot_validation.csv')},limitations=['Linear elastic reference results; stresses above yield are not proof of elastic physical behavior.','Mesh comparisons are baseline/representative checks, not individual convergence proofs for all 800 cases.','Holdout is one entire family (T07), not proof of generalization to arbitrary geometry.'])
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent,prefix='finalize_') as tmp:
        stage=Path(tmp)/'package';stage.mkdir()
        write(stage/'model_dataset_1000.csv',result);write(stage/'numerical_diagnostics.csv',qa);write(stage/'mass_direction_pairs.csv',pairs)
        for split in EXPECTED_SPLITS:
            # Model tables contain only design ID, the frozen features, and the two labels.
            write(stage/(split+'.csv'),[{k:r[k] for k in ('design_id',*schema['feature_columns'],*Y)} for r in result if r['split']==split])
        for p in ('design_manifest.csv','split_manifest.csv','feature_schema.json'):shutil.copy2(ir/p,stage/p)
        (stage/'audit_summary.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
        (stage/'README.md').write_text('# Role 1 dataset — 1000 designs\n\nUse train.csv to fit; validation.csv to tune; heldout_test.csv only for final T07 evaluation. Exclude design_id from ML features. model_dataset_1000.csv is for archival/analysis, NOT training without filtering. Geometry, mass and safety factor are not extra independent targets. See feature_schema.json for the exact 20 inputs and 2 labels.\n\nAll references: 500 N in -Y, fixed back X=0, load on horizontal free-end face. E=200000 MPa, yield=250 MPa, density=7850 kg/m^3, variable Poisson ratio. Numerical QA does not establish physical design feasibility. Source manifest retains original pending_fea plan status intentionally; completed label_source is in model_dataset_1000.csv. Keep original checkpoints and logs until Handoff is complete.\n',encoding='utf-8')
        require(not (root/'batch.lock').exists() and not output.exists(),'Batch started or output exists; abort publish')
        stage.rename(output)
    print('FINAL DATASET AUDIT: PASS (736 train / 184 validation / 80 heldout)',flush=True)
    print('Output:',output,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('data-root','legacy-dir','output'):p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args();run(a.data_root,a.legacy_dir,a.output)

if __name__=='__main__':main()
