"""app._stream turns progress events into lines on the running tool message."""
import app


class FakeAgent:
    def run(self, text):
        yield {"type": "tool", "name": "first_look", "input": {"a": 1}}
        yield {"type": "progress", "tool": "first_look", "text": "design 1 done"}
        yield {"type": "progress", "tool": "first_look", "text": "design 2 done"}
        yield {"type": "tool_result", "name": "first_look", "content": "ok", "is_error": False}


def test_progress_lines_and_table(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "agent", lambda: FakeAgent())
    monkeypatch.setattr(app.wb, "table", lambda: calls.append(1) or "TABLE")
    snaps = []
    for hist, tbl in app._stream([], "go"):
        assert tbl == "TABLE"
        snaps.append((hist[-1]["content"], hist[-1]["metadata"]["status"]))
    assert len(calls) == 4  # one table per event
    assert snaps[1][0].endswith("\n- design 1 done") and snaps[1][1] == "pending"
    assert snaps[2][0].endswith("- design 1 done\n- design 2 done") and snaps[2][1] == "pending"
    assert "- design 2 done" in snaps[3][0] and "Result:" in snaps[3][0] and snaps[3][1] == "done"


def test_stop_calls_request_stop(monkeypatch):
    hit = []
    monkeypatch.setattr(app.wb, "request_stop", lambda: hit.append(1))
    app.on_stop()
    assert hit == [1]


def test_agent_turns_share_one_queue():
    """Upload and send must never run two agent turns at once (they share Agent.messages)."""
    fns = [f for f in app.demo.fns.values() if getattr(f, "name", None) in ("on_upload", "on_send")]
    assert len(fns) == 3  # upload, send click, box submit
    assert {f.concurrency_id for f in fns} == {"agent"}
    assert all(f.concurrency_limit == 1 for f in fns)
    stop = next(f for f in app.demo.fns.values() if getattr(f, "name", None) == "on_stop")
    assert stop.queue is False  # Stop must bypass the busy queue


def test_queued_send_keeps_earlier_turn(monkeypatch):
    """A send queued behind a running turn arrives with stale browser history; nothing may be lost."""
    class TextAgent:
        def run(self, text):
            yield {"type": "text", "text": f"reply to {text}"}

    monkeypatch.setattr(app, "agent", lambda: TextAgent())
    monkeypatch.setattr(app.wb, "table", lambda: "TABLE")
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
