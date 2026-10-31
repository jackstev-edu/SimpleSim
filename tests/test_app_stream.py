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
