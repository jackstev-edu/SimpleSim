r"""Measure the features an agent proposed that are in no answer key ("unmatched"), with ANSYS.

  ..\.venv\Scripts\python.exe -m scripts.check_extras practice/results/<run> lbr_clean_auto
Collects the unmatched features of every problem on the same part as the named problem, runs them
through first looks with that problem's setup (and key.fixed_edits), and writes
practice/extras/<problem id>.yaml with one label per feature and reviewed: false. A person checks
the labels; an approved feature joins the keys of that part with optional: true (graded by its
label when proposed, not required for recall). One part per process (one embedded Mechanical).
"""
from __future__ import annotations

import argparse
import datetime
import json
import tempfile
from pathlib import Path

import yaml

from src.agent.tools import Workbench
from src.eval.keys import label_effects
from src.eval.problems import REPO, load_problems, resolve_part

CHUNK = 4  # first_look takes 1-4 features


def collect(results: Path, problem_ids: list[str]) -> list[dict]:
    """Unmatched features over the given problems, one per (action, faces); the narrowest range wins."""
    found: dict[tuple, dict] = {}
    for pid in problem_ids:
        saved = Path(results) / f"{pid}.json"
        if not saved.is_file():
            continue
        for f in json.loads(saved.read_text(encoding="utf-8"))["checklist"].get("unmatched_features", []):
            if f.get("low") is None or f.get("high") is None:
                continue
            k = (f["action"], f["face_id"], f.get("other_face_id"), tuple(sorted(f.get("also_face_ids") or [])))
            old = found.get(k)
            if old is None or f["high"] - f["low"] < old["high"] - old["low"]:
                found[k] = dict(f, seen_in=(old["seen_in"] if old else []))
            found[k]["seen_in"].append(pid)
    return list(found.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("problem")
    args = ap.parse_args()
    problems = load_problems()
    p = next(q for q in problems if q.id == args.problem)
    feats = collect(Path(args.results), [q.id for q in problems if q.part == p.part])
    if not feats:
        raise SystemExit("no unmatched features for this part")
    part = resolve_part(p)
    if part is None:
        raise SystemExit(f"part file missing: {p.part}")
    key, setup = p.key, p.key["setup"]
    wb = Workbench(Path(tempfile.mkdtemp(prefix=f"extras_{p.id}_")))
    evidence = []
    try:
        wb.load_part(part)
        wb.call("set_material", {"material": setup["material"], "safety_factor": setup["safety_factor"]})
        wb.call("set_load_case", {k: setup[k] for k in ("fixed_face_ids", "load_face_ids", "force_N")})
        for i in range(0, len(feats), CHUNK):
            chunk = feats[i:i + CHUNK]
            fl = [{k: f[k] for k in ("face_id", "action", "low", "high")} | {"label": f.get("label") or f["face_id"]}
                  | {k: f[k] for k in ("other_face_id", "also_face_ids") if f.get(k)} for f in chunk]
            out = json.loads(wb.call("first_look", {"features": fl, "fixed_edits": key.get("fixed_edits", []),
                                                    "objective": key["objective"], "limits": key["limits"]}))
            if not out["reference"]["responses"]:
                raise SystemExit(f"the reference design failed, nothing written: {out.get('errors')}")
            for f, label, s in zip(chunk, label_effects(out, key["objective"], key["limits"]), out["features"]):
                measured = any(r.get("change_rel") is not None for r in s["responses"].values())
                f["effect"] = label if measured else "not_measured"
            evidence.append(out)
    finally:
        wb.close()
    side = REPO / "practice" / "extras" / f"{p.id}.yaml"
    side.parent.mkdir(exist_ok=True)
    side.write_text(yaml.safe_dump({"problem": p.id, "results": Path(args.results).name,
                                    "built": datetime.date.today().isoformat(), "reviewed": False,
                                    "features": feats, "evidence": evidence}, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    print(side)
    for f in feats:
        print(f"  {f['effect']:16} {f['action']} {f['face_id']} [{f['low']}, {f['high']}]  {f.get('label')}")


if __name__ == "__main__":
    main()
