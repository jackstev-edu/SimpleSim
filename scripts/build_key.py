r"""Build a practice problem's answer-key effects with one-at-a-time first looks in ANSYS.

  ..\.venv\Scripts\python.exe -m scripts.build_key practice/problems/lbr_clean_auto.yaml
Writes practice/problems/<id>.effects.yaml with reviewed: false. A person checks the labels and
sets reviewed: true; the grader ignores unreviewed keys. Refuses to overwrite a reviewed sidecar
unless --force. Optional key.fixed_edits (same form as evaluate_design edits) are applied to every
key design, e.g. fillets at sharp inside corners so the stress is not a singularity.
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
from src.eval.problems import load_problem, resolve_part

CHUNK = 4  # first_look takes 1-4 features


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("problem")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    path = Path(args.problem)
    p = load_problem(path)
    side = path.with_name(f"{p.id}.effects.yaml")
    if side.is_file() and (yaml.safe_load(side.read_text(encoding="utf-8")) or {}).get("reviewed") and not args.force:
        raise SystemExit(f"{side} is reviewed; pass --force to rebuild it.")
    part = resolve_part(p)
    if part is None:
        raise SystemExit(f"part file missing: {p.part}")
    key, setup = p.key, p.key["setup"]
    wb = Workbench(Path(tempfile.mkdtemp(prefix=f"key_{p.id}_")))
    effects, evidence, warnings = {}, [], []
    try:
        wb.load_part(part)
        wb.call("set_material", {"material": setup["material"], "safety_factor": setup["safety_factor"]})
        wb.call("set_load_case", {k: setup[k] for k in ("fixed_face_ids", "load_face_ids", "force_N")})
        feats = key["features"]
        for i in range(0, len(feats), CHUNK):
            chunk = feats[i:i + CHUNK]
            fl = [{"face_id": f["face_id"], "action": f["action"], "low": f["key_low"], "high": f["key_high"],
                   "label": f.get("label", f["id"])}
                  | {k: f[k] for k in ("other_face_id", "also_face_ids") if f.get(k)} for f in chunk]
            varied = {frozenset((f["face_id"], f.get("other_face_id"))) for f in fl if f["action"] == "fillet"}
            fixed = [e for e in key.get("fixed_edits", [])  # a fillet varied in this chunk is not also fixed
                     if not (e["action"] == "fillet" and frozenset((e["face_id"], e.get("other_face_id"))) in varied)]
            out = json.loads(wb.call("first_look", {"features": fl, "fixed_edits": fixed,
                                                    "objective": key["objective"], "limits": key["limits"]}))
            if not out["reference"]["responses"]:  # no reference -> no effects; labels would all read "negligible"
                raise SystemExit(f"chunk {i // CHUNK + 1}: the reference design failed, no key written: {out.get('errors')}")
            for f, label, s in zip(chunk, label_effects(out, key["objective"], key["limits"]), out["features"]):
                effects[f["id"]] = label
                if all(r.get("change_rel") is None for r in s["responses"].values()):
                    warnings.append(f"{f['id']}: no measured effect (designs failed?)")
            if out.get("failed"):
                warnings.append(f"chunk {i // CHUNK + 1}: {out['failed']} designs failed: {out['errors']}")
            evidence.append(out)
    finally:
        wb.close()
    side.write_text(yaml.safe_dump({"problem": p.id, "reviewed": False, "built": datetime.date.today().isoformat(),
                                    "effects": effects, "warnings": warnings, "evidence": evidence},
                                   sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"{side}\n" + "\n".join(f"  {k}: {v}" for k, v in effects.items()) + ("\n" + "\n".join(warnings) if warnings else ""))


if __name__ == "__main__":
    main()
