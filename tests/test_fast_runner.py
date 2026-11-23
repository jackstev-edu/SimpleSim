"""Fast-path benchmark runner with a scripted session and student (no LLM, no ANSYS, no network)."""
import json
from pathlib import Path
from types import SimpleNamespace

from src.eval import fast_runner
from src.eval.fast_keys import FastProblem
from src.llm import BudgetExceeded

STUDY = {"force_n": 500.0, "angle_deg": -90.0, "material_key": "structural_steel", "safety_factor": 1.5,
         "objective": {"metric": "mass_kg", "direction": "minimize"}, "limits": [], "vary": ["all"]}


def problem(handoff=False):
    key = {"study": STUDY, "must_ask": [], "expect_handoff": handoff,
           "model_optimum": {"design": {}, "objective_value": 0.2}, "sim_optimum": {"design_id": None, "reason": "x"}}
    return FastProblem("p", "l_bracket_v0", "make it lighter", "facts", key, Path("p.yaml"))


class FakeResult:
    def model_dump(self, mode="json"):
        return {"spec": {"load": {"force_n": 500.0}}, "recommended": None}


class FakeSession:
    made = []

    def __init__(self, client, out_dir, backend="ansys", **kw):
        self.kw, self.backend, self.turns, self.path = kw, backend, 0, None
        self.part = SimpleNamespace(last_result=None)
        self.front = SimpleNamespace(tools=SimpleNamespace(part=self.part))
        self.finish_after = 2
        FakeSession.made.append(self)

    def upload(self, path):
        assert Path(path).exists()
        yield {"type": "path", "path": "fast", "reason": "known family"}
        yield {"type": "text", "text": "This is the L-bracket. Where is it fixed?"}

    def send(self, text):
        self.turns += 1
        yield {"type": "tool", "name": "describe_setup", "input": {}}
        if self.turns >= self.finish_after:
            self.part.last_result = FakeResult()
            yield {"type": "tool", "name": "optimize_design", "input": {}}
        yield {"type": "text", "text": f"turn {self.turns}: anything else?"}

    def close(self):
        pass


class Student:
    def __init__(self, problem, client=None):
        self.n = 0

    def reply(self, text):
        self.n += 1
        return f"answer {self.n}"


def run(p, tmp_path, factory=FakeSession, **kw):
    return fast_runner.run_fast_problem(p, client=None, out_dir=tmp_path, session_factory=factory, student_factory=Student, **kw)


def test_runner_stops_on_result(tmp_path):
    log = run(problem(), tmp_path)
    assert log["result"] == FakeResult().model_dump() and log["stopped"] is None
    assert FakeSession.made[-1].backend == "fitted"
    assert [e["seq"] for e in log["events"]] == list(range(len(log["events"])))
    assert log["events"][2] == {"seq": 2, "type": "student", "text": "make it lighter"}


def test_runner_stops_at_reply_cap(tmp_path):
    class Never(FakeSession):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.finish_after = 10_000
    log = run(problem(), tmp_path, factory=Never, max_replies=12)
    assert log["stopped"] == "reply cap" and log["result"] is None
    assert sum(e["type"] == "student" for e in log["events"]) == 13  # the request plus 12 replies


def test_handoff_uses_stubs(tmp_path):
    class Slow(FakeSession):
        def send(self, text):
            self.path = "slow"
            yield {"type": "path", "path": "slow", "reason": "titanium"}
    log = run(problem(handoff=True), tmp_path, factory=Slow)
    kw = FakeSession.made[-1].kw
    assert {"make_workbench", "make_agent"} <= set(kw)
    wb = kw["make_workbench"](tmp_path)
    wb.load_part(tmp_path / "x")  # the stub accepts any part and starts nothing
    assert list(kw["make_agent"](wb, None).run("hi"))[0]["type"] == "text"
    assert log["stopped"] == "handoff"


def test_budget_stop(tmp_path):
    class Broke(FakeSession):
        def send(self, text):
            raise BudgetExceeded("cap")
            yield
    log = run(problem(), tmp_path, factory=Broke)
    assert log["stopped"] == "budget"


def test_regrade_reads_saved_logs(tmp_path):
    log = run(problem(), tmp_path)
    (tmp_path / "p.json").write_text(json.dumps(log), encoding="utf-8")
    graded = fast_runner.regrade(tmp_path, {"p": problem()})
    assert graded[0]["id"] == "p" and "force" in graded[0]["checks"]
    assert "| p |" in fast_runner.summary_markdown(graded)


def test_unexpected_handoff_stops_without_ansys(tmp_path):
    class Slow(FakeSession):
        def send(self, text):
            self.path = None  # the real session resets the path when ANSYS fails to start
            yield {"type": "path", "path": "slow", "reason": "load direction"}
    log = run(problem(handoff=False), tmp_path, factory=Slow)
    assert {"make_workbench", "make_agent"} <= set(FakeSession.made[-1].kw)  # never the real ANSYS
    assert log["stopped"] == "handoff"
    assert sum(e["type"] == "student" for e in log["events"]) == 1


def test_error_ends_problem_without_crashing(tmp_path):
    # I4: an API error or empty student reply ends this problem as a graded failure
    class Mute(Student):
        def reply(self, text):
            raise RuntimeError("simulated student gave no text")

    class Never(FakeSession):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.finish_after = 10_000
    log = fast_runner.run_fast_problem(problem(), client=None, out_dir=tmp_path, session_factory=Never, student_factory=Mute)
    assert log["stopped"].startswith("error: RuntimeError")
