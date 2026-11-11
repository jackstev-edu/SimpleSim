r"""Write a part's face list (describe_part output) for writing practice-set keys.

  ..\.venv\Scripts\python.exe -m scripts.dump_faces examples/l_bracket.scdocx --name l_bracket
  ..\.venv\Scripts\python.exe -m scripts.dump_faces ..\external\ninsample\NinSample.STEP --name ninsample --external
--external writes to practice/parts/external/ (git-ignored), for parts kept out of the public repo.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from src.agent.tools import Workbench
from src.eval.problems import REPO


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("part")
    ap.add_argument("--name", required=True)
    ap.add_argument("--external", action="store_true")
    args = ap.parse_args()
    folder = REPO / "practice" / "parts" / ("external" if args.external else "")
    folder.mkdir(parents=True, exist_ok=True)
    wb = Workbench(Path(tempfile.mkdtemp(prefix="dump_faces_")))
    try:
        desc = wb.load_part(Path(args.part).resolve())
    finally:
        wb.close()
    path = folder / f"{args.name}.faces.json"
    path.write_text(json.dumps(desc, indent=1, default=str), encoding="utf-8")
    print(f"{path}: {len(desc['faces'])} faces, {len(desc['inside_corners'])} inside corners, "
          f"volume {desc['volume_mm3']} mm3, min wall {desc['min_wall_mm']} mm")


if __name__ == "__main__":
    main()
