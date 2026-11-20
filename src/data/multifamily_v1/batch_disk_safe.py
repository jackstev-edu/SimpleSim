"""Disk-aware front end to the unchanged Step 6 batch runner."""
from __future__ import annotations
import errno, multiprocessing, os, shutil, tempfile, time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from . import batch_v1 as b
available_gb=b.available_gb
HEAVY=b.HEAVY
job=b.job
export=b.export

def disk_issue(root):
    # Keep a conservative reserve on the output and temp volumes, and protect Windows system volume.
    checks=[(Path(root), 80), (Path(tempfile.gettempdir()), 80)]
    if os.name=='nt': checks.append((Path(os.environ.get('SystemDrive','C:')+'\\'), 8))
    for p,reserve in checks:
        free=shutil.disk_usage(p).free/2**30
        if free<reserve: return f'{p}: {free:.1f} GiB free, reserve {reserve} GiB'
    return None

def dispatch(tasks,workers,heavy_workers,min_free,root,by,levels,risk,typical,executor=ProcessPoolExecutor):
    """Independent processes, each with a distinct output directory. No shared Gmsh state."""
    pending=list(tasks); running={}; started=time.monotonic(); done=0; min_seen=None; blocked_since=None
    paused_reason=None
    options={'max_workers':workers}
    if executor is ProcessPoolExecutor:options['mp_context']=multiprocessing.get_context('spawn')
    with executor(**options) as pool:
        while pending or running:
            free=available_gb();min_seen=free if min_seen is None else (min(min_seen,free) if free is not None else min_seen)
            heavy=sum(p[0]['design_id'].split('_')[0] in HEAVY for p in running.values())
            if paused_reason is None:
                paused_reason=disk_issue(root)
                if paused_reason:
                    print('DISK GUARD: '+paused_reason+'; no new tasks, draining active cases.',flush=True)
            # Start at most one task per loop; sample memory between launches.
            if paused_reason is None and len(running)<workers and (free is None or free>=min_free):
                ix=next((i for i,p in enumerate(pending) if p[0]['design_id'].split('_')[0] not in HEAVY or heavy<heavy_workers),None)
                if ix is not None:
                    p=pending.pop(ix); running[pool.submit(job,p)]=p;blocked_since=None
                    print(f"START {p[0]['design_id']} | active {len(running)}/{workers} | free RAM {free if free is not None else 'unknown'}",flush=True)
            if not running:
                if not pending or paused_reason:break
                if blocked_since is None:blocked_since=time.monotonic()
                if time.monotonic()-blocked_since>60:raise RuntimeError('Not enough free RAM to start a case. Close other apps or reduce memory pressure.')
                time.sleep(5);continue
            finished,_=wait(running,timeout=3,return_when=FIRST_COMPLETED)
            for f in finished:
                p=running.pop(f)
                try:
                    result=f.result()
                except OSError as exc:
                    if exc.errno != errno.ENOSPC: raise
                    paused_reason='disk write failed: '+str(exc)
                    print('DISK GUARD: '+paused_reason+'; saved checkpoints retained.',flush=True)
                    continue
                done+=1
                r=result['record']; cmp=result.get('comparison',{})
                print(f"DONE {r['design_id']} | {r['status']} | {r['wall_s']:.1f}s | comparison {cmp.get('convergence_status','n/a')} | {done}/{len(tasks)}",flush=True)
                if r['status']!='PASS':print(r.get('error',''),flush=True)
                if (done%5==0 or not pending and not running) and disk_issue(root) is None:
                    s=export(root,by,levels,risk,typical)
                    print(f"CHECKPOINT accepted={s['accepted_labels']}/800 | solved={s['successful_solver_cases']} | elapsed={(time.monotonic()-started)/60:.1f} min",flush=True)
            if len(running)==workers and not finished:continue
    return dict(tasks=len(tasks),elapsed_s=time.monotonic()-started,min_free_gb=min_seen,workers=workers,paused_reason=paused_reason,completed_tasks=done)


def main():
    b.dispatch=dispatch
    return b.main()

if __name__=='__main__': raise SystemExit(main())
