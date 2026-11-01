"""Smoke test of first_look and the user stop (real ANSYS + optiSLang, no LLM).

Run from the repo root:  python -u -m scripts.smoke_first_look
(a) first_look on the L-bracket: base offset [-0.5, 1.5] and fillet radius [2, 6], 5 solves.
(b) optimize stopped by the user after the 3rd design.
(c) optimize stopped during its first design (Review Focus 3).
After each optimize, no optiSLang process may be left.
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from src.agent.tools import Workbench

ROOT = Path(__file__).resolve().parents[1]
LIMITS = [{"response": "safety_factor", "op": ">=", "value": 1.6667}]
fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(("PASS: " if ok else "FAIL: ") + msg, flush=True)
    if not ok:
        fails.append(msg)


def optislang_procs() -> list[str]:
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
    return [ln.strip() for ln in out.splitlines() if "optislang" in ln.lower()]


def wait_gone(timeout=15.0) -> list[str]:
    end = time.time() + timeout
    procs = optislang_procs()
    while procs and time.time() < end:
        time.sleep(1)
        procs = optislang_procs()
    return procs


def run_optimize(wb, args, stop_after):
    """Run optimize in a thread; call request_stop after `stop_after` progress events (None = never)."""
    while not wb.progress.empty():  # drop events left over from earlier tool calls
        wb.progress.get_nowait()
    box = {}
    t = threading.Thread(target=lambda: box.update(out=json.loads(wb.call("optimize", args))), daemon=True)
    t0 = time.time()
    t.start()
    seen = 0
    while t.is_alive():
        try:
            ev = wb.progress.get(timeout=1)
        except Exception:  # queue.Empty
            continue
        seen += 1
        print(f"  progress {seen}: {ev}", flush=True)
        if stop_after and seen == stop_after:
            print("  -> request_stop()", flush=True)
            wb.request_stop()
    t.join()
    print(f"  optimize returned after {time.time() - t0:.0f} s, {seen} progress events", flush=True)
    return box["out"]


def main():
    wb = Workbench(ROOT / "outputs" / "smoke_first_look")
    try:
        part = wb.load_part(ROOT / "examples" / "l_bracket.scdocx")
        a, b = part["inside_corners"][0]["face_ids"]
        faces = part["faces"]
        fixed = next(f["id"] for f in faces if f["bbox_max_mm"][0] == 0)
        load = next(f["id"] for f in faces if f["bbox_min_mm"][0] == 80)
        base_top = next(f["id"] for f in faces if f.get("normal") == [0, 1, 0] and f["bbox_max_mm"][0] > 70)
        wb.call("set_load_case", {"fixed_face_ids": [fixed], "load_face_ids": [load], "force_N": [0, -500, 0]})

        print("== (a) first_look ==", flush=True)
        t0 = time.time()
        out = json.loads(wb.call("first_look", {
            "features": [
                {"face_id": base_top, "action": "offset", "low": -0.5, "high": 1.5, "label": "base thickness offset"},
                {"face_id": a, "other_face_id": b, "action": "fillet", "low": 2, "high": 6, "label": "fillet radius"}],
            "objective": "mass_kg", "limits": LIMITS}))
        print(json.dumps(out, indent=1), flush=True)
        print(f"  (a) took {time.time() - t0:.0f} s", flush=True)
        errors = []
        while not wb.progress.empty():  # show every design, including any that failed
            ev = wb.progress.get_nowait()
            print(f"  progress: {ev}", flush=True)
            if "error" in ev:
                errors.append(f"{ev['design']}: {ev['error']}")
        check(not errors, f"(a) no failed designs {errors}")
        check(out.get("solves") == 5, f"(a) solves == 5 (got {out.get('solves')})")
        check(out.get("stopped") == "done", f"(a) stopped == done (got {out.get('stopped')})")
        print("  ranking:", out.get("ranking"), "\n  best_feasible:", out.get("best_feasible"), flush=True)

        fixed_fillet = [{"face_id": a, "other_face_id": b, "action": "fillet", "value_mm": 4}]
        base = {"variables": [{"face_id": base_top, "action": "offset", "lower": 0.0, "upper": 2.0, "start": 1.0}],
                "fixed_edits": fixed_fillet, "objective": "mass_kg", "limits": LIMITS, "budget": 10}

        print("== (b) optimize, stop after 3rd design ==", flush=True)
        out = run_optimize(wb, {**base, "prefix": "stopb"}, 3)
        print("  stopped:", out["stopped"], "| designs:", len(out["designs"]), flush=True)
        check(out["stopped"] == "user", f"(b) stopped == user (got {out['stopped']})")
        check(len(out["designs"]) in (3, 4), f"(b) 3 or 4 designs stored (got {len(out['designs'])})")
        left = wait_gone()
        check(not left, f"(b) no optislang process left {left}")

        # (c) Review Focus 3: Stop pressed during the very first design. A stop pressed *before*
        # the tool starts is cleared on purpose (Review Focus 1), so fire it inside design 1.
        print("== (c) optimize, stop during the first design ==", flush=True)
        orig_evaluate = wb._evaluate

        def evaluate_and_stop(*a, **k):
            if not wb.stop_event.is_set():
                print("  -> request_stop() inside design 1", flush=True)
                wb.request_stop()
            return orig_evaluate(*a, **k)

        wb._evaluate = evaluate_and_stop
        try:
            out = run_optimize(wb, {**base, "prefix": "stopc"}, None)
        finally:
            wb._evaluate = orig_evaluate
        print("  stopped:", out["stopped"], "| designs:", len(out["designs"]), flush=True)
        check(out["stopped"] == "user", f"(c) stopped == user (got {out['stopped']})")
        check(len(out["designs"]) == 1, f"(c) exactly 1 design stored (got {len(out['designs'])})")
        left = wait_gone()
        check(not left, f"(c) no optislang process left {left}")
    finally:
        wb.close()
    print("FAILED: " + "; ".join(fails) if fails else "ALL PASS", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
