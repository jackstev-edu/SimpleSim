"""Stage the audited dataset and compact evidence, without altering original data or Git."""
from pathlib import Path
import argparse, csv, json, shutil, tempfile, hashlib
from . import finalize_v1 as f

FINAL = ('audit_summary.json','design_manifest.csv','feature_schema.json','heldout_test.csv','mass_direction_pairs.csv','model_dataset_1000.csv','numerical_diagnostics.csv','README.md','split_manifest.csv','train.csv','validation.csv')
EVIDENCE = {
    'cad_gate': ('cad_checks.csv',),
    'interface_gate': ('geometry_features.csv','interface_summary.json'),
    'fea_gate': ('gate_config.json','baseline_results.csv','mesh_convergence.csv','fea_gate_summary.json','fast_mesh_convergence.csv','fast_gate_summary.json'),
    'batch': ('batch_config.json','batch_summary.json','new_fea_labels.csv','pilot_validation.csv','failures.csv','performance_history.json'),
}

# Step 5B was interrupted before writing these end-of-run reports.
# Step 5C's completed reports are required instead. Preserve the old reports if available.
OPTIONAL_EVIDENCE = {'fea_gate': ('refined_mesh_convergence.csv','refined_gate_summary.json')}

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def check(ok,msg):
    if not ok: raise ValueError(msg)

def stage(data_root, repo_root, audit_runner=f.run):
    root=Path(data_root).resolve(); repo=Path(repo_root).resolve()
    src=root/'final_dataset'; dst=repo/'data/processed/formal_multifamily_v1'
    check((repo/'src/data/multifamily_v1/finalize_v1.py').is_file(),'Run from the SimpleSim repository')
    check(not (root/'batch/batch.lock').exists(),'Batch active')
    check(not dst.exists(),'Destination already exists; refusing to overwrite')
    check(all((src/n).is_file() for n in FINAL),'Final dataset missing files')
    # Perform the same final audit on the real checkpoints; no numerical solves.
    with tempfile.TemporaryDirectory(prefix='publish_check_',dir=root) as temp:
        audited=Path(temp)/'recheck'
        audit_runner(root, repo/'data/processed/formal_v0', audited)
        for n in FINAL:
            check(digest(src/n)==digest(audited/n),'Final output differs on re-audit: '+n)
    for folder,names in EVIDENCE.items():
        for n in names:
            check((root/folder/n).is_file(), 'Required evidence missing: '+str(root/folder/n))
    dst.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='role1_stage_',dir=dst.parent) as temp:
        p=Path(temp)/'package'; p.mkdir()
        for n in FINAL: shutil.copy2(src/n,p/n)
        ev=p/'evidence'
        for folder,names in EVIDENCE.items():
            d=ev/folder; d.mkdir(parents=True)
            for n in names: shutil.copy2(root/folder/n,d/n)
        omitted=[]
        for folder,names in OPTIONAL_EVIDENCE.items():
            for n in names:
                source=root/folder/n
                if source.is_file(): shutil.copy2(source,ev/folder/n)
                else: omitted.append(f'{folder}/{n}')
        if omitted:
            (ev/'optional_evidence_omissions.json').write_text(json.dumps({'omitted':omitted,'reason':'Superseded Step 5B reports not present; completed Step 5C reports are included.'},indent=2)+'\n',encoding='utf-8')
            print('Optional superseded reports absent:', ', '.join(omitted))
        with (root/'batch/pilot_validation.csv').open(newline='',encoding='utf-8-sig') as file:
            pilot=list(csv.DictReader(file))
        check(len(pilot)==10 and all(x['pilot_status']=='PASS' for x in pilot),'Pilot table incomplete')
        for row in pilot:
            k=row['risk_id']; check(Path(k).name==k and k.startswith(row['template_id']+'_'),'Invalid risk ID')
            d=ev/'pilot_comparisons'/k; d.mkdir(parents=True)
            case=root/'batch/cases'/k
            shutil.copy2(case/'comparison.json',d/'comparison.json')
            for n in ('comparison_before_t02_global_reference.json','t02_global_reference_check.json'):
                if (case/n).is_file(): shutil.copy2(case/n,d/n)
        cad=p/'baseline_step'; cad.mkdir()
        for i in range(1,11):
            n=f'T{i:02d}_baseline.step'; shutil.copy2(root/'cad_gate'/n,cad/n)
        hashes={a.relative_to(p).as_posix():digest(a) for a in sorted(p.rglob('*')) if a.is_file()}
        (p/'package_sha256.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf-8')
        check(not dst.exists() and not (root/'batch/batch.lock').exists(),'Destination changed or batch running')
        p.rename(dst)
    print(f'PUBLISH STAGING: PASS ({len(hashes)+1} files; {sum(x.stat().st_size for x in dst.rglob("*") if x.is_file())} bytes)')
    print('Output:',dst)
    return dst

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-root',type=Path,required=True);p.add_argument('--repo-root',type=Path,default=Path.cwd());a=p.parse_args();stage(a.data_root,a.repo_root)
