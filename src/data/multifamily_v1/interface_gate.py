"""Freeze CAD inputs, build a unified feature table, and split by anchor group.
No FEA calls. Legacy data are read-only; new FEA labels are not yet available.
"""
import argparse,csv,hashlib,json,math,random,tempfile
from collections import Counter,defaultdict
from pathlib import Path
from ..bracket_spec import BracketCaseInput,WORKING_STRUCTURAL_STEEL
from ..geometry import build_bracket_geometry
from .templates import FAMILIES,make_case,ranges,build_template,check_faces,opening_area_mm2
from .features import FEATURE_NAMES,extract_features
SCHEMA='role1_multifamily_1.0.0'
SEED=20261117
OLD='l_bracket_v0'
HOLDOUT=FAMILIES['T07'].key
OLD_KEYS=('thickness_mm','flange_length_mm','arm_width_mm','hole_diameter_mm','fillet_radius_mm')


def require(ok,message):
    if not ok: raise ValueError(message)


def read_csv(path):
    with Path(path).open(newline='',encoding='utf-8-sig') as f: return list(csv.DictReader(f))


def unique(rows):
    out={r['design_id']:r for r in rows}
    require(len(out)==len(rows),'Duplicate design IDs'); return out


def close(a,b): return math.isclose(float(a),float(b),rel_tol=1e-6,abs_tol=1e-6)


def legacy_case(r):
    require(r['source']=='calculix_2.23','Unexpected legacy source')
    require(close(r['reference_load_force_n'],500) and close(r['reference_youngs_modulus_mpa'],200000),'Legacy reference mismatch')
    nu=float(r['poissons_ratio']); require(.25<=nu<=.35,'Invalid Poisson ratio')
    return BracketCaseInput(design_id=r['design_id'],material=WORKING_STRUCTURAL_STEEL.model_copy(update={'poissons_ratio':nu}),load_force_n=500.,**{k:float(r[k]) for k in OLD_KEYS})


def new_case(r):
    key=r['design_id'].split('_')[0]
    require(key in FAMILIES and r['bracket_family']==FAMILIES[key].key,'Family/ID mismatch')
    return make_case(key,r['design_id'],{**json.loads(r['params_json']),'poissons_ratio':float(r['poissons_ratio'])},sampling_method=r['sampling_method'],anchor_id=r['anchor_id'],changed_parameter=r['changed_parameter'])


def check_plan(rows):
    require(len(rows)==810,'Expected 810 CAD gate rows'); unique(rows); cases=[]
    for key,family in FAMILIES.items():
        group=[new_case(r) for r in rows if r['bracket_family']==family.key]
        require(len(group)==81 and Counter(c.sampling_method for c in group)=={'baseline':1,'lhs':60,'paired':20},f'{key}: wrong sample counts')
        lhs={c.design_id:c for c in group if c.sampling_method=='lhs'}; pairs=defaultdict(list)
        vals=lambda c:{**c.params,'poissons_ratio':c.material.poissons_ratio}
        for p,(lo,hi) in ranges(key).items():
            require({math.floor(60*(vals(c)[p]-lo)/(hi-lo)) for c in lhs.values()}==set(range(60)),f'{key}: invalid LHS coverage {p}')
        for c in lhs.values(): require(c.anchor_id==c.design_id,f'{c.design_id}: wrong anchor')
        for c in group:
            if c.sampling_method=='paired':
                require(c.anchor_id in lhs,f'{c.design_id}: missing anchor')
                changes=[k for k,v in vals(c).items() if v!=vals(lhs[c.anchor_id])[k]]
                require(changes==[c.changed_parameter],f'{c.design_id}: changed fields {changes}')
                pairs[c.anchor_id].append(c)
        require(len(pairs)==10 and all(len(v)==2 for v in pairs.values()),f'{key}: wrong paired groups')
        selected=[c for c in group if c.sampling_method!='baseline']
        require(len({tuple(vals(c).items()) for c in selected})==80,f'{key}: duplicate parameters'); cases.extend(selected)
    require(len(cases)==800,'Expected 800 new cases'); return cases


def manifest_row(c,family,method,anchor,changed,params,source):
    return dict(design_id=c.design_id,bracket_family=family,sampling_method=method,anchor_id=anchor,changed_parameter=changed,params_json=json.dumps(params,sort_keys=True,separators=(',',':')),poissons_ratio=c.material.poissons_ratio,reference_load_force_n=c.load_force_n,reference_youngs_modulus_mpa=c.material.youngs_modulus_mpa,reference_yield_strength_mpa=c.material.yield_strength_mpa,reference_density_kg_m3=c.material.density_kg_m3,label_source=source)


def split_manifest(manifest):
    unique(manifest); result=[]
    for family in sorted({r['bracket_family'] for r in manifest}):
        group=sorted((r for r in manifest if r['bracket_family']==family),key=lambda r:r['design_id']); groups=defaultdict(list)
        for r in group: groups[r['anchor_id']].append(r['design_id'])
        rng=random.Random(f'{SEED}:{family}'); triples=sorted(k for k,v in groups.items() if len(v)==3); singles=sorted(k for k,v in groups.items() if len(v)==1)
        rng.shuffle(triples); rng.shuffle(singles)
        if family==OLD:
            require(len(group)==len(singles)==200,'Unexpected legacy groups'); val_ids=set(singles[:40])
        else:
            require(len(group)==80 and len(triples)==10 and len(singles)==50,'Unexpected new groups'); val_ids=set(triples[:2]+singles[:10])
        for r in group:
            split='heldout_test' if family==HOLDOUT else ('validation' if r['anchor_id'] in val_ids else 'train')
            result.append(dict(design_id=r['design_id'],bracket_family=family,anchor_id=r['anchor_id'],split=split))
    require(Counter(r['split'] for r in result)=={'train':736,'validation':184,'heldout_test':80},'Split counts mismatch'); return result


def write_csv(path,rows):
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(legacy_path,cad_path,output):
    legacy_path,cad_path,output=map(Path,(legacy_path,cad_path,output))
    require(not output.exists(),f'Output already exists: {output}; choose a fresh directory')
    legacy=read_csv(legacy_path); old_ids=unique(legacy); require(len(legacy)==200,'Expected 200 original solved designs')
    cad=read_csv(cad_path); cad_ids=unique(cad); cases=check_plan(cad)
    require(not set(old_ids)&set(cad_ids),'Design ID collision'); manifest=[]; features=[]
    for r in legacy:
        c=legacy_case(r); f=extract_features(c,build_bracket_geometry(c))
        require(close(f['cad_volume_mm3'],r['volume_mm3']),f'{c.design_id}: old volume changed')
        require(close(f['cad_volume_mm3']*7850e-9,r['reference_mass_kg']),f'{c.design_id}: old mass mismatch')
        require(all(math.isfinite(float(r[k])) and float(r[k])>0 for k in ('reference_max_stress_mpa','reference_max_disp_mm')),f'{c.design_id}: invalid old label')
        params={**{k:getattr(c,k) for k in OLD_KEYS},'vertical_height_mm':60.,'hole_center_x_mm':60.,'hole_center_z_mm':20.}
        manifest.append(manifest_row(c,OLD,'legacy_lhs',c.design_id,'',params,'formal_v0')); features.append(dict(design_id=c.design_id,**f))
    print('Legacy CAD reconstruction: PASS (200/200; no re-solves)',flush=True)
    for key,family in FAMILIES.items():
        for c in (c for c in cases if c.template_id==key):
            s=build_template(c); fixed,load=check_faces(c,s); f=extract_features(c,s); r=cad_ids[c.design_id]
            for a,b in ((f['cad_volume_mm3'],r['volume_mm3']),(fixed,r['fixed_face_area_mm2']),(load,r['load_face_area_mm2']),(opening_area_mm2(c),r['opening_area_mm2'])): require(close(a,b),f'{c.design_id}: CAD gate differs')
            manifest.append(manifest_row(c,family.key,c.sampling_method,c.anchor_id,c.changed_parameter,c.params,'pending_fea')); features.append(dict(design_id=c.design_id,**f))
        print(f'{key}: shared CAD features PASS (80/80)',flush=True)
    by_id=unique(features); require(set(by_id)==set(unique(manifest)),'Manifest/feature IDs mismatch')
    for r in manifest:
        if r['sampling_method']=='paired': require(by_id[r['design_id']]['cad_volume_mm3']<by_id[r['anchor_id']]['cad_volume_mm3'],f"{r['design_id']}: mass direction")
    splits=split_manifest(manifest); require(set(unique(splits))==set(by_id),'Split IDs mismatch')
    schema=dict(schema_version=SCHEMA,feature_columns=list(FEATURE_NAMES),label_columns=['reference_max_stress_mpa','reference_max_disp_mm'],deterministic_outputs=['volume_mm3','reference_mass_kg'],mass_formula='cad_volume_mm3 * density_kg_m3 * 1e-9',metadata_excluded_from_model=['design_id','bracket_family','anchor_id','sampling_method','changed_parameter','params_json','label_source','split','source','mesh_rule'],coordinate_frame='X fixed to free end, Y up, Z across width; 500 N in -Y',feature_notes='CAD only. Inertia is centroidal, unit-density volume integrals (mm^5).',limitations='Finite descriptors do not uniquely encode arbitrary shapes. Volume/mass are deterministic CAD values, not independent ML targets. No FEA diagnostics as features.',shared_contract_changed=False,heldout_family=HOLDOUT,split_seed=SEED)
    summary=dict(schema_version=SCHEMA,designs=len(manifest),legacy_labels=200,pending_fea=800,feature_count=len(FEATURE_NAMES),family_counts=dict(Counter(r['bracket_family'] for r in manifest)),split_counts=dict(Counter(r['split'] for r in splits)),inputs_sha256={'legacy_model_dataset':sha(legacy_path),'cad_checks':sha(cad_path)},status='CAD features ready; new FEA labels pending')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='interface_gate_',dir=output.parent) as td:
        stage=Path(td)/'result'; stage.mkdir()
        for name,rows in [('design_manifest.csv',manifest),('geometry_features.csv',features),('split_manifest.csv',splits)]: write_csv(stage/name,rows)
        for name,obj in [('feature_schema.json',schema),('interface_summary.json',summary)]: (stage/name).write_text(json.dumps(obj,indent=2)+'\n',encoding='utf-8')
        stage.rename(output)
    print('SPLIT CHECK: PASS (736 train / 184 validation / 80 heldout; anchor groups kept together)',flush=True)
    print('INTERFACE GATE: PASS (1000 feature rows; 200 legacy labels; 800 FEA pending)',flush=True)


def main():
    p=argparse.ArgumentParser(); p.add_argument('--legacy',required=True,type=Path); p.add_argument('--cad',required=True,type=Path); p.add_argument('--output',required=True,type=Path)
    a=p.parse_args(); generate(a.legacy,a.cad,a.output)

if __name__=='__main__': main()
