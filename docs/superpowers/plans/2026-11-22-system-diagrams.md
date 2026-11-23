# System Diagrams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two figures (system overview, inside the assistant) for the instructor check-in, final report and presentation.

**Architecture:** Each figure is a Mermaid source rendered by the gstack `diagram` skill into a triplet
(`.mmd`, `.excalidraw`, `.svg` + `.png`), as for the 11-01 diagram. A short `README.md` next to them holds captions.

**Tech Stack:** Mermaid, gstack `diagram` skill (offline render), Excalidraw JSON.

**Spec:** `docs/superpowers/specs/2026-11-22-system-diagram-design.md`

## Global Constraints

- Show the **agreed final system**, not today's wiring.
- Diagram 1: no more than ~12 boxes; left to right; readable at report column width.
- Boxes coloured by role, legend: Role 1 data (Can), Role 2 prediction + search (Jack), Role 3 assistant + integration (Yunus), Role 4 users + interface (Arin).
- Arrows carry the contract names `runs.csv`, `study_spec.json`, `prediction.json`, `result.json`.
- No status marks, no tool-by-tool list. Not-yet-wired parts go in a one-line caption only.
- Repo conventions: no em dashes or double-dash separators in text; log GenAI use in `docs/genai-log.md`.
- Files in `docs/figures/`; commit to `main`; push only with the user's OK.

## Review Focus

- A teammate reads their own box: wording must match their part (Role 1 = CalculiX simulation table + finalist main check; Role 2 = PyDiscovery search on the trained model; Role 4 = GUI). Check each box against `README.md` Team agreement and `docs/pydiscovery.md`.
- Printed in greyscale: role colours must differ in lightness too, or the legend is useless.
- Mermaid `<br/>`, `≥`, `→` and `.json` labels must survive the Excalidraw conversion (open the `.excalidraw` text and check the labels).
- Diagram 2 must agree with diagram 1's names (same box names for "assistant", "path picker", "ANSYS agent").

---

### Task 1: System overview figure

**Files:**
- Create: `docs/figures/system-overview.mmd`, `.excalidraw`, `.svg`, `.png`

- [ ] **Step 1:** Write `system-overview.mmd` (`graph LR`) with the spec's Diagram 1 flow: Student → Assistant (asks every missing input, student confirms) → Path picker (states choice, user can override) → Fast path [PyDiscovery search on trained model ← `runs.csv` ← Role 1 simulation table; shortlist (`prediction.json`) → finalist check: CalculiX main, ANSYS cross-check] / Slow path [ANSYS agent: SpaceClaim edits, Mechanical solves, optiSLang search] → Explanation with number check → `result.json` + decision log → GUI → "winner, why, trade-off, values for Discovery" → Student. `study_spec.json` on the assistant → fast path arrow. `classDef` per role plus a legend subgraph.
- [ ] **Step 2:** Render with the `diagram` skill into the triplet in `docs/figures/`.
- [ ] **Step 3: Verify:** `grep -c` finds all four contract names in the `.mmd`; node count ≤ 12 excluding legend; open the PNG and confirm it is legible and the colours differ in greyscale lightness; labels present in the `.excalidraw`.

### Task 2: Inside-the-assistant figure

**Files:**
- Create: `docs/figures/assistant-detail.mmd`, `.excalidraw`, `.svg`, `.png`

- [ ] **Step 1:** Write `assistant-detail.mmd` with the spec's Diagram 2: front layer (follow-ups: supports, loaded faces, force + units, material, safety factor, objective → path rule → `study_spec.json` or hand-off); ANSYS agent loop (describe part, wait for OK → baseline → first look → stop or go deeper → optiSLang ARSM optimize with live progress and Stop → finalize `.scdocx` + `.mechdb`); checks box (reaction = load, mesh refinement, exclusion zones at supports and load, 128K-node guard); output (explanation from `result.json`, numbers checked, decision log). Same box names as Task 1 for shared boxes; all Role 3 colour.
- [ ] **Step 2:** Render the triplet.
- [ ] **Step 3: Verify:** names shared with Task 1 match exactly; PNG legible; labels present in `.excalidraw`.

### Task 3: Captions, log, commit

**Files:**
- Create: `docs/figures/README.md`
- Modify: `docs/genai-log.md` (one row, 2026-11-22)

- [ ] **Step 1:** `README.md`: one caption per figure, plus the one-line "not yet connected" note (fast-path finalists go to ANSYS only today; the GUI runs on saved `result.json` files).
- [ ] **Step 2: Verify:** `grep -n "—\| -- " docs/figures/README.md` finds nothing.
- [ ] **Step 3: Commit** on `main`:

```bash
git add docs/figures/system-overview.* docs/figures/assistant-detail.* docs/figures/README.md docs/genai-log.md docs/superpowers/specs/2026-11-22-system-diagram-design.md docs/superpowers/plans/2026-11-22-system-diagrams.md
git commit -m "docs: system overview and assistant diagrams for check-in and report"
```
