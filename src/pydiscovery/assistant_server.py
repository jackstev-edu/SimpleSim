"""The GUI's assistant API: Role 3's AssistantSession behind a few HTTP routes, with no network needed to test it.

launch_gui.py routes GET and POST under /api/assistant to AssistantAPI.handle(), next to the PyDiscovery
routes. handle() is a pure dispatcher returning (status, payload): payload is a JSON object for the plain
routes and an iterator of event objects for the two streaming routes, which the launcher writes out as
newline-delimited JSON as the session produces them. docs/assistant-api.md describes the routes and events.

    GET  /api/assistant                 status: available?, engine, model, budget left, current path and part
    POST /api/assistant/upload?name=f   raw file bytes in the body; saved under data/raw/uploads, then session.upload()
    POST /api/assistant/send            {"text": ...}; streams session.send() events
    POST /api/assistant/stop            ask a running ANSYS search to stop
    POST /api/assistant/reset           close the session (SpaceClaim and Mechanical too); the next upload starts fresh

The OpenAI client is Role 3's counted_client (src/llm.py): every call adds to a usage ledger and the
session stops at the budget cap. The API key is read by the openai package from OPENAI_API_KEY; this
module only reports whether it is set, never its value.
"""

from __future__ import annotations

import json
import os
import threading
import traceback
from collections.abc import Iterator
from pathlib import Path

from src.llm import MODE, MODEL, BudgetExceeded, cost, counted_client, session_budget

from .backends import BACKENDS
from .server import UPLOAD_SUFFIXES, _first, _json_object, safe_name

PREFIX = "/api/assistant"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class AssistantAPI:
    """One AssistantSession (created on the first upload) plus the routes the GUI's chat box needs."""

    def __init__(self, root: str | Path, engine: str = "fitted", make_client=None, budget_usd: float | None = None,
                 out_dir: str | Path | None = None):
        """make_client: None for the real OpenAI client; the tests pass a factory for a fake with responses.create()."""
        if engine not in BACKENDS:
            raise ValueError(f"Unknown engine {engine!r}. Choose from {sorted(BACKENDS)}.")
        self.root = Path(root)
        self.engine = engine
        self.uploads = self.root / "data" / "raw" / "uploads"
        self.out_dir = Path(out_dir) if out_dir is not None else self.root / "outputs"  # gitignored, as app.py uses it
        self.make_client = make_client
        self.budget_usd = session_budget() if budget_usd is None else float(budget_usd)
        self.usage: dict = {}  # tokens spent through this server, per model; the cap applies to the whole server run
        self.session = None
        self.part_name: str | None = None
        self._turn = threading.Lock()  # one turn at a time: two turns would interleave the model's message history
        self._sent_result: int | None = None  # id() of the last fast-path Result sent in a done event

    # ------------------------------------------------------------ availability and status

    def availability(self) -> tuple[bool, str, dict]:
        """(available, plain-language message, flags). Never includes the key itself."""
        flags = {"openai_installed": _openai_installed(), "api_key_set": bool(os.environ.get("OPENAI_API_KEY"))}
        if self.make_client is not None:
            return True, "Ready (test client).", flags
        missing = []
        if not flags["openai_installed"]:
            missing.append("the openai package is not installed (it is in src/sim/requirements.txt)")
        if not flags["api_key_set"]:
            missing.append("OPENAI_API_KEY is not set in the environment")
        if missing:
            return False, "The assistant can't run: " + " and ".join(missing) + ". Fix that and restart the launcher.", flags
        return True, "Ready.", flags

    def status(self) -> dict:
        available, message, flags = self.availability()
        spent = cost(self.usage)
        return {"ok": True, "available": available, "message": message, **flags,
                "engine": self.engine, "model": MODEL, "mode": MODE,
                "budget_usd": round(self.budget_usd, 6), "spent_usd": round(spent, 6), "budget_left_usd": round(max(self.budget_usd - spent, 0.0), 6),
                "path": self.session.path if self.session is not None else None, "part": self.part_name,
                "busy": self._turn.locked()}

    # ------------------------------------------------------------ dispatch

    def handle(self, method: str, path: str, query: dict, body: bytes) -> tuple[int, dict | Iterator[dict]]:
        """Route one request. Never raises: unexpected errors become a 500 with a short message."""
        try:
            return self._route(method.upper(), path.rstrip("/") or "/", query or {}, body or b"")
        except Exception as exc:  # the traceback goes to the console, never to the browser
            traceback.print_exc()
            return 500, {"ok": False, "message": f"Server error: {type(exc).__name__}: {str(exc)[:200]}"}

    def _route(self, method: str, path: str, query: dict, body: bytes) -> tuple[int, dict | Iterator[dict]]:
        if path == PREFIX:
            if method != "GET":
                return 405, {"ok": False, "message": "Use GET for the status route."}
            return 200, self.status()
        if not path.startswith(PREFIX + "/"):
            return 404, {"ok": False, "message": f"Unknown path {path}."}
        rest = path[len(PREFIX) + 1:]
        if method != "POST":
            return 405, {"ok": False, "message": f"Use POST for {path}."}
        if rest == "upload":
            return self._upload(_first(query, "name"), body)
        if rest == "send":
            return self._send(body)
        if rest == "stop":
            if self.session is not None:
                self.session.stop()
            return 200, {**self.status(), "message": "Stop requested." if self.session is not None else "Nothing is running."}
        if rest == "reset":
            self.reset()
            return 200, {**self.status(), "message": "Session closed. Upload a part to start again."}
        return 404, {"ok": False, "message": f"Unknown path {path}."}

    # ------------------------------------------------------------ the routes

    def _upload(self, name: str | None, body: bytes) -> tuple[int, dict | Iterator[dict]]:
        if not name:
            return 400, {"ok": False, "message": "Give the file name as ?name=<file name>."}
        safe = safe_name(name)
        if not safe:
            return 400, {"ok": False, "message": f"{name!r} isn't a usable file name."}
        suffix = Path(safe).suffix.lower()
        if suffix not in UPLOAD_SUFFIXES:
            return 400, {"ok": False, "message": f"{suffix or 'That file type'} isn't supported. Use {', '.join(UPLOAD_SUFFIXES)}."}
        if not body:
            return 400, {"ok": False, "message": "The upload is empty."}
        if len(body) > MAX_UPLOAD_BYTES:
            return 400, {"ok": False, "message": f"{safe} is over the 50 MB upload limit."}
        available, message, _ = self.availability()
        if not available:
            return 503, {"ok": False, "message": message}
        if not self._turn.acquire(blocking=False):
            return 409, {"ok": False, "message": "A turn is still running. Wait for it to finish, or stop it first."}
        try:
            self.uploads.mkdir(parents=True, exist_ok=True)
            dst = self.uploads / safe
            dst.write_bytes(body)
            session = self._session()
            self.part_name = safe
        except Exception:
            self._turn.release()
            raise
        return 200, self._stream(lambda: session.upload(dst))

    def _send(self, body: bytes) -> tuple[int, dict | Iterator[dict]]:
        args = _json_object(body)
        if args is None or not isinstance(args.get("text"), str) or not args["text"].strip():
            return 400, {"ok": False, "message": 'The body must be a JSON object like {"text": "..."} with a non-empty text.'}
        if self.session is None:
            return 200, {"ok": False, "message": "Upload a part first."}
        if not self._turn.acquire(blocking=False):
            return 409, {"ok": False, "message": "A turn is still running. Wait for it to finish, or stop it first."}
        text = args["text"]
        return 200, self._stream(lambda: self.session.send(text))

    def _stream(self, events) -> Iterator[dict]:
        """The session's events, then one 'done' event; the turn lock is held until the stream is exhausted or closed."""

        def gen():
            try:
                try:
                    yield from events()
                except BudgetExceeded as exc:
                    yield {"type": "error", "text": f"The {exc}. No further model calls this session (${cost(self.usage):.2f} spent). "
                                                    "Restart the launcher to continue, or raise the cap with SIMPLESIM_SESSION_BUDGET_USD.",
                           "budget_exceeded": True}
                except Exception as exc:  # noqa: BLE001 - the browser gets one line, the console the traceback
                    traceback.print_exc()
                    yield {"type": "error", "text": f"Server error: {type(exc).__name__}: {str(exc)[:200]}"}
                yield self._done()
            finally:
                self._turn.release()

        return gen()

    def _done(self) -> dict:
        s = self.session
        run = self._new_run()  # first: the verdict inside it makes paid model calls that the spend below must count
        spent = cost(self.usage)
        done = {"type": "done", "path": s.path if s is not None else None, "part": self.part_name,
                "spent_usd": round(spent, 6), "budget_left_usd": round(max(self.budget_usd - spent, 0.0), 6),
                "table": s.table() if s is not None else []}
        if run is not None:
            done["run"] = run  # the three contract files of a fast-path optimize, once, so the GUI can show them
        return done

    def _new_run(self) -> dict | None:
        """The fast path's latest optimize as {study_spec, prediction, result}, or None when nothing new ran."""
        front = getattr(self.session, "front", None)
        part = getattr(getattr(front, "tools", None), "part", None)
        result = getattr(part, "last_result", None)
        if result is None or id(result) == self._sent_result:
            return None
        self._sent_result = id(result)
        prediction = getattr(part, "last_prediction", None)
        run = {"study_spec": result.spec.model_dump(mode="json"),
               "prediction": prediction.model_dump(mode="json") if prediction is not None else None,
               "result": result.model_dump(mode="json")}
        verdict = self._verdict(run["result"], run["prediction"])
        if verdict is not None:  # None: even the template failed; the run still goes out, without a verdict box
            run["verdict"] = verdict
        return run

    def _verdict(self, result: dict, prediction: dict | None) -> dict | None:
        """The judgement call on the run (src/assistant/verdict.py): one model call under the budget cap, else the template."""
        from src.assistant.verdict import template_verdict, verdict

        try:
            text, source, warnings = verdict(result, self.session.client, prediction=prediction)
        except Exception as exc:  # noqa: BLE001 - a paid search result must never be lost over its summary
            if not isinstance(exc, BudgetExceeded):
                traceback.print_exc()
            note = f"the {exc}" if isinstance(exc, BudgetExceeded) else f"{type(exc).__name__}: {str(exc)[:120]}"
            try:
                text, source, warnings = template_verdict(result, prediction), "template", [f"Verdict used the template: {note}."]
            except Exception:  # noqa: BLE001 - the template failed too
                traceback.print_exc()
                return None
        return {"text": text, "source": source, "warnings": warnings}

    # ------------------------------------------------------------ the session

    def _session(self):
        if self.session is None:
            from src.assistant.session import AssistantSession  # Role 3's session; its ANSYS imports are lazy

            client = counted_client(self.usage, self.budget_usd, client=self.make_client() if self.make_client else None)
            self.session = AssistantSession(client, self.out_dir, backend=self.engine)
        return self.session

    def reset(self) -> None:
        """Close the session, SpaceClaim and Mechanical included. The spend ledger keeps counting across resets."""
        if self.session is not None:
            self.session.shutdown()
        self.session, self.part_name = None, None

    def shutdown(self) -> float:
        """At launcher exit: close everything and return the USD spent through this server."""
        self.reset()
        return cost(self.usage)


def _openai_installed() -> bool:
    try:
        import openai  # noqa: F401  Role 3's package, from src/sim/requirements.txt

        return True
    except ImportError:
        return False


def ndjson(event: dict) -> bytes:
    """One event as a line of newline-delimited JSON."""
    return json.dumps(event).encode("utf-8") + b"\n"
