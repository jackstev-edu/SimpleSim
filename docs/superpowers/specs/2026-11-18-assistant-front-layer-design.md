# Assistant front layer: one conversation, fast path or slow path

Date: 2026-11-18. Owner: Yunus (Role 3). Status: draft for review.
Backlog item 2 (request → `study_spec.json`), redirected on 2026-11-16 and 2026-11-18.

## Intent and why

A beginner brings a bracket and a goal. The assistant asks for every input the request leaves out,
waits for the user's yes, picks how to search, runs the search and explains the winner with real
numbers. The team agreed (Jack, 2026-11-18) on two ways to search:

- **Fast path:** Jack's PyDiscovery tools (`src/pydiscovery`). Known part families only (today the
  L-bracket v0). The predictor scores thousands of designs, finalists get a real ANSYS solve
  (engine `ansys`, user's choice 2026-11-18), the output
  follows the team contracts (`StudySpec`, `Result`) and gives the Discovery values to type in.
- **Slow path:** the existing ANSYS agent (`src/agent`). Any part, real ANSYS solves with mesh
  checks, about 30–40 s per design.

**The assistant picks the path, says which one and why, and the user can override** (Jack's answer).

## Goals

1. One conversation. The user uploads a part, and the front layer opens it with Jack's `open_part`
   (a file read, so no ANSYS start is needed yet).
2. Ask for every missing input before any solve (CLAUDE.md 2026-11-13: no assumed calculation
   inputs): supports, load (force and direction), material, safety factor, objective, and which
   dimensions may change. Offer 1.5 as the safety factor, but use it only after the user confirms.
3. Pick the path from facts, not from the LLM's judgment (see "Path rule"). State it in one plain
   sentence with the reason.
4. Fast path: call Jack's tools; **always pass the constraints explicitly** to `optimize_design`, so
   Jack's silent SF ≥ 2.0 default never applies.
5. Slow path: hand the conversation to the ANSYS agent, carrying over what the user already
   confirmed so they are not asked again.
6. Fast-path result: "why this won" through the existing `explain()` with its number check, and a
   decision log, both saved in one run folder with `study_spec.json` and `result.json`.

## Non-goals

- No changes inside Jack's folder (`src/pydiscovery`, `contracts/`).
- No new part families. Covering other families belongs to Jack's or Can's work.
- No change to the ANSYS agent's behaviour, apart from accepting a carried-over setup.
- No merged prompt (approach 2 was rejected).

## Anti-goals (failure even if it runs)

- A solve or search runs on a value the user never gave or confirmed.
- The path changes silently, or the user cannot see which path a number came from.
- The user answers the same setup question twice because of the hand-off.

## Approaches considered

1. **Front layer, then hand-off (chosen by the user, 2026-11-18).** A new short loop in
   `src/assistant/` runs Jack's tools. For the slow path it hands the chat to the ANSYS agent. Both
   prompts stay small, and each part is testable on its own. The cost: two loops share one chat,
   and the hand-off has to be built carefully.
2. One merged agent (Jack's 11 tools added to the ANSYS agent). One loop, but a long prompt with two
   setup flows, and a risk to the practice-set score.
3. Router only, with the slow path in a separate GUI. Simplest to build, but the override is clumsy
   and the user has to switch windows.

## Path rule

Decided in code from Jack's `Part` (`family`, `problems`, `missing`), so it is testable without an LLM:

| Situation | Path | What the assistant says |
| --- | --- | --- |
| The file matches a family (parameters match), or the user confirms "it is the L-bracket v0" (`use_family`) | fast | "This is the L-bracket the team's predictor knows, so I'll use the fast search: the predictor scores thousands of designs, then the best few are solved in ANSYS (a few minutes)." |
| `Part.problems` is not empty (unknown parameters, several loads, other conditions) | slow | The first problem, in plain words, then "so I'll use the ANSYS search (about 30–40 s per design)." |
| Plain geometry the user says is not an L-bracket | slow | Same as above. |
| A confirmed input falls outside the family: force outside 100–1000 N, a direction other than −Y, a support other than the back face, a material other than the two in the table | slow | Names the input and the family limit. |
| The user asks for ANSYS | slow | Confirms the switch. |
| The user asks for fast on a part the fast path can't model | stays slow | Says why it can't be done. **The override works one way only.** |

The rule can switch to slow at any point up to the search. After a switch it never goes back to
fast in the same session (Claude's call: going back would need a fresh setup on Jack's side for no
gain).

## Conversation flow

1. **Upload.** The front layer opens the part with Jack's `open_part` (fast, no SpaceClaim) and
   shows what it found, including what the file already contains (`sources` = file).
2. **Which bracket.** For a plain geometry file the assistant asks whether it is the L-bracket v0
   (it never decides this on its own). If yes, `use_family`. If no, slow path.
3. **Missing inputs**, one question at a time in plain words. Material follows the novice rule
   (CLAUDE.md 2026-11-13) inside what the fast path offers: it offers the two table materials,
   recommends one for the user's goal, and says that the ANSYS path has more materials. If the user
   wants another material, the path switches to slow.
4. **Safety factor and objective**: asked like every other input. They are stored by the front
   layer, since Jack's setup card has no safety-factor field (requested in the PartSetupCard review).
5. **Setup card** (`describe_setup`, plus the safety factor and objective). It waits for "yes" and
   then calls `confirm_setup`. A guard refuses `optimize_design` until confirmation, safety factor
   and objective are all recorded.
6. **Path announcement**: one sentence, with "say 'use ANSYS' to switch".
7. **Fast search**: `optimize_design(objective, direction, constraints=[safety_factor ≥ SF, plus any
   the user gave], vary)`, then `explain()` on the result, the binding limit, and the Discovery values.
8. **Save**: `save_design` when the user asks, plus the run folder (below).

## Hand-off to the slow path

- **When:** the moment the path rule says slow. If this happens before any questions, nothing carries over.
- **How (Claude's call):** the `AssistantSession` (below) closes the fast path's ANSYS engine if it
  started one (PyMechanical allows one embedded instance per process), loads the same file into
  the ANSYS `Workbench` (SpaceClaim, about 1 min, with a "starting ANSYS" event), resets the ANSYS
  agent, and sends its first turn a generated message: "The user uploaded X. Already confirmed:
  load 500 N, −Y on the end face; material Structural Steel; safety factor 1.5; objective minimise
  mass. Still to do: map these to faces and confirm with the user." From then on the session
  routes every user message to the ANSYS agent.
- **What carries over:** force, direction in words, material name, safety factor, objective and
  any limits. **Faces do not carry over**: Jack's face names ("back face at X = 0") are not ANSYS
  face ids. The ANSYS agent still shows its face choice and asks "is this right?". This is one
  confirmation, not a repeat of every question.
- A material name from Jack's table is mapped to the ANSYS library name (structural_steel →
  Structural Steel, aluminum_6061 → Aluminum Alloy 6061-T6 or the closest library entry; the
  builder checks the library).

## Outputs (fast path)

One folder per run, `outputs/assistant/<timestamp>/`: `study_spec.json` (from the `Result` or
Jack's spec), `result.json`, `decision_log.json/.md` (questions asked, answers, path decision and
reason, override if any), `explanation.txt`.

## Where the code goes (Claude's calls)

- `src/assistant/front.py`: the loop (OpenAI Responses API, counted client and session budget from
  `src/llm.py`), short prompt, Jack's `TOOL_SPECS` wrapped as functions, plus two own tools:
  `record_study_inputs(safety_factor, objective, direction, limits)` and `switch_to_ansys(reason)`.
- `src/assistant/path_rule.py`: the pure path rule above, unit-tested.
- `src/assistant/guard.py` (or inside `front.py`): refuses `optimize_design` until confirmed, and
  builds the explicit constraints.
- `src/assistant/session.py`: `AssistantSession`, **GUI-free**. It owns the front layer, the ANSYS
  agent and the hand-off, and exposes `upload(path)`, `send(text)` (both yield the same event
  dicts the ANSYS agent yields today, plus `{"type": "path", "path": "fast"|"slow", "reason": ...}`),
  `stop()` and `close()`. This is the interface handed to Arin.
- **The interface is Role 4's job (Arin), not ours** (the user, 2026-11-18). We do not edit
  `src/ui/` or `launch_gui.py`. Our existing Gradio `app.py`, already our development harness for
  the ANSYS agent, switches to `AssistantSession` so we can test the flow by hand. We tell Arin and
  Jack the interface when it is ready.
- Engine: `PyDiscoveryTools(backend="ansys")`, so every finalist gets a real ANSYS solve (user,
  2026-11-18). `n_verify` stays at Jack's default of 5 (about 3–4 min including the SpaceClaim start).
- Model: same `MODEL`/`SIMPLESIM_MODE` as today (`gpt-6-luna` in every mode for now).

## Testing

- Unit: the path rule for every table row; the guard refuses `optimize_design` without SF,
  objective or confirmation; the constraints passed always contain the user's SF; the hand-off
  message contains every confirmed value; scripted-LLM loop tests (recorded replies, as in
  backlog item 5).
- Live, one cheap check with ANSYS (close other programs first): one L-bracket practice problem on
  the fast path and one saddle problem to see the hand-off (`gpt-6-luna`, under the $0.20 cap).
- Whether the practice grader is adapted to the front layer is left for the plan.

## Constraints

- Budget: the $20 course key, the per-session cap from `session_budget()`, and the spend ledger.
- Jack's API as on `origin/main` (6cca515): `Yunus` is 5 commits behind, so the first step merges
  `origin/main` into `Yunus`. After that, `optimize_design` already returns `study_spec` and
  `prediction` next to `result`, and `PyDiscoveryTools(backend, predictor, runs)` takes the
  predictor choice. If PartSetupCard lands, the safety-factor field moves from the front layer to the card.
- The team GUI (`src/ui/index.html` + `launch_gui.py`, main 02d026d) now has a form-based
  PyDiscovery tab that calls the same 11 tools. Its Agent chat replays fixtures and is not a live LLM.
- Both paths need ANSYS (fast path for the finalists). Unit tests must still pass without ANSYS
  (SimpleSim divergence rule): they use Jack's `formula` engine or a fake backend.
- One embedded Mechanical per process: the fast path's `AnsysBackend` and the slow path's
  `Workbench` must never be open at the same time.

## Left to the builder

Prompt wording; event format shared with the existing GUI renderer; exact library names for
material mapping; how the run folder is named.
