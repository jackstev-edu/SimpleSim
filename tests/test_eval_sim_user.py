"""Simulated user: hidden facts and rules in the system prompt, its own alternating history."""
from types import SimpleNamespace as NS

from src.eval.problems import Problem
from src.eval.sim_user import SIM_MODEL, SimUser


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": [dict(m) for m in kwargs["messages"]]})
        return NS(content=[NS(type="text", text=f"reply {len(self.calls)}")])


def problem(named=(), kind="clean_auto"):
    return Problem(id="p", part="x", kind=kind, request="Make it lighter.",
                   hidden_facts="The back face is bolted. 500 N down on the end.",
                   user_named_features=list(named),
                   key={"features": [{"id": "wall", "label": "base thickness"}]})


def test_system_prompt_holds_hidden_facts_and_rules():
    fake = FakeClient()
    SimUser(problem(), fake).reply("Which face is fixed?")
    s = fake.calls[0]["system"]
    assert "The back face is bolted. 500 N down on the end." in s
    assert "Never volunteer" in s and "face ids" in s and "OK, go ahead." in s


def test_named_features_approval_phrase():
    fake = FakeClient()
    SimUser(problem(named=["wall"]), fake).reply("Shall I run this?")
    s = fake.calls[0]["system"]
    assert "OK, go ahead with the features I asked for." in s and "base thickness" in s


def test_history_alternates_and_grows():
    fake = FakeClient()
    u = SimUser(problem(), fake)
    assert u.reply("first?") == "reply 1"
    u.reply("second?")
    msgs = fake.calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"] == "first?" and msgs[1]["content"] == "reply 1"


def test_uses_haiku():
    fake = FakeClient()
    SimUser(problem(), fake).reply("x")
    assert fake.calls[0]["model"] == SIM_MODEL == "claude-haiku-5-5"
