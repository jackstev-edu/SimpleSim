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


# ---------------------------------------------------------------- 3. quick replies under the chat input


def test_quick_replies_follow_the_question_and_send_as_a_message(open_gui, tmp_path):
    page, expect, client, errors = open_gui(*fast_run_script())
    page.get_by_role("tab", name="Agent", exact=True).click()
    expect(page.locator("#astQuick")).to_be_hidden()
    page.locator("#astFile").set_input_files(str(write_demo_part(tmp_path / "l_bracket.dsco")))
    expect(page.locator("#log")).to_contain_text("What safety factor do you want?")
    buttons = page.locator("#astQuick .qbtn")
    expect(buttons).to_have_text(["Use 1.5", "Use 2.0"])
    buttons.first.click()  # sends "Use 1.5" as an ordinary user message
    expect(page.locator("#log .msg.user").last).to_contain_text("Use 1.5")
    expect(page.locator("#log")).to_contain_text("The result is in the Variations table")
    assert any(isinstance(m, dict) and m.get("role") == "user" and m.get("content") == "Use 1.5" for m in client.sent[1]["input"])
    expect(page.locator("#astQuick .qbtn")).to_have_text(["Why this design?", "Compare to baseline", "Lighter but less safe", "Try aluminum"])
    assert errors == []


def test_options_on_a_text_event_replace_the_guess(open_gui):
    page, expect, _, errors = open_gui(assistant=True)
    page.get_by_role("tab", name="Agent", exact=True).click()
    page.evaluate("""() => { AST.status = { ok: true, available: true, engine: "fitted", model: "m", budget_usd: 1, budget_left_usd: 1 };
      astEvent({ type: "text", text: "Which support?", options: ["Back face", "Bottom face"] }); renderAst(); }""")
    expect(page.locator("#astQuick .qbtn")).to_have_text(["Back face", "Bottom face"])
    page.evaluate("""() => { astEvent({ type: "text", text: "Is the setup card right?" }); renderAst(); }""")
    expect(page.locator("#astQuick .qbtn")).to_have_text(["Yes, the setup is right", "No, something is off"])
    page.evaluate("""() => { astEvent({ type: "text", text: "Done. I set the material." }); renderAst(); }""")
    expect(page.locator("#astQuick")).to_be_hidden()  # a statement, not a question
    assert errors == []


# ---------------------------------------------------------------- 4. output rows with requirement sliders


def set_slider(page, metric, value):
    """Move one limit slider the way a drag does: input events while moving, change on release."""
    page.evaluate("""([m, v]) => { const s = document.querySelector(`[data-limit="${m}"]`); s.value = v;
      s.dispatchEvent(new Event("input", { bubbles: true })); s.dispatchEvent(new Event("change", { bubbles: true })); }""", [metric, value])


def test_output_rows_show_values_and_sliders_set_limits(open_gui):
    page, expect, _, errors = open_gui(query="?fixture=normal")
    page.get_by_role("tab", name="Parameters", exact=True).click()
    outs = page.locator("#pane-parameters .outs tbody tr")
    expect(outs).to_have_count(4)
    expect(outs.nth(0)).to_contain_text("Mass")
    expect(outs.nth(1)).to_contain_text("Max. Stress")  # the formula fixture is not a von Mises peak
    expect(page.locator("#pane-parameters .outs .onote")).to_contain_text("beam-formula")
    expect(page.locator('[data-limit="safety_factor"]')).to_have_count(1)
    expect(page.locator('tr[data-out="safety_factor"] .lval')).to_contain_text("≥ 2")
    expect(page.locator('tr[data-out="mass_kg"] .lval')).to_contain_text("no limit")
    expect(page.locator("#pane-variations th", has_text="Your limits")).to_have_count(0)
    # Raise the safety factor well above every row: nothing passes any more.
    set_slider(page, "safety_factor", 50)
    expect(page.locator('tr[data-out="safety_factor"] .lval')).to_have_class("lval edited")
    expect(page.locator("#pane-variations th", has_text="Your limits")).to_have_count(1)
    expect(page.locator("#pane-variations td.yl.no")).not_to_have_count(0)
    assert page.locator("#pane-variations td.yl.yes").count() == 0
    expect(page.locator("#bfoot")).to_contain_text("0 of")
    expect(page.locator("#bfoot")).to_contain_text("rows pass your limits")
    # The contract's own Feasible column is untouched.
    assert page.locator("#pane-variations td.m.yes").count() > 0
    page.click('[data-limits="reset"]')
    expect(page.locator("#pane-variations th", has_text="Your limits")).to_have_count(0)
    expect(page.locator('[data-limits="reopt"]')).to_be_disabled()  # nothing live to re-run against
    assert errors == []


def pyd_confirm_demo(page, expect):
    page.get_by_role("tab", name="PyDiscovery", exact=True).click()
    page.click('[data-pyd="demo"]')
    expect(page.locator("#pyd .card")).to_be_visible()
    for act in ("support", "load", "material"):
        if page.locator(f'[data-pyd="{act}"]').count():
            page.click(f'[data-pyd="{act}"]')
    page.click('[data-pyd="confirm"]')
    expect(page.locator("#pyd .pok")).to_contain_text("Confirmed")


def test_reoptimize_runs_the_emulator_with_the_dragged_limits(open_gui):
    page, expect, _, errors = open_gui()
    pyd_confirm_demo(page, expect)
    page.click('[data-pyd="optimize"]')
    expect(page.locator("#loaded")).to_contain_text("PyDiscovery result")
    page.get_by_role("tab", name="Parameters", exact=True).click()
    expect(page.locator("#pane-parameters .outs")).to_contain_text("Max. Von Mises Stress")  # fitted on CalculiX runs
    top = page.evaluate("() => Number(document.querySelector('[data-limit=\"safety_factor\"]').max)")
    set_slider(page, "safety_factor", top)  # the slider's own upper mark, above every row on the table
    want = page.evaluate("() => S.limits.safety_factor.value")
    assert want > 1.5
    expect(page.locator('[data-limits="reopt"]')).to_be_enabled()
    page.click('[data-limits="reopt"]')
    expect(page.locator("#pyd")).not_to_contain_text("Working")
    expect(page.locator("#loaded")).to_contain_text("PyDiscovery result")
    cons = page.evaluate("() => S.res.spec.constraints")
    assert {"metric": "safety_factor", "op": ">=", "value": want} in cons
    expect(page.locator("#pane-variations th", has_text="Your limits")).to_have_count(0)  # the new spec carries the limit
    assert errors == []


def test_reoptimize_on_the_fast_search_asks_the_assistant(open_gui, tmp_path):
    page, expect, client, errors = open_gui(*fast_run_script(), reply(say("Searching again.")))
    run_fast_path(page, expect, tmp_path)
    page.get_by_role("tab", name="Parameters", exact=True).click()
    set_slider(page, "max_disp_mm", 0.3)
    expect(page.locator('[data-limits="reopt"]')).to_be_enabled()
    page.click('[data-limits="reopt"]')
    expect(page.locator("#log .msg.user").last).to_contain_text("Run the search again with these limits")
    expect(page.locator("#log .msg.user").last).to_contain_text("max deflection <= 0.3 mm")
    expect(page.locator("#astStatus")).not_to_contain_text("Working")
    sent = [m.get("content") for m in client.sent[-1]["input"] if isinstance(m, dict) and m.get("role") == "user"]
    assert sent[-1].startswith("Run the search again with these limits: max deflection <= 0.3 mm, safety factor >= 1.5")
    assert errors == []
