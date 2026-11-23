"""GUI polish checks in a browser: window stacking, chat rendering, quick replies, output sliders,
the compare view and the verdict. Driven against the real server and a fake model.

Set SIMPLESIM_CHROME to a Chrome executable (Playwright's own Chromium works) to run these; they skip
otherwise, like the other browser checks. No API key, no openai package, no ANSYS and no network.
"""

import os
from threading import Thread

import pytest

from launch_gui import build_server
from src.pydiscovery.assistant_server import AssistantAPI
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.server import PyDiscoveryAPI
from tests.test_assistant_api import FakeOpenAI, call, reply, say


@pytest.fixture(scope="module")
def browser():
    executable = os.environ.get("SIMPLESIM_CHROME")
    if not executable:
        pytest.skip("set SIMPLESIM_CHROME to run the optional browser checks")
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as tools:
        with tools.chromium.launch(executable_path=executable) as chrome:
            yield chrome


@pytest.fixture
def open_gui(browser, tmp_path, monkeypatch):
    """The real GUI with the PyDiscovery API and, when a script is given, the assistant on a fake model."""
    from playwright.sync_api import expect

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    servers, pages = [], []

    def open_page(*script, assistant=False, query=""):
        client = FakeOpenAI(*script)
        api = AssistantAPI(tmp_path, make_client=lambda: client) if assistant or script else None
        server = build_server(PyDiscoveryAPI(tmp_path, predictor="power_law"), assistant=api)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        pages.append(page)
        errors = []  # script errors; a 404 for an API the server does not serve is a resource error, not a script error
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.goto(f"http://127.0.0.1:{server.server_port}/{query}")
        expect(page.locator("#pydTab")).to_be_visible()
        return page, expect, client, errors

    yield open_page
    for page in pages:
        page.close()
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join()


# A scripted fast-path run: upload, one answer, optimize, the explanation call, the verdict call, the closing line.
def fast_run_script(closing="The lighter design is in the table."):
    return [
        reply(say("I see an L-bracket. What safety factor do you want? 1.5 is a common choice.")),
        reply(call("record_study_inputs", {"safety_factor": 1.5, "objective": "mass_kg", "direction": "minimize"}, "a")),
        reply(call("confirm_setup", {}, "b"), call("optimize_design", {}, "c")),
        reply(say("The recommended design is lighter than the baseline.")),  # the explanation step's own model call
        reply(say("Adopt it. It is lighter and meets every limit. Checked with the fitted model only. Next, solve it once in Ansys.")),  # the verdict's call
        reply(say(closing)),
    ]


def run_fast_path(page, expect, tmp_path, answer="1.5, minimize mass, the setup is right"):
    page.get_by_role("tab", name="Agent", exact=True).click()
    page.locator("#astFile").set_input_files(str(write_demo_part(tmp_path / "l_bracket.dsco")))
    expect(page.locator("#log")).to_contain_text("What safety factor do you want?")
    page.fill("#astText", answer)
    page.click("#astSend")
    expect(page.locator("#log")).to_contain_text("The result is in the Variations table")
    expect(page.locator("#loaded")).to_contain_text("assistant result")


# ---------------------------------------------------------------- 1. windows never outrank the overlays


def test_hundred_focus_clicks_keep_the_tooltip_on_top(open_gui):
    page, expect, _, errors = open_gui(query="?fixture=normal")
    bars = [page.locator("#winVar .wtabs"), page.locator("#winAgent .wtabs")]
    for i in range(100):
        bars[i % 2].click(position={"x": 200, "y": 12})  # the empty part of the tab bar, so no tab switches
    z = page.evaluate("() => [...document.querySelectorAll('.win')].map((w) => Number(w.style.zIndex))")
    assert max(z) < 300 and min(z) >= 5 and z[0] != z[1]  # inside the window band, still ordered
    page.get_by_role("tab", name="Graphs", exact=True).click()
    svg = page.locator("#graphs svg[data-ch='0']")
    expect(svg).to_be_visible()
    # Hover the first plotted point of the first chart, in client coordinates.
    pt = page.evaluate("""() => {
      const svg = document.querySelector("#graphs svg[data-ch='0']"), b = svg.getBoundingClientRect(), ch = CHARTS[0];
      const k = b.width / ch.W, p = ch.pts[0];
      return { x: b.left + p.x * k, y: b.top + p.y * k };
    }""")
    page.mouse.move(pt["x"], pt["y"])
    tip = page.locator("#tip")
    expect(tip).to_be_visible()
    on_top = page.evaluate("""() => {
      const t = document.getElementById("tip"), b = t.getBoundingClientRect();
      t.style.pointerEvents = "auto"; // the tooltip ignores the pointer by design; let elementFromPoint see it
      const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      t.style.pointerEvents = "";
      return t.contains(hit);
    }""")
    assert on_top, "the tooltip was covered by a window"
    assert errors == []


# ---------------------------------------------------------------- 2. readable replies, Markdown as DOM nodes


def test_replies_render_bold_lists_and_code_without_raw_asterisks(open_gui, tmp_path):
    md = "Use **1.5** as the safety factor.\n\n- thinner *arm*\n- call `set_material` next\n\n1. first\n2. second <b>not html</b>"
    page, expect, _, errors = open_gui(reply(say(md)))
    page.get_by_role("tab", name="Agent", exact=True).click()
    page.locator("#astFile").set_input_files(str(write_demo_part(tmp_path / "l_bracket.dsco")))
    msg = page.locator("#log .msg.agent").last
    expect(msg).to_contain_text("Use 1.5 as the safety factor.")
    expect(msg.locator("strong")).to_have_text("1.5")
    expect(msg.locator("em")).to_have_text("arm")
    expect(msg.locator("code")).to_have_text("set_material")
    expect(msg.locator("ul.md li")).to_have_count(2)
    expect(msg.locator("ol.md li")).to_have_count(2)
    expect(msg.locator("ol.md li").last).to_have_text("second <b>not html</b>")  # tags in model text stay text
    assert msg.locator("b").count() == 0
    assert "**" not in msg.inner_text() and "`" not in msg.inner_text()
    size = page.evaluate("() => [getComputedStyle(document.querySelector('#log .msg.agent')).fontSize, getComputedStyle(document.getElementById('astText')).fontSize]")
    assert size == ["14.5px", "14.5px"]
    assert errors == []
