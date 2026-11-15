"""The design-optimization agent: an OpenAI model with the tools in tools.py.

Manual tool-use loop on the Responses API so every tool call can be shown in the GUI as it
happens. The API key comes from the OPENAI_API_KEY environment variable, never from code
(course-provided key, decision 2026-11-15).

SIMPLESIM_MODE picks the model: "debug" (default) and "demo" use gpt-6-luna (cheap, priced like
Haiku 5.5); "deploy" uses gpt-6.1-sol (priced like Sonnet 5.5, 20x luna) for the deployed demo.
"""
from __future__ import annotations

import json
import os
import queue
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator

from src.agent.tools import TOOLS, Workbench

MODELS = {"debug": "gpt-6-luna", "demo": "gpt-6-luna", "deploy": "gpt-6.1-sol"}
MODE = os.environ.get("SIMPLESIM_MODE", "debug")
if MODE not in MODELS:
    raise ValueError(f"SIMPLESIM_MODE must be one of {sorted(MODELS)}, got {MODE!r}")
MODEL = MODELS[MODE]
REASONING_EFFORT = "low"  # keeps hidden reasoning tokens (billed as output) small
MAX_OUTPUT_TOKENS = 8000
MAX_TOOL_ROUNDS = 30  # per user message, a cost guard
# The tool definitions stay in Anthropic's shape in tools.py; the Responses API wants function tools.
# strict=False: our schemas have optional fields, which strict mode does not allow.
FUNCTIONS = [{"type": "function", "name": t["name"], "description": t["description"],
              "parameters": t["input_schema"], "strict": False} for t in TOOLS]
# Hosted web search, used only to find material prices (decision 2026-10-31).
WEB_SEARCH = {"type": "web_search"}

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
5. Pick the features that may drive the objective and say why. Propose a first look: list each
   feature with its low and high values, the number of solves (2n + 1) and the estimated time
   (seconds_per_design x solves), and wait for the user's OK or changes. Then call first_look.
   Report in plain words which features matter and why, and the best feasible design so far.
   Name the feature that controls each limit (limit_ranking) as well as the one that moves the
   objective most; if no design meets the limits, lead with the features that move the limit.
   Say that first-look values are indicative and not mesh-checked, and that a one-at-a-time
   screen cannot show interactions between features. Then ask whether to stop here or dive
   deeper. To dive deeper, propose optimize on the features that mattered (bounds, objective,
   limits, budget, estimated time) and wait for the user's OK. The user can press Stop at any
   time. After a stop, report what was found and ask how to go on.
6. Before recommending any design (including the best design from a first look or a stopped
   search), run check_mesh on it. Say which limit is active at the optimum (the one the design
   sits on) and what that means. When the user accepts it, call finalize_design.

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
- Holes: a round hole is often stored as two half-cylinder faces (same radius and axis). A radius
  edit on either face resizes the whole hole automatically. To give several holes one common size
  (e.g. all mounting holes as one variable), pass one face of the first hole as face_id and one face
  of each other hole in also_face_ids.
- Resizing: when the user asks to make the whole part larger or smaller, use scale_part (uniform,
  about the origin). Say what changes: every dimension x factor, volume and mass x factor^3. Ask
  whether the force should change too (same force on a larger part: stress about / factor^2), and
  report the new default element size. Scaling is not a design variable for first_look or optimize.
- If a tool fails with MeshTooLarge, the design was not solved (Ansys Student node limit). Tell the
  user plainly at once, quote the node count and limit, and propose a coarser element size or a
  larger fillet radius; do not retry the same design and mesh. Never call a design checked when its
  mesh check failed this way.
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
- Before any batch, state the number of solves and the estimated time from seconds_per_design.
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
    def __init__(self, workbench: Workbench, client=None):
        self.wb = workbench
        self._pool = ThreadPoolExecutor(max_workers=1)  # tools run here so progress can stream
        if client is None:
            from openai import OpenAI
            client = OpenAI()
        self.client = client
        self.messages: list[dict] = []  # Responses API input items: the whole conversation so far
        # Set while tools run: {"ids": call ids, "results": function_call_outputs so far, "future": running tool}.
        # Still set at the next run() only if the last turn was abandoned mid-tool (page refresh/close).
        self._open: dict | None = None

    @staticmethod
    def _progress_event(ev: dict) -> dict:
        return {"type": "progress", "tool": ev.get("tool", ""), "text": _progress_text(ev)}

    def reset(self) -> None:
        self.messages = []
        self._open = None

    def _close_abandoned_turn(self) -> list[dict]:
        """function_call_output items that answer the calls of an abandoned turn ([] if there was none).

        Every function_call must be followed by its output or the API rejects the whole history.
        Waits for the abandoned tool (it was asked to stop) and drops its progress events.
        """
        if self._open is None:
            return []
        future = self._open["future"]
        if future is not None:
            try:
                future.result()
            except Exception:  # noqa: BLE001 - its outcome is reported as interrupted below
                pass
        while True:
            try:
                self.wb.progress.get_nowait()
            except queue.Empty:
                break
        done = {r["call_id"] for r in self._open["results"]}
        results = list(self._open["results"]) + [
            {"type": "function_call_output", "call_id": i,
             "output": "Interrupted: the turn was cancelled (page closed or refreshed) before this tool's "
                       "result was delivered. Designs it finished are still stored; ask the user how to go on."}
            for i in self._open["ids"] if i not in done]
        self._open = None
        return results

    def _search_events(self, item) -> Iterator[dict]:
        """GUI events for one hosted web search; its source URLs feed the price-citation rule."""
        action = getattr(item, "action", None)
        urls = [s.url for s in (getattr(action, "sources", None) or []) if getattr(s, "url", None)]
        self.wb.seen_urls.update(urls)
        yield {"type": "tool", "name": "web_search", "input": {"query": getattr(action, "query", None) or ""}}
        status = getattr(item, "status", None)
        yield {"type": "tool_result", "name": "web_search", "is_error": status == "failed",
               "content": "\n".join(urls) or f"(search {status}, no sources listed)"}

    def run(self, user_text: str) -> Iterator[dict]:
        """Send one user message and run tools until the model answers. Yields GUI events."""
        self.messages.extend(self._close_abandoned_turn())
        self.messages.append({"role": "user", "content": user_text})
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.responses.create(
                model=MODEL,
                instructions=SYSTEM,
                input=self.messages,
                tools=FUNCTIONS + [WEB_SEARCH],
                include=["web_search_call.action.sources"],
                reasoning={"effort": REASONING_EFFORT},
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
            self.messages.extend(item.model_dump(exclude_none=True) for item in response.output)
            calls = []
            for item in response.output:
                if item.type == "message":
                    parts = [c for c in item.content if c.type == "output_text"]
                    for c in parts:
                        self.wb.seen_urls.update(a.url for a in (getattr(c, "annotations", None) or [])
                                                 if a.type == "url_citation")
                    text = "".join(c.text for c in parts)
                    if text.strip():
                        yield {"type": "text", "text": text}
                elif item.type == "web_search_call":
                    yield from self._search_events(item)
                elif item.type == "function_call":
                    calls.append(item)

            if not calls:
                if response.status == "incomplete":
                    reason = getattr(response.incomplete_details, "reason", None) or "unknown reason"
                    yield {"type": "error", "text": f"The model's reply was cut off ({reason})."}
                return

            results = []
            self._open = {"ids": [c.call_id for c in calls], "results": results, "future": None}
            try:
                for c in calls:
                    try:
                        args, bad = json.loads(c.arguments or "{}"), None
                    except json.JSONDecodeError as e:
                        args, bad = c.arguments, f"Error: the tool arguments are not valid JSON ({e})."
                    yield {"type": "tool", "name": c.name, "input": args}
                    if bad is None:
                        future = self._open["future"] = self._pool.submit(self.wb.call, c.name, args)
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
                        except Exception as e:  # noqa: BLE001 - report any tool failure back to the model
                            content, is_error = f"Error: {type(e).__name__}: {e}", True
                    else:
                        content, is_error = bad, True
                    results.append({"type": "function_call_output", "call_id": c.call_id, "output": content})
                    yield {"type": "tool_result", "name": c.name, "content": content, "is_error": is_error}
            except GeneratorExit:
                # The GUI dropped this turn (page refresh/close): stop a running search. The next
                # run() answers the open tool calls (self._open stays set).
                stop = getattr(self.wb, "stop_event", None)
                if stop is not None:
                    stop.set()
                raise
            self._open = None
            self.messages.extend(results)

        yield {"type": "error", "text": f"Stopped after {MAX_TOOL_ROUNDS} tool rounds (cost guard)."}
