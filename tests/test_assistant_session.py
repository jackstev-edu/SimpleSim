"""AssistantSession: one conversation over the fast path and the ANSYS agent."""
from dataclasses import replace

import pytest

from src.assistant.session import AssistantSession
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.families import L_BRACKET_V0
from tests.test_agent_loop import ScriptClient, call, msg, resp


class FakeWB:
    def __init__(self, out_dir, order):
        self.order, self.loaded, self.closed = order, None, False

    def load_part(self, path):
        self.order.append("load_part")
        self.loaded = str(path)

    def request_stop(self):
        self.order.append("stop")

    def table(self):
        return [["d1"]]

    def close(self):
        self.closed = True


class FakeAgent:
    def __init__(self, wb, client=None):
        self.wb, self.texts = wb, []

    def run(self, text):
        self.texts.append(text)
        yield {"type": "text", "text": "ANSYS agent here"}


@pytest.fixture
def make(tmp_path):
    order = []

    def _make(*script, backend="formula"):
        return AssistantSession(ScriptClient(*script), tmp_path / "out", backend=backend,
                                make_workbench=lambda out_dir: FakeWB(out_dir, order),
                                make_agent=lambda wb, client: FakeAgent(wb, client)), order
    return _make


def _family_part(tmp_path):
    return write_demo_part(tmp_path / "l.dsco")


def _odd_part(tmp_path):
    odd = replace(L_BRACKET_V0, labels={k: f"Wobble{i}" for i, k in enumerate(L_BRACKET_V0.labels)})
    return write_demo_part(tmp_path / "odd.dsco", family=odd)


def test_family_upload_is_fast(make, tmp_path):
    s, order = make(resp(msg("I see an L-bracket.")))
    events = list(s.upload(_family_part(tmp_path)))
    assert events[0]["type"] == "path" and events[0]["path"] == "fast"
    assert s.path == "fast" and s.workbench is None and "load_part" not in order


def test_unknown_part_hands_off_at_once(make, tmp_path):
    s, _ = make()
    path = _odd_part(tmp_path)
    events = list(s.upload(path))
    first = [e for e in events if e["type"] == "path"][0]
    assert first["path"] == "slow"
    assert s.workbench.loaded == str(path)
    assert first["reason"] in s.agent.texts[0]


def test_backend_closed_before_workbench(make, tmp_path):
    s, order = make(resp(msg("ok")), resp(call("switch_to_ansys", {"reason": "the user asked for ANSYS"})))
    list(s.upload(_family_part(tmp_path)))
    s.front.tools.app.backend.close = lambda: order.append("close")
    list(s.send("use ANSYS"))
    assert order.index("close") < order.index("load_part")


def test_user_override_mid_setup(make, tmp_path):
    s, _ = make(resp(msg("ok")), resp(call("record_study_inputs", {"safety_factor": 1.5}, "a")),
                resp(call("switch_to_ansys", {"reason": "the user asked for ANSYS"}, "b")))
    list(s.upload(_family_part(tmp_path)))
    list(s.send("1.5 is fine. Actually, use ANSYS."))
    text = s.agent.texts[0]
    assert "1.5" in text and "objective" not in text
    assert s.path == "slow"


def test_messages_route_to_ansys_after_handoff(make, tmp_path):
    s, _ = make()
    list(s.upload(_odd_part(tmp_path)))
    events = list(s.send("hi"))
    assert s.agent.texts[-1] == "hi" and events == [{"type": "text", "text": "ANSYS agent here"}]


def test_new_upload_resets_to_front(make, tmp_path):
    s, _ = make(resp(msg("I see an L-bracket.")))
    list(s.upload(_odd_part(tmp_path)))
    old_wb = s.workbench
    events = list(s.upload(_family_part(tmp_path)))
    assert not old_wb.closed and s.workbench is None and s.agent is None  # kept for the next ANSYS start
    assert s.path == "fast" and events[0]["path"] == "fast"
    assert s.front.inputs.safety_factor is None


def test_unsupported_file_goes_slow(make, tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("x", encoding="utf-8")
    s, _ = make()
    events = list(s.upload(bad))
    first = [e for e in events if e["type"] == "path"][0]
    assert first["path"] == "slow" and "supported" in first["reason"]


def test_table_and_stop(make, tmp_path):
    s, order = make(resp(msg("ok")))
    list(s.upload(_family_part(tmp_path)))
    assert s.table() == []
    s.stop()
    list(s.upload(_odd_part(tmp_path)))
    assert s.table() == [["d1"]]
    s.stop()
    assert order.count("stop") == 1


def test_close_twice_is_safe(make, tmp_path):
    s, _ = make(resp(msg("ok")))
    list(s.upload(_family_part(tmp_path)))
    s.close()
    s.close()
    s.shutdown()
    s.shutdown()


def test_one_workbench_for_the_whole_session(make, tmp_path):
    """PyMechanical starts once per process: the fast engine and the ANSYS agent share one workbench."""
    s, order = make(resp(msg("ok")), resp(call("switch_to_ansys", {"reason": "the user asked for ANSYS"})),
                    backend="ansys")
    list(s.upload(_family_part(tmp_path)))
    engine = s.front.tools.app.backend._engine
    lent = engine._factory(tmp_path)
    list(s.send("use ANSYS"))
    assert s.workbench is lent._wb  # the agent got the same workbench the engine borrowed
    s.front.close()  # the engine's close must not shut the shared workbench
    assert not s.workbench.closed
    shared = s.workbench
    s.shutdown()
    assert shared.closed
