# First look, then optional deeper search: design

Date: 2026-10-31. Covers build-order items (2), a fast first-look search, and the progress part of (3), optimizer progress in the GUI.

## Intent

SimpleSim users are beginners. When the agent starts searching for a better design, they **want a first answer within a few minutes** and want to **stay in control** after that. This answers the open question from 2026-10-31 ("does the search need a time limit?"). The short wait applies to the **first look** only. Any longer search happens only when the user asks for it, shows live progress, and can be stopped at any moment.

The first answer is **not** a design recommendation. It is an understanding of **which features matter** for the user's objective, together with the best design seen so far. With that, the user decides whether to stop or to dive deeper.

## User decisions (2026-10-31)

| Decision | From the user |
|---|---|
| First answer within a few minutes | "they'd want a first answer within a few minutes" |
| First look shows which features matter, then the agent asks whether to dive deeper or stop | "do the initial search, get back to the user and give the results and ask whether to dive deeper or stop" |
| The deeper search shows progress and can be stopped | "pause partway to show progress and allow stopping" |
| Stopping works through a **Stop button with live progress**, not periodic check-in questions | "the stop button with live progress" |
| Before the first look runs, the agent **shows the features and ranges and waits for the user's OK** | "show them and wait for the user's OK" |

## Flow

1. **Set-up** (exists already): describe the part, agree the load case, material, required safety factor (default 1.5, always stated), objective and limits. Fillet the sharp inside corners in the load path.
2. **Propose the first look.** The agent lists the candidate features. Each one has the edit (offset, radius or fillet on given faces), a low and high value, the reason it might matter, the number of solves, and the estimated time. Then it **waits for the user's OK** (or the user's changes).
3. **First look runs.** Live progress appears in the chat, and the Stop button is active.
4. **First-look report.** For each feature, the agent explains how the responses moved between the low and high values (mass, peak stress, safety factor, deflection, cost when a price is set). It ranks the features, names the best feasible design so far (flagged as **not mesh-checked**), and asks: **stop here, or dive deeper?**
5. **Stop:** the agent runs check_mesh on the best design and, if the user accepts it, finalizes it. The rules for recommending a design are unchanged.
6. **Dive deeper:** the agent proposes an `optimize` run on the features that mattered, with bounds, budget and estimated time, and waits for the user's OK. The run shows live progress and can be stopped. When it finishes or is stopped, the agent reports the best feasible design, runs check_mesh on it, and explains which limit is active.

## Parts

### A. First-look screen (new tool, e.g. `first_look`)
- **Inputs:** 1–4 features (same edit shapes as `optimize` variables, each with `low` and `high`), plus `fixed_edits` such as sharp-corner fillets. The objective and the limits use the same schema as `optimize`.
- **Designs:** one **reference design**, which is the original part with `fixed_edits` and each feature at its current value (offset 0, the existing radius), plus each feature at `low` and at `high` with the others at the reference. That makes **2n + 1 solves**: 9 for four features, about 4.5 min at about 30 s per solve.
- Every design is stored like an `evaluate_design` result (names such as `look_ref`, `look_<k>_low`, `look_<k>_high`), so check_mesh, finalize_design and the GUI table work on them.
- **Output, per feature:** the response values at low, reference and high, the change from low to high (absolute and relative to the reference), and whether each design is feasible. Overall: the best feasible design, and a ranking by how strongly each feature moves the objective and the limited responses.
- **Mesh:** the normal 2 mm global mesh, plus r/4 local sizing on fillets as now. The values are **indicative**. The report must say so, and no first-look design is recommended without check_mesh.

### B. Time estimates
- Before any batch (first look, optimize), the agent states the solve count and the estimated time. The estimate uses this session's measured mean solve time (or 30 s before any solve) and is exposed to the agent, for example in the tool's result or in `describe_part`.

### C. Live progress and the Stop button
- While a long tool (first look, optimize, check_mesh) runs, each finished design appears in the chat as a short progress line (design k of N, its key responses, best feasible so far). The results table updates as designs come in.
- A **Stop** button in the GUI is active while a search runs. Pressing it lets the solve in progress finish (a running ANSYS solve is not interrupted, which takes at most about 30 s). No new design is started after that. The tool then returns normally with what it has, marked `stopped: "user"`.
- After a stop, the agent reports the results so far (best feasible design, flagged as not mesh-checked) and asks how to go on.

### D. Agent prompt changes
- Replace "explore a few designs at a time" with the flow above: propose the first look and wait for the OK, report which features matter, ask stop or deeper, propose optimize only on the features that matter, and give the solve count and time before every batch.
- Explain that the first look changes one feature at a time and cannot show interactions between features. The deeper search can.

## Goals
- The first answer arrives within a few minutes, with a predictable time that is stated up front.
- The user approves the features and ranges before any batch runs.
- The user always sees progress during a batch and can stop it.
- The first-look report teaches **which features matter and why**, in plain words.

## Non-goals
- No wall-clock cutoff. Time is managed through solve counts that are known in advance.
- No periodic "continue?" check-in questions (decided: Stop button instead).
- No interrupting an ANSYS solve that is already running.
- No interaction effects in the first look. The deeper search covers those.

## Anti-goals (what would make this a failure even if it works)
- A beginner stares at a frozen chat for minutes with no sign of progress.
- A first-look design gets recommended as final without a mesh check.
- A batch starts before the user has seen and approved what it will run and how long it takes.
- A stopped search loses the designs it already solved.

## Constraints
- All ANSYS calls stay on the single worker thread. The Stop button only sets a flag that the search loop checks between designs.
- The optiSLang session must always be disposed (Student licence seats; see CLAUDE.md). A user stop must take the same `project.stop` and `dispose` path as the budget stop.
- Student limit: 128K nodes.
- Gradio GUI (`app.py`). The Stop button must work while the agent's generator is busy, so its handler must not queue behind the running chat event.

## Calls I made (Claude), not decided by the user
- The first look uses a **one-feature-at-a-time low/high screen** with 2n + 1 solves, chosen over a random or space-filling sample because it gives a clear answer for each feature that a beginner can read.
- The first look is capped at **4 features (9 solves, about 4.5 min)**, to fit "a few minutes".
- There is no numeric "matters" threshold. The agent ranks the features, proposes which to carry into the deeper search, and the user approves.
- First-look designs use the normal 2 mm mesh and fillet local sizing.
- Each feature's reference value is its current value (offset 0, the existing radius) when that lies inside [low, high]. Otherwise, as for a fillet feature on a corner that starts sharp, it is the middle of the range.
- After a stop, the best design so far is mesh-checked before any recommendation, the same rule as everywhere else.

## Left to the builder
- How progress travels from the ANSYS thread to the Gradio generator, for example a queue the agent loop drains while a tool call runs in a helper thread.
- How the progress lines look in the chat, and whether they update in place.
- Tool and argument names, and how the first-look result is laid out, as long as it carries the fields above.
- Tests: a no-LLM smoke test for the first look on `examples/l_bracket.scdocx`, plus a stop test (set the stop flag after k designs; check that the optimize run returns `stopped: "user"`, keeps its designs, and disposes optiSLang).
