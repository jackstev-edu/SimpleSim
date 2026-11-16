"""End-to-end demo: open a part, fill gaps, confirm, optimize, save. Prints each step.

    python -m src.pydiscovery.demo                          # synthetic Discovery L-bracket, asks you to confirm
    python -m src.pydiscovery.demo --yes                    # same, confirms for you
    python -m src.pydiscovery.demo --part data/raw/demo_study.dsco      # a real Discovery file: read only
    python -m src.pydiscovery.demo --part data/raw/l_bracket.scdocx --yes   # SpaceClaim: gaps filled with defaults
    python -m src.pydiscovery.demo --engine calculix --yes  # Role 1's solver checks the finalists (needs src/data)
    python -m src.pydiscovery.demo --predictor gp --yes --gui   # GP shortlist, then open the result in the GUI

Outputs go to data/processed/pydiscovery_demo/ (gitignored): study_spec.json, prediction.json and
result.json in the contract format, and the updated part (.dsco copy, or .json of values).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .backends import BACKENDS
from .demo_part import write_demo_part
from .gui import prepare_site, serve, write_run
from .session import PREDICTORS, App, SetupError

DEFAULT_PART = Path("data/raw/l_bracket_demo.dsco")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--part", type=Path, default=DEFAULT_PART)
    ap.add_argument("--engine", default="fitted", choices=sorted(BACKENDS))
    ap.add_argument("--predictor", default="power_law", choices=PREDICTORS, help="gp needs scikit-learn and Role 1's runs.csv")
    ap.add_argument("--runs", type=Path, default=None, help="runs.csv for the GP (default data/processed/formal_v0/runs.csv)")
    ap.add_argument("--min-sf", type=float, default=1.5, help="minimum safety factor")
    ap.add_argument("--max-disp-mm", type=float, default=0.5, help="maximum deflection")
    ap.add_argument("--yes", action="store_true", help="fill gaps with defaults and confirm without asking")
    ap.add_argument("--out", type=Path, default=Path("data/processed/pydiscovery_demo"))
    ap.add_argument("--gui", action="store_true", help="open the result in the team GUI afterwards")
    ap.add_argument("--no-browser", action="store_true", help="with --gui: print the address instead of opening it")
    args = ap.parse_args(argv)

    if args.part == DEFAULT_PART and not args.part.exists():
        write_demo_part(args.part)  # synthetic until a real l_bracket.dsco is saved from Discovery
        print(f"Wrote a synthetic demo part to {args.part}")

    try:
        part = App(args.engine, args.predictor, args.runs).open(args.part)
    except ValueError as exc:
        print(exc)
        return 1
    _step(1, "Open the part and read what it carries")
    print(part.describe())
    if part.problems:
        print("\nStopping here: the emulator can read this file but can't continue with it.")
        return 0

    if part.missing:
        _step(2, "Fill what the file doesn't say")
        print("The agent would ask the user for: " + "; ".join(part.missing))
        if not args.yes:
            print("Run with --yes to fill these with the L-bracket defaults.")
            return 0
        if part.family is None:
            part.use_family("l_bracket_v0")
        if part.support is None:
            part.set_support()
        if part.force_n is None:
            part.set_load(500.0)
        if part.material is None:
            part.set_material("structural_steel")
        print("Filled with defaults: L-bracket baseline, fixed back face, 500 N along -Y, structural steel.")

    _step(3, "Confirm the setup with the user")
    print(part.describe())
    if not args.yes and not _ask("Is this setup right? [y/N] "):
        print("Not confirmed, so nothing was solved.")
        return 0
    part.confirm()

    _step(4, f"Optimize: minimize mass, safety factor >= {args.min_sf:g}, deflection <= {args.max_disp_mm:g} mm")
    try:
        result = part.optimize(constraints=[("safety_factor", ">=", args.min_sf), ("max_disp_mm", "<=", args.max_disp_mm)])
    except (SetupError, ImportError, FileNotFoundError) as exc:
        print(f"Couldn't optimize: {exc}")
        return 1
    print(result.explanation)
    labels = part.family.labels
    for f in [result.baseline] + result.finalists:
        print(f"  {f.design_id:>14}: mass {f.mass_kg:.4f} kg, SF {f.safety_factor:.2f}, deflection {f.max_disp_mm:.3f} mm, "
              f"{'meets limits' if f.feasible else 'fails ' + ', '.join(f.violated)}")
    for w in result.warnings:
        print(f"  Warning: {w}")

    _step(5, "Save")
    args.out.mkdir(parents=True, exist_ok=True)
    write_run(args.out, result.spec, part.last_prediction, result)
    print(f"Contract files: {args.out / 'study_spec.json'}, prediction.json, result.json")
    if result.recommended:
        print("Values to enter in Discovery: " + ", ".join(f"{labels[c]} {v:.2f} mm" for c, v in result.discovery_values.items()))
        suffix = ".dsco" if part.setup.file_type == "discovery" and part.file_names else ".json"
        saved = part.save(args.out / f"{args.part.stem}_optimized{suffix}", result.recommended.design)
        print(f"Updated part: {saved}")
    if args.gui:
        serve(prepare_site(result.spec, part.last_prediction, result), open_browser=not args.no_browser)
    return 0


def _step(n: int, title: str) -> None:
    print(f"\n=== {n}. {title}")


def _ask(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:  # no terminal attached, e.g. inside an agent
        print("\nNo terminal to answer from; rerun with --yes.")
        return False


if __name__ == "__main__":
    raise SystemExit(main())
