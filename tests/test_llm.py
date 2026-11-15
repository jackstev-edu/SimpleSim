"""OpenAI cost accounting: token prices, a hard budget cap per run, and the spend ledger."""
import csv
from types import SimpleNamespace as NS

import pytest

from src.llm import BudgetExceeded, append_spend, cost, counted_client


def usage_of(inp, cached, out):
    return NS(input_tokens=inp, output_tokens=out, input_tokens_details=NS(cached_tokens=cached))


class FakeClient:
    def __init__(self, usage):
        self.usage, self.calls = usage, 0
        self.responses = self

    def create(self, **kwargs):
        self.calls += 1
        return NS(usage=self.usage, output_text="ok")


def test_cost_charges_cached_input_at_the_cache_price():
    u = {"gpt-6-luna": {"input_tokens": 1_000_000, "cached_tokens": 400_000, "output_tokens": 100_000}}
    # 600k uncached x $0.10 + 400k cached x $0.01 + 100k out x $0.50, per million
    assert cost(u) == pytest.approx(0.06 + 0.004 + 0.05)


def test_unknown_model_has_no_price():
    with pytest.raises(ValueError, match="no price"):
        cost({"gpt-unknown": {"input_tokens": 1, "cached_tokens": 0, "output_tokens": 1}})


def test_counted_client_adds_usage_per_model():
    usage = {}
    c = counted_client(usage, budget_usd=1.0, client=FakeClient(usage_of(1000, 200, 50)))
    c.responses.create(model="gpt-6-luna", input="x")
    c.responses.create(model="gpt-6-luna", input="y")
    assert usage == {"gpt-6-luna": {"input_tokens": 2000, "cached_tokens": 400, "output_tokens": 100}}


def test_budget_cap_stops_further_calls():
    usage = {}
    fake = FakeClient(usage_of(1_000_000, 0, 0))   # $0.10 per call on luna
    c = counted_client(usage, budget_usd=0.15, client=fake)
    c.responses.create(model="gpt-6-luna", input="1")   # $0.10 spent
    c.responses.create(model="gpt-6-luna", input="2")   # under the cap before the call: allowed, $0.20 spent
    with pytest.raises(BudgetExceeded, match="0.20"):
        c.responses.create(model="gpt-6-luna", input="3")
    assert fake.calls == 2


def test_spend_ledger_appends_one_row_per_run(tmp_path):
    path = tmp_path / "spend.csv"
    u = {"gpt-6-luna": {"input_tokens": 1_000_000, "cached_tokens": 0, "output_tokens": 0}}
    append_spend(path, "after", 12, u)
    append_spend(path, "after2", 1, u)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert [r["label"] for r in rows] == ["after", "after2"]
    assert rows[0]["problems"] == "12" and rows[0]["cost_usd"] == "0.1000" and rows[0]["models"] == "gpt-6-luna"
    assert rows[1]["total_usd"] == "0.2000"


def test_session_budget_defaults_per_mode_and_env_override(monkeypatch):
    from src.llm import session_budget
    monkeypatch.delenv("SIMPLESIM_SESSION_BUDGET_USD", raising=False)
    assert session_budget("debug") == 0.20 and session_budget("demo") == 0.20
    assert session_budget("deploy") == 2.00
    monkeypatch.setenv("SIMPLESIM_SESSION_BUDGET_USD", "0.5")
    assert session_budget("deploy") == 0.5


def test_session_budget_must_be_a_positive_number(monkeypatch):
    import pytest
    from src.llm import session_budget
    for bad in ("0", "-1", "abc"):
        monkeypatch.setenv("SIMPLESIM_SESSION_BUDGET_USD", bad)
        with pytest.raises(ValueError, match="SIMPLESIM_SESSION_BUDGET_USD"):
            session_budget("debug")
