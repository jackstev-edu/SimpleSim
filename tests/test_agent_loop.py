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
