"""Front layer: the conversation in front of Jack's PyDiscovery tools (the fast path).

The LLM asks for every missing input and calls Jack's tools. Code, not the LLM, decides when the
part leaves the fast path: an input outside the family, or the user asking for ANSYS, ends the
turn with a "handoff" event, and AssistantSession moves the chat to the ANSYS agent.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from src.assistant.decision_log import DecisionLog
from src.assistant.explain import explain
from src.assistant.path_rule import FAST_MATERIALS, outside_family
from src.assistant.study import StudyInputs, build_constraints, optimize_blocker
from src.llm import MODEL
from src.pydiscovery.tools import TOOL_SPECS

MAX_TOOL_ROUNDS = 20  # per user message, a cost guard
MAX_OUTPUT_TOKENS = 4000

_METRICS = ["mass_kg", "max_stress_mpa", "max_disp_mm", "safety_factor"]
OWN_SPECS = [
    {"name": "record_study_inputs",
     "description": "Store the safety factor, objective, extra limits or dimensions to vary. Only values "
                    "the user stated or confirmed in this conversation.",
     "parameters": {"type": "object", "properties": {
         "safety_factor": {"type": "number", "exclusiveMinimum": 0},
         "objective": {"type": "string", "enum": _METRICS},
         "direction": {"type": "string", "enum": ["minimize", "maximize"]},
         "limits": {"type": "array", "items": {"type": "object", "properties": {
             "metric": {"type": "string", "enum": _METRICS}, "op": {"type": "string", "enum": [">=", "<="]},
             "value": {"type": "number"}}, "required": ["metric", "op", "value"]}},
         "vary": {"type": "array", "items": {"type": "string"}}}, "required": []}},
    {"name": "switch_to_ansys",
     "description": "Move to the ANSYS search. Call when the user asks for ANSYS.",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
]
# open_part is left out: the session opens the part by its full path before the first turn.
FUNCTIONS = [{"type": "function", "strict": False, **s} for s in TOOL_SPECS + OWN_SPECS if s["name"] != "open_part"]

PROMPT = f"""\
You help a beginner engineering student improve a bracket. You run the team's fast search for the
L-bracket: a predictor scores thousands of designs, then the best few are checked again. Do not say
how the finalists were checked (ANSYS, CalculiX or a fitted model) until optimize_design reports it.

Rules:
- Never assume an input. Ask, one question at a time in plain words, for everything the setup card
  lists as missing and for the safety factor, the objective, and which dimensions may change.
- Safety factor: offer 1.5 as a common choice and use it only after the user says yes.
- Material: the fast search knows {' and '.join(FAST_MATERIALS)}. Recommend one for the user's goal
  (lighter or cheaper) and say the ANSYS search has more materials. The user picks. If the user wants
  another material, call switch_to_ansys. If the user names a material only by type ("steel",
  "aluminium"), say which grade the fast search has and wait for their yes before set_material.
- Directions: in the fast search's brackets +Y is up, so "down" or "straight down" is [0, -1, 0],
  never Z. If the load is not straight down, ask; do not guess another axis.
- The part is already open: start with describe_setup.
- The fast search's support and load faces are fixed by the bracket type. When the user's words
  match them, call set_support with the exact face text describe_setup shows (e.g. "back face at
  X = 0") and do not ask again. If the user means another face, say the fast search only covers
  that face and offer the ANSYS search.
- A part file with no parameters: ask whether it is the L-bracket. Never decide that yourself. If the
  user says it is not the L-bracket, call switch_to_ansys.
- Store the safety factor, objective and limits with record_study_inputs as soon as the user gives them.
- Show the setup card with the safety factor and objective, and call confirm_setup only after the
  user says yes.
- Tell the user once which search you are using and why, ending with "say 'use ANSYS' to switch".
  If they ask for ANSYS, call switch_to_ansys.
- Then call optimize_design. Report the explanation it returns. Never compute or invent numbers.
- Write plain text with short paragraphs or a dash list. Use bold sparingly: at most once per reply,
  and only for the single number or design that matters. No headings.
"""


class FrontAgent:
    def __init__(self, tools, client, out_root: Path, model: str = MODEL):
        self.tools = tools
        self.client = client
        self.out_root = Path(out_root)
        self.model = model
        self.inputs = StudyInputs()
        self.log = DecisionLog()
        self.handoff_reason: str | None = None
        self.given: dict = {}  # values the user stated in tool calls; the hand-off passes only these on
        self.last_run_dir: Path | None = None
        self.messages: list[dict] = []
        self._pool = ThreadPoolExecutor(max_workers=1)  # Mechanical must stay on one thread
        self._closed = False

    def call_tool(self, name: str, args: dict) -> dict:
        """Jack's tool, run on the single worker thread."""
        return self._pool.submit(self.tools.call, name, args).result()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        backend = getattr(getattr(self.tools, "app", None), "backend", None)
        if backend is not None and hasattr(backend, "close"):
            self._pool.submit(backend.close).result()  # dispose ANSYS on the thread that started it
        self._pool.shutdown(wait=True)

    # ------------------------------------------------------------ tool routing

    def _handoff(self, reason: str) -> tuple[str, bool]:
        self.handoff_reason = reason
        self.log.add("spec", f"path: slow ({reason})")
        return f"Switching to the ANSYS search: {reason}.", False

    def _dispatch(self, name: str, args: dict) -> tuple[str, bool]:
        """(content for the model, is_error)."""
        part = self.tools.part
        family = part.family if part is not None else None
        if name == "switch_to_ansys":
            return self._handoff(args.get("reason") or "the user asked for ANSYS")
        if name == "record_study_inputs":
            for key in ("safety_factor", "objective", "direction", "limits", "vary"):
                if args.get(key) is not None:
                    setattr(self.inputs, key, args[key])
            self.log.add("answer", "study inputs from the user", **args)
            return json.dumps({"ok": True, "inputs": vars(self.inputs)}), False
        if name == "set_material":
            why = outside_family(family, material=args.get("material"))  # material check needs no family
            if why:
                self._note(args, ("material",))
                return self._handoff(why)
        if name == "set_load" and family is not None:
            why = outside_family(family, force_n=args.get("force_n"), direction=args.get("direction"))
            if why:
                self._note(args, ("force_n", "direction"))
                return self._handoff(why)
        if name == "optimize_design":
            return self._optimize(args)
        out = self.call_tool(name, args)
        if out.get("ok") and name in ("set_load", "set_material"):
            self._note(args, ("force_n", "direction", "material"))
        if name == "confirm_setup" and out.get("ok"):
            self.log.add("spec", "setup confirmed by the user", state=out.get("state"))
        return json.dumps(out), not out.get("ok", False)

    def _note(self, args: dict, keys: tuple[str, ...]) -> None:
        self.given.update({k: args[k] for k in keys if args.get(k) is not None})

    def _optimize(self, args: dict) -> tuple[str, bool]:
        blocker = optimize_blocker(self.tools.part, self.inputs)
        if blocker:
            return blocker, True
        sent = {"objective": self.inputs.objective, "direction": self.inputs.direction or "minimize",
                "constraints": build_constraints(self.inputs)}  # never the model's own constraints
        vary = self.inputs.vary or args.get("vary")
        if vary:
            sent["vary"] = vary
        if args.get("n_verify"):
            sent["n_verify"] = args["n_verify"]
        out = self.call_tool("optimize_design", sent)
        if not out.get("ok"):
            return json.dumps(out), True
        try:
            text, warnings = explain(out["result"], self.client, model=self.model)
        except Exception as e:  # noqa: BLE001 - a paid search result must never be lost
            text = out["result"].get("explanation", "")
            warnings = [f"The explanation step failed ({type(e).__name__}: {e}); showing the template explanation."]
        self._save_run(out, text, warnings)
        tail = ("\nWarnings: " + " ".join(warnings)) if warnings else ""
        return f"{out.get('message', '')}\n\nExplanation for the user:\n{text}{tail}", False

    def _save_run(self, out: dict, text: str, warnings: list[str]) -> None:
        run = self.out_root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        run.mkdir(parents=True)
        self.log.add_result(out["result"])
        self.log.add("explanation", text)
        for w in warnings:
            self.log.add("warning", w)
        if out.get("study_spec") is not None:
            (run / "study_spec.json").write_text(json.dumps(out["study_spec"], indent=1), encoding="utf-8")
        (run / "result.json").write_text(json.dumps(out["result"], indent=1), encoding="utf-8")
        (run / "explanation.txt").write_text(text, encoding="utf-8")
        self.log.save(run / "decision_log")
        self.last_run_dir = run

    # ------------------------------------------------------------ the loop

    def run(self, user_text: str) -> Iterator[dict]:
        """One user message; yields text, tool, tool_result, error and handoff events."""
        self.messages.append({"role": "user", "content": user_text})
        self.log.add("answer", user_text)  # the conversation itself goes in the decision log
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.responses.create(
                model=self.model, instructions=PROMPT, input=self.messages, tools=FUNCTIONS,
                reasoning={"effort": "low"}, max_output_tokens=MAX_OUTPUT_TOKENS)
            self.messages.extend(item.model_dump(exclude_none=True) for item in response.output)
            calls = []
            for item in response.output:
                if item.type == "message":
                    text = "".join(c.text for c in item.content if c.type == "output_text")
                    if text.strip():
                        self.log.add("question", text)
                        yield {"type": "text", "text": text}
                elif item.type == "function_call":
                    calls.append(item)
            if not calls:
                if response.status == "incomplete":
                    yield {"type": "error", "text": "The model's reply was cut off."}
                return
            for c in calls:
                try:
                    args, bad = json.loads(c.arguments or "{}"), None
                except json.JSONDecodeError as e:
                    args, bad = {}, f"Error: the tool arguments are not valid JSON ({e})."
                yield {"type": "tool", "name": c.name, "input": args}
                if bad is not None:
                    content, is_error = bad, True
                else:
                    try:
                        content, is_error = self._dispatch(c.name, args)
                    except Exception as e:  # noqa: BLE001 - report any tool failure back to the model
                        content, is_error = f"Error: {type(e).__name__}: {e}", True
                self.messages.append({"type": "function_call_output", "call_id": c.call_id, "output": content})
                yield {"type": "tool_result", "name": c.name, "content": content, "is_error": is_error}
                if self.handoff_reason is not None:
                    yield {"type": "handoff", "reason": self.handoff_reason}
                    return
        yield {"type": "error", "text": f"Stopped after {MAX_TOOL_ROUNDS} tool rounds (cost guard)."}
