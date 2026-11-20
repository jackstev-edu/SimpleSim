"""Merge shard output folders of the v1 pipeline into one dataset folder.

    python -m src.data.gusset_v1.merge --output merged shards/s0 shards/s1 ...

Every shard must carry the same frozen sample_plan.csv. The merge refuses duplicate design IDs,
reports missing ones, then rewrites the tables in plan order and recomputes the QA and summary
with the same code the pipeline uses. Exit code 1 when IDs are duplicated or missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .pipeline import _read_rows, write_outputs
from .plan import read_plan, write_plan


def merge_shards(output_directory: str | Path, shard_directories: Sequence[str | Path]) -> dict[str, object]:
    output_dir = Path(output_directory)
    shards = [Path(p) for p in shard_directories]
    if not shards:
        raise ValueError("Give at least one shard folder.")
    plans = [read_plan(s / "sample_plan.csv") for s in shards]
    if any(p != plans[0] for p in plans[1:]):
        raise ValueError("The shards do not share one sample_plan.csv; they come from different plans.")
    plan = plans[0]
    plan_ids = [str(r["design_id"]) for r in plan]

    runs: dict[str, dict[str, object]] = {}
    diagnostics: dict[str, dict[str, object]] = {}
    model_rows: dict[str, dict[str, object]] = {}
    attempts: list[dict[str, object]] = []
    duplicates: set[str] = set()
    for shard in shards:
        for row in _read_rows(shard / "runs.csv"):
            design_id = str(row["design_id"])
            if design_id in runs:
                duplicates.add(design_id)
            runs[design_id] = dict(row)
        diagnostics.update({str(r["design_id"]): dict(r) for r in _read_rows(shard / "run_diagnostics.csv")})
        model_rows.update({str(r["design_id"]): dict(r) for r in _read_rows(shard / "model_dataset.csv")})
        attempts.extend(dict(r) for r in _read_rows(shard / "failure_attempts.csv"))
    unknown = set(runs) - set(plan_ids)
    missing = [i for i in plan_ids if i not in runs]
    if unknown:
        raise ValueError("Merged runs contain design IDs outside the plan: " + ", ".join(sorted(unknown)[:5]))

    output_dir.mkdir(parents=True, exist_ok=True)
    write_plan(output_dir / "sample_plan.csv", plan)
    all_passed, unresolved = write_outputs(output_dir, plan, runs, diagnostics, model_rows, attempts, attempted_this_run=0)
    report = {"shards": [str(s) for s in shards], "merged_runs": len(runs), "planned": len(plan_ids), "duplicates": sorted(duplicates),
              "missing": missing, "unresolved_failures": unresolved, "all_quality_checks_passed": all_passed,
              "ok": not duplicates and not missing}
    print(f"Merged {len(runs)}/{len(plan_ids)} runs from {len(shards)} shards into {output_dir}; duplicates={len(duplicates)}, "
          f"missing={len(missing)}, unresolved failures={unresolved}, all checks passed={all_passed}.")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge v1 shard folders into one dataset folder.")
    parser.add_argument("--output", required=True)
    parser.add_argument("shards", nargs="+")
    args = parser.parse_args(argv)
    return 0 if merge_shards(args.output, args.shards)["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
