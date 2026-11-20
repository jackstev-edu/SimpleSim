"""app._stream turns session events into chat messages (progress lines, path lines, warnings)."""
import pytest

# Role 3 packages live in src/sim/requirements.txt, not the root one, so CI skips this file.
pytest.importorskip("ansys.geometry.core")
pytest.importorskip("ansys.mechanical.core")
pytest.importorskip("ansys.optislang.core")
pytest.importorskip("gradio")

import app


class FakeSession:
    """Stands in for AssistantSession: send/upload yield the given events; table counts its calls."""
    def __init__(self, events=(), raises=None):
        self.events, self.raises = list(events), raises
        self.tables, self.stops, self.uploaded = 0, 0, None
        self.workbench = None

    def send(self, text):
        yield from (e(text) if callable(e) else e for e in self.events)
        if self.raises:
            raise self.raises

    def upload(self, path):
        self.uploaded = path
        yield {"type": "text", "text": f"opened {path}"}

    def table(self):
        self.tables += 1
        return "TABLE"

    def stop(self):
        self.stops += 1


PROGRESS = [{"type": "tool", "name": "first_look", "input": {"a": 1}},
            {"type": "progress", "tool": "first_look", "text": "design 1 done"},
            {"type": "progress", "tool": "first_look", "text": "design 2 done"},
            {"type": "tool_result", "name": "first_look", "content": "ok", "is_error": False}]


def test_progress_lines_and_table(monkeypatch):
    fake = FakeSession(PROGRESS)
    monkeypatch.setattr(app, "session", lambda: fake)
    snaps = []
    for hist, tbl in app._stream([], "go"):
        assert tbl == "TABLE"
        snaps.append((hist[-1]["content"], hist[-1]["metadata"]["status"]))
    assert fake.tables == 4  # one table per event
    assert snaps[1][0].endswith("\n- design 1 done") and snaps[1][1] == "pending"
    assert snaps[2][0].endswith("- design 1 done\n- design 2 done") and snaps[2][1] == "pending"
    assert "- design 2 done" in snaps[3][0] and "Result:" in snaps[3][0] and snaps[3][1] == "done"


def test_path_event_is_shown(monkeypatch):
    fake = FakeSession([{"type": "path", "path": "slow", "reason": "its shape is not a known bracket"}])
    monkeypatch.setattr(app, "session", lambda: fake)
    hist = list(app._stream([], "go"))[-1][0]
    assert hist[-1]["content"] == "🛣️ **ANSYS search**: its shape is not a known bracket"
    fake.events = [{"type": "path", "path": "fast", "reason": "this is the L-bracket v0"}]
    hist = list(app._stream([], "go"))[-1][0]
    assert hist[-1]["content"].startswith("🛣️ **Fast search (team predictor + ANSYS finalists)**: ")


def test_upload_goes_through_the_session(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(app, "session", lambda: fake)
    monkeypatch.setattr(app, "_chat", [])
    hist = None
    for hist, _ in app.on_upload("part.scdocx", []):
        pass
    assert fake.uploaded == "part.scdocx" and hist[-1]["content"] == "opened part.scdocx"


def test_stop_calls_session_stop(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(app, "session", lambda: fake)
    app.on_stop()
    assert fake.stops == 1


def test_agent_turns_share_one_queue():
    """Upload and send must never run two agent turns at once (they share the conversation)."""
    fns = [f for f in app.demo.fns.values() if getattr(f, "name", None) in ("on_upload", "on_send")]
    assert len(fns) == 3  # upload, send click, box submit
    assert {f.concurrency_id for f in fns} == {"agent"}
    assert all(f.concurrency_limit == 1 for f in fns)
    stop = next(f for f in app.demo.fns.values() if getattr(f, "name", None) == "on_stop")
    assert stop.queue is False  # Stop must bypass the busy queue


def test_queued_send_keeps_earlier_turn(monkeypatch):
    """A send queued behind a running turn arrives with stale browser history; nothing may be lost."""
    monkeypatch.setattr(app, "session", lambda: FakeSession([lambda t: {"type": "text", "text": f"reply to {t}"}]))
    monkeypatch.setattr(app, "_chat", [{"role": "assistant", "content": "earlier turn"}])
    stale = []  # what the browser held when the user clicked Send
    hist = None
    for hist, _ in app.on_send("second", stale):
        pass
    assert [m["content"] for m in hist] == ["earlier turn", "second", "reply to second"]


def test_send_clears_textbox_before_the_turn_runs():
    assert app.take_text("hello") == ("", "hello")
    clear = [f for f in app.demo.fns.values() if getattr(f, "name", None) == "take_text"]
    assert len(clear) == 2 and all(f.queue is False for f in clear)  # not stuck behind a running turn


def test_gui_session_uses_the_counted_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    monkeypatch.setattr(app, "_session", None)
    try:
        assert app.session().client.responses.create.__name__ == "counted"
    finally:
        app._session = None


def test_budget_cap_ends_the_turn_with_a_warning(monkeypatch):
    from src.llm import BudgetExceeded

    fake = FakeSession([{"type": "text", "text": "hi"}],
                       raises=BudgetExceeded("budget cap reached: $0.20 spent of $0.20"))
    monkeypatch.setattr(app, "session", lambda: fake)
    hist = list(app._stream([], "go"))[-1][0]
    assert "budget cap" in hist[-1]["content"] and "SIMPLESIM_SESSION_BUDGET_USD" in hist[-1]["content"]


def test_open_buttons_need_a_workbench(monkeypatch):
    import gradio as gr
    import pytest

    monkeypatch.setattr(app, "session", lambda: FakeSession())
    for fn in (app.on_open_ansys, app.on_open_mechanical):
        with pytest.raises(gr.Error):
            fn()


def test_session_spend_goes_to_the_ledger(monkeypatch, tmp_path):
    ledger = tmp_path / "spend.csv"
    monkeypatch.setattr(app, "USAGE", {})
    assert app.record_spend(ledger) is None and not ledger.exists()  # nothing spent, no row
    monkeypatch.setattr(app, "USAGE", {"gpt-6-luna": {"input_tokens": 1000, "cached_tokens": 0, "output_tokens": 100}})
    app.record_spend(ledger)
    rows = ledger.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2 and ",gui," in rows[1]
