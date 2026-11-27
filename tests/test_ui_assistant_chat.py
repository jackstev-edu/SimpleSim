"""The assistant chat bar in the shared GUI, driven in a browser against the real server and a fake model.

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
    """The real GUI and both APIs; the assistant talks to a scripted fake model."""
    from playwright.sync_api import expect

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    servers, pages = [], []

    def open_page(*script, assistant=True):
        client = FakeOpenAI(*script)
        api = AssistantAPI(tmp_path, make_client=lambda: client) if assistant else None
        server = build_server(PyDiscoveryAPI(tmp_path, predictor="power_law"), assistant=api)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        pages.append(page)
        errors = []  # script errors; a 404 for an API the server does not serve is expected and logged by the browser as a resource error
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.goto(f"http://127.0.0.1:{server.server_port}/")
        expect(page.locator("#pydTab")).to_be_visible()
        return page, expect, client, errors

    yield open_page
    for page in pages:
        page.close()
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join()


def test_chat_bar_lives_under_the_agent_tab_and_keeps_the_four_tabs(open_gui):
    page, expect, _, errors = open_gui()
    expect(page.locator("#winAgent").get_by_role("tab")).to_have_text(["PyDiscovery", "Recommendations", "Graphs", "Agent"])
    expect(page.locator("#achat")).to_be_hidden()  # the page opens on PyDiscovery
    page.get_by_role("tab", name="Agent", exact=True).click()
    expect(page.locator("#achat")).to_be_visible()
    expect(page.locator("#astStatus")).to_contain_text("engine fitted")
    expect(page.locator("#astStatus")).to_contain_text("No part uploaded yet")
    expect(page.locator("#astStop")).to_be_disabled()
    assert errors == []


def test_upload_then_optimize_lands_in_the_variations_table(open_gui, tmp_path):
    page, expect, client, errors = open_gui(
        reply(say("I see an L-bracket. What safety factor do you want?")),
        reply(call("record_study_inputs", {"safety_factor": 1.5, "objective": "mass_kg", "direction": "minimize"}, "a")),
        reply(call("confirm_setup", {}, "b"), call("optimize_design", {}, "c")),
        reply(say("The recommended design is lighter than the baseline.")),  # the explanation step's own model call
        reply(say("The lighter design is in the table.")))
    page.get_by_role("tab", name="Agent", exact=True).click()
    page.locator("#astFile").set_input_files(str(write_demo_part(tmp_path / "l_bracket.dsco")))
    expect(page.locator("#log")).to_contain_text("Fast search")
    expect(page.locator("#log")).to_contain_text("What safety factor do you want?")
    expect(page.locator("#astStatus")).to_contain_text("Part l_bracket.dsco on the fast search")
    page.fill("#astText", "1.5, minimize mass, the setup is right")
    page.click("#astSend")
    expect(page.locator("#log")).to_contain_text("Assistant tool: optimize_design")
    expect(page.locator("#log")).to_contain_text("The lighter design is in the table.")
    expect(page.locator("#log")).to_contain_text("The result is in the Variations table")
    expect(page.locator("#loaded")).to_contain_text("assistant result")
    expect(page.locator("#pane-variations")).to_be_visible()
    assert page.locator("#pane-variations tbody tr").count() > 1
    assert page.evaluate("() => S.res.spec.bracket_family") == "l_bracket_v0"
    assert len(client.sent) == 6 and errors == []  # the sixth call writes the verdict that rides with the run
    page.get_by_role("tab", name="Agent", exact=True).click()
    page.click("#astReset")
    expect(page.locator("#astStatus")).to_contain_text("No part uploaded yet")
    expect(page.locator("#log")).not_to_contain_text("Assistant tool")


def test_without_the_assistant_api_the_bar_stays_hidden(open_gui):
    page, expect, _, errors = open_gui(assistant=False)
    page.get_by_role("tab", name="Agent", exact=True).click()
    expect(page.locator("#log")).to_be_visible()
    expect(page.locator("#achat")).to_be_hidden()
    assert errors == []
