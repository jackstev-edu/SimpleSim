# Figures

Each diagram is a Mermaid source (`.mmd`, the file to edit), an editable Excalidraw scene (`.excalidraw`, open it at
excalidraw.com with File, Open) and rendered `.svg` and `.png` files. Both diagrams show the agreed final system.

## system-overview

**Figure: How a student's request becomes a verified design.** The assistant asks for every input the request leaves
out and the student confirms the setup. The path picker sends known bracket families to the fast path (a search on a
model trained from the CalculiX simulation table) and any other part to the slow path (the ANSYS agent). Fast-path
finalists are checked by CalculiX (main check) and ANSYS (cross-check). Every result is explained with a check that
each number comes from `result.json`, and every decision is logged. Colours show which team role owns each part;
arrows carry the shared contract files.

## assistant-detail

**Figure: Inside the assistant.** The front layer asks for supports, loaded faces, force and units, material, safety
factor and objective, then picks the path. On the slow path the ANSYS agent describes the part and waits for the
student's OK, solves the baseline, runs a first look to find which features matter, and asks whether to stop or go
deeper with the optiSLang optimizer. Every solve passes the plausibility checks (reaction balance, mesh refinement,
exclusion zones around supports and the load, and the 128K-node Student limit). Colours as in the overview.

## Not yet connected (as of 2026-11-22)

Today the fast-path finalists are checked in ANSYS only (not yet CalculiX), and the GUI runs on saved `result.json`
files rather than the live assistant.
