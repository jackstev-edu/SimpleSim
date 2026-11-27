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
    except Exception as e:  # noqa: BLE001 - an API error or empty reply ends this problem, not the run
        log["stopped"] = f"error: {type(e).__name__}: {e}"
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
              "## Pick", "", "| problem | gap to the fast path's own best (scored) | gap to 400,000-design best (info) | gap to CalculiX best (info) |",
              "| --- | --- | --- | --- |"]
    pct = lambda v: "" if v is None else f"{v:+.1%}"
    lines += [f"| {g['id']} | {pct(g['pick']['gap'])} | {pct(g['pick'].get('dense_gap'))} | {pct(g['pick']['sim_gap'])} |" for g in graded]
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


def combined_summary(repeats: list[list[dict]], ids: list[str]) -> str:
    """One table over N repeats: a check passes only when every repeat passes it (spec 2026-11-27)."""
    n = len(repeats)
    by = [{g["id"]: g for g in rep} for rep in repeats]
    names = list(dict.fromkeys(c for rep in repeats for g in rep for c in g["checks"]))
    mark = {"pass": "pass", "fail": "**fail**", "n/a": "n/a", "info": "info"}
    lines = [f"# Fast-path benchmark, {n} repeats", "", "A check passes only when all repeats pass it; "
             "`fail (k/N)` gives the passing repeats. A repeat that never reached a problem counts as not passing.", "",
             "| problem | " + " | ".join(names) + " |", "|" + " --- |" * (len(names) + 1)]
    passed = counted = 0
    for pid in ids:
        cells = []
        for c in names:
            res = [b[pid]["checks"].get(c, {}).get("result") if pid in b else None for b in by]
            never_ran = all(r is None for r in res) and not any(pid in b for b in by)
            if never_ran or any(r in ("pass", "fail") for r in res):  # scored; a problem no repeat reached fails
                k = sum(r == "pass" for r in res)
                counted += 1
                passed += k == n
                cells.append("pass" if k == n else f"**fail** ({k}/{n})")
            else:  # info / n/a: the first repeat that has the problem
                first = next((r for r in res if r), None)
                cells.append(mark.get(first, ""))
        lines.append(f"| {pid} | " + " | ".join(cells) + " |")
    pct = lambda v: "" if v is None else f"{v:+.1%}"
    lines += ["", f"**{passed} / {counted} combined checks pass** (n/a and info not counted).", "",
              "## Pick (gap to the fast path's own best, per repeat)", "",
              "| problem | " + " | ".join(f"r{i + 1}" for i in range(n)) + " |", "|" + " --- |" * (n + 1)]
    lines += [f"| {pid} | " + " | ".join(pct(b[pid]["pick"]["gap"]) if pid in b else "not run" for b in by) + " |"
              for pid in ids]
    fails = [f"- r{i + 1} {pid} `{c}`: {chk['detail']}" for i, b in enumerate(by) for pid in ids if pid in b
             for c, chk in b[pid]["checks"].items() if chk["result"] == "fail"]
    missing = [f"- r{i + 1} {pid}: not run" for i, b in enumerate(by) for pid in ids if pid not in b]
    return "\n".join(lines + ["", "## Failures", "", *(fails + missing or ["none"])]) + "\n"


def regrade_folder(folder: Path, problems: dict[str, FastProblem]) -> str:
    """Regrade a run folder; a repeat run (r1/ .. rN/) also gets its combined summary rebuilt."""
    folder = Path(folder)
    subs = sorted((p for p in folder.glob("r*") if p.is_dir() and p.name[1:].isdigit()), key=lambda p: int(p.name[1:]))
    if not subs:
        md = summary_markdown(regrade(folder, problems))
    else:
        repeats = []
        for sub in subs:
            graded = regrade(sub, problems)
            (sub / "summary.md").write_text(summary_markdown(graded), encoding="utf-8")
            repeats.append(graded)
        meta = folder / "repeats.json"  # the requested N and problem list; a stopped run lacks later r*/ folders
        want = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
        repeats += [[] for _ in range(want.get("repeat", 0) - len(repeats))]
        seen = {g["id"] for rep in repeats for g in rep}
        md = combined_summary(repeats, want.get("ids") or [pid for pid in problems if pid in seen])
    (folder / "summary.md").write_text(md, encoding="utf-8")
    return md
