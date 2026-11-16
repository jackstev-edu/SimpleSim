"""Show a PyDiscovery run in the team GUI, without changing anything in src/ui.

Copies src/ui to a temporary folder, adds the run as the fixture "pydiscovery" (plus a menu
entry in that copy only), serves it on a free local port and opens the browser on it.

    python -m src.pydiscovery.demo --yes --gui            # run, then show
    python -m src.pydiscovery.gui                         # show the last run again
    python -m src.pydiscovery.gui data/processed/pydiscovery_demo
"""

from __future__ import annotations

import functools
import http.server
import shutil
import sys
import tempfile
import webbrowser
from pathlib import Path

from contracts.models import Prediction, Result, StudySpec

UI = Path(__file__).resolve().parents[1] / "ui"  # src/ui
NAME = "pydiscovery"
LAST_RUN = Path("data/processed/pydiscovery_demo")


def write_run(folder: str | Path, spec: StudySpec, prediction: Prediction, result: Result, suffix: str = "") -> list[Path]:
    """The three contract files the GUI reads: study_spec, prediction and result."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for stem, model in (("study_spec", spec), ("prediction", prediction), ("result", result)):
        path = folder / f"{stem}{suffix}.json"
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
        out.append(path)
    return out


def read_run(folder: str | Path) -> tuple[StudySpec, Prediction, Result]:
    folder = Path(folder)
    missing = [n for n in ("study_spec", "prediction", "result") if not (folder / f"{n}.json").exists()]
    if missing:
        raise FileNotFoundError(f"{folder} has no {', '.join(missing)}.json. Run: python -m src.pydiscovery.demo --yes")
    load = lambda n, cls: cls.model_validate_json((folder / f"{n}.json").read_text(encoding="utf-8"))  # noqa: E731
    return load("study_spec", StudySpec), load("prediction", Prediction), load("result", Result)


def prepare_site(spec: StudySpec, prediction: Prediction, result: Result, root: str | Path | None = None) -> Path:
    """A throwaway copy of src/ui with this run added as ?fixture=pydiscovery."""
    if not (UI / "index.html").exists():
        raise FileNotFoundError(f"Can't find the GUI at {UI / 'index.html'}")
    root = Path(root) if root else Path(tempfile.mkdtemp(prefix="simplesim_gui_"))
    site = root / "ui"
    shutil.copytree(UI, site, ignore=shutil.ignore_patterns("__pycache__", "*.py", "*.md"))
    write_run(site / "fixtures", spec, prediction, result, suffix=f"_{NAME}")
    page = site / "index.html"
    page.write_text(add_menu_entry(page.read_text(encoding="utf-8")), encoding="utf-8")
    return site


def add_menu_entry(html: str) -> str:
    """Add 'PyDiscovery result' to the fixture menu of the copy; leave the page alone if it has changed."""
    option = '<option value="out_of_range">Out of range</option>'
    suffixes = 'const SUFFIX = { normal: "", infeasible: "_infeasible", out_of_range: "_out_of_range" };'
    if option not in html or suffixes not in html:
        return html  # ?fixture=pydiscovery still loads; the menu just won't name it
    html = html.replace(option, option + f'\n          <option value="{NAME}">PyDiscovery result</option>', 1)
    return html.replace(suffixes, suffixes[:-3] + f', {NAME}: "_{NAME}" }};', 1)


def serve(site: Path, open_browser: bool = True) -> None:
    """Serve the copy until Ctrl+C, then delete it."""
    handler = functools.partial(_Quiet, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)  # port 0: any free port
    url = f"http://127.0.0.1:{server.server_port}/?fixture={NAME}"
    print(f"\nSimpleSim GUI with the PyDiscovery result: {url}\nPress Ctrl+C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        server.server_close()
        shutil.rmtree(site.parent, ignore_errors=True)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the console readable
        pass


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else LAST_RUN
    serve(prepare_site(*read_run(folder)), open_browser="--no-browser" not in sys.argv)
