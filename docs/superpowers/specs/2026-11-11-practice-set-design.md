# Practice set and grader for the design agent (Role 3, backlog item 1)

Date: 2026-11-11. Branch: `Yunus`. Status: draft for the user's review.

## Why

The agent (`src/agent/`, Claude + PyAnsys tools) is meant to work on brackets it has never seen.
Nobody can tell today how often it gets a part wrong. The user expects most mistakes in
**proposing the design features to change**, caused by **misreading the geometry**. Earlier runs
back this up (CLAUDE.md): offset ranges proposed as absolute thicknesses on the L-bracket; the
wrong flange ranked first on the saddle bracket; support singularities at bolt holes not
understood.

The practice set is a repeatable test. It shows *which skill* failed on *which part*. We rerun it
after every prompt or tool change to see whether the agent got better or worse. It checks the same
things a careful TA would check: study setup, boundary conditions, force and units, material,
safety factor, and the features chosen.

## Goals

1. Grade two jobs separately:
   - **Setup check**: fixed faces, load faces, force (magnitude, direction, units), material,
     safety factor stated. It also checks whether the agent **asked** when something was missing
     or wrong, instead of inventing a value.
   - **Feature proposal**: which features the agent wants to vary, and over which ranges.
2. Cover **several bracket shapes**, not only the team L-bracket.
3. Give a **per-item checklist score** for each problem, plus a summary across problems. A single
   number alone is not enough.
4. Keep problem files **independent of this agent**. A problem is a part, a request, hidden facts
   and an answer key. Any future assistant (e.g. the team one on `contracts/`) could be graded
   with the same files.

## Scope: measure, then improve (decided by the user, 2026-11-11)

1. Build the practice set and grader.
2. Run **today's agent** once and save it as the **"before"** score. Some checks are expected to
   fail, because the current SYSTEM prompt has none of the behaviours below.
3. Update the agent's instructions (SYSTEM prompt, and tool descriptions where needed) to the
   behaviours below.
4. Run again and save it as the **"after"** score. The before/after table goes into the report.

## Non-goals

- Finding the true best design for each part by brute force. This is ruled out because there are
  too many possible brackets.
- Grading parts nobody has labelled. Those are handled at run time; see "Agent behaviour".
- Grading the explanation text or the decision log (backlog items 3 and 4).
- Running in CI. Grading needs ANSYS on the user's laptop. A CI version with recorded replies is
  backlog item 5.

## Anti-goals (would make this a failure even if it runs)

- A grader that rewards the agent for **overriding the user**. Features the user names are
  binding, and so is the setup the user describes, unless it is physically wrong. In that case the
  agent must flag it and ask; it must not fix it silently.
- An answer key that nobody checked. Every key is reviewed by a person before it is used.
- A score that hides which item failed.

## Agent behaviour being graded (decided by the user, 2026-11-11)

- **The user names features.** The agent tests exactly those, with no silent additions or drops.
  It may add **clearly labelled suggestions** ("changing X might also help, because ...") and runs
  them only after the user says OK.
- **The user names nothing.** The agent recommends the features itself.
- **Unseen parts, at run time.**
  1. The agent states what it sees (holes, walls, corners, likely fixed and load faces) and waits
     for the user to confirm before solving.
  2. It lets the first-look run check its suggestions, and says so when a suggested feature does
     nothing.
  3. On a really complex part it says the part **looks complex** and asks the user to name the
     features. It does not say "I have never seen a part like this".

## Parts (first version)

| Part | File | Why it is in the set |
| --- | --- | --- |
| L-bracket | `examples/l_bracket.scdocx` (in repo) | The team's v0 part. Can's 200 CalculiX runs exist for it. |
| Saddle bracket | `examples/pyansys/saddle_bracket.scdoc` (in repo, MIT) | Angled faces. Support singularity at the bolt holes. |
| SimJEB #148 | `Project2/external/simjeb/148.stp` (outside repo, non-commercial) | STEP assembly, 481 faces, split holes. Tests the "looks complex" behaviour. Skipped with a notice when the file is missing. |
| NinSample (the user's own part, 4 split mounting holes) | `Project2/external/ninsample/NinSample.STEP` (outside repo until the user says it may go in the public repo) | The part that exposed the split-hole problem. |

## Problems (first version: about 12)

Each part gets 2–4 problems drawn from these kinds:

| Kind | Example | Expected agent behaviour |
| --- | --- | --- |
| Clean, no features named | "Make it lighter, it must hold 500 N down on the end." | Sets up correctly and **recommends** features. |
| Clean, features named | "... only change the base thickness and the fillet." | Tests **exactly** those. Any suggestion is labelled. |
| Missing information | No force given. | **Asks** before calling `set_load_case`. |
| Wrong or odd units | "holds 50 kg" | Converts to 490.5 N and **says so**, or asks. |
| Bad requirement | Safety factor 0.8 | Flags it and asks. Does not silently use 1.5 or 0.8. |
| Complex part | SimJEB, no features named | Says the part looks complex and asks the user to name features (or proposes few, with reasons). |

## Problem file (one YAML file per problem, `practice/problems/<id>.yaml`)

- `id`, `part` (path; repo-relative, or an `external:` path that may be missing), `kind`.
- `request`: the user's first message, written as a beginner would type it.
- `hidden_facts`: the full truth the simulated user knows, e.g. fixed faces in plain words,
  force, material, safety factor and allowable stress. These are revealed only when the agent
  asks.
- `user_named_features`: list of feature ids, or empty.
- `key`, written once and checked by a person (see "Answer key"):
  - `setup`: fixed face ids, load face ids, force vector in N, material, required safety factor.
    Where a hole is split, every half face counts as correct.
  - `must_ask`: items the agent must ask about before acting (e.g. `force`).
  - `must_flag`: items the agent must point out (e.g. `safety_factor_below_1`, `unit_conversion`).
  - `features`: every candidate feature on the part, as face id + action, plus a human label.
    Each is marked with the measured effect from the key run: `controls_limit`, `moves_objective`
    or `negligible`.
  - `range_rules`: offset ranges must include 0; radius ranges must be > 0 and fit the geometry.

## How a problem is run

1. **Simulated user.** A small LLM (Haiku) gets the `hidden_facts` and the rules: answer only
   what is asked, never volunteer the missing fact, approve a proposal with "OK, go ahead", and
   reject features outside `user_named_features` when there are some.
2. **Agent run.** The real agent and its real tools run against it: SpaceClaim to read the part
   and Mechanical for the baseline and mesh check. The cap is 8 user turns.
3. **Grade point.** The harness **intercepts** the first call to `first_look` or `optimize`. It
   records the arguments and does not run the search. The proposal is therefore graded from
   structured tool arguments, not from free text.
4. **Recorded for grading:** every tool call and its arguments (especially `set_load_case`,
   `set_material`, `first_look`/`optimize`), every agent message, and the order of events. The
   order is needed to tell whether the agent asked before acting.

## Grading (no ANSYS needed once the run log exists)

The grader reads the run log and the key and outputs a checklist per problem:

- **Setup**: fixed faces correct; load faces correct; force magnitude and direction correct
  (±1 %); material correct; the safety factor was stated in a message.
- **Asking and flagging**: each `must_ask` item was asked before the matching tool call; each
  `must_flag` item was mentioned.
- **Obedience** (only when the user named features): the proposal holds exactly the named
  features; anything extra is labelled as a suggestion and was not run without an OK.
- **Feature quality** (when the agent chose): every `controls_limit` feature is proposed (recall);
  `negligible` features are counted (precision). Ranges follow `range_rules`.
- **Complex part**: the agent said the part looks complex, or asked the user to name features.

Text checks (safety factor stated, "looks complex", unit conversion mentioned) use simple keyword
and number matching in v1.

Output: `practice/results/<timestamp>/` holds one JSON file per problem plus a `summary.md`
table, with problems as rows and checklist items as columns, ✓/✗.

## Answer key (built once per problem)

1. A person writes `setup`, `must_ask`, `must_flag` and the feature list with labels.
2. A script runs **one first-look** in ANSYS over all candidate features under the key's load case.
   It marks each feature `controls_limit` / `moves_objective` / `negligible` from `limit_ranking`
   and the effects. For the L-bracket, Can's CalculiX data can be used as a cross-check.
3. The user reviews the key before the problem is used.

## Constraints

- ANSYS Student 2026 R1 on the user's laptop. 128K-node limit; the existing `MeshTooLarge` guard
  applies.
- Cost: debug model (Haiku) for both the agent and the simulated user. Rough estimate: about $0.01
  per problem, well under the $5 credit. Sonnet only for a final demo run.
- Runtime: about 3–6 min per problem (part load, baseline, mesh check). The full set takes about
  an hour. The search itself is never run during grading.
- SimJEB files stay outside the repo.

## Decided by the user

Everything under "Agent behaviour being graded". Other bracket shapes are included. The agent
under test is the user's ANSYS agent. Answer keys are built once with ANSYS, checked by a person
and saved. Can Jin's data is Role 1's.

## Claude's calls (the user may overrule)

- About 12 problems on 4 parts for v1.
- A simulated user (LLM) instead of fixed scripted replies, because the agent's questions vary
  from run to run.
- Grading is at the intercepted `first_look`/`optimize` call. Geometry understanding is graded
  **indirectly**, through the face ids the agent picks for supports, loads and features. There is
  no LLM judge of the description text in v1.
- YAML problem files under `practice/`. Grader in `src/eval/`. Runner script in `scripts/`.
- **The simulated user answers, it does not correct.** It answers questions in plain words from
  `hidden_facts`, cannot check face ids, and approves any proposal ("OK, go ahead"). It rejects
  nothing. The intercepted arguments are therefore the agent's own choices. The grader scores the
  **first** `set_load_case` call and the **first** intercepted `first_look`/`optimize` call. A
  later re-call is recorded as "needed correction".
- **Complex part:** `hidden_facts` lists the features the user would name if asked. "Few" means at
  most 4 (the `first_look` limit). Those features are graded against the key like any others.
- **Runs that never reach a proposal:** hitting the 8-turn cap is a **fail** ("no proposal").
  A tool error or `MeshTooLarge` is **not graded**, with the reason shown in `summary.md`.
- **Asking and flagging is checked by event order.** A `must_ask` / `must_flag` item passes only
  if the agent raised it **before** any tool call that uses the value. Example: calling
  `set_material(safety_factor=0.8)`, getting the ValueError, and only then flagging it is a fail.
- **Feature equivalence:** the grader treats both halves of a split hole as one feature (grouped
  with `screen.hole_faces`), treats a fillet pair (a, b) the same as (b, a), and accepts a
  grouped-hole proposal (`also_face_ids`) when it covers the key's holes. For walls, the key lists
  every face id whose offset changes that wall (`alt_face_ids`).
- **"Says when a suggestion does nothing" is out of v1.** It happens after the search runs, and
  grading stops before the search.
- **Key run:** Claude drafts the candidate features and ranges per part and the user reviews them.
  Parts with more than 4 candidates get several first-look runs. Default labels:
  `controls_limit` if the feature moves a limited response by ≥ 5 % or ranks first in
  `limit_ranking`; `moves_objective` if it moves the objective by ≥ 5 %; otherwise `negligible`.
  The human review can override any label.

## Left to the builder

- Exact module layout, the simulated user's prompt wording, the results JSON schema, and how
  interception hooks into `Workbench.call`.
- Tests for the grader on hand-written run logs (no ANSYS, no API).

## Open (team)

- Whether the ANSYS agent becomes the team assistant, and opening `StudySpec`'s fixed
  `ParamName` list for other parts.
