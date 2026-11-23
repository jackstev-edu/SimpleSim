"""Exclusion-zone check in real ANSYS (no LLM): baseline + check_mesh on three practice parts.

Run from the repo root:  ..\\.venv\\Scripts\\python.exe -u -m scripts.smoke_zones [lbr sad jeb]
Pass criteria (exclusion-zone plan, Task 5):
  L-bracket: nothing excluded (peak at the fillet), stress_for_limit_MPa == max_stress_MPa.
  Saddle: excluded_at == "support"; check_mesh change below the raw-peak +34 % of 2026-11-01.
  SimJEB 148: excluded_at == "support"; stress_for_limit_MPa within about 15 % of 1,337 MPa.
  All: the zone step takes under 10 s.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

from src.agent.tools import Workbench
from src.eval.problems import load_problem, resolve_part
from src.sim import mechanical

ROOT = Path(__file__).resolve().parents[1]
PROBLEMS = ROOT / "practice" / "problems"
# The L-bracket key has no fixed edits; a 2.5 mm fillet at the inside corner moves its peak off the sharp edge.
LBR_FILLET = [{"face_id": "0:39", "other_face_id": "0:42", "action": "fillet", "value_mm": 2.5}]
PARTS = {"lbr": ("lbr_clean_auto", LBR_FILLET), "sad": ("sad_clean_auto", []), "jeb": ("jeb_named", [])}
FIELDS = ("max_stress_MPa", "max_stress_at_mm", "stress_for_limit_MPa", "stress_for_limit_at_mm",
          "excluded_peak_MPa", "excluded_peak_at_mm", "excluded_at", "zone_mm", "zone_settled", "nodes", "solve_s")

ZONE_TIMES: list[float] = []
_zone_from_mesh = mechanical._zone_from_mesh


def _timed_zone(*a, **k):
    # wall time of the zone step alone (coordinate lookup + zone search)
    t0 = time.time()
    try:
        return _zone_from_mesh(*a, **k)
    finally:
        ZONE_TIMES.append(round(time.time() - t0, 1))


mechanical._zone_from_mesh = _timed_zone


def run(key: str) -> dict:
    pid, edits = PARTS[key]
    p = load_problem(PROBLEMS / f"{pid}.yaml")
    setup = p.key["setup"]
    part = resolve_part(p)
    if part is None:
        raise SystemExit(f"part file missing: {p.part}")
    wb = Workbench(ROOT / "outputs" / f"smoke_zones_{key}")
    out = {"part": key, "problem": pid}
    try:
        t0 = time.time()
        desc = wb.load_part(part)
        print(f"[{key}] load_part {time.time() - t0:.0f} s, min_wall {desc['min_wall_mm']} mm, "
              f"element {desc['default_element_size_mm']} mm", flush=True)
        wb.call("set_material", {"material": setup["material"], "safety_factor": setup["safety_factor"]})
        wb.call("set_load_case", {k: setup[k] for k in ("fixed_face_ids", "load_face_ids", "force_N")})
        ZONE_TIMES.clear()
        t0 = time.time()
        base = json.loads(wb.call("evaluate_design", {"name": "baseline", "edits": edits}))
        print(f"[{key}] baseline {time.time() - t0:.0f} s", flush=True)
        res = wb.designs["baseline"]["results"]
        out["baseline"] = {k: res.get(k) for k in FIELDS} | {"safety_factor": base.get("safety_factor"),
                                                            "messages": res.get("messages")}
        t0 = time.time()
        chk = json.loads(wb.call("check_mesh", {"name": "baseline"}))
        print(f"[{key}] check_mesh {time.time() - t0:.0f} s", flush=True)
        out["check_mesh"] = {k: chk[k] for k in ("stress_change", "converged", "coarse", "fine")}
        out["zone_step_s"] = list(ZONE_TIMES)  # baseline, coarse, fine
    except Exception as e:  # noqa: BLE001 - report and go on to the next part
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        wb.close()
    print(json.dumps(out, indent=1, default=str), flush=True)
    return out


def verdict(r: dict) -> list[str]:
    if "error" in r:
        return [f"FAIL {r['part']}: {r['error']}"]
    b, lines = r["baseline"], []
    ok = lambda c, msg: lines.append(("PASS " if c else "FAIL ") + f"{r['part']}: {msg}")  # noqa: E731
    if r["part"] == "lbr":
        ok(b["excluded_at"] is None and b["stress_for_limit_MPa"] == b["max_stress_MPa"],
           f"nothing excluded (excluded_at {b['excluded_at']}, limit {b['stress_for_limit_MPa']}, raw {b['max_stress_MPa']})")
    elif r["part"] == "sad":
        ok(b["excluded_at"] == "support", f"excluded_at {b['excluded_at']}")
        ok(abs(r["check_mesh"]["stress_change"]) < 0.34, f"check_mesh change {r['check_mesh']['stress_change']:+.1%} vs raw +34 %")
    elif r["part"] == "jeb":
        ok(b["excluded_at"] == "support", f"excluded_at {b['excluded_at']}")
        s = b["stress_for_limit_MPa"]
        ok(abs(s / 1337 - 1) <= 0.15, f"stress_for_limit {s:.0f} MPa vs SimJEB 1337 ({s / 1337 - 1:+.1%})")
    ok(max(r["zone_step_s"]) < 10, f"zone step {r['zone_step_s']} s (< 10 s)")
    return lines


MARK = "SMOKE_ZONES_RESULT "


def main(keys: list[str]) -> None:
    if len(keys) == 1:  # child: one part per process (one embedded Mechanical per process)
        print(MARK + json.dumps(run(keys[0]), default=str), flush=True)
        return
    results = []
    for k in keys:
        proc = subprocess.run([sys.executable, "-u", "-m", "scripts.smoke_zones", k], cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        print(proc.stdout, flush=True)
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(MARK)), None)
        results.append(json.loads(line[len(MARK):]) if line else {"part": k, "error": f"exit {proc.returncode}, no result"})
    print("\n".join(line for r in results for line in verdict(r)), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or list(PARTS))
