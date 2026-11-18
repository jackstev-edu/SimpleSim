"""AssistantSession: one conversation over the fast path (Jack's tools) and the ANSYS agent.

GUI-free on purpose: the team interface (Role 4) calls upload/send/stop/close and renders the
events. Events are the ANSYS agent's (text, tool, progress, tool_result, error) plus
{"type": "path", "path": "fast" | "slow", "reason": str}.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from src.agent.agent import Agent
from src.agent.tools import Workbench
from src.assistant.front import FrontAgent
from src.assistant.path_rule import part_path
from src.assistant.study import handoff_message
from src.pydiscovery.tools import PyDiscoveryTools


class AssistantSession:
    def __init__(self, client, out_dir: Path, backend: str = "ansys",
                 make_workbench=Workbench, make_agent=Agent):
        self.client = client
        self.out_dir = Path(out_dir)
        self.backend = backend
        self.make_workbench = make_workbench
        self.make_agent = make_agent
        self.path: str | None = None
        self.part_path: Path | None = None
        self.front: FrontAgent | None = None
        self.workbench = None
        self.agent = None

    # ------------------------------------------------------------ public

    def upload(self, path: str | Path) -> Iterator[dict]:
        """Start over on a new part: Jack's reader first, then the path rule."""
        self.close()
        self.path, self.part_path = None, Path(path)
        self.front = FrontAgent(PyDiscoveryTools(backend=self.backend), self.client, self.out_dir / "assistant")
        opened = self.front.call_tool("open_part", {"path": str(path)})
        if not opened.get("ok"):
            yield from self._handoff(opened.get("message") or "the fast search cannot read this file")
            return
        decision = part_path(self.front.tools.part)
        if decision is not None and decision.path == "slow":
            yield from self._handoff(decision.reason)
            return
        if decision is not None:
            self.path = "fast"
            yield {"type": "path", "path": "fast", "reason": decision.reason}
        yield from self._front_turn(f"I uploaded my part '{self.part_path.name}'. "
                                    "Please look at it and tell me what you see.")

    def send(self, text: str) -> Iterator[dict]:
        if self.path == "slow":
            yield from self.agent.run(text)
        elif self.front is not None:
            yield from self._front_turn(text)
        else:
            yield {"type": "error", "text": "Upload a part first."}

    def stop(self) -> None:
        if self.workbench is not None:
            self.workbench.request_stop()

    def table(self) -> list[list]:
        return self.workbench.table() if self.path == "slow" and self.workbench is not None else []

    def close(self) -> None:
        if self.front is not None:
            self.front.close()
        if self.workbench is not None:
            self.workbench.close()
        self.workbench = self.agent = None

    # ------------------------------------------------------------ internals

    def _front_turn(self, text: str) -> Iterator[dict]:
        for ev in self.front.run(text):
            if ev["type"] == "handoff":
                yield from self._handoff(ev["reason"])
                return
            yield ev
        part = self.front.tools.part
        if self.path is None and part is not None and part.family is not None:
            self.path = "fast"  # the user confirmed it is the known bracket

    def _handoff(self, reason: str) -> Iterator[dict]:
        """Close the fast path's ANSYS, start the ANSYS agent on the same file, pass on what is confirmed."""
        front = self.front
        front.close()  # one embedded Mechanical per process
        if front.handoff_reason is None:
            front.log.add("spec", f"path: slow ({reason})")
        front.log.save(self.out_dir / "assistant" / "handoff_log")
        self.path = "slow"
        yield {"type": "path", "path": "slow", "reason": reason}
        yield {"type": "text", "text": "Starting ANSYS (about a minute)…"}
        self.workbench = self.make_workbench(self.out_dir / "session")
        self.workbench.load_part(self.part_path)
        self.agent = self.make_agent(self.workbench, self.client)
        yield from self.agent.run(handoff_message(self.part_path.name, front.tools.part, front.inputs, reason))
