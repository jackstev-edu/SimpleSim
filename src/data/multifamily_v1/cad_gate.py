"""Generate baseline STEP files and validate the planned CADs."""
import argparse,csv,json
from pathlib import Path
from .templates import FAMILIES,baseline,sample_plan,build_template,check_faces,opening_area_mm2


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',required=True,type=Path); args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True); rows=[]
    for key,family in FAMILIES.items():
        cases=[baseline(key),*sample_plan(key)]
        assert len(cases)==81 and len({c.design_id for c in cases})==81
        for case in cases:
            solid=build_template(case); fixed,load=check_faces(case,solid)
            if case.sampling_method=='baseline':
                path=args.output/f'{key}_baseline.step'; solid.exportStep(str(path)); assert path.stat().st_size>0
            rows.append(dict(design_id=case.design_id,bracket_family=family.key,sampling_method=case.sampling_method,anchor_id=case.anchor_id,changed_parameter=case.changed_parameter,params_json=json.dumps(case.params,sort_keys=True),poissons_ratio=case.material.poissons_ratio,volume_mm3=solid.Volume(),opening_area_mm2=opening_area_mm2(case),fixed_face_area_mm2=fixed,load_face_area_mm2=load))
        print(f'{key}: baseline + 80 planned geometries PASS',flush=True)
    # The controlled pairs must actually reduce CAD volume, not merely alter an input.
    by_id={row['design_id']:row for row in rows}
    pairs=[row for row in rows if row['sampling_method']=='paired']
    if len(pairs)!=20*len(FAMILIES): raise ValueError('Unexpected paired sample count')
    for row in pairs:
        anchor=by_id[row['anchor_id']]
        if not row['volume_mm3'] < anchor['volume_mm3']:
            raise ValueError(f"{row['design_id']}: intended reduction did not reduce volume")
    print(f'CAD MASS-DIRECTION CHECK: PASS ({len(pairs)}/{len(pairs)} pairs)',flush=True)
    with (args.output/'cad_checks.csv').open('w',newline='',encoding='utf-8') as stream:
        w=csv.DictWriter(stream,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f'CAD GATE: PASS ({len(rows)} geometry checks; no FEA labels)',flush=True)

if __name__=='__main__': main()
