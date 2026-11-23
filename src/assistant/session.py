"""AssistantSession: one conversation over the fast path (Jack's tools) and the ANSYS agent.

GUI-free on purpose: the team interface (Role 4) calls upload/send/stop/close and renders the
events. Events are the ANSYS agent's (text, tool, progress, tool_result, error) plus
{"type": "path", "path": "fast" | "slow", "reason": str}.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from src.assistant.front import FrontAgent
from src.assistant.path_rule import part_path
from src.assistant.study import handoff_message
from src.pydiscovery.tools import PyDiscoveryTools


class _Borrowed:
    """The session's workbench lent to Jack's ANSYS engine: the engine's close() must not shut it down."""

    def __init__(self, wb):
        self._wb = wb

    def __getattr__(self, name):
        return getattr(self._wb, name)

    def close(self) -> None:
        pass


class AssistantSession:
    def __init__(self, client, out_dir: Path, backend: str = "ansys",
                 make_workbench=None, make_agent=None):
        self.client = client
        self.out_dir = Path(out_dir)
        self.backend = backend
        # None means the real Workbench and Agent, imported on first use: src.agent.tools loads the ANSYS
        # packages, so the fast path (and the GUI server on a laptop without ANSYS) must not import them here.
        self.make_workbench = make_workbench
        self.make_agent = make_agent
        self.path: str | None = None
        self.part_path: Path | None = None
        self.front: FrontAgent | None = None
        self.workbench = None  # the shared workbench while the ANSYS agent has the chat
        self.agent = None
        self._wb = None  # one per session: PyMechanical can start only once per process

    def _shared_wb(self):
        if self._wb is None:
            if self.make_workbench is None:
                from src.agent.tools import Workbench  # needs the ANSYS packages; callers catch the ImportError

                self.make_workbench = Workbench
            self._wb = self.make_workbench(self.out_dir / "session")
        return self._wb

    def _new_tools(self) -> PyDiscoveryTools:
        tools = PyDiscoveryTools(backend=self.backend)
        backend = tools.app.backend
        if hasattr(backend, "_engine"):  # Jack's AnsysBackend: its engine borrows the shared workbench
            from src.sim.ansys_engine import AnsysEngine
            backend._engine = AnsysEngine(out_dir=self.out_dir / "ansys_engine", mesh_check=backend.mesh_check,
                                          workbench_factory=lambda _out: _Borrowed(self._shared_wb()))
        return tools

    # ------------------------------------------------------------ public

    def upload(self, path: str | Path) -> Iterator[dict]:
        """Start over on a new part: Jack's reader first, then the path rule."""
        self.close()
        self.path, self.part_path = None, Path(path)
        self.front = FrontAgent(self._new_tools(), self.client, self.out_dir / "assistant")
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
        yield from self._front_turn(f"I uploaded my part '{self.part_path.name}'. It is already open; "
                                    "use describe_setup and tell me what you see.")

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
        """End the current part's conversation; the shared workbench stays for the next part."""
        if self.front is not None:
            self.front.close()
        self.workbench = self.agent = None

    def shutdown(self) -> None:
        """At app exit: also close SpaceClaim and Mechanical."""
        self.close()
        if self._wb is not None:
            self._wb.close()
            self._wb = None

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
        """Start the ANSYS agent on the same file in the shared workbench, passing on what is confirmed."""
        front = self.front
        front.close()  # ends the fast loop; the shared workbench stays open
        if front.handoff_reason is None:
            front.log.add("spec", f"path: slow ({reason})")
        front.log.save(self.out_dir / "assistant" / f"handoff_log-{datetime.now():%Y%m%d-%H%M%S-%f}")
        self.path = "slow"
        yield {"type": "path", "path": "slow", "reason": reason}
        yield {"type": "text", "text": "Starting ANSYS (about a minute)…"}
        try:
            self.workbench = self._shared_wb()
            self.workbench.load_part(self.part_path)
            if self.make_agent is None:
                from src.agent.agent import Agent  # same: imported only when the ANSYS agent takes over

                self.make_agent = Agent
            self.agent = self.make_agent(self.workbench, self.client)
        except Exception as e:  # noqa: BLE001 - no ANSYS, licence limit, unreadable file
            self.path, self.front, self.workbench, self.agent = None, None, None, None
            yield {"type": "error", "text": f"ANSYS could not start ({type(e).__name__}: {e}). "
                                            "Upload the part again to retry."}
            return
        yield from self.agent.run(handoff_message(self.part_path.name, front.tools.part, front.inputs, reason,
                                                  front.given))
