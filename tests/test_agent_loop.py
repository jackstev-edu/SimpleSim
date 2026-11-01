"""Agent loop: tools run in a helper thread and progress events stream to the GUI."""
import queue
import time
from types import SimpleNamespace as NS

from src.agent.agent import Agent, _progress_text


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            blk = NS(type="tool_use", id="t1", name="first_look", input={})
            return NS(content=[blk], stop_reason="tool_use")
        return NS(content=[NS(type="text", text="done")], stop_reason="end_turn")


class FakeWB:
    def __init__(self, raises=False):
        self.progress = queue.Queue()
        self.seen_urls = set()
        self.raises = raises

    def call(self, name, args):
        for k in range(1, 4):
            time.sleep(0.05)
            self.progress.put({"tool": name, "k": k, "n": 3, "design": f"look_{k}",
                               "responses": {"mass_kg": 0.271, "safety_factor": 1.82},
                               "best_feasible": "look_ref"})
        if self.raises:
            raise RuntimeError("boom")
        return "{}"


def test_progress_events_stream_before_result():
    ag = Agent(FakeWB(), client=FakeClient())
    types = [e["type"] for e in ag.run("go")]
    assert types == ["tool", "progress", "progress", "progress", "tool_result", "text"]


def test_tool_exception_becomes_error_result():
    ag = Agent(FakeWB(raises=True), client=FakeClient())
    evs = list(ag.run("go"))
    res = [e for e in evs if e["type"] == "tool_result"][0]
    assert res["is_error"] is True and res["content"] == "Error: RuntimeError: boom"
    assert evs[-1]["type"] == "text"


def test_progress_text_tolerates_missing_keys_and_errors():
    ev = {"tool": "first_look", "k": 3, "n": 5, "design": "look_1_high",
          "responses": {"mass_kg": 0.271, "safety_factor": 1.82}, "best_feasible": "look_ref"}
    assert _progress_text(ev) == "look_1_high: 3/5 · mass 0.271 kg · SF 1.82 · best look_ref"
    assert _progress_text({"k": 1, "n": 2, "design": "d", "responses": {}, "best_feasible": None}) == "d: 1/2"
    assert "boom" in _progress_text({"k": 1, "n": 2, "design": "d", "error": "boom"})
    assert "MPa" in _progress_text({"k": 1, "n": 2, "design": "d",
                                    "responses": {"max_stress_MPa": 140.0, "element_size_mm": 1.0}})


class TwoToolClient:
    """Asks for two tools, then answers; records the messages of every request."""
    def __init__(self):
        self.calls, self.sent = 0, []
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        self.sent.append(list(kwargs["messages"]))
        if self.calls == 1:
            return NS(content=[NS(type="tool_use", id="t1", name="first_look", input={}),
                               NS(type="tool_use", id="t2", name="check_mesh", input={})],
                      stop_reason="tool_use")
        return NS(content=[NS(type="text", text="ok")], stop_reason="end_turn")


class StopAwareWB(FakeWB):
    def __init__(self):
        super().__init__()
        import threading
        self.stop_event = threading.Event()


def test_abandoned_turn_is_repaired_on_next_run():
    wb = StopAwareWB()
    client = TwoToolClient()
    ag = Agent(wb, client=client)
    gen = ag.run("go")
    for ev in gen:
        if ev["type"] == "progress":
            break
    gen.close()                       # browser refresh mid-tool
    assert wb.stop_event.is_set()     # a running search is asked to stop
    evs = list(ag.run("hello again"))
    assert evs[-1] == {"type": "text", "text": "ok"}
    sent = client.sent[-1]
    # every tool_use in the history has a matching tool_result in the next user message
    assistant, user = sent[-2], sent[-1]
    ids = [b.id for b in assistant["content"] if b.type == "tool_use"]
    results = [b for b in user["content"] if b.get("type") == "tool_result"]
    assert [r["tool_use_id"] for r in results] == ids
    assert all(r["is_error"] for r in results)
    assert user["content"][-1] == {"type": "text", "text": "hello again"}
    # no stale progress from the abandoned tool leaks into the new turn
    assert not any(e["type"] == "progress" for e in evs)
