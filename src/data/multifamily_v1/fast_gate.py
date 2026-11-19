"""Targeted mesh sensitivity test, keeping all existing global-mesh evidence."""
import argparse
import json
import math
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from . import fea_gate as g, refine_gate as r

POLICY='multifamily_targeted_mesh_1'

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))

def plan_for(case, medium, fine, threads):
    radius=min(5.,max(3.,case.thickness_mm*.65))
    points=[]
    for record in (fine,medium):
        xyz=record['metrics']['max_stress_location_mm']
        g.require(len(xyz)==3 and all(math.isfinite(x) for x in xyz),'Missing hotspot')
        candidates=[list(xyz)]
        if case.template_id!='T10':
            candidates.append([xyz[0],xyz[1],case.params['arm_width_mm']-xyz[2]])
        for p in candidates:
            if not any(math.dist(p,q)<radius*.4 for q in points):points.append(p)
    mesh=g.mesh_parameters(case,'fine')
    mesh['minimum_size_mm']=r.extra_mesh(case)['minimum_size_mm']
    return dict(base=mesh,centers_mm=points,radius_mm=radius,transition_mm=radius,threads=threads,policy=POLICY)

def apply_fields(gmsh, plan):
    f=gmsh.model.mesh.field; tags=[]
    for x,y,z in plan['centers_mm']:
        tag=f.add('Ball');tags.append(tag)
        for key,value in dict(XCenter=x,YCenter=y,ZCenter=z,Radius=plan['radius_mm'],Thickness=plan['transition_mm'],VIn=plan['base']['minimum_size_mm'],VOut=plan['base']['maximum_size_mm']).items():
            f.setNumber(tag,key,value)
    tag=tags[0]
    if len(tags)>1:
        tag=f.add('Min');f.setNumbers(tag,'FieldsList',tags)
    f.setAsBackgroundMesh(tag)
    gmsh.option.setNumber('General.NumThreads',plan['threads'])

def targeted_solve(case,folder,plan,timeout):
    import gmsh
    for key in ('OMP_NUM_THREADS','CCX_NPROC_STIFFNESS','CCX_NPROC_RESULTS','CCX_NPROC_EQUATION_SOLVER'):
        os.environ[key]=str(plan['threads'])
    for key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
    original=gmsh.model.mesh.generate; called=False
    def generate(dim=3):
        nonlocal called
        if dim==3:
            g.require(not called,'Duplicate mesh generation')
            apply_fields(gmsh,plan);called=True
        return original(dim)
    # Shared mesh.py initializes/imports CAD and sets its options before generate(3).
    with patch.object(gmsh.model.mesh,'generate',generate):
        result=g.real_solve(case,folder,plan['base'],timeout)
    g.require(called,'Local mesh field was not installed')
    return replace(result,mesh_rule=result.mesh_rule+'_targeted1')

def local_check(fine,upper,plan):
    check=g.convergence(fine,upper);covered=False
    if upper['status']=='PASS':
        xyz=upper['metrics']['max_stress_location_mm']
        covered=len(xyz)==3 and all(math.isfinite(v) for v in xyz) and any(math.dist(xyz,p)<=plan['radius_mm'] for p in plan['centers_mm'])
        if not covered and check['convergence_status']=='PASS':check['convergence_status']='REVIEW'
    return check,covered

def generate(cad,interface,output,timeout=1800.,threads=4,solve=targeted_solve):
    output=Path(output);g.require(1<=threads<=8 and math.isfinite(timeout) and timeout>0,'Invalid threads/timeout')
    baseline=g.baseline_inputs(cad,interface)
    expected=dict(policy=g.POLICY,cad_sha256=g.sha(Path(cad)),interface_sha256=g.sha(Path(interface)))
    g.require(read_json(output/'gate_config.json')==expected,'Step 5A inputs changed')
    old={}
    for key,row in sorted(baseline.items()):
        c=g.new_case(row);old[key]=(r.cached(c,row,'medium',output,timeout),r.cached(c,row,'fine',output,timeout))
    rows=[];selected={}
    for key,row in sorted(baseline.items()):
        case=g.new_case(row);medium,fine=old[key];lower,upper=medium,fine
        check=g.convergence(lower,upper);mode='global';covered='';candidate='medium' if check['convergence_status']=='PASS' else ''
        if not candidate:
            path=output/'cases'/key/r.EXTRA_LEVEL/'result.json'
            if path.is_file() and read_json(path)['status']=='PASS':
                def forbidden(*args):raise RuntimeError('Do not recompute cached global result')
                upper=g.attempt(case,row,r.EXTRA_LEVEL,output,timeout,forbidden,mesh_override=r.extra_mesh(case));lower=fine
                check=g.convergence(lower,upper)
            else:
                mode='targeted';lower=fine;plan=plan_for(case,medium,fine,threads)
                upper=g.attempt(case,row,'targeted_fine_v1',output,timeout,solve,mesh_override=plan)
                check,covered=local_check(fine,upper,plan)
            if check['convergence_status']=='PASS':candidate='fine'
        if candidate:selected[key]=candidate
        a,b=lower['metrics'],upper.get('metrics',{})
        rows.append(dict(template_id=key,approach=mode,comparison=f"{lower['level']} vs {upper['level']}",**check,provisional_production_level=candidate,peak_in_refined_core=covered,lower_nodes=a['node_count'],upper_nodes=b.get('node_count',''),lower_stress_mpa=a['max_stress_mpa'],upper_stress_mpa=b.get('max_stress_mpa',''),upper_elapsed_s=b.get('elapsed_total_s',''),error=upper.get('error','')))
        g.write_csv(output/'fast_mesh_convergence.csv',rows,list(rows[0]))
        print(f"{key}: {mode} {check['convergence_status']}; candidate={candidate or 'none'}",flush=True)
    summary=dict(policy=POLICY,convergence_pass=len(selected),baseline_gate_pass=len(selected)==10,provisional_production_levels=selected,convergence_review=[v['template_id'] for v in rows if not v['provisional_production_level']],targeted_templates=[v['template_id'] for v in rows if v['approach']=='targeted'],threshold_stress_pct=g.STRESS_LIMIT_PCT,threshold_displacement_pct=g.DISP_LIMIT_PCT,representative_case_study_pending=True,formal_labels_added=0,note='Targeted hotspot sensitivity is not full-domain global refinement.')
    g.atomic_json(output/'fast_gate_summary.json',summary)
    print(f"FAST BASELINE GATE: {'PASS' if summary['baseline_gate_pass'] else 'REVIEW'} ({len(selected)}/10); representative study pending",flush=True)
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('cad','interface','output'):p.add_argument('--'+k,required=True,type=Path)
    p.add_argument('--threads',type=int,default=4);p.add_argument('--timeout-seconds',type=float,default=1800.)
    a=p.parse_args();s=generate(a.cad,a.interface,a.output,a.timeout_seconds,a.threads)
    return 0 if s['baseline_gate_pass'] else 1

if __name__=='__main__':raise SystemExit(main())
