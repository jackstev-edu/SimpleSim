"""Run one fast-path benchmark problem: the assistant (fitted engine) against a simulated student.

The run log records every event with a running `seq`; the Result is read from Jack's part after each turn,
since the optimize tool returns the explanation text to the model, not the Result itself.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.eval.fast_grader import grade_fast
from src.eval.fast_keys import FastProblem
from src.eval.sim_user import APPROVE, MAX_TOKENS, RULES, SIM_MODEL
from src.llm import BudgetExceeded
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.families import FAMILIES

MAX_REPLIES = 12
NO_TEXT = "(The assistant said nothing. Reply: Please continue.)"


class FastStudent:
    """The practice set's simulated beginner, fed a fast-path problem's hidden facts."""

    def __init__(self, problem: FastProblem, client=None):
        if client is None:
            from openai import OpenAI
            client = OpenAI()
        self.client = client
        self.system = RULES.format(facts=problem.hidden_facts.strip(), approve=APPROVE, named="", complex="")
        self.messages: list[dict] = []

    def reply(self, assistant_text: str) -> str:
        self.messages.append({"role": "user", "content": assistant_text})
        r = self.client.responses.create(model=SIM_MODEL, max_output_tokens=MAX_TOKENS, instructions=self.system,
                                         input=self.messages, reasoning={"effort": "low"})
        text = (r.output_text or "").strip()
        if not text:
            raise RuntimeError(f"simulated student gave no text (status {r.status})")
        self.messages.append({"role": "assistant", "content": text})
        return text


class _StubWorkbench:
    """Stands in for SpaceClaim + Mechanical on the hand-off problem: starts nothing."""

    def __init__(self, out_dir=None):
        pass

    def load_part(self, path) -> None:
        pass

    def close(self) -> None:
        pass

    def request_stop(self) -> None:
        pass


class _StubAgent:
    def __init__(self, workbench, client):
        pass

    def run(self, text):
        yield {"type": "text", "text": "(ANSYS agent stand-in: the benchmark ends at the hand-off.)"}


def _result(session) -> dict | None:
    part = getattr(getattr(getattr(session, "front", None), "tools", None), "part", None)
    res = getattr(part, "last_result", None)
    return res.model_dump(mode="json") if res is not None else None


def run_fast_problem(problem: FastProblem, client, out_dir: Path, max_replies: int = MAX_REPLIES,
                     session_factory=None, student_factory=FastStudent) -> dict:
    if session_factory is None:
        from src.assistant.session import AssistantSession
        session_factory = AssistantSession
    out_dir = Path(out_dir)
    events: list[dict] = []

    def rec(ev: dict) -> None:
        events.append({"seq": len(events), **{k: v for k, v in ev.items() if k != "seq"}})

    def turn(gen) -> str:
        """Record one assistant turn; return its text for the student."""
        texts = []
        for e in gen:
            rec(e)
            if e.get("type") == "text":
                texts.append(e["text"])
        return "\n\n".join(texts)

    # always the stand-ins: the benchmark never starts ANSYS, and any hand-off ends the run
    session = session_factory(client, out_dir / problem.id, backend="fitted",
                              make_workbench=_StubWorkbench, make_agent=_StubAgent)
    student = student_factory(problem, client)
    part = write_demo_part(out_dir / problem.id / f"{problem.id}.dsco", family=FAMILIES[problem.family], physics=False)
    log = {"id": problem.id, "events": events, "result": None, "stopped": None}
    try:
        turn(session.upload(part))
        rec({"type": "student", "text": problem.request})
        said = turn(session.send(problem.request))
        replies = 0
        while True:
            log["result"] = _result(session)
            if log["result"] is not None:
                break
            if any(e["type"] in ("path", "handoff") and e.get("path", "slow") == "slow" for e in events):
                log["stopped"] = "handoff"
                break
            if replies >= max_replies:
                log["stopped"] = "reply cap"
                break
            answer = student.reply(said or NO_TEXT)
            replies += 1
            rec({"type": "student", "text": answer})
            said = turn(session.send(answer))
    except BudgetExceeded:
        log["stopped"] = "budget"
    finally:
        close = getattr(session, "close", None)
        if close:
            close()
    return log


def summary_markdown(graded: list[dict]) -> str:
    names = list(graded[0]["checks"]) if graded else []
    lines = ["# Fast-path benchmark", "", "| problem | " + " | ".join(names) + " |",
             "|" + " --- |" * (len(names) + 1)]
    mark = {"pass": "pass", "fail": "**fail**", "n/a": "n/a", "info": "info"}
    for g in graded:
        lines.append(f"| {g['id']} | " + " | ".join(mark[g["checks"][n]["result"]] for n in names) + " |")
    passed = sum(c["result"] == "pass" for g in graded for c in g["checks"].values())
    counted = sum(c["result"] in ("pass", "fail") for g in graded for c in g["checks"].values())
    lines += ["", f"**{passed} / {counted} checks pass** (n/a and info not counted).", "",
              "## Pick", "", "| problem | gap to model optimum | gap to CalculiX best (info) |", "| --- | --- | --- |"]
    pct = lambda v: "" if v is None else f"{v:+.1%}"
    lines += [f"| {g['id']} | {pct(g['pick']['gap'])} | {pct(g['pick']['sim_gap'])} |" for g in graded]
    fails = [f"- {g['id']} `{n}`: {c['detail']}" for g in graded for n, c in g["checks"].items() if c["result"] == "fail"]
    return "\n".join(lines + ["", "## Failures", "", *(fails or ["none"])]) + "\n"


def regrade(folder: Path, problems: dict[str, FastProblem]) -> list[dict]:
    graded = []
    for path in sorted(Path(folder).glob("*.json")):
        if path.name.endswith(".graded.json") or path.stem not in problems:
            continue
        g = grade_fast(problems[path.stem], json.loads(path.read_text(encoding="utf-8")))
        (path.parent / f"{path.stem}.graded.json").write_text(json.dumps(g, indent=2), encoding="utf-8")
        graded.append(g)
    return graded
