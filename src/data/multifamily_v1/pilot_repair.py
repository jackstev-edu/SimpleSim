"""Repeat the hotspot sensitivity check for T02/T10 without replacing production labels."""
from __future__ import annotations
import argparse
import math
import os
from pathlib import Path
from . import batch_v1 as b, fast_gate as fast, fea_gate as g

KIND = 'targeted_second_peak'
LEVEL = 'targeted_fine_v2'


def retry_one(row, root, timeout, threads, solve=None):
    root = Path(root)
    case = b.new_case(row)
    key = case.design_id
    folder = root / 'cases' / key
    path = folder / 'comparison.json'
    first = b.read_json(path)
    if first['convergence_status'] == 'PASS':
        return first
    lower = b.checkpoint(root, key)
    prior = b.read_json(folder / 'targeted' / 'result.json')
    g.require(lower['status'] == prior['status'] == 'PASS', 'Both previous solves must have succeeded: ' + key)
    g.require(prior['config']['row'] == row and prior['config']['kind'] == 'targeted', 'Prior checkpoint mismatch: ' + key)
    g.require(lower['config']['row'] == row and lower['level'] == 'fine', 'Production checkpoint mismatch: ' + key)
    # Include the original and migrated peaks and (where applicable) their mirror points.
    plan = fast.plan_for(case, lower, prior, threads)
    def real(c, f, m, t, n):
        return fast.targeted_solve(c, f, m, t)
    archive = folder / 'comparison_before_second_peak.json'
    if not archive.exists():
        g.atomic_json(archive, first)
    print(f'{key}: checking both peak locations, centers={plan["centers_mm"]}', flush=True)
    upper = b.save_solve(row, LEVEL, KIND, plan, root, timeout, threads, solve or real)
    check, covered = fast.local_check(lower, upper, plan)
    report = dict(**check, approach='targeted_second_peak', peak_in_refined_core=covered,
                  upper_status=upper['status'], upper_wall_s=upper['wall_s'], upper_error=upper.get('error', ''),
                  evidence_kind=KIND, centers_mm=plan['centers_mm'], radius_mm=plan['radius_mm'])
    g.atomic_json(path, report)
    print(f'{key}: {report["convergence_status"]} | stress change {report["stress_change_pct"]}% | peak covered {covered}', flush=True)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for k in ('cad', 'interface', 'gate', 'output'):
        p.add_argument('--' + k, required=True, type=Path)
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--timeout-seconds', type=float, default=1800.)
    a = p.parse_args(argv)
    g.require(1 <= a.threads <= 8 and math.isfinite(a.timeout_seconds) and a.timeout_seconds > 0, 'Invalid resources')
    root = a.output.resolve()
    by, levels, config = b.inputs(a.cad, a.interface, a.gate)
    risk, typical = b.select_pilot(by)
    config.update(risk=risk, typical=typical)
    lock = root / 'batch.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError('Batch is locked. Wait for the current batch to finish; do not run two jobs on this directory.')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))
        g.require(b.read_json(root / 'batch_config.json') == config, 'Frozen batch configuration changed')
        b.threads_set(a.threads)
        for family in ('T02', 'T10'):
            g.require(levels[family] == 'fine', 'Unexpected production mesh')
            retry_one(by[risk[family]], root, a.timeout_seconds, a.threads)
            b.export(root, by, levels, risk, typical)
        s = b.export(root, by, levels, risk, typical)
        print('PILOT GATE: ' + ('PASS' if len(s['pilot_passed']) == 10 else 'REVIEW'), flush=True)
        print(f'Accepted labels: {s["accepted_labels"]}/800; remaining review: {s["pilot_review"]}', flush=True)
        return 0 if len(s['pilot_passed']) == 10 else 1
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
