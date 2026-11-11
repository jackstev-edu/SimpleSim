"""Run one practice problem: the real agent and tools against the simulated user.

The first first_look/optimize call is the grade point: RecordingWorkbench records its arguments
and never runs the search. Every event goes into a JSON-able run log for the grader.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.agent.agent import MODEL
from src.agent.tools import Workbench
from src.eval.problems import Problem

SEARCH_TOOLS = ("first_look", "optimize")
INTERCEPTED = json.dumps({"practice_run": "search intercepted; not run"})
MAX_RESULT_CHARS = 4000  # describe_part results are long; the grader never reads them


class RecordingWorkbench(Workbench):
    def __init__(self, out_dir: Path):
        super().__init__(out_dir)
        self.proposal: dict | None = None

    def _call_through(self, name: str, args: dict) -> str:
        return super().call(name, args)

    def call(self, name: str, args: dict) -> str:
        if name in SEARCH_TOOLS:
            if self.proposal is None:
                self.proposal = {"tool": name, "input": args}
            return INTERCEPTED
        return self._call_through(name, args)


def run_problem(problem: Problem, part_path, wb: RecordingWorkbench, agent, sim_user, label: str,
                max_user_turns: int = 8) -> dict:
    log = {"problem_id": problem.id, "label": label, "model": MODEL, "faces": [], "events": [],
           "proposal": None, "end": None, "error": None, "user_turns": 0}
    events = log["events"]

    def add(ev: dict) -> None:
        events.append({"seq": len(events), **ev})

    try:
        wb.load_part(part_path)
        log["faces"] = list(wb.faces)
        user_text = problem.request
        while True:
            add({"type": "user", "text": user_text})
            log["user_turns"] += 1
            said = []
            for ev in agent.run(user_text):
                if ev["type"] == "text":
                    said.append(ev["text"])
                    add({"type": "agent_text", "text": ev["text"]})
                elif ev["type"] == "tool":
                    add({"type": "tool_call", "name": ev["name"], "input": ev["input"]})
                elif ev["type"] == "tool_result":
                    add({"type": "tool_result", "name": ev["name"], "is_error": ev["is_error"],
                         "content": str(ev["content"])[:MAX_RESULT_CHARS]})
                elif ev["type"] == "error":
                    add({"type": "agent_error", "text": ev["text"]})
            if wb.proposal is not None:
                log["end"], log["proposal"] = "proposal", wb.proposal
                break
            if log["user_turns"] >= max_user_turns:
                log["end"] = "turn_cap"
                break
            user_text = sim_user.reply("\n\n".join(said) or "(the assistant said nothing)")
    except Exception as e:  # noqa: BLE001 - any failure ends the run; the grader marks it not graded
        log["end"], log["error"] = "error", f"{type(e).__name__}: {e}"
    return log
