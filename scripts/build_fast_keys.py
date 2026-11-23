"""Write model_optimum and sim_optimum into each fast-path benchmark problem (practice/fast/*.yaml).

    python -m scripts.build_fast_keys [--only ID ...] [--n 400000]

The keys must be reviewed by a person before a run is graded against them (practice/fast/README.md).
"""
from __future__ import annotations

import argparse

import yaml

from src.eval.fast_keys import load_fast_problems, model_optimum, sim_optimum


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="problem ids")
    ap.add_argument("--n", type=int, default=400_000, help="designs sampled for the brute-force search")
    args = ap.parse_args()
    for p in load_fast_problems(require_key=False):
        if args.only and p.id not in args.only:
            continue
        data = yaml.safe_load(p.path.read_text(encoding="utf-8"))
        if p.key.get("expect_handoff"):
            print(f"{p.id}: hand-off problem, no optimum")
            continue
        study = p.key["study"]
        data["key"]["model_optimum"] = model_optimum(study, p.family, n=args.n)
        data["key"]["sim_optimum"] = sim_optimum(study, p.family)
        p.path.write_text(yaml.safe_dump(data, sort_keys=False, width=110), encoding="utf-8")
        m, s = data["key"]["model_optimum"], data["key"]["sim_optimum"]
        print(f"{p.id}: model {m['objective_value']:.5g} (median {m['median']['objective_value']:.5g}), "
              f"sim {s['objective_value'] if s['design_id'] else s['reason']}")


if __name__ == "__main__":
    main()
