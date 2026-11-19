"""Step 5A: real baseline solves and two-level mesh comparisons (no training labels).

Use the pinned Role 1 solver. Diagnostic results are isolated from formal data.
A completed case is resumed only with the same CAD inputs and mesh policy.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

from .interface_gate import check_plan, close, new_case, read_csv, require, sha, unique
from .templates import FAMILIES, build_template, check_faces

POLICY = 'multifamily_baseline_mesh_1'
LEVELS = {'medium': 1.0, 'fine': 2.0 / 3.0}
STRESS_LIMIT_PCT = 3.0
DISP_LIMIT_PCT = 1.0


def mesh_parameters(case, level):
    """HXT tet10, curvature sizing; allow the mesh to resolve small rounds and thin ribs."""
    s = LEVELS[level]
    p = case.params
    cap = min(3., case.thickness_mm / 2., p.get('gusset_thickness_mm', 3.))
    # Smallest explicit feature radii: T05 notch mouth 0.8, T07/T10 shoulder 1.
    radius = min(p['fillet_radius_mm'], 2.)
    if case.template_id == 'T05':
        radius = min(radius, .8)
    if case.template_id in ('T07', 'T10'):
        radius = min(radius, 1.)
    maximum = cap * s
    curvature = max(4, round(16 / s))
    minimum = min(maximum / 4., 2. * math.pi * radius / curvature)
    return dict(minimum_size_mm=minimum, maximum_size_mm=maximum, element_order=2,
                curvature_elements=curvature, algorithm_3d=10, high_order_optimize=2)


def real_solve(case, folder, mesh, timeout):
    # Real solver path only; the injectable function is used by isolated unit tests.
    from ..mesh import MeshSettings
    from ..solver import solve_case
    settings = MeshSettings(**mesh)
    result = solve_case(case, folder, mesh_settings=settings, timeout_seconds=timeout,
                        family=FAMILIES[case.template_id])
    require(result.mesh_rule == settings.mesh_rule, 'Solver used a different mesh rule')
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')
    temp.replace(path)


def write_csv(path, rows, fields):
    with NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    temp.replace(path)


def baseline_inputs(cad_path, interface_path):
    cad_path, interface_path = Path(cad_path), Path(interface_path)
    summary = json.loads(interface_path.read_text(encoding='utf-8'))
    require(summary['designs'] == 1000 and summary['pending_fea'] == 800, 'Unexpected Step 4 counts')
    require(summary['inputs_sha256']['cad_checks'] == sha(cad_path), 'CAD input differs from frozen Step 4; stop')
    rows = read_csv(cad_path)
    check_plan(rows)
    selected = [r for r in rows if r['sampling_method'] == 'baseline']
    require(Counter(r['bracket_family'] for r in selected) == Counter(f.key for f in FAMILIES.values()),
            'Expected one baseline per family')
    return {new_case(r).template_id: r for r in selected}


def verify_result(case, result, expected, folder):
    require(result.design_id == case.design_id, 'Result design ID mismatch')
    require(result.source == 'calculix_2.23', 'Unexpected solver source')
    positive = ['max_stress_mpa', 'max_disp_mm', 'volume_mm3', 'mass_kg', 'factor_of_safety']
    for key in positive:
        require(math.isfinite(float(getattr(result, key))) and getattr(result, key) > 0., f'Invalid {key}')
    require(close(result.volume_mm3, expected['volume_mm3']), 'FEA CAD volume differs from Step 3')
    require(close(result.mass_kg, result.volume_mm3 * case.material.density_kg_m3 * 1e-9), 'Mass mismatch')
    require(close(result.factor_of_safety, case.material.yield_strength_mpa / result.max_stress_mpa), 'Safety factor mismatch')
    reaction = tuple(map(float, result.reaction_force_n))
    require(len(reaction) == 3 and all(math.isfinite(v) for v in reaction), 'Invalid reaction vector')
    imbalance = math.sqrt(sum((a+b)**2 for a,b in zip(reaction,case.load_vector_n))) / case.load_force_n
    require(close(imbalance, result.reaction_relative_imbalance) and 0 <= imbalance <= .01, 'Reaction imbalance over limit')
    require(.1 < result.minimum_element_quality <= 1.000001, 'Minimum mesh quality must be > 0.1')
    require(result.node_count > 0 and result.volume_element_count > 0, 'Missing mesh')
    artifacts = {}
    for name in ('deck_path', 'frd_path', 'dat_path', 'log_path'):
        path = Path(getattr(result, name)).resolve()
        require(path.is_relative_to(folder.resolve()) and path.is_file() and path.stat().st_size > 0, f'Missing/empty {name}')
        artifacts[name] = str(path.relative_to(folder.resolve()))
    data = {key: float(getattr(result, key)) for key in positive}
    data.update(source=result.source, mesh_rule=result.mesh_rule,
                reaction_force_n=list(reaction), reaction_relative_imbalance=imbalance,
                minimum_element_quality=float(result.minimum_element_quality),
                node_count=int(result.node_count), volume_element_count=int(result.volume_element_count),
                max_stress_location_mm=list(result.max_stress_location_mm),
                max_disp_location_mm=list(result.max_disp_location_mm),
                elapsed_total_s=sum(float(getattr(result,k)) for k in ('elapsed_geometry_s','elapsed_mesh_s','elapsed_solve_s')),
                artifacts=artifacts)
    return data


def attempt(case, row, level, output, timeout, solve=real_solve, *, mesh_override=None):
    mesh = mesh_parameters(case, level) if mesh_override is None else dict(mesh_override)
    target = output / 'cases' / case.template_id / level
    config = dict(policy=POLICY, input=row, mesh_parameters=mesh)
    path = target / 'result.json'
    if path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        require(saved['config'] == config, f'{case.design_id}/{level}: changed inputs, use a new output folder')
        if saved['status'] == 'PASS':
            folder = target / saved['attempt']
            require(all((folder / p).is_file() and (folder / p).stat().st_size > 0
                        for p in saved['metrics']['artifacts'].values()), f'{case.design_id}/{level}: saved solver artifacts missing')
            print(f'{case.template_id} {level}: resumed PASS', flush=True)
            return saved
    else:
        target.mkdir(parents=True, exist_ok=True)
    ix = 1
    while (target / f'attempt_{ix:03d}').exists(): ix += 1
    folder = target / f'attempt_{ix:03d}'
    folder.mkdir()
    result_record = dict(config=config, attempt=folder.name, design_id=case.design_id,
                         template_id=case.template_id, level=level, status='FAIL')
    try:
        solid = build_template(case)
        fixed, load = check_faces(case, solid)
        require(close(solid.Volume(), row['volume_mm3']) and close(fixed,row['fixed_face_area_mm2'])
                and close(load,row['load_face_area_mm2']), 'Baseline CAD differs from frozen gate')
        print(f'{case.template_id} {level}: solving...', flush=True)
        result = solve(case, folder, mesh, timeout)
        result_record['metrics'] = verify_result(case, result, row, folder)
        result_record['status'] = 'PASS'
        m = result_record['metrics']
        print(f'{case.template_id} {level}: PASS | stress={m["max_stress_mpa"]:.3f} MPa | '
              f'disp={m["max_disp_mm"]:.6f} mm | quality={m["minimum_element_quality"]:.4f}', flush=True)
    except Exception as exc:
        result_record['error'] = f'{type(exc).__name__}: {exc}'
        print(f'{case.template_id} {level}: FAIL | {result_record["error"]}', flush=True)
    atomic_json(folder / 'attempt.json', result_record)
    atomic_json(path, result_record)
    return result_record


def convergence(medium, fine):
    if not medium or not fine or medium['status'] != 'PASS' or fine['status'] != 'PASS':
        return dict(stress_change_pct='', displacement_change_pct='', convergence_status='PENDING_OR_FAILED')
    a,b=medium['metrics'],fine['metrics']
    stress=abs(a['max_stress_mpa']-b['max_stress_mpa'])/b['max_stress_mpa']*100.
    disp=abs(a['max_disp_mm']-b['max_disp_mm'])/b['max_disp_mm']*100.
    refined=b['node_count']>a['node_count'] and b['volume_element_count']>a['volume_element_count']
    return dict(stress_change_pct=stress,displacement_change_pct=disp,
                convergence_status='PASS' if refined and stress<=STRESS_LIMIT_PCT and disp<=DISP_LIMIT_PCT else 'REVIEW')


def generate(cad, interface, output, levels, timeout, solve=real_solve):
    output=Path(output); require(math.isfinite(timeout) and timeout>0,'Invalid timeout')
    baseline_rows=baseline_inputs(cad,interface)
    output.mkdir(parents=True,exist_ok=True)
    configuration=dict(policy=POLICY,cad_sha256=sha(Path(cad)),interface_sha256=sha(Path(interface)))
    config_path=output/'gate_config.json'
    if config_path.exists(): require(json.loads(config_path.read_text())==configuration,'Gate inputs changed; use a new output directory')
    else: atomic_json(config_path,configuration)
    records={}
    for key,row in sorted(baseline_rows.items()):
        case=new_case(row)
        for level in LEVELS:
            path=output/'cases'/key/level/'result.json'
            if level in levels:
                if level=='fine' and records.get((key,'medium'),{}).get('status')!='PASS':
                    print(f'{key} fine: not run; medium must pass first',flush=True)
                    continue
                records[key,level]=attempt(case,row,level,output,timeout,solve)
            elif path.exists():
                saved=json.loads(path.read_text(encoding='utf-8'))
                if saved['status']=='PASS':
                    # Resume through the same validation, without running the solver.
                    saved=attempt(case,row,level,output,timeout,solve)
                records[key,level]=saved
    rows=[]
    for (key,level), rec in sorted(records.items()):
        metrics=rec.get('metrics',{})
        rows.append(dict(template_id=key,level=level,status=rec['status'],
                         **{k:metrics.get(k,'') for k in ('max_stress_mpa','max_disp_mm','mass_kg','factor_of_safety','reaction_relative_imbalance','minimum_element_quality','node_count','volume_element_count','mesh_rule','elapsed_total_s')},
                         error=rec.get('error','')))
    fields=['template_id','level','status','max_stress_mpa','max_disp_mm','mass_kg','factor_of_safety','reaction_relative_imbalance','minimum_element_quality','node_count','volume_element_count','mesh_rule','elapsed_total_s','error']
    write_csv(output/'baseline_results.csv',rows,fields)
    compared=[dict(template_id=key,**convergence(records.get((key,'medium')),records.get((key,'fine')))) for key in sorted(baseline_rows)]
    write_csv(output/'mesh_convergence.csv',compared,list(compared[0]))
    count=lambda level:sum(rec['status']=='PASS' for (k,l),rec in records.items() if l==level)
    mid,fin,conv=count('medium'),count('fine'),sum(r['convergence_status']=='PASS' for r in compared)
    summary=dict(policy=POLICY,medium_pass=mid,fine_pass=fin,convergence_pass=conv,
                 failed=[dict(template_id=k,level=l,error=r.get('error','')) for (k,l),r in records.items() if r['status']!='PASS'],
                 convergence_review=[r['template_id'] for r in compared if r['convergence_status']=='REVIEW'],
                 threshold_stress_pct=STRESS_LIMIT_PCT,threshold_displacement_pct=DISP_LIMIT_PCT,
                 baseline_gate_pass=mid==fin==conv==10,formal_labels_added=0)
    atomic_json(output/'fea_gate_summary.json',summary)
    print(f'BASELINE SOLVES: medium {mid}/10; fine {fin}/10; convergence {conv}/10',flush=True)
    if summary['baseline_gate_pass']: print('BASELINE FEA GATE: PASS (representative-case study still required)',flush=True)
    elif mid==10 and fin==0 and levels==['medium']: print('MEDIUM BASELINE GATE: PASS; fine study pending',flush=True)
    else: print('BASELINE FEA GATE: INCOMPLETE OR REVIEW; see fea_gate_summary.json',flush=True)
    return summary


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cad',required=True,type=Path); p.add_argument('--interface',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path); p.add_argument('--level',choices=['medium','fine','both'],default='medium')
    p.add_argument('--timeout-seconds',type=float,default=1800.)
    a=p.parse_args()
    # Match Jack's serial per-job CalculiX settings, rather than oversubscribing the machine.
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','CCX_NPROC_RESULTS','CCX_NPROC_STIFFNESS','CCX_NPROC_EQUATION_SOLVER'):
        os.environ[key]='1'
    levels=list(LEVELS) if a.level=='both' else [a.level]
    summary=generate(a.cad,a.interface,a.output,levels,a.timeout_seconds)
    return 0 if (summary['medium_pass']==10 if a.level=='medium' else summary['baseline_gate_pass']) else 1

if __name__=='__main__': sys.exit(main())
