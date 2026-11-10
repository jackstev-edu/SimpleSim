"""Start the SimpleSim demo GUI: serve src/ui on a free local port and open the browser.

    python launch_gui.py            # Ctrl+C to stop
    python launch_gui.py --no-browser
"""

from __future__ import annotations

import functools
import http.server
import sys
import webbrowser
from pathlib import Path

UI = Path(__file__).resolve().parent / "src" / "ui"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the console readable
        pass


def main() -> None:
    if not (UI / "index.html").exists():
        sys.exit(f"Cannot find {UI / 'index.html'}")
    # Port 0 asks the OS for any free port, so a second copy never collides.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(UI)))
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"SimpleSim demo GUI running at {url}", flush=True)
    print("Close this window or press Ctrl+C to stop.", flush=True)
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
