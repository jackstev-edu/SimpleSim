"""T02-only full-domain mesh comparison; preserves frozen production checkpoints."""
from __future__ import annotations
import argparse
import math
import os
from pathlib import Path
from . import batch_v1 as b, batch_disk_safe as disk, fea_gate as g, refine_gate as refine

KIND = 't02_global_reference_v1'


def verify(row, root, timeout, threads, solve=None):
    root = Path(root)
    case = b.new_case(row)
    g.require(case.template_id == 'T02', 'T02 only')
    folder = root / 'cases' / case.design_id
    path = folder / 'comparison.json'
    old = b.read_json(path)
    if old['convergence_status'] == 'PASS':
        return old
    lower = b.checkpoint(root, case.design_id)
    g.require(lower is not None and lower['status'] == 'PASS' and lower['level'] == 'fine' and lower['config']['row'] == row,
              'Expected original fine production result')
    g.require(lower['config']['mesh'] == g.mesh_parameters(case, 'fine'), 'Production mesh mismatch')
    archive = folder / 'comparison_before_t02_global_reference.json'
    if not archive.exists():
        g.atomic_json(archive, old)
    if solve is None:
        issue = disk.disk_issue(root)
        g.require(issue is None, 'DISK GUARD: ' + str(issue))
    print(f'{case.design_id}: comparing existing fine to independent global extra_fine', flush=True)
    upper = b.save_solve(row, 'extra_fine', KIND, refine.extra_mesh(case), root, timeout, threads, solve)
    check = g.convergence(lower, upper)
    a, u = lower['metrics'], upper.get('metrics', {})
    report = dict(**check, approach='global_fine_extra_fine', evidence_kind=KIND,
                  upper_status=upper['status'], upper_wall_s=upper['wall_s'], upper_error=upper.get('error',''),
                  lower_stress_mpa=a['max_stress_mpa'], upper_stress_mpa=u.get('max_stress_mpa'),
                  lower_nodes=a['node_count'], upper_nodes=u.get('node_count'),
                  lower_elements=a['volume_element_count'], upper_elements=u.get('volume_element_count'))
    g.atomic_json(folder / 't02_global_reference_check.json', report)
    # Only a real PASS supersedes the prior representative REVIEW. Preserve every earlier report.
    if check['convergence_status'] == 'PASS':
        g.atomic_json(path, report)
    print(f'T02 GLOBAL: {check["convergence_status"]} | stress change {check["stress_change_pct"]}% | displacement change {check["displacement_change_pct"]}%', flush=True)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('cad','interface','gate','output'):
        p.add_argument('--'+key, required=True, type=Path)
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--timeout-seconds', type=float, default=3600.)
    a = p.parse_args(argv)
    g.require(1 <= a.threads <= 8 and math.isfinite(a.timeout_seconds) and a.timeout_seconds > 0, 'Invalid resources')
    root = a.output.resolve()
    by, levels, config = b.inputs(a.cad, a.interface, a.gate)
    risk, typical = b.select_pilot(by)
    config.update(risk=risk, typical=typical)
    lock = root / 'batch.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))
        g.require(b.read_json(root / 'batch_config.json') == config, 'Frozen batch configuration changed')
        g.require(levels['T02'] == 'fine', 'Unexpected T02 production mesh')
        _, passed = b.pilot_status(root, risk, typical)
        g.require(set(b.FAMILIES)-{'T02'} <= passed, 'Other families not ready')
        b.threads_set(a.threads)
        verify(by[risk['T02']], root, a.timeout_seconds, a.threads)
        s = b.export(root, by, levels, risk, typical)
        print(f'Accepted labels: {s["accepted_labels"]}/800; remaining review: {s["pilot_review"]}', flush=True)
        print('PILOT GATE: ' + ('PASS' if not s['pilot_review'] else 'REVIEW'), flush=True)
        return 0 if not s['pilot_review'] else 1
    finally:
        lock.unlink(missing_ok=True)

if __name__ == '__main__':
    raise SystemExit(main())
