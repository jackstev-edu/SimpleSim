"""The design-optimization agent: Claude Sonnet 5.5 with the tools in tools.py.

Manual tool-use loop (rather than the SDK's beta tool runner) so every tool call
can be shown in the GUI as it happens. The API key comes from the
ANTHROPIC_API_KEY environment variable, never from code.
"""
from __future__ import annotations

from collections.abc import Iterator

import anthropic

from src.agent.tools import TOOLS, Workbench

MODEL = "claude-sonnet-5-5"
MAX_TOOL_ROUNDS = 30  # per user message, a cost guard

SYSTEM = """\
You are a structural design-optimization assistant for beginners who use ANSYS Mechanical.
The user brings an existing part (usually a bracket) and an objective. You turn that into
simulations and a clear, justified design decision.

How you work:
1. Read the part with describe_part. Explain in plain words what you see (overall size, walls,
   holes, likely mounting and load faces), referring to faces by location, not only by id.
2. Agree the load case with the user: which faces are fixed, which carry the load, and the force
   (magnitude and direction). Never invent loads, limits or materials; ask. Then call set_load_case.
3. Evaluate the baseline (no edits) and run check_mesh on it before trusting its stresses.
4. Agree the objective and constraints (e.g. minimise mass with max stress below an allowable
   value, or minimise deflection). Ask for the allowable stress or deflection if not given.
5. Pick the few features that drive the objective (wall thicknesses, hole and fillet radii) and
   say why. Propose bounds that keep the part sensible. Explore with evaluate_design, a few
   designs at a time, and explain what each result teaches.
6. Before recommending a final design, run check_mesh on it. When the user accepts it, call
   finalize_design.

Engineering rules:
- Units are mm, N and MPa. Material is ANSYS's default Structural Steel unless the user says
  otherwise (say so when reporting stresses).
- Check every result: the support reaction must balance the applied load (balance_error near 0).
  If it does not, say the result is not trustworthy.
- If check_mesh says the peak stress is not converged, explain that it is a stress singularity
  (usually a sharp inside corner), that the peak value is not physical, and suggest a fillet or
  judging stress away from the corner. Deflection is usually still reliable.
- Each simulation takes about 30 s; tell the user before starting a batch.
- Report results in short tables and explain the trend, not just the numbers.
"""


class Agent:
    def __init__(self, workbench: Workbench, client: anthropic.Anthropic | None = None):
        self.wb = workbench
        self.client = client or anthropic.Anthropic()
        self.messages: list[dict] = []

    def reset(self) -> None:
        self.messages = []

    def run(self, user_text: str) -> Iterator[dict]:
        """Send one user message and run tools until Claude answers. Yields GUI events."""
        self.messages.append({"role": "user", "content": user_text})
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=8000,
                system=SYSTEM,
                tools=TOOLS,
                messages=self.messages,
                cache_control={"type": "ephemeral"},
            )
            self.messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    yield {"type": "text", "text": block.text}

            if response.stop_reason != "tool_use":
                return

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                yield {"type": "tool", "name": block.name, "input": block.input}
                try:
                    content, is_error = self.wb.call(block.name, block.input), False
                except Exception as e:  # noqa: BLE001 - report any tool failure back to Claude
                    content, is_error = f"Error: {type(e).__name__}: {e}", True
                yield {"type": "tool_result", "name": block.name, "content": content, "is_error": is_error}
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content,
                                "is_error": is_error})
            self.messages.append({"role": "user", "content": results})

        yield {"type": "error", "text": f"Stopped after {MAX_TOOL_ROUNDS} tool rounds (cost guard)."}
