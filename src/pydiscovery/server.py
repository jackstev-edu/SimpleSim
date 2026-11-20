"""The GUI's PyDiscovery API: one tools session behind a few HTTP routes, with no network needed to test it.

launch_gui.py routes GET and POST under /api/pydiscovery to PyDiscoveryAPI.handle(). The GUI's
PyDiscovery tab calls the same 11 tools (tools.py) that Role 3's agent calls, through /tool/<name>.
handle() is a pure dispatcher returning (status, json), so the tests call it without a server.

    GET  /api/pydiscovery                  status: engine, predictor and the tool names
    POST /api/pydiscovery/session          {engine?, predictor?}: start a fresh session
    POST /api/pydiscovery/demo-part        write the synthetic demo .dsco if missing, then open it
    POST /api/pydiscovery/upload?name=f    raw file bytes in the body; saved under data/raw/uploads
    POST /api/pydiscovery/tool/<name>      JSON args; returns tools.call(name, args)
"""

from __future__ import annotations

import json
import re
import traceback
from pathlib import Path

from .backends import BACKENDS
from .demo_part import write_demo_part
from .part_file import UnsupportedPartError, open_part
from .session import PREDICTORS
from .tools import TOOL_SPECS, PyDiscoveryTools

PREFIX = "/api/pydiscovery"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
UPLOAD_SUFFIXES = (".dsco", ".scdocx", ".scdoc", ".step", ".stp")
RUNS_CSV = Path("data/processed/formal_v0/runs.csv")  # Role 1's v0 runs, relative to the repo root
MODEL_DATASET_CSV = Path("data/processed/formal_v0/model_dataset.csv")  # preferred: carries Poisson's ratio


class PyDiscoveryAPI:
    """One PyDiscoveryTools session plus the routes the GUI needs around it."""

    def __init__(self, root: str | Path, engine: str = "fitted", predictor: str = "auto"):
        self.root = Path(root)
        self.uploads = self.root / "data" / "raw" / "uploads"
        self.saves = self.root / "data" / "processed" / "pydiscovery_gui"
        self.demo_part = self.root / "data" / "raw" / "l_bracket_demo.dsco"
        self.engine = self.predictor = self.predictor_note = None
        self.tools: PyDiscoveryTools | None = None
        self.start_session(engine, predictor)

    # ------------------------------------------------------------ session

    def start_session(self, engine: str = "fitted", predictor: str = "auto") -> None:
        """Replace the session. Raises ValueError for an unknown engine or predictor."""
        if engine not in BACKENDS:
            raise ValueError(f"Unknown engine {engine!r}. Choose from {sorted(BACKENDS)}.")
        picked, note = self._pick_predictor(predictor)
        runs = None
        if picked == "gp":  # the v0 GP trains from the model dataset when it is there; other families use their own
            v0 = self.root / MODEL_DATASET_CSV if (self.root / MODEL_DATASET_CSV).exists() else self.root / RUNS_CSV
            runs = {"l_bracket_v0": v0}
        self.tools = PyDiscoveryTools(engine, picked, runs=runs)
        self.engine, self.predictor, self.predictor_note = engine, picked, note

    def _pick_predictor(self, predictor: str) -> tuple[str, str]:
        if predictor != "auto":
            if predictor not in PREDICTORS:
                raise ValueError(f"Unknown predictor {predictor!r}. Choose from {PREDICTORS} or 'auto'.")
            return predictor, f"{predictor}: chosen on the command line"
        runs = self.root / RUNS_CSV
        if not runs.exists():
            return "power_law", f"power_law: {RUNS_CSV.as_posix()} is missing, so the GP has nothing to train on"
        try:
            import sklearn  # noqa: F401  the GP needs scikit-learn, from src/models/requirements.txt
        except ImportError:
            return "power_law", "power_law: scikit-learn is not installed, so the GP can't run"
        return "gp", f"gp: scikit-learn is installed and {RUNS_CSV.as_posix()} exists"

    def status(self) -> dict:
        return {"ok": True, "engine": self.engine, "predictor": self.predictor, "predictor_note": self.predictor_note,
                "tools": [s["name"] for s in TOOL_SPECS]}

    # ------------------------------------------------------------ dispatch

    def handle(self, method: str, path: str, query: dict, body: bytes) -> tuple[int, dict]:
        """Route one request. Never raises: unexpected errors become a 500 with a short message."""
        try:
            return self._route(method.upper(), path.rstrip("/") or "/", query or {}, body or b"")
        except Exception as exc:  # the traceback goes to the console, never to the browser
            traceback.print_exc()
            return 500, {"ok": False, "message": f"Server error: {type(exc).__name__}: {str(exc)[:200]}"}

    def _route(self, method: str, path: str, query: dict, body: bytes) -> tuple[int, dict]:
        if path == PREFIX:
            if method != "GET":
                return 405, {"ok": False, "message": "Use GET for the status route."}
            return 200, self.status()
        if not path.startswith(PREFIX + "/"):
            return 404, {"ok": False, "message": f"Unknown path {path}."}
        rest = path[len(PREFIX) + 1:]
        if method != "POST":
            return 405, {"ok": False, "message": f"Use POST for {path}."}
        if rest == "session":
            return self._session(body)
        if rest == "demo-part":
            if not self.demo_part.exists():
                write_demo_part(self.demo_part)  # synthetic until a real l_bracket.dsco is saved from Discovery
            return 200, self.tools.call("open_part", {"path": str(self.demo_part)})
        if rest == "upload":
            return self._upload(_first(query, "name"), body)
        if rest.startswith("tool/"):
            return self._tool(rest[len("tool/"):], body)
        return 404, {"ok": False, "message": f"Unknown path {path}."}

    def _session(self, body: bytes) -> tuple[int, dict]:
        args = _json_object(body)
        if args is None:
            return 400, {"ok": False, "message": "The body must be a JSON object with optional engine and predictor."}
        try:
            self.start_session(args.get("engine", self.engine), args.get("predictor", "auto"))
        except ValueError as exc:
            return 400, {"ok": False, "message": str(exc)}
        return 200, self.status()

    def _tool(self, name: str, body: bytes) -> tuple[int, dict]:
        args = _json_object(body)
        if args is None:
            return 400, {"ok": False, "message": "Tool arguments must be a JSON object."}
        if name == "save_design" and not args.get("path"):
            args = {**args, "path": str(self.default_save_path())}
        return 200, self.tools.call(name, args)

    def default_save_path(self) -> Path:
        """data/processed/pydiscovery_gui/<part stem>_optimized.dsco for Discovery parts, .json otherwise."""
        part = self.tools.part
        if part is None:
            return self.saves / "part_optimized.json"
        discovery = part.setup.file_type == "discovery" and bool(part.file_names)  # same rule as Part.save
        return self.saves / f"{Path(part.setup.path).stem}_optimized{'.dsco' if discovery else '.json'}"

    # ------------------------------------------------------------ upload

    def _upload(self, name: str | None, body: bytes) -> tuple[int, dict]:
        if not name:
            return 400, {"ok": False, "message": "Give the file name as ?name=<file name>."}
        safe = safe_name(name)
        if not safe:
            return 400, {"ok": False, "message": f"{name!r} isn't a usable file name."}
        suffix = Path(safe).suffix.lower()
        if suffix == ".stl":
            try:
                open_part(safe)  # refuses STL before touching the disk, with the reader's own message
            except UnsupportedPartError as exc:
                return 200, {"ok": False, "message": str(exc)}
        if suffix not in UPLOAD_SUFFIXES:
            return 400, {"ok": False, "message": f"{suffix or 'That file type'} isn't supported. Use {', '.join(UPLOAD_SUFFIXES)}."}
        if not body:
            return 400, {"ok": False, "message": "The upload is empty."}
        if len(body) > MAX_UPLOAD_BYTES:
            return 400, {"ok": False, "message": f"{safe} is over the 50 MB upload limit."}
        self.uploads.mkdir(parents=True, exist_ok=True)
        dst = self.uploads / safe
        dst.write_bytes(body)
        return 200, self.tools.call("open_part", {"path": str(dst)})


def safe_name(name: str) -> str:
    """The base name only, with anything outside letters, digits, dot, dash and underscore replaced."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return base


def _first(query: dict, key: str) -> str | None:
    """urllib.parse.parse_qs gives lists; plain dicts give strings. Accept both."""
    v = query.get(key)
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def _json_object(body: bytes) -> dict | None:
    """Decode a JSON object body; an empty body is {}; anything else is None."""
    if not body.strip():
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
