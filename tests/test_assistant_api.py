"""The GUI's assistant API (src/pydiscovery/assistant_server.py): handle() as a pure dispatcher over Role 3's
AssistantSession, plus one real server from launch_gui streaming newline-delimited JSON.

Everything runs with the root requirements only. A small fake OpenAI client stands in for the real one
(no key, no openai package, no network), the fitted engine stands in for ANSYS, and the session's own
ANSYS imports are lazy, so a hand-off on a laptop without ANSYS ends in the session's error event.
"""

import json
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.llm import PRICES
from src.pydiscovery.assistant_server import PREFIX, AssistantAPI, ndjson
from src.pydiscovery.demo_part import write_demo_part

MODEL = next(iter(PRICES))


# ---------------------------------------------------------------- a fake OpenAI client


class _Item(SimpleNamespace):
    """An output item of the Responses API: attribute access plus model_dump, which the front agent calls."""

    def model_dump(self, **_):
        def conv(v):
            if isinstance(v, SimpleNamespace):
                return {k: conv(x) for k, x in vars(v).items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v
        return conv(self)


def say(text):
    return _Item(type="message", role="assistant", content=[_Item(type="output_text", text=text, annotations=[])])


def call(name, args=None, call_id="c1"):
    return _Item(type="function_call", call_id=call_id, name=name, arguments=json.dumps(args or {}))


def reply(*items, tokens=(100, 0, 50), status="completed"):
    usage = SimpleNamespace(input_tokens=tokens[0], input_tokens_details=SimpleNamespace(cached_tokens=tokens[1]), output_tokens=tokens[2])
    text = "".join(c.text for i in items if i.type == "message" for c in i.content)  # output_text, which the explanation step reads
    return SimpleNamespace(output=list(items), status=status, usage=usage, incomplete_details=None, output_text=text)


class FakeOpenAI:
    """responses.create returns the scripted replies in order, then 'done'; every request's kwargs are kept."""

    def __init__(self, *script, tokens=(100, 0, 50)):
        self.script, self.sent, self.tokens = list(script), [], tokens
        self.responses = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return self.script.pop(0) if self.script else reply(say("done"), tokens=self.tokens)


# ---------------------------------------------------------------- helpers


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # the fake client never needs it

    def _make(*script, **kwargs):
        client = FakeOpenAI(*script, tokens=kwargs.pop("tokens", (100, 0, 50)))
        api = AssistantAPI(tmp_path, engine="fitted", make_client=lambda: client, **kwargs)
        return api, client
    return _make


def get(api, path=PREFIX):
    return api.handle("GET", path, {}, b"")


def post(api, rest, body=None, query=None):
    raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode("utf-8")
    return api.handle("POST", f"{PREFIX}/{rest}", query or {}, raw)


def upload(api, tmp_path, name="l_bracket.dsco", **kwargs):
    raw = write_demo_part(tmp_path / "src.dsco", **kwargs).read_bytes()
    status, events = post(api, "upload", raw, query={"name": [name]})
    assert status == 200, events
    return list(events)


def types(events):
    return [e["type"] for e in events]


# ---------------------------------------------------------------- status and availability


def test_status_without_openai_or_key_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    api = AssistantAPI(tmp_path)  # the real client would be used, so the checks apply
    status, out = get(api)
    assert status == 200 and out["ok"] is True and out["available"] is False
    assert out["api_key_set"] is False and "OPENAI_API_KEY" in out["message"]
    if not out["openai_installed"]:
        assert "openai package" in out["message"]
    assert out["engine"] == "fitted" and out["model"] == MODEL and out["budget_left_usd"] == out["budget_usd"] > 0
    assert out["path"] is None and out["part"] is None and out["busy"] is False
    assert "sk-" not in json.dumps(out)  # never the key, only whether it is set
    status, out = post(api, "upload", b"x", query={"name": ["l.dsco"]})
    assert status == 503 and "can't run" in out["message"]


def test_status_with_a_key_but_no_openai_package_still_names_the_package(tmp_path, monkeypatch):
    pytest.importorskip("sys")
    import src.pydiscovery.assistant_server as mod

    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(mod, "_openai_installed", lambda: False)
    status, out = get(AssistantAPI(tmp_path))
    assert out["available"] is False and out["api_key_set"] is True and "openai package" in out["message"]
    monkeypatch.setattr(mod, "_openai_installed", lambda: True)
    assert get(AssistantAPI(tmp_path))[1]["available"] is True


def test_unknown_engine_is_refused(tmp_path):
    with pytest.raises(ValueError, match="warp_drive"):
        AssistantAPI(tmp_path, engine="warp_drive")


# ---------------------------------------------------------------- upload and send


def test_upload_streams_the_fast_path_then_done(make, tmp_path):
    api, client = make(reply(say("I see an L-bracket. Which safety factor?")))
    events = upload(api, tmp_path)
    assert types(events) == ["path", "text", "done"]
    assert events[0]["path"] == "fast" and "L-bracket" in events[0]["reason"]
    assert events[1]["text"].startswith("I see an L-bracket")
    done = events[-1]
    assert done["path"] == "fast" and done["part"] == "l_bracket.dsco" and done["table"] == []
    assert done["spent_usd"] == pytest.approx((100 * 0.10 + 50 * 0.50) / 1e6, rel=1e-6)
    assert (tmp_path / "data" / "raw" / "uploads" / "l_bracket.dsco").exists()
    assert "already open" in client.sent[0]["input"][0]["content"] and client.sent[0]["model"] == MODEL
    status, out = get(api)
    assert out["path"] == "fast" and out["part"] == "l_bracket.dsco" and out["busy"] is False
    assert out["spent_usd"] == done["spent_usd"] and out["budget_left_usd"] == pytest.approx(out["budget_usd"] - done["spent_usd"])


def test_send_runs_tools_and_streams_each_step(make, tmp_path):
    api, client = make(reply(say("ok")),
                       reply(call("record_study_inputs", {"safety_factor": 1.5}, "a"), call("confirm_setup", {}, "b")),
                       reply(say("Setup confirmed. Shall I optimize?")))
    upload(api, tmp_path)
    status, events = post(api, "send", {"text": "1.5 is fine, the setup is right"})
    events = list(events)
    assert types(events) == ["tool", "tool_result", "tool", "tool_result", "text", "done"]
    assert events[0]["name"] == "record_study_inputs" and events[0]["input"] == {"safety_factor": 1.5}
    assert events[1]["is_error"] is False and json.loads(events[1]["content"])["inputs"]["safety_factor"] == 1.5
    assert events[2]["name"] == "confirm_setup" and events[3]["is_error"] is False
    assert json.loads(events[3]["content"])["state"]["confirmed"] is True
    assert events[4]["text"].startswith("Setup confirmed")
    assert api.session.front.tools.part.confirmed is True
    outputs = [m for m in client.sent[-1]["input"] if isinstance(m, dict) and m.get("type") == "function_call_output"]
    assert [m["call_id"] for m in outputs] == ["a", "b"]  # the model saw both tool results


def test_fast_path_optimize_comes_back_in_the_done_event_once(make, tmp_path):
    """The front agent runs optimize_design through its own tools; the done event carries the three contract files."""
    from contracts.models import Prediction, Result, StudySpec

    api, client = make(reply(say("ok")),
                  reply(call("record_study_inputs", {"safety_factor": 1.5, "objective": "mass_kg", "direction": "minimize"}, "a")),
                  reply(call("confirm_setup", {}, "b"), call("optimize_design", {}, "c")),
                  reply(say("The recommended design is lighter than the baseline and sits on the deflection limit.")),  # the explanation step's call
                  reply(say("Here is the lighter design.")),
                  reply(say("Adopt it: it is lighter and meets every limit. The numbers are a fitted estimate. Next, solve it once in Ansys.")))  # the verdict's call
    upload(api, tmp_path)
    events = list(post(api, "send", {"text": "safety factor 1.5, minimize mass, the setup is right, go"})[1])  # the loop runs on after each tool
    tools = [e["name"] for e in events if e["type"] == "tool"]
    assert tools == ["record_study_inputs", "confirm_setup", "optimize_design"] and all(not e["is_error"] for e in events if e["type"] == "tool_result")
    optimize = [e for e in events if e["type"] == "tool_result" and e["name"] == "optimize_design"][0]
    assert "Explanation for the user:\nThe recommended design is lighter" in optimize["content"]  # the model's explanation, numbers checked
    assert events[-2] == {"type": "text", "text": "Here is the lighter design."}
    done = events[-1]
    assert done["type"] == "done" and "run" in done
    spec, pred, res = StudySpec.model_validate(done["run"]["study_spec"]), Prediction.model_validate(done["run"]["prediction"]), Result.model_validate(done["run"]["result"])
    assert res.recommended is not None and res.recommended.feasible and res.spec == spec and len(pred.shortlist) > 0
    assert [c.value for c in spec.constraints if c.metric == "safety_factor"] == [1.5]  # the user's safety factor, never a default
    verdict = done["run"]["verdict"]
    assert verdict["source"] == "model" and verdict["text"].startswith("Adopt it:") and verdict["warnings"] == []
    assert '"finalist_numbers": "estimated by the fitted model only' in client.sent[-1]["input"][0]["content"]
    events = list(post(api, "send", {"text": "thanks"})[1])
    assert "run" not in events[-1]  # the same result is not sent twice


def test_verdict_with_invented_numbers_falls_back_to_the_template(make, tmp_path):
    from src.assistant.numbers import unknown_numbers

    api, client = make(reply(say("ok")),
                       reply(call("record_study_inputs", {"safety_factor": 1.5, "objective": "mass_kg", "direction": "minimize"}, "a")),
                       reply(call("confirm_setup", {}, "b"), call("optimize_design", {}, "c")),
                       reply(say("Lighter than the baseline.")),  # explanation
                       reply(say("Done.")),
                       reply(say("Adopt it: 73% lighter.")), reply(say("It holds 9000 N.")))  # the verdict twice invents a number
    upload(api, tmp_path)
    done = list(post(api, "send", {"text": "safety factor 1.5, minimize mass, the setup is right, go"})[1])[-1]
    v = done["run"]["verdict"]
    assert v["source"] == "template" and "9000" in v["warnings"][0] and v["text"].startswith("Adopt it")
    assert unknown_numbers(v["text"], done["run"]["result"]) == []
    assert len(client.sent) == 7  # one retry, then the template: no third call


def test_send_before_upload_and_bad_bodies(make):
    api, _ = make()
    assert post(api, "send", {"text": "hello"}) == (200, {"ok": False, "message": "Upload a part first."})
    assert post(api, "send", {"text": "   "})[0] == 400
    assert post(api, "send", b"not json")[0] == 400
    assert post(api, "send", {"text": 5})[0] == 400


def test_upload_checks_name_type_and_size(make):
    api, _ = make()
    assert post(api, "upload", b"x")[0] == 400  # no name
    assert post(api, "upload", b"x", query={"name": ["notes.txt"]})[0] == 400
    assert post(api, "upload", b"", query={"name": ["l.dsco"]})[0] == 400
    assert post(api, "upload", b"\0" * (50 * 1024 * 1024 + 1), query={"name": ["big.dsco"]})[0] == 400
    assert api.session is None  # nothing started for a refused upload


def test_upload_names_stay_inside_the_uploads_folder(make, tmp_path):
    api, _ = make(reply(say("ok")))
    upload(api, tmp_path, name="../../evil.dsco")
    assert (tmp_path / "data" / "raw" / "uploads" / "evil.dsco").exists()
    assert not (tmp_path / "evil.dsco").exists() and not (tmp_path.parent / "evil.dsco").exists()


def test_tool_errors_are_streamed_not_raised(make, tmp_path):
    api, _ = make(reply(say("ok")), reply(call("set_dimensions", {"values": {"Thickness": 99.0}}, "a")), reply(say("too thick")))
    upload(api, tmp_path)
    events = list(post(api, "send", {"text": "make it 99 mm thick"})[1])
    assert types(events) == ["tool", "tool_result", "text", "done"]
    assert events[1]["is_error"] is True and "outside" in events[1]["content"]


# ---------------------------------------------------------------- budget, concurrency, stop and reset


def test_budget_cap_ends_the_turn_with_an_error_event(make, tmp_path):
    api, _ = make(reply(say("ok"), tokens=(1_000_000, 0, 1_000_000)), budget_usd=0.01)
    events = upload(api, tmp_path)  # the first call is allowed (nothing spent yet) and blows the cap
    assert types(events) == ["path", "text", "done"] and events[-1]["budget_left_usd"] == 0
    events = list(post(api, "send", {"text": "hello"})[1])
    assert types(events) == ["error", "done"]
    assert events[0]["budget_exceeded"] is True and "budget cap reached" in events[0]["text"] and "SIMPLESIM_SESSION_BUDGET_USD" in events[0]["text"]
    assert get(api)[1]["budget_left_usd"] == 0 and get(api)[1]["busy"] is False


def test_a_second_turn_while_one_runs_is_refused(make, tmp_path):
    api, _ = make(reply(say("ok")))
    upload(api, tmp_path)
    status, stream = post(api, "send", {"text": "first"})
    assert status == 200 and get(api)[1]["busy"] is True  # the lock is held until the stream is consumed
    assert post(api, "send", {"text": "second"})[0] == 409
    assert post(api, "upload", b"x" * 10, query={"name": ["l.dsco"]})[0] == 409
    list(stream)
    assert get(api)[1]["busy"] is False and post(api, "send", {"text": "third"})[0] == 200


def test_closing_the_stream_early_releases_the_turn(make, tmp_path):
    api, _ = make(reply(say("ok")), reply(say("a")))
    upload(api, tmp_path)
    stream = post(api, "send", {"text": "x"})[1]
    next(stream)  # the browser read one event, then went away
    stream.close()
    assert get(api)[1]["busy"] is False


def test_stop_and_reset(make, tmp_path):
    api, _ = make(reply(say("ok")))
    status, out = post(api, "stop")
    assert status == 200 and out["message"] == "Nothing is running."
    upload(api, tmp_path)
    status, out = post(api, "stop")
    assert status == 200 and out["message"] == "Stop requested." and api.session is not None  # fast path: nothing to stop
    spent = get(api)[1]["spent_usd"]
    status, out = post(api, "reset")
    assert status == 200 and out["path"] is None and out["part"] is None and api.session is None
    assert out["spent_usd"] == spent > 0  # the cap keeps counting across resets
    assert post(api, "send", {"text": "hi"})[1]["message"] == "Upload a part first."
    assert api.shutdown() == spent


def test_unknown_routes_and_methods(make):
    api, _ = make()
    assert api.handle("POST", PREFIX, {}, b"")[0] == 405
    assert api.handle("GET", PREFIX + "/send", {}, b"")[0] == 405
    assert api.handle("POST", PREFIX + "/nothing", {}, b"")[0] == 404
    assert api.handle("GET", "/api/elsewhere", {}, b"")[0] == 404


# ---------------------------------------------------------------- the hand-off on a laptop without ANSYS


@pytest.mark.skipif(pytest.importorskip("importlib").util.find_spec("ansys") is not None, reason="ANSYS packages are installed here")
def test_handoff_without_ansys_is_an_error_event_not_a_crash(make, tmp_path):
    api, _ = make(reply(say("ok")), reply(call("switch_to_ansys", {"reason": "the user asked for ANSYS"})))
    upload(api, tmp_path)
    events = list(post(api, "send", {"text": "use ANSYS"})[1])
    assert types(events) == ["tool", "tool_result", "path", "text", "error", "done"]
    assert events[2]["path"] == "slow" and "Starting ANSYS" in events[3]["text"]
    assert "ANSYS could not start" in events[4]["text"] and "ModuleNotFoundError" in events[4]["text"]
    assert events[-1]["path"] is None  # the session reset itself; the next upload starts over
    assert list(post(api, "send", {"text": "hi"})[1])[0] == {"type": "error", "text": "Upload a part first."}


# ---------------------------------------------------------------- the real server


def test_launch_gui_streams_ndjson_next_to_the_pydiscovery_routes(tmp_path, monkeypatch):
    import launch_gui
    from src.pydiscovery.server import PyDiscoveryAPI

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = FakeOpenAI(reply(say("Hello from the fake model.")))
    assistant = AssistantAPI(tmp_path, make_client=lambda: client)
    server = launch_gui.build_server(PyDiscoveryAPI(tmp_path), port=0, assistant=assistant)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status = json.loads(urllib.request.urlopen(base + "/api/assistant", timeout=10).read())
        assert status["available"] is True and status["engine"] == "fitted"
        assert json.loads(urllib.request.urlopen(base + "/api/pydiscovery", timeout=10).read())["ok"] is True  # untouched
        raw = write_demo_part(tmp_path / "src.dsco").read_bytes()
        req = urllib.request.Request(base + "/api/assistant/upload?name=l_bracket.dsco", data=raw, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            assert r.headers["Content-Type"].startswith("application/x-ndjson")
            lines = [json.loads(line) for line in r.read().decode("utf-8").splitlines()]
        assert types(lines) == ["path", "text", "done"] and lines[1]["text"] == "Hello from the fake model."
        req = urllib.request.Request(base + "/api/assistant/send", data=json.dumps({"text": "thanks"}).encode(), method="POST")
        lines = [json.loads(line) for line in urllib.request.urlopen(req, timeout=30).read().decode("utf-8").splitlines()]
        assert types(lines) == ["text", "done"] and lines[0]["text"] == "done"
        req = urllib.request.Request(base + "/api/assistant/reset", data=b"", method="POST")
        assert json.loads(urllib.request.urlopen(req, timeout=10).read())["part"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_launch_gui_without_the_assistant_answers_404(tmp_path):
    import launch_gui

    server = launch_gui.build_server(None, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/assistant", timeout=10)
        assert err.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_launch_gui_main_takes_the_assistant_engine_flag(monkeypatch, tmp_path):
    import launch_gui

    seen = {}

    class Stop(Exception):
        pass

    monkeypatch.setattr(launch_gui, "load_api", lambda engine, predictor: None)
    monkeypatch.setattr(launch_gui, "load_assistant", lambda engine: seen.setdefault("engine", engine) and None)
    monkeypatch.setattr(launch_gui, "build_server", lambda api, assistant=None: (_ for _ in ()).throw(Stop()))
    with pytest.raises(Stop):
        launch_gui.main(["--no-browser", "--assistant-engine", "calculix"])
    assert seen["engine"] == "calculix"
    assert launch_gui.load_assistant("warp_drive") is None  # a bad engine prints one line and leaves the route off


def test_ndjson_is_one_line_per_event():
    assert ndjson({"type": "text", "text": "a\nb"}) == b'{"type": "text", "text": "a\\nb"}\n'
