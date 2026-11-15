"""Agent loop (OpenAI Responses API): tools run in a helper thread and progress events stream to the GUI."""
import json
import queue
import time
from types import SimpleNamespace

from src.agent.agent import FUNCTIONS, Agent, _progress_text
from src.agent.tools import TOOLS


class Item(SimpleNamespace):
    """Stands in for an SDK output item: attribute access plus model_dump like pydantic."""
    def model_dump(self, **_):
        def conv(v):
            if isinstance(v, SimpleNamespace):
                return {k: conv(x) for k, x in vars(v).items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v
        return conv(self)


def call(name, args=None, call_id="t1"):
    return Item(type="function_call", call_id=call_id, name=name, arguments=json.dumps(args or {}))


def msg(text, annotations=()):
    return Item(type="message", role="assistant",
                content=[Item(type="output_text", text=text, annotations=list(annotations))])


def resp(*items, status="completed"):
    return SimpleNamespace(output=list(items), status=status,
                           incomplete_details=SimpleNamespace(reason="max_output_tokens") if status == "incomplete" else None)


class ScriptClient:
    """Returns the scripted responses in order; records the kwargs of every request."""
    def __init__(self, *script):
        self.script, self.sent = list(script), []
        self.responses = self

    def create(self, **kwargs):
        self.sent.append({**kwargs, "input": [dict(i) if isinstance(i, dict) else i for i in kwargs["input"]]})
        return self.script.pop(0) if self.script else resp(msg("done"))


class FakeWB:
    def __init__(self, raises=False):
        self.progress = queue.Queue()
        self.seen_urls = set()
        self.raises = raises
        self.args = []

    def call(self, name, args):
        self.args.append(args)
        for k in range(1, 4):
            time.sleep(0.05)
            self.progress.put({"tool": name, "k": k, "n": 3, "design": f"look_{k}",
                               "responses": {"mass_kg": 0.271, "safety_factor": 1.82},
                               "best_feasible": "look_ref"})
        if self.raises:
            raise RuntimeError("boom")
        return "{}"


def test_progress_events_stream_before_result():
    ag = Agent(FakeWB(), client=ScriptClient(resp(call("first_look"))))
    types = [e["type"] for e in ag.run("go")]
    assert types == ["tool", "progress", "progress", "progress", "tool_result", "text"]


def test_tool_arguments_are_parsed_from_json():
    wb = FakeWB()
    list(Agent(wb, client=ScriptClient(resp(call("set_material", {"name": "Aluminum Alloy"})))).run("go"))
    assert wb.args == [{"name": "Aluminum Alloy"}]


def test_tool_exception_becomes_error_result():
    ag = Agent(FakeWB(raises=True), client=ScriptClient(resp(call("first_look"))))
    evs = list(ag.run("go"))
    res = [e for e in evs if e["type"] == "tool_result"][0]
    assert res["is_error"] is True and res["content"] == "Error: RuntimeError: boom"
    assert evs[-1]["type"] == "text"


def test_tool_output_follows_its_call_in_the_next_request():
    client = ScriptClient(resp(call("first_look", call_id="c9")))
    list(Agent(FakeWB(), client=client).run("go"))
    second = client.sent[1]["input"]
    assert second[0] == {"role": "user", "content": "go"}
    assert second[1]["type"] == "function_call" and second[1]["call_id"] == "c9"
    assert second[2] == {"type": "function_call_output", "call_id": "c9", "output": "{}"}


def test_request_uses_instructions_functions_and_web_search():
    client = ScriptClient()
    list(Agent(FakeWB(), client=client).run("go"))
    kw = client.sent[0]
    assert kw["instructions"].startswith("You are a structural design-optimization assistant")
    names = [t.get("name") for t in kw["tools"] if t["type"] == "function"]
    assert names == [t["name"] for t in TOOLS]
    assert {"type": "web_search"} in kw["tools"]
    assert "web_search_call.action.sources" in kw["include"]


def test_functions_mirror_tool_schemas_without_strict_mode():
    for f, t in zip(FUNCTIONS, TOOLS):
        assert f == {"type": "function", "name": t["name"], "description": t["description"],
                     "parameters": t["input_schema"], "strict": False}


def test_web_search_urls_are_recorded_for_the_price_rule():
    search = Item(type="web_search_call", id="ws1", status="completed",
                  action=Item(type="search", query="aluminium price per kg",
                              sources=[Item(type="url", url="https://a.example/price")]))
    cited = msg("Aluminium is about $3/kg.",
                [Item(type="url_citation", url="https://b.example/lme", title="LME", start_index=0, end_index=5)])
    wb = FakeWB()
    evs = list(Agent(wb, client=ScriptClient(resp(search, cited))).run("price?"))
    assert wb.seen_urls == {"https://a.example/price", "https://b.example/lme"}
    assert evs[0] == {"type": "tool", "name": "web_search", "input": {"query": "aluminium price per kg"}}
    assert evs[1]["type"] == "tool_result" and "https://a.example/price" in evs[1]["content"]
    assert evs[-1] == {"type": "text", "text": "Aluminium is about $3/kg."}


def test_cut_off_reply_is_reported():
    evs = list(Agent(FakeWB(), client=ScriptClient(resp(msg("partial"), status="incomplete"))).run("go"))
    assert evs[-1]["type"] == "error" and "max_output_tokens" in evs[-1]["text"]


def test_progress_text_tolerates_missing_keys_and_errors():
    ev = {"tool": "first_look", "k": 3, "n": 5, "design": "look_1_high",
          "responses": {"mass_kg": 0.271, "safety_factor": 1.82}, "best_feasible": "look_ref"}
    assert _progress_text(ev) == "look_1_high: 3/5 · mass 0.271 kg · SF 1.82 · best look_ref"
    assert _progress_text({"k": 1, "n": 2, "design": "d", "responses": {}, "best_feasible": None}) == "d: 1/2"
    assert "boom" in _progress_text({"k": 1, "n": 2, "design": "d", "error": "boom"})
    assert "MPa" in _progress_text({"k": 1, "n": 2, "design": "d",
                                    "responses": {"max_stress_MPa": 140.0, "element_size_mm": 1.0}})


class StopAwareWB(FakeWB):
    def __init__(self):
        super().__init__()
        import threading
        self.stop_event = threading.Event()


def test_abandoned_turn_is_repaired_on_next_run():
    wb = StopAwareWB()
    client = ScriptClient(resp(call("first_look", call_id="t1"), call("check_mesh", call_id="t2")), resp(msg("ok")))
    ag = Agent(wb, client=client)
    gen = ag.run("go")
    for ev in gen:
        if ev["type"] == "progress":
            break
    gen.close()                       # browser refresh mid-tool
    assert wb.stop_event.is_set()     # a running search is asked to stop
    evs = list(ag.run("hello again"))
    assert evs[-1] == {"type": "text", "text": "ok"}
    sent = client.sent[-1]["input"]
    # every function_call in the history has a matching function_call_output before the new message
    calls = [i["call_id"] for i in sent if i.get("type") == "function_call"]
    outputs = [i for i in sent if i.get("type") == "function_call_output"]
    assert calls == ["t1", "t2"] and [o["call_id"] for o in outputs] == calls
    assert all(o["output"].startswith("Interrupted") for o in outputs)
    assert sent[-1] == {"role": "user", "content": "hello again"}
    # no stale progress from the abandoned tool leaks into the new turn
    assert not any(e["type"] == "progress" for e in evs)
