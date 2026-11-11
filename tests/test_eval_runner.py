"""Practice runner: intercepts the search, stops at the proposal or the turn cap, logs errors."""
import json
import queue
import threading
from types import SimpleNamespace as NS

from src.agent.agent import Agent
from src.eval.problems import Problem
from src.eval.runner import RecordingWorkbench, run_problem

PROB = Problem(id="p", part="x", kind="clean_auto", request="Make it lighter.", hidden_facts="h",
               user_named_features=[], key={"features": []})
FL_INPUT = {"features": [{"face_id": "a", "action": "offset", "low": -1, "high": 1}], "objective": "mass_kg",
            "limits": []}


class FakeWB(RecordingWorkbench):
    def __init__(self, load_error=None):  # no ANSYS, no super().__init__
        self.progress, self.seen_urls, self.stop_event = queue.Queue(), set(), threading.Event()
        self.proposal, self.faces, self.searched, self.calls = None, [], 0, []
        self.load_error = load_error

    def load_part(self, path):
        if self.load_error:
            raise self.load_error
        self.faces = [{"id": "a", "type": "plane"}]
        return {}

    def _call_through(self, name, args):
        self.calls.append(name)
        if name in ("first_look", "optimize"):
            self.searched += 1
        return json.dumps({"ok": True})


class ScriptClient:
    """Agent replies: each entry is a list of blocks for one API call."""
    def __init__(self, script):
        self.script, self.messages = list(script), self

    def create(self, **kwargs):
        blocks = self.script.pop(0) if self.script else [NS(type="text", text="Anything else?")]
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return NS(content=blocks, stop_reason=stop)


class FakeUser:
    def __init__(self):
        self.seen = []

    def reply(self, text):
        self.seen.append(text)
        return "OK, go ahead."


def text(t):
    return NS(type="text", text=t)


def tool(name, inp, i="t1"):
    return NS(type="tool_use", id=i, name=name, input=inp)


def run(script, wb=None, max_user_turns=8):
    wb = wb or FakeWB()
    user = FakeUser()
    agent = Agent(wb, client=ScriptClient(script))
    return run_problem(PROB, "part.scdocx", wb, agent, user, "before", max_user_turns=max_user_turns), wb, user


def test_intercepts_first_look_and_stops():
    lg, wb, user = run([[text("I propose a first look. OK?")],
                        [tool("set_load_case", {"fixed_face_ids": ["a"], "load_face_ids": ["a"], "force_N": [0, -1, 0]}, "t0")],
                        [tool("first_look", FL_INPUT)], [text("Done.")]])
    assert lg["end"] == "proposal" and lg["proposal"] == {"tool": "first_look", "input": FL_INPUT}
    assert wb.searched == 0 and wb.calls == ["set_load_case"]
    assert lg["user_turns"] == 2 and user.seen == ["I propose a first look. OK?"]


def test_turn_cap_ends_run():
    lg, _, _ = run([], max_user_turns=8)
    assert lg["end"] == "turn_cap" and lg["user_turns"] == 8 and lg["proposal"] is None


def test_tool_exception_saves_log_with_error():
    class MeshTooLarge(RuntimeError):
        pass
    lg, _, _ = run([], wb=FakeWB(load_error=MeshTooLarge("152000 nodes")))
    assert lg["end"] == "error" and lg["error"] == "MeshTooLarge: 152000 nodes"


def test_events_in_order_with_seq():
    lg, _, _ = run([[tool("describe_part", {}, "t0")], [text("I see a bracket. Which face is fixed?")],
                    [tool("first_look", FL_INPUT)], [text("ok")]])
    types = [e["type"] for e in lg["events"]]
    assert [e["seq"] for e in lg["events"]] == list(range(len(types)))
    assert types[:4] == ["user", "tool_call", "tool_result", "agent_text"]
    assert lg["faces"] == [{"id": "a", "type": "plane"}] and lg["label"] == "before"
