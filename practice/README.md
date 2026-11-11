# Practice set

A repeatable test of the design agent on bracket problems it has to understand by itself.
Design: `docs/superpowers/specs/2026-11-11-practice-set-design.md`.
Plan and file schemas: `docs/superpowers/plans/2026-11-11-practice-set.md`.

## Layout

| Path | What |
| --- | --- |
| `problems/<id>.yaml` | one problem: part, beginner request, hidden facts, named features, answer key |
| `problems/<id>.effects.yaml` | measured feature effects from the key run; graded only when `reviewed: true` |
| `parts/<name>.faces.json` | face list of each part (`describe_part`), used to write keys |
| `parts/external/` | face lists of parts kept out of the repo (git-ignored) |
| `results/<timestamp>-<label>/` | one JSON per problem (run log + checklist) and `summary.md` |

Parts outside the repo (SimJEB, NinSample) are referenced as `external:<path relative to Project2>`;
when the file is missing the problem is skipped.

## Workflow (run from the repo root)

1. Dump the faces of a new part: `..\.venv\Scripts\python.exe -m scripts.dump_faces PART --name NAME`
2. Write `problems/<id>.yaml` (fields: see the plan's "Problem YAML").
3. Build its key (ANSYS, a few minutes): `..\.venv\Scripts\python.exe -m scripts.build_key practice/problems/<id>.yaml`
4. A person checks the labels in `<id>.effects.yaml` and sets `reviewed: true`.
5. Run: `set PYTHONIOENCODING=utf-8 && ..\.venv\Scripts\python.exe -m scripts.run_practice --label before`
6. Compare two runs: `..\.venv\Scripts\python.exe -m scripts.run_practice --compare practice/results/A practice/results/B`
