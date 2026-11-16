"""Start the SimpleSim demo GUI: serve src/ui on a free local port and open the browser.

    python launch_gui.py                         # Ctrl+C to stop
    python launch_gui.py --no-browser
    python launch_gui.py --engine fitted --predictor gp   # PyDiscovery engine and predictor (default auto)

Static files come from src/ui exactly as before. GET and POST under /api/pydiscovery go to the
PyDiscovery emulator (src/pydiscovery/server.py) so the GUI's PyDiscovery tab can open a part,
confirm the setup, optimize and save. Without src.pydiscovery the API answers 404 and the tab hides.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
UI = ROOT / "src" / "ui"
API_PREFIX = "/api/pydiscovery"
MAX_BODY_BYTES = 50 * 1024 * 1024


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    api = None  # a PyDiscoveryAPI, or None when src.pydiscovery can't be imported

    def log_message(self, *args) -> None:  # keep the console readable
        pass

    def do_GET(self) -> None:
        if self._is_api():
            self._api("GET")
        else:
            super().do_GET()  # static serving, unchanged

    def do_POST(self) -> None:
        if self._is_api():
            self._api("POST")
        else:
            self.send_error(404)

    def _is_api(self) -> bool:
        path = urlsplit(self.path).path
        return path == API_PREFIX or path.startswith(API_PREFIX + "/")

    def _api(self, method: str) -> None:
        if self.api is None:
            self._json(404, {"ok": False, "message": "PyDiscovery is not available on this server."})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._json(413, {"ok": False, "message": "Request body over the 50 MB limit."})
            return
        body = self.rfile.read(length) if length else b""
        url = urlsplit(self.path)
        status, payload = self.api.handle(method, url.path, parse_qs(url.query), body)
        self._json(status, payload)

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def load_api(engine: str = "fitted", predictor: str = "auto"):
    """The PyDiscovery API, or None with one printed line when the emulator can't load."""
    try:
        from src.pydiscovery.server import PyDiscoveryAPI  # lazy: the GUI must still serve without it

        return PyDiscoveryAPI(root=ROOT, engine=engine, predictor=predictor)
    except Exception as exc:  # any import or setup failure leaves the GUI static
        print(f"PyDiscovery unavailable: {exc}; serving the GUI only", flush=True)
        return None


def build_server(api, host: str = "127.0.0.1", port: int = 0) -> http.server.ThreadingHTTPServer:
    """The GUI server on host:port (port 0 asks the OS for any free port) with the API attached."""
    handler = type("GUIHandler", (QuietHandler,), {"api": api})  # a subclass per server, so tests don't share state
    return http.server.ThreadingHTTPServer((host, port), functools.partial(handler, directory=str(UI)))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-browser", action="store_true", help="print the address instead of opening the browser")
    ap.add_argument("--engine", default="fitted", help="PyDiscovery engine: fitted, formula, calculix, ansys, discovery")
    ap.add_argument("--predictor", default="auto", help="shortlisting model: auto, power_law or gp")
    args = ap.parse_args(argv)
    if not (UI / "index.html").exists():
        sys.exit(f"Cannot find {UI / 'index.html'}")
    sys.path.insert(0, str(ROOT))  # so src.pydiscovery imports when started from another folder
    api = load_api(args.engine, args.predictor)
    server = build_server(api)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"SimpleSim demo GUI running at {url}", flush=True)
    if api is not None:
        print(f"PyDiscovery engine: {api.engine}. Predictor {api.predictor_note}.", flush=True)
    print("Close this window or press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
