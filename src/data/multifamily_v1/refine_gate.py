"""Step 5B: compare adjacent refined baseline meshes without changing old results."""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
from pathlib import Path
from . import fea_gate as g

EXTRA_LEVEL = 'extra_fine'


def extra_mesh(case):
    # Fine -> extra-fine: scale sizes by 2/3 and curvature count by 1.5.
    m = g.mesh_parameters(case, 'fine')
    for key in ('minimum_size_mm', 'maximum_size_mm'):
        m[key] *= 2.0 / 3.0
    m['curvature_elements'] = round(m['curvature_elements'] * 1.5)
    return m


def cached(case, row, level, output, timeout):
    path = output / 'cases' / case.template_id / level / 'result.json'
    g.require(path.is_file(), f'Missing earlier {case.template_id}/{level} result; complete Step 5A first')
    saved = json.loads(path.read_text(encoding='utf-8'))
    g.require(saved['status'] == 'PASS', f'Earlier {case.template_id}/{level} did not pass solver QA')
    def no_solve(*args):
        raise RuntimeError('Earlier solve must not be rerun by refinement')
    return g.attempt(case, row, level, output, timeout, no_solve)


def report_hotspot(record, case, prefix):
    xyz = record.get('metrics', {}).get('max_stress_location_mm', [])
    if len(xyz) != 3:
        return {prefix+k: '' for k in ('x_mm','y_mm','z_mm','distance_to_fixed_mm','distance_to_load_mm')}
    return {prefix+'x_mm':xyz[0],prefix+'y_mm':xyz[1],prefix+'z_mm':xyz[2],
            prefix+'distance_to_fixed_mm':abs(xyz[0]),
            prefix+'distance_to_load_mm':abs(case.flange_length_mm-xyz[0])}


def generate(cad, interface, output, timeout=3600., solve=g.real_solve):
    output=Path(output); g.require(math.isfinite(timeout) and timeout>0,'Invalid timeout')
    baseline_rows=g.baseline_inputs(cad,interface)
    config_path=output/'gate_config.json'
    expected=dict(policy=g.POLICY,cad_sha256=g.sha(Path(cad)),interface_sha256=g.sha(Path(interface)))
    g.require(config_path.is_file() and json.loads(config_path.read_text(encoding='utf-8'))==expected,
              'Missing or changed Step 5A config')
    # Validate every old result before starting expensive work.
    old={}
    for key,row in sorted(baseline_rows.items()):
        case=g.new_case(row)
        old[key]=(cached(case,row,'medium',output,timeout),cached(case,row,'fine',output,timeout))
    rows=[]; selections={}; review=[]; failures=[]; extra_count=0
    for key,row in sorted(baseline_rows.items()):
        case=g.new_case(row); medium,fine=old[key]
        first=g.convergence(medium,fine)
        if first['convergence_status']=='PASS':
            lower,upper=medium,fine; check=first; selected='medium'
        else:
            lower=fine
            upper=g.attempt(case,row,EXTRA_LEVEL,output,timeout,solve,mesh_override=extra_mesh(case))
            check=g.convergence(lower,upper)
            selected='fine' if check['convergence_status']=='PASS' else ''
            if upper['status']=='PASS':extra_count+=1
            else: failures.append(dict(template_id=key,error=upper.get('error','Unknown failure')))
        if selected: selections[key]=selected
        else: review.append(key)
        a,b=lower['metrics'],upper.get('metrics',{})
        rows.append(dict(template_id=key,comparison=f"{lower['level']} vs {upper['level']}",
                         lower_stress_mpa=a['max_stress_mpa'],upper_stress_mpa=b.get('max_stress_mpa',''),
                         lower_nodes=a['node_count'],upper_nodes=b.get('node_count',''),
                         **check,provisional_production_level=selected,
                         **report_hotspot(lower,case,'lower_peak_'),**report_hotspot(upper,case,'upper_peak_')))
        print(f'{key}: {rows[-1]["comparison"]}: {check["convergence_status"]}; candidate={selected or "none"}',flush=True)
    g.write_csv(output/'refined_mesh_convergence.csv',rows,list(rows[0]))
    summary=dict(policy='multifamily_baseline_refinement_1',baseline_gate_pass=len(selections)==10,
                 convergence_pass=len(selections),additional_successful_solves=extra_count,
                 provisional_production_levels=selections,convergence_review=review,failed=failures,
                 threshold_stress_pct=g.STRESS_LIMIT_PCT,threshold_displacement_pct=g.DISP_LIMIT_PCT,
                 representative_case_study_pending=True,formal_labels_added=0)
    g.atomic_json(output/'refined_gate_summary.json',summary)
    print(f'REFINED BASELINE GATE: {"PASS" if summary["baseline_gate_pass"] else "REVIEW"} ({len(selections)}/10); representative study pending',flush=True)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('cad','interface','output'):p.add_argument('--'+k,required=True,type=Path)
    p.add_argument('--timeout-seconds',type=float,default=3600.)
    a=p.parse_args()
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','CCX_NPROC_RESULTS','CCX_NPROC_STIFFNESS','CCX_NPROC_EQUATION_SOLVER'):
        os.environ[key]='1'
    s=generate(a.cad,a.interface,a.output,a.timeout_seconds)
    return 0 if s['baseline_gate_pass'] else 1

if __name__=='__main__':sys.exit(main())
