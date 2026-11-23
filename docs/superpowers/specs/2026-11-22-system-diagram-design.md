# System diagrams (backlog item 5, part 1): short design

Date: 2026-11-22. Owner: Yunus (Role 3). Status: approved by the user 2026-11-22.

## Intent
Two figures for the check-in with the instructor, the final report and possibly the final presentation. A reader who
was not in the team must see how a student's request becomes a verified design, and where the decisions are made
(the course grades how clear the decisions behind the designs are). They replace the 11-01 diagram
(`Project2/presentation/simplesim-architecture.*`), which shows only the ANSYS slow path and the old Claude model;
that file stays as it is.

## Goals / non-goals
- Goal: show the **agreed final system** (user decision 2026-11-22), not today's wiring.
- Goal: decision points visible: inputs confirmed by the student, path picker (states its choice, user can override),
  verification of finalists, number check on the explanation.
- Non-goal: build/stand-in status marks; a tool-by-tool list; code module names beyond the role owners.
- Anti-goal: anything a teammate would read as wrong about their own part.

## Approaches (picked: B)
- A. One overview only. Simplest, but the agent (Role 3, the report section Yunus writes) shrinks to two boxes.
- **B. Overview + a zoom on the assistant.** Covers the report's agent section and "how does the agent decide?".
  Picked by the user.
- C. B plus a status-marked check-in copy. Extra version that goes stale fastest; not chosen.

## Diagram 1: system overview
Student (part + goal) → **assistant** asks for every missing input; the student confirms → **path picker**:
- **Fast path** (known families: L-bracket v0, gusseted v1): Role 2's PyDiscovery search on the model trained from
  Role 1's simulation table → shortlist → finalists re-checked by **CalculiX (main check, Role 1)** and **ANSYS
  (cross-check, Role 3)**.
- **Slow path** (any other part): the ANSYS agent (SpaceClaim edits, Mechanical solves, optiSLang search).
Both paths → **explanation with number check** → `result.json` + **decision log** → **GUI (Role 4)** → winning design,
why it won, trade-off, values to enter in Ansys Discovery.
Arrows carry the contract names (`runs.csv`, `study_spec.json`, `prediction.json`, `result.json`). Boxes coloured by
role, with a legend: Role 1 data (Can), Role 2 prediction + search (Jack), Role 3 assistant + integration (Yunus),
Role 4 users + interface (Arin).

## Diagram 2: inside the assistant
- Front layer: ask follow-ups (supports, loaded faces, force + units, material, safety factor, objective) → path
  rule → `StudySpec` for the fast path, or hand-off to the ANSYS agent.
- ANSYS agent loop: describe part and wait for OK → baseline → first look (which features matter) → stop or go
  deeper → optiSLang ARSM optimize (live progress, Stop) → finalize (part `.scdocx`, analysis `.mechdb`).
- Checks on every result: reaction = load, mesh refinement, exclusion zones at supports and load, 128K-node guard.
- Output side: explanation from `result.json`, numbers checked, decision log.

## Decided
- Final system, not today's (user). Approach B (user). Audience: instructor, report, presentation (user).

## Claude's calls (correct if wrong)
- Format: Mermaid source + Excalidraw, rendered to SVG and PNG, as for the 11-01 diagram.
- Location: `SimpleSim/docs/figures/system-overview.*` and `assistant-detail.*`; committed to `main` (minor change,
  no teammate code touched); push only with the user's OK.
- Not-yet-wired parts (fast-path finalists still go to ANSYS only; GUI fed from fixtures) go in a one-line caption
  for the report and check-in, not in the figure.
- Left-to-right flow, readable when printed at report column width; no more than ~12 boxes in diagram 1.
