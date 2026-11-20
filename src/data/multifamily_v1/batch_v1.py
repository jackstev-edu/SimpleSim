"""Role 1: staged parallel real FEA, using the frozen 800-case design plan."""
from __future__ import annotations
import argparse, json, math, multiprocessing, os, time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from unittest.mock import patch
from . import fea_gate as g, fast_gate as fast
from .interface_gate import read_csv, new_case, sha, close, require, unique
from .templates import FAMILIES, build_template, check_faces

POLICY = 'multifamily_batch_v1.0'
HEAVY = {'T08','T09'}
THREAD_KEYS = ('OMP_NUM_THREADS','CCX_NPROC_STIFFNESS','CCX_NPROC_RESULTS','CCX_NPROC_EQUATION_SOLVER')

def threads_set(n):
    for k in THREAD_KEYS: os.environ[k]=str(n)
    for k in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'): os.environ[k]='1'

def read_json(p): return json.loads(Path(p).read_text(encoding='utf-8'))

def available_gb():
    if os.name=='nt':
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)] + [(k,ctypes.c_ulonglong) for k in ('total','avail','total_page','avail_page','total_virtual','avail_virtual','avail_extended')]
        m=Memory(); m.length=ctypes.sizeof(m)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)): raise OSError('GlobalMemoryStatusEx failed')
        return m.avail/2**30
    p=Path('/proc/meminfo')
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith('MemAvailable:'): return int(line.split()[1])/2**20
    return None

def inputs(cad,interface,gate):
    cad,interface,gate=map(Path,(cad,interface,gate))
    g.baseline_inputs(cad,interface)
    expected=dict(policy=g.POLICY,cad_sha256=sha(cad),interface_sha256=sha(interface))
    require(read_json(gate/'gate_config.json')==expected,'Baseline gate input mismatch')
    summary=read_json(gate/'fast_gate_summary.json')
    levels=summary['provisional_production_levels']
    require(summary['baseline_gate_pass'] and summary['convergence_pass']==10 and set(levels)==set(FAMILIES) and all(v in ('medium','fine') for v in levels.values()),'Complete Step 5C first')
    require(summary['threshold_stress_pct']==g.STRESS_LIMIT_PCT and summary['threshold_displacement_pct']==g.DISP_LIMIT_PCT,'Gate tolerance mismatch')
    rows=[r for r in read_csv(cad) if r['sampling_method']!='baseline']; by=unique(rows)
    manifest=unique(read_csv(interface.parent/'design_manifest.csv'))
    features=unique(read_csv(interface.parent/'geometry_features.csv'))
    splits=unique(read_csv(interface.parent/'split_manifest.csv'))
    require(len(by)==800 and len(manifest)==len(features)==len(splits)==1000 and set(manifest)==set(features)==set(splits),'Unexpected manifest counts')
    require(set(by)=={k for k,v in manifest.items() if v['label_source']=='pending_fea'},'Formal IDs mismatch')
    for k,r in by.items():
        m=manifest[k]; c=new_case(r)
        require(json.loads(m['params_json'])==json.loads(r['params_json']) and close(m['poissons_ratio'],r['poissons_ratio']) and m['bracket_family']==r['bracket_family'] and m['anchor_id']==r['anchor_id'] and m['sampling_method']==r['sampling_method'] and m['changed_parameter']==r['changed_parameter'],'Frozen parameters differ: '+k)
        require(close(features[k]['cad_volume_mm3'],r['volume_mm3']) and close(features[k]['fixed_face_area_mm2'],r['fixed_face_area_mm2']) and close(features[k]['load_face_area_mm2'],r['load_face_area_mm2']),'Frozen CAD feature differs: '+k)
        require(splits[k]['bracket_family']==m['bracket_family'] and splits[k]['anchor_id']==m['anchor_id'],'Split mismatch')
        require(all(close(m[f],v) for f,v in [('reference_load_force_n',c.load_force_n),('reference_youngs_modulus_mpa',c.material.youngs_modulus_mpa),('reference_yield_strength_mpa',c.material.yield_strength_mpa),('reference_density_kg_m3',c.material.density_kg_m3)]),'Reference mismatch')
    require(Counter(v['split'] for v in splits.values())=={'train':736,'validation':184,'heldout_test':80},'Split counts changed')
    require(all((v['split']=='heldout_test')==(v['bracket_family']==FAMILIES['T07'].key) for v in splits.values()),'Holdout changed')
    require(all(splits[k]['split']==splits[v['anchor_id']]['split'] for k,v in manifest.items()),'Pair split changed')
    config=dict(policy=POLICY,levels=levels,input_hashes={str(p.name):sha(p) for p in (cad,interface,gate/'fast_gate_summary.json',interface.parent/'design_manifest.csv',interface.parent/'geometry_features.csv',interface.parent/'split_manifest.csv',interface.parent/'feature_schema.json')})
    return by,levels,config

def select_pilot(by):
    """A deliberately thin/long case and a near-baseline LHS case per family.
    The risk selection is CAD-only and independent of FEA/ML results.
    """
    risk={}; typical={}
    for key in FAMILIES:
        group=[r for r in by.values() if r['design_id'].startswith(key+'_')]
        require(len(group)==80,key+' missing cases')
        cases=[new_case(r) for r in group]
        a=max(cases,key=lambda c:(c.flange_length_mm/c.thickness_mm, c.design_id))
        from .templates import baseline,ranges
        b=baseline(key); base={**b.params,'poissons_ratio':b.material.poissons_ratio}
        def distance(c):
            p={**c.params,'poissons_ratio':c.material.poissons_ratio}
            return sum(((p[k]-base[k])/(hi-lo))**2 for k,(lo,hi) in ranges(key).items())
        t=min((c for c in cases if c.sampling_method=='lhs' and c.anchor_id!=a.anchor_id),key=lambda c:(distance(c),c.design_id))
        risk[key]=a.design_id;typical[key]=t.design_id
    return risk,typical

def solve_base(case,folder,mesh,timeout,threads):
    import gmsh
    threads_set(threads)
    original=gmsh.model.mesh.generate
    def generate(dim=3):
        gmsh.option.setNumber('General.NumThreads',threads)
        return original(dim)
    with patch.object(gmsh.model.mesh,'generate',generate):
        return g.real_solve(case,folder,mesh,timeout)

def save_solve(row,level,kind,mesh,root,timeout,threads,solve=None):
    """One unique case/mesh checkpoint. Only validated results are reusable."""
    root=Path(root); case=new_case(row); target=root/'cases'/case.design_id/kind
    path=target/'result.json'; target.mkdir(parents=True,exist_ok=True)
    # Thread count affects performance, not the intended mesh/physical definition.
    physical_mesh={k:v for k,v in mesh.items() if k!='threads'}
    config=dict(policy=POLICY,row=row,level=level,kind=kind,mesh=physical_mesh)
    if path.exists():
        old=read_json(path); require(old['config']==config,'Checkpoint input/mesh mismatch: '+case.design_id)
        if old['status']=='PASS':
            require((target/old['attempt']/old['metrics']['artifacts']['log_path']).is_file(),'Missing retained solver log')
            return old
    ix=1
    while (target/f'attempt_{ix:03d}').exists():ix+=1
    folder=target/f'attempt_{ix:03d}';folder.mkdir()
    record=dict(config=config,attempt=folder.name,design_id=case.design_id,template_id=case.template_id,level=level,status='FAIL',threads=threads)
    start=time.perf_counter()
    try:
        s=build_template(case);fix,load=check_faces(case,s)
        require(close(s.Volume(),row['volume_mm3']) and close(fix,row['fixed_face_area_mm2']) and close(load,row['load_face_area_mm2']),'CAD differs from frozen input')
        if solve is not None: result=solve(case,folder,mesh,timeout,threads)
        elif kind=='targeted': result=fast.targeted_solve(case,folder,mesh,timeout)
        else: result=solve_base(case,folder,mesh,timeout,threads)
        m=g.verify_result(case,result,row,folder)
        m['artifact_sizes_bytes']={k:(folder/v).stat().st_size for k,v in m['artifacts'].items()}
        record.update(status='PASS',metrics=m)
    except Exception as e:
        record['error']=f'{type(e).__name__}: {e}'
    record['wall_s']=time.perf_counter()-start
    # Checkpoint is written before cleanup; failure artifacts retained for diagnosis.
    g.atomic_json(folder/'attempt.json',record);g.atomic_json(path,record)
    if record['status']=='PASS':
        keep={'attempt.json',record['metrics']['artifacts']['log_path']}
        for p in folder.iterdir():
            if p.is_file() and p.name not in keep:
                try:p.unlink()
                except OSError:pass
    return record

def production(row,level,root,timeout,threads,solve=None):
    c=new_case(row);return save_solve(row,level,'production',g.mesh_parameters(c,level),root,timeout,threads,solve)

def compare(row,level,lower,root,timeout,threads,solve=None):
    if lower['status']!='PASS':return dict(convergence_status='PENDING_OR_FAILED',stress_change_pct='',displacement_change_pct='',approach='not_run')
    c=new_case(row)
    if level=='medium':
        upper=save_solve(row,'fine','upper_global',g.mesh_parameters(c,'fine'),root,timeout,threads,solve)
        return dict(**g.convergence(lower,upper),approach='global_medium_fine',upper_status=upper['status'],upper_wall_s=upper['wall_s'],upper_error=upper.get('error',''))
    # Same local strategy as Step 5C. Production hotspot + its symmetry counterpart, if applicable.
    plan=fast.plan_for(c,lower,lower,threads)
    upper=save_solve(row,'targeted_fine_v1','targeted',plan,root,timeout,threads,solve)
    check,covered=fast.local_check(lower,upper,plan)
    return dict(**check,approach='targeted_fine',peak_in_refined_core=covered,upper_status=upper['status'],upper_wall_s=upper['wall_s'],upper_error=upper.get('error',''))

def job(payload):
    row,level,root,timeout,threads,verify=payload
    lo=production(row,level,root,timeout,threads)
    result=dict(design_id=row['design_id'],template_id=row['design_id'].split('_')[0],record=lo)
    if verify:
        print(f"{lo['design_id']}: production {lo['status']} ({lo['wall_s']:.1f}s); representative comparison next",flush=True)
        result['comparison']=compare(row,level,lo,root,timeout,threads)
        g.atomic_json(Path(root)/'cases'/row['design_id']/'comparison.json',result['comparison'])
    return result

def checkpoint(root,k):
    p=root/'cases'/k/'production'/'result.json'
    if not p.is_file():return None
    record=read_json(p)
    require(record['design_id']==k and record['config']['policy']==POLICY and record['config']['kind']=='production','Invalid production checkpoint: '+k)
    if record['status']=='PASS':
        log=p.parent/record['attempt']/record['metrics']['artifacts']['log_path']
        require(log.is_file() and log.stat().st_size>0,'Missing retained solver log: '+k)
    return record

def pilot_status(root,risk,typical):
    rows=[]; passed=set()
    for key in sorted(risk):
        a,b=risk[key],typical[key];ra,rb=checkpoint(root,a),checkpoint(root,b)
        p=root/'cases'/a/'comparison.json'; c=read_json(p) if p.is_file() else {}
        good=ra is not None and rb is not None and ra['status']==rb['status']=='PASS' and c.get('convergence_status')=='PASS'
        if good:passed.add(key)
        rows.append(dict(template_id=key,risk_id=a,typical_id=b,risk_status=ra['status'] if ra else 'PENDING',typical_status=rb['status'] if rb else 'PENDING',approach=c.get('approach',''),stress_change_pct=c.get('stress_change_pct',''),displacement_change_pct=c.get('displacement_change_pct',''),convergence_status=c.get('convergence_status','PENDING'),pilot_status='PASS' if good else 'REVIEW',upper_wall_s=c.get('upper_wall_s',''),error=c.get('upper_error','')))
    return rows,passed

def export(root,by,levels,risk,typical):
    pilot,passed=pilot_status(root,risk,typical)
    records=[];failures=[];valid=Counter();provisional=Counter();times={k:[] for k in FAMILIES}
    for k,row in sorted(by.items()):
        r=checkpoint(root,k)
        if not r:continue
        key=r['template_id']
        if r['status']!='PASS':failures.append(dict(design_id=k,template_id=key,error=r.get('error',''),attempt=r['attempt']));continue
        provisional[key]+=1;times[key].append(r['wall_s'])
        if key not in passed:continue
        valid[key]+=1;m=r['metrics']
        records.append(dict(design_id=k,bracket_family=row['bracket_family'],production_level=levels[key],source=m['source'],mesh_rule=m['mesh_rule'],reference_max_stress_mpa=m['max_stress_mpa'],reference_max_disp_mm=m['max_disp_mm'],volume_mm3=m['volume_mm3'],reference_mass_kg=m['mass_kg'],reference_factor_of_safety=m['factor_of_safety'],reaction_relative_imbalance=m['reaction_relative_imbalance'],minimum_element_quality=m['minimum_element_quality'],node_count=m['node_count'],volume_element_count=m['volume_element_count'],wall_s=r['wall_s'],max_stress_location_mm=json.dumps(m['max_stress_location_mm'])))
    g.write_csv(root/'pilot_validation.csv',pilot,list(pilot[0]))
    fields=['design_id','bracket_family','production_level','source','mesh_rule','reference_max_stress_mpa','reference_max_disp_mm','volume_mm3','reference_mass_kg','reference_factor_of_safety','reaction_relative_imbalance','minimum_element_quality','node_count','volume_element_count','wall_s','max_stress_location_mm']
    g.write_csv(root/'new_fea_labels.csv',records,fields)
    g.write_csv(root/'failures.csv',failures,['design_id','template_id','error','attempt'])
    s=dict(policy=POLICY,planned=800,successful_solver_cases=sum(provisional.values()),accepted_labels=len(records),pilot_passed=sorted(passed),pilot_review=sorted(set(FAMILIES)-passed),family_accepted={k:valid[k] for k in sorted(FAMILIES)},unresolved_production_failures=len(failures),family_mean_production_s={k:round(sum(t)/len(t),2) if t else None for k,t in times.items()},complete=len(records)==800 and len(passed)==10 and not failures)
    g.atomic_json(root/'batch_summary.json',s);return s

def dispatch(tasks,workers,heavy_workers,min_free,root,by,levels,risk,typical,executor=ProcessPoolExecutor):
    """Independent processes, each with a distinct output directory. No shared Gmsh state."""
    pending=list(tasks); running={}; started=time.monotonic(); done=0; min_seen=None; blocked_since=None
    options={'max_workers':workers}
    if executor is ProcessPoolExecutor:options['mp_context']=multiprocessing.get_context('spawn')
    with executor(**options) as pool:
        while pending or running:
            free=available_gb();min_seen=free if min_seen is None else (min(min_seen,free) if free is not None else min_seen)
            heavy=sum(p[0]['design_id'].split('_')[0] in HEAVY for p in running.values())
            # Start at most one task per loop; sample memory between launches.
            if len(running)<workers and (free is None or free>=min_free):
                ix=next((i for i,p in enumerate(pending) if p[0]['design_id'].split('_')[0] not in HEAVY or heavy<heavy_workers),None)
                if ix is not None:
                    p=pending.pop(ix); running[pool.submit(job,p)]=p;blocked_since=None
                    print(f"START {p[0]['design_id']} | active {len(running)}/{workers} | free RAM {free if free is not None else 'unknown'}",flush=True)
            if not running:
                if not pending:break
                if blocked_since is None:blocked_since=time.monotonic()
                if time.monotonic()-blocked_since>60:raise RuntimeError('Not enough free RAM to start a case. Close other apps or reduce memory pressure.')
                time.sleep(5);continue
            finished,_=wait(running,timeout=3,return_when=FIRST_COMPLETED)
            for f in finished:
                p=running.pop(f); result=f.result(); done+=1
                r=result['record']; cmp=result.get('comparison',{})
                print(f"DONE {r['design_id']} | {r['status']} | {r['wall_s']:.1f}s | comparison {cmp.get('convergence_status','n/a')} | {done}/{len(tasks)}",flush=True)
                if r['status']!='PASS':print(r.get('error',''),flush=True)
                if done%5==0 or not pending and not running:
                    s=export(root,by,levels,risk,typical)
                    print(f"CHECKPOINT accepted={s['accepted_labels']}/800 | solved={s['successful_solver_cases']} | elapsed={(time.monotonic()-started)/60:.1f} min",flush=True)
            if len(running)==workers and not finished:continue
    return dict(tasks=len(tasks),elapsed_s=time.monotonic()-started,min_free_gb=min_seen,workers=workers)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('cad','interface','gate','output'):p.add_argument('--'+k,required=True,type=Path)
    p.add_argument('--phase',choices=('pilot','full'),required=True);p.add_argument('--workers',type=int,default=3);p.add_argument('--threads',type=int,default=2);p.add_argument('--heavy-workers',type=int,default=1);p.add_argument('--min-free-gb',type=float,default=8.);p.add_argument('--timeout-seconds',type=float,default=1800.);p.add_argument('--limit',type=int,default=None)
    args=p.parse_args(argv)
    require(1<=args.workers<=8 and 1<=args.threads<=8 and args.workers*args.threads<=20 and 1<=args.heavy_workers<=args.workers,'Invalid parallel setting')
    require(math.isfinite(args.timeout_seconds) and args.timeout_seconds>0 and math.isfinite(args.min_free_gb) and args.min_free_gb>=4,'Invalid resource limit')
    require(args.limit is None or args.limit>0,'Invalid limit')
    root=args.output.resolve();by,levels,config=inputs(args.cad,args.interface,args.gate)
    risk,typical=select_pilot(by);config.update(risk=risk,typical=typical)
    root.mkdir(parents=True,exist_ok=True)
    lock=root/'batch.lock'
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise RuntimeError('Batch lock exists. Do not start two batches. Check that the old Python/ccx jobs stopped before removing the lock.')
    try:
        with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
        path=root/'batch_config.json'
        if path.exists():require(read_json(path)==config,'Frozen batch configuration changed; use a different output folder')
        else:g.atomic_json(path,config)
        # Thread environment inherited by spawned workers before their imports.
        threads_set(args.threads)
        rows,passed=pilot_status(root,risk,typical)
        order=[]
        if args.phase=='pilot':
            # Interleave risky and typical designs; all are already in the 800-case plan.
            for key in ('T08','T01','T02','T09','T03','T04','T05','T06','T07','T10'):order.extend([(risk[key],True),(typical[key],False)])
        else:
            require(passed,'No family passed representative validation yet; run pilot first')
            if len(passed)<10:print('Only pilot-approved families will run; REVIEW families remain blocked.',flush=True)
            pilot_ids=set(risk.values())|set(typical.values())
            groups=[[k for k in sorted(by) if k.startswith(family+'_') and k not in pilot_ids] for family in sorted(passed)]
            order=[(group[i],False) for i in range(max(map(len,groups),default=0)) for group in groups if i<len(group)]
        tasks=[]
        for k,verify in order:
            rec=checkpoint(root,k);path=root/'cases'/k/'comparison.json'
            if rec and rec['status']=='PASS' and (not verify or (path.exists() and read_json(path).get('convergence_status')=='PASS')):continue
            tasks.append((by[k],levels[k.split('_')[0]],str(root),args.timeout_seconds,args.threads,verify))
        if args.limit is not None:tasks=tasks[:args.limit]
        print(f'{args.phase.upper()}: {len(tasks)} pending tasks; workers={args.workers}, threads={args.threads}, heavy cap={args.heavy_workers}',flush=True)
        perf=dispatch(tasks,args.workers,args.heavy_workers,args.min_free_gb,root,by,levels,risk,typical)
        perf.update(phase=args.phase,threads=args.threads,heavy_workers=args.heavy_workers,utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
        perfpath=root/'performance_history.json';history=read_json(perfpath) if perfpath.exists() else [];history.append(perf);g.atomic_json(perfpath,history)
        s=export(root,by,levels,risk,typical); print(json.dumps(s,indent=2),flush=True)
        print('PILOT GATE: '+('PASS' if len(s['pilot_passed'])==10 else 'REVIEW') if args.phase=='pilot' else 'FORMAL BATCH: '+('PASS' if s['complete'] else 'INCOMPLETE'),flush=True)
        return 0 if (len(s['pilot_passed'])==10 if args.phase=='pilot' else s['complete']) else 1
    finally:
        lock.unlink(missing_ok=True)

if __name__=='__main__':raise SystemExit(main())
