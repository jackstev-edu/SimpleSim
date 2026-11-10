# GenAI Use Log

Record each meaningful use of a GenAI tool on this project.

| Date | Who | Tool | What it was used for | What we kept or changed |
| --- | --- | --- | --- | --- |
| 2026-11-09 | Jack Stevens | Claude (Fable 5.1) | Drafted the `contracts/` package: four Pydantic hand-off models, beam-formula fakes, example files, contract tests, CI workflow, README and CLAUDE.md. Based on the roles doc and the team's MVPs. | Team reviews the v0 bracket family and field names; Jack keeps the GP and AutoGluon plan. |
| 2026-11-10 | Jack Stevens | Claude Code (Fable 5.2) | Built the Discovery-style demo front end in `src/ui/`: `index.html`, `make_fixtures.py` with the infeasible and out-of-range fixtures, `tests/test_ui_fixtures.py` and the UI README. Checked with headless-browser screenshots of all three fixtures. | Layout follows Discovery's arrangement with our own icons and palette; every number comes from the contract JSON. Arin reviews before the live agent replaces the fixtures. |
