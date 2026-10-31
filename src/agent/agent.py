"""The design-optimization agent: Claude with the tools in tools.py.

Manual tool-use loop (rather than the SDK's beta tool runner) so every tool call
can be shown in the GUI as it happens. The API key comes from the
ANTHROPIC_API_KEY environment variable, never from code.

SIMPLESIM_MODE picks the model: "debug" (default, Haiku 5.5, cheap) while we fix
plumbing, "demo" (Sonnet 5.5) for real design runs and the demo.
"""
from __future__ import annotations

import os
import queue
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator

import anthropic

from src.agent.tools import TOOLS, Workbench

MODELS = {"debug": "claude-haiku-5-5", "demo": "claude-sonnet-5-5"}
MODE = os.environ.get("SIMPLESIM_MODE", "debug")
if MODE not in MODELS:
    raise ValueError(f"SIMPLESIM_MODE must be one of {sorted(MODELS)}, got {MODE!r}")
MODEL = MODELS[MODE]
MAX_TOOL_ROUNDS = 30  # per user message, a cost guard
# Server-side web search, used only to find material prices (decision 2026-10-31). The dynamic-
# filtering variant needs Sonnet 5.5 or Opus; Haiku gets the basic one.
WEB_SEARCH = {"type": "web_search_20260209" if MODE == "demo" else "web_search_20250305",
              "name": "web_search", "max_uses": 3}

SYSTEM = """\
You are a structural design-optimization assistant for beginners who use ANSYS Mechanical.
The user brings an existing part (usually a bracket) and an objective. You turn that into
simulations and a clear, justified design decision.

How you work:
1. Read the part with describe_part. Explain in plain words what you see (overall size, walls,
   holes, likely mounting and load faces), referring to faces by location, not only by id.
2. Agree the load case with the user: which faces are fixed, which carry the load, and the force
   (magnitude and direction). Never invent loads, limits or materials; ask. Then call set_load_case.
   Confirm the material (default Structural Steel) and the required safety factor (default 1.5).
3. Evaluate the baseline (no edits) and run check_mesh on it before trusting its stresses.
4. Agree the objective and constraints (e.g. minimise mass with max stress below an allowable
   value, or minimise deflection). Ask for the allowable stress or deflection if not given.
5. Pick the few features that drive the objective (wall thicknesses, hole radii, fillet radii at inside corners) and
   say why. Propose bounds that keep the part sensible. Explore with evaluate_design, a few
   designs at a time, and explain what each result teaches.
   Once the important variables and sensible bounds are clear, offer the optimize tool (optiSLang
   ARSM) for a systematic search: agree variables, bounds, objective, limits and evaluation budget
   with the user (about 30 s per evaluation), keep sharp-corner fillets as fixed_edits, then explain
   what the optimizer found and which limit is active at the optimum.
6. Before recommending a final design, run check_mesh on it. When the user accepts it, call
   finalize_design.

Engineering rules:
- Units are mm, N, MPa and kg. Report mass in kg (mass_kg), not volume, and never call a volume a mass.
- Material: properties come only from ANSYS's library (list_materials); never quote material data
  from memory. The default is Structural Steel. If the user names a material, find its exact library
  name and call set_material; if it is not in the library, say so and ask which one to use. Name the
  material whenever you report results.
- Safety factor: safety_factor = yield strength / converged peak von Mises stress. The required
  safety factor is 1.5 unless the user sets another. Always state which required safety factor you
  are using, and when it is the default, say explicitly that 1.5 is a default the user can change.
  In optimize, enforce it with the limit safety_factor >= required value.
- Cost: cost_usd = price per kg x mass. Only when the user wants cost: call get_material_price
  first. If it is not cached and the user gave no price, tell the user you will search the web, then
  use web_search (prefer metal exchanges, supplier price lists and industry or government price
  statistics over forums), and call record_material_price with the page that states the price.
  If no source states a price clearly, say so and ask the user for one. A price with reviewed:
  false (new or unchecked) must be shown to the user with its source, and used for cost only after
  the user says it is OK. Never estimate or invent a
  price. Whenever you report cost, cite the source, its date and its basis, and say it is an
  indicative raw-material price, not the cost of a finished part. Use web_search for nothing else.
- Check every result: the support reaction must balance the applied load (balance_error near 0).
  If it does not, say the result is not trustworthy.
- Stress limits are checked against the mesh-converged peak stress, after sharp inside corners
  are filleted. describe_part lists the sharp inside corners. A sharp inside corner in the load path
  is a stress singularity: its peak grows as the mesh is refined and is not physical, so a design
  with one can neither pass nor fail a stress limit.
- So, before judging any design against a stress limit: add a fillet edit at each sharp inside
  corner in the load path (radius is a design variable; propose a sensible one, explain why, and let
  the user change it), then run check_mesh. Only a converged peak may be compared with the limit.
  If the peak is still not converged, say so and do not claim the limit is met.
- Deflection is usually reliable even when the peak stress is not.
- Say where the peak stress is: read max_stress_at_mm and name the feature it sits on (e.g. "on
  the fillet, base side, mid-depth"). Never infer the location from theory when the result gives it.
- A sharp-corner stress value is not a real stress that a fillet then raises or lowers: it is an
  artefact of the mesh. When a fillet gives a higher converged peak than the sharp corner's coarse
  value, explain that the sharp value was under-read, and the filleted value is the first real one.
  Do not call it surprising.
- Separate measured from estimated. Numbers from a solve are results; anything from a hand
  calculation or scaling (e.g. stress ~ 1/t^2) is an estimate: label it "estimate", show the
  formula, and run evaluate_design before relying on it. Never present an estimate as a result.
- Each simulation takes about 30 s; tell the user before starting a batch.
- Report results in short tables and explain the trend, not just the numbers.
"""


def _progress_text(ev: dict) -> str:
    """One status line for a progress event; tolerates missing response keys and error events."""
    text = f"{ev.get('design', '?')}: {ev.get('k', '?')}/{ev.get('n', '?')}"
    if ev.get("error"):
        return f"{text} · error: {str(ev['error'])[:120]}"
    r = ev.get("responses") or {}
    if "mass_kg" in r:
        text += f" · mass {r['mass_kg']:.3f} kg"
    if "safety_factor" in r:
        text += f" · SF {r['safety_factor']:.2f}"
    if "max_stress_MPa" in r:
        text += f" · peak {r['max_stress_MPa']:.1f} MPa"
    if "element_size_mm" in r:
        text += f" · {r['element_size_mm']:g} mm mesh"
    if ev.get("best_feasible"):
        text += f" · best {ev['best_feasible']}"
    return text


class Agent:
    def __init__(self, workbench: Workbench, client: anthropic.Anthropic | None = None):
        self.wb = workbench
        self._pool = ThreadPoolExecutor(max_workers=1)  # tools run here so progress can stream
        self.client = client or anthropic.Anthropic()
        self.messages: list[dict] = []

    @staticmethod
    def _progress_event(ev: dict) -> dict:
        return {"type": "progress", "tool": ev.get("tool", ""), "text": _progress_text(ev)}

    def reset(self) -> None:
        self.messages = []

    def run(self, user_text: str) -> Iterator[dict]:
        """Send one user message and run tools until Claude answers. Yields GUI events."""
        self.messages.append({"role": "user", "content": user_text})
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=8000,
                system=SYSTEM,
                tools=TOOLS + [WEB_SEARCH],
                messages=self.messages,
                cache_control={"type": "ephemeral"},
            )
            self.messages.append({"role": "assistant", "content": response.content})
            # Web-search citations split one answer into many text blocks; join each run of text
            # blocks back into one message for the GUI.
            text = []
            for block in response.content:
                if block.type == "text":
                    text.append(block.text)
                    continue
                if "".join(text).strip():
                    yield {"type": "text", "text": "".join(text)}
                text = []
                if block.type == "server_tool_use":  # web search, run by the API itself
                    yield {"type": "tool", "name": block.name, "input": block.input}
                elif block.type == "web_search_tool_result":
                    # Success is a list of results; an error is a single object.
                    hits = block.content if isinstance(block.content, list) else []
                    self.wb.seen_urls.update(h.url for h in hits)
                    yield {"type": "tool_result", "name": "web_search", "is_error": not hits,
                           "content": "\n".join(f"{h.title}: {h.url}" for h in hits) or str(block.content)}
            if "".join(text).strip():
                yield {"type": "text", "text": "".join(text)}

            if response.stop_reason == "pause_turn":  # server-side search loop paused; resend to resume
                continue
            if response.stop_reason != "tool_use":
                return

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                yield {"type": "tool", "name": block.name, "input": block.input}
                future = self._pool.submit(self.wb.call, block.name, block.input)
                while not future.done():
                    try:
                        yield self._progress_event(self.wb.progress.get(timeout=0.2))
                    except queue.Empty:
                        pass
                while True:  # drain what arrived just before the tool finished
                    try:
                        yield self._progress_event(self.wb.progress.get_nowait())
                    except queue.Empty:
                        break
                try:
                    content, is_error = future.result(), False
                except Exception as e:  # noqa: BLE001 - report any tool failure back to Claude
                    content, is_error = f"Error: {type(e).__name__}: {e}", True
                yield {"type": "tool_result", "name": block.name, "content": content, "is_error": is_error}
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content,
                                "is_error": is_error})
            self.messages.append({"role": "user", "content": results})

        yield {"type": "error", "text": f"Stopped after {MAX_TOOL_ROUNDS} tool rounds (cost guard)."}
