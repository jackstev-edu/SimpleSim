"""LLM simulated user for practice runs: a beginner who knows the true facts of the problem.

It answers only what it is asked, never gives face ids, never corrects the agent, and approves
any proposal (spec: "Claude's calls"), so the graded tool arguments are the agent's own choices.
"""
from __future__ import annotations

import anthropic

from src.eval.problems import Problem

SIM_MODEL = "claude-haiku-5-5"
MAX_TOKENS = 2000  # thinking + reply
APPROVE = "OK, go ahead."
APPROVE_NAMED = "OK, go ahead with the features I asked for."

RULES = """\
You are a beginner engineer talking to a design assistant about a bracket you want to improve.
You already sent your first message. These are the true facts you know:

{facts}

Rules:
- Answer only what the assistant asks, in plain words. Never volunteer a fact it did not ask for.
- Never give face ids, coordinates or numbers you do not have. Describe places in plain words.
- Do not correct the assistant's choices or interpretations; just answer its questions.
- If it asks about something not in your facts, say you do not know and that it should choose a
  sensible default.
- When it proposes a set of features or a search and asks for your OK, reply exactly: "{approve}"
{named}{complex}- Keep replies under 60 words.
"""


class SimUser:
    def __init__(self, problem: Problem, client: anthropic.Anthropic | None = None):
        self.problem = problem
        self.client = client or anthropic.Anthropic()
        self.messages: list[dict] = []
        labels = {f["id"]: f.get("label", f["id"]) for f in problem.key.get("features", [])}
        named = [labels[i] for i in problem.user_named_features]
        named_rule = ("- The features you want changed are: " + "; ".join(named) + ". If asked, name only these.\n"
                      if named else "")
        complex_rule = ""
        if problem.kind == "complex":
            complex_rule = ("- If the assistant asks which features to change, "
                            + ("name: " + "; ".join(named) + ".\n" if named else "say you are not sure.\n"))
        told = bool(named) and problem.kind != "complex"  # on a complex part they are named only when asked
        self.system = RULES.format(facts=problem.hidden_facts.strip(), approve=APPROVE_NAMED if told else APPROVE,
                                   named=named_rule, complex=complex_rule)

    def reply(self, agent_text: str) -> str:
        self.messages.append({"role": "user", "content": agent_text})
        # The model thinks before it answers and thinking counts against max_tokens: with 300 the
        # whole budget went to thinking and the reply was empty (2026-11-11). The 60-word rule keeps
        # the reply itself short.
        r = self.client.messages.create(model=SIM_MODEL, max_tokens=MAX_TOKENS, system=self.system,
                                        messages=self.messages)
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        if not text:
            raise RuntimeError(f"simulated user gave no text (stop_reason {r.stop_reason})")
        self.messages.append({"role": "assistant", "content": text})
        return text
