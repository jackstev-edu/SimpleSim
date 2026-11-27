"""The front-layer prompt asks for plain text with bold used at most once per reply (GUI polish PR)."""

from src.assistant.front import PROMPT


def test_prompt_limits_bold_to_one_per_reply():
    p = " ".join(PROMPT.split())
    assert "Use bold sparingly" in p and "at most once per reply" in p and "No headings" in p
