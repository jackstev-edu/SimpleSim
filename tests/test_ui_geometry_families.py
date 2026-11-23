"""Shared GUI browser coverage using the real local fitted-engine API."""

import os
from threading import Thread

import pytest

from contracts import FAMILY_PARAMS, Result
from launch_gui import build_server
from src.models.template_families import TEMPLATES
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.families import FAMILIES
from src.pydiscovery.server import PyDiscoveryAPI

T02 = "t02_single_slot_v1"
T03 = "t03_twin_slot_v1"
T09 = "t09_perforated_gusset_v1"
STEP = {"name": "template.step", "mimeType": "model/step", "buffer": b"ISO-10303-21;\nEND-ISO-10303-21;"}


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
def open_gui(browser, tmp_path):
    """Host the real GUI and API, keeping uploads and results under pytest's temp directory."""
    from playwright.sync_api import expect

    servers = []
    pages = []

    def open_page(engine="fitted", width=1600, query=""):
        api = PyDiscoveryAPI(tmp_path, engine=engine, predictor="power_law") if engine else None
        server = build_server(api)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        page = browser.new_page(viewport={"width": width, "height": 900})
        pages.append(page)
        page.goto(f"http://127.0.0.1:{server.server_port}/{query}")
        expect(page.locator("#banner")).to_be_hidden()
        if engine:
            expect(page.locator("#pydTab")).to_be_visible()
        return page, expect

    yield open_page
    for page in pages:
        page.close()
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join()


def button(page, action):
    return page.locator(f'[data-pyd="{action}"]')


def choose_template(page, expect, family):
    page.locator("#pydFile").set_input_files(STEP)
    selector = page.locator('[data-form="family"]')
    expect(selector.locator("option")).to_have_count(len(FAMILIES))
    assert selector.locator("option").evaluate_all(
        "(options) => Object.fromEntries(options.map(o => [o.value, o.textContent]))"
    ) == {key: fam.title for key, fam in FAMILIES.items()}
    selector.select_option(family)
    button(page, "family").click()
    expect(page.locator("#pyd .card")).to_contain_text(FAMILIES[family].title)


@pytest.mark.parametrize("family", list(FAMILY_PARAMS))
def test_family_workflow(open_gui, tmp_path, family):
    page, expect = open_gui()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    is_template = family in TEMPLATES
    if is_template:
        choose_template(page, expect, family)
        inputs = page.locator("#pydDimensions input")
        expect(inputs).to_have_count(len(FAMILY_PARAMS[family]))
        assert inputs.evaluate_all(
            "(inputs) => Object.fromEntries(inputs.map(i => [i.name, [Number(i.min), Number(i.max)]]))"
        ) == {name: list(bounds) for name, bounds in FAMILIES[family].ranges_mm.items()}
        expect(page.locator("#pyd")).to_contain_text("Fitted-model optimization only.")
        button(page, "support").click()
        button(page, "load").click()
        button(page, "material").click()
        if family == T03:
            width = page.locator('#pydDimensions input[name="slot_width_mm"]')
            width.fill("100")
            page.get_by_role("button", name="Apply dimensions").click()
            assert not width.evaluate("(input) => input.validity.valid")
            width.fill("9")
            with page.expect_response(lambda r: r.url.endswith("/tool/set_dimensions")) as applied:
                page.get_by_role("button", name="Apply dimensions").click()
            assert applied.value.json()["ok"]
            expect(page.locator("#pyd .card")).to_contain_text("Slot width 9 mm")
    elif family == "l_bracket_v0":
        button(page, "demo").click()
    else:
        path = write_demo_part(tmp_path / "gusset.dsco", family=FAMILIES[family])
        page.locator("#pydFile").set_input_files(path)

    expect(button(page, "confirm")).to_be_enabled()
    expect(button(page, "optimize")).to_be_disabled()
    button(page, "confirm").click()
    with page.expect_response(lambda r: r.url.endswith("/tool/optimize_design"), timeout=30_000) as pending:
        button(page, "optimize").click()
    payload = pending.value.json()
    assert payload["ok"], payload
    result = Result.model_validate(payload["result"])
    assert result.spec.bracket_family == family
    assert set(result.baseline.design) == set(FAMILY_PARAMS[family])
    expect(page.locator('#fixture option:checked')).to_have_text("PyDiscovery (live)")
    expect(page.get_by_role("tab", name="Recommendations", exact=True)).to_have_attribute("aria-selected", "true")
    expect(page.locator('#winVar table thead th.pc')).to_have_count(len(FAMILY_PARAMS[family]))
    expect(page.locator("#banner")).to_be_hidden()
    if is_template:
        assert result.baseline.source == f"power_law_{family}"
        expect(page.locator("#scenebox")).to_contain_text("Geometry preview unavailable")
        expect(page.locator("#winVar tr.base .chip")).to_have_text("Fitted check")
        expect(page.locator("#log")).not_to_contain_text("Verified with")
    else:
        expect(page.locator("#scenebox svg")).to_be_visible()
    page.locator('[data-atab="graphs"]').click()
    assert page.locator("#graphs svg[data-ch]").count() > 0
    page.locator('[data-atab="recs"]').click()
    cards = page.locator("#recs .rec-card")
    expect(cards).to_have_count(3)
    expect(cards.first).to_have_attribute("open", "")
    assert cards.nth(1).get_attribute("open") is None
    expect(cards.first).to_contain_text("Recommended first")
    expect(cards.first.locator(".rec-action")).to_contain_text(" mm")
    expect(cards.first.locator(".rec-performance")).to_contain_text("Factor of safety")
    expect(cards.first.locator(".rec-limits")).to_contain_text("margin")
    cards.first.get_by_text("Inspect dimensions").click()
    expect(cards.first.locator("tbody tr")).to_have_count(len(FAMILY_PARAMS[family]))
    expect(page.locator("#recs .rec-more [data-rank]")).to_have_count(2)
    if is_template:
        expect(page.locator("#recs")).to_contain_text(result.baseline.source)
        expect(page.locator("#recs")).to_contain_text("Fitted-model optimization only.")
        expect(page.locator("#recs math")).to_have_count(0)
    assert not errors


def test_named_slot_requires_explicit_confirmation(open_gui, tmp_path):
    page, expect = open_gui()
    path = write_demo_part(tmp_path / "twin-slot.dsco", family=FAMILIES[T03])
    page.locator("#pydFile").set_input_files(path)
    slot = page.locator("[data-slot-family]")
    expect(slot).to_be_visible()
    expect(button(page, "confirm")).to_be_disabled()
    slot.select_option(T03)
    expect(page.locator("#pyd")).to_contain_text("Open a STEP")
    expect(button(page, "confirm")).to_be_disabled()
    slot.select_option(T02)
    expect(button(page, "confirm")).to_be_enabled()


@pytest.mark.parametrize("engine", ["calculix", "ansys"])
def test_template_blocks_unsupported_engine(open_gui, engine):
    page, expect = open_gui(engine)
    choose_template(page, expect, T02)
    expect(page.locator("#pyd")).to_contain_text("Restart the shared GUI with the fitted engine")
    expect(button(page, "confirm")).to_be_disabled()
    expect(button(page, "optimize")).to_be_disabled()


def test_invalid_template_file_shows_backend_validation(open_gui, tmp_path):
    page, expect = open_gui()
    family = FAMILIES[T09]
    path = write_demo_part(
        tmp_path / "bad-gusset.dsco", family=family,
        values_mm={**family.baseline_mm, "gusset_length_mm": 28, "rib_hole_diameter_mm": 10},
    )
    page.locator("#pydFile").set_input_files(path)
    expect(page.locator("#pyd .perr").filter(has_text="rib_hole_clearance")).to_be_visible()
    expect(button(page, "confirm")).to_be_disabled()


def test_recommendation_ranking_and_evidence(open_gui):
    page, expect = open_gui()
    options = page.evaluate("""() => {
      const baseline = S.res.baseline;
      const design = (delta) => ({...baseline.design, thickness_mm: baseline.design.thickness_mm + delta});
      const row = (id, delta, mass, safety, source = "calculix", feasible = true) => ({
        ...baseline, design_id: id, design: design(delta), mass_kg: mass, safety_factor: safety, source, feasible,
      });
      const candidate = (id, delta) => ({
        ...S.pred.shortlist[0], design_id: id, design: design(delta), feasible_p95: true,
      });
      const preferred = row("backend-first", 1, .4, 1.7);
      const best = row("best", 2, .3, 2.1, "power_law_v0");
      const fail = row("failed", 3, .2, 1.2, "calculix", false);
      const result = {...S.res, recommended: preferred, finalists: [preferred, fail, best, {...best, design_id: "copy"}]};
      const prediction = {...S.pred, model_name: "gp", trained_on_runs: 200,
        shortlist: [candidate("would-repeat-failure", 3), candidate("baseline-copy", 0),
          candidate("would-repeat-best", 2), candidate("new-1", 4), candidate("new-2", 5), candidate("new-3", 6)]};
      const {top, more} = rankedRecommendations(result, prediction);
      const reversed = rankedRecommendations({...result, spec: {...result.spec, objective: {
        metric: "mass_kg", direction: "maximize",
      }}}, prediction);
      const absent = rankedRecommendations({...result, recommended: null, finalists: [fail]}, {...prediction, shortlist: []});
      Object.assign(S, {res: result, pred: prediction});
      showATab("recs");
      return {top: top.map(row => row.design_id), more: more.map(row => row.design_id),
        reversed: reversed.top[0].design_id, empty: absent.top.length + absent.more.length};
    }""")
    assert options == {
        "top": ["best", "backend-first"], "more": ["new-1", "new-2"], "reversed": "backend-first", "empty": 0,
    }
    cards = page.locator(".rec-card")
    expect(cards.nth(0).locator(".rec-source")).to_have_text("Fitted check")
    expect(cards.nth(1).locator(".rec-source")).to_have_text("CalculiX FEA")
    expect(page.locator(".rec-compact .rec-source").first).to_have_text("Trained prediction")
    expect(page.locator(".rec-compact").first).to_contain_text("Predicted feasible")
    expect(cards.first.locator(".rec-dimensions")).not_to_contain_text("Model ranges")


def test_fixture_recommendations_keep_equations_and_show_empty_state(open_gui):
    page, expect = open_gui()
    page.locator('[data-atab="recs"]').click()
    expect(page.locator(".rec-card").first.locator(".rec-source")).to_have_text("Formula demo")
    expect(page.get_by_text("Fixture beam equations")).to_be_visible()
    page.locator("#fixture").select_option("infeasible")
    expect(page.locator("#recs")).to_contain_text("No evaluated design met every requirement.")
    expect(page.locator("#recs [data-rank]")).to_have_count(0)


@pytest.mark.parametrize("width", [1024, 1200, 1280, 1440, 1920])
def test_live_agent_tabs_fit_and_switch(open_gui, width):
    page, expect = open_gui(width=width)
    tabs = page.locator("#winAgent").get_by_role("tab")
    expect(tabs).to_have_text(["PyDiscovery", "Recommendations", "Graphs", "Agent"])
    expect(tabs.first).to_have_attribute("aria-selected", "true")
    assert tabs.evaluate_all("""tabs => {
      const panel = document.getElementById("winAgent").getBoundingClientRect();
      const bar = tabs[0].parentElement.getBoundingClientRect();
      return tabs.every(tab => {
        const button = tab.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(tab);
        const label = range.getBoundingClientRect();
        return button.left >= panel.left && button.right <= Math.min(panel.right, innerWidth)
          && button.top >= bar.top && button.bottom <= bar.bottom
          && label.left >= button.left && label.right <= button.right
          && tab.contains(document.elementFromPoint((label.left + label.right) / 2, (label.top + label.bottom) / 2));
      });
    }""")
    for label, pane in [("Recommendations", "recs"), ("Graphs", "graphs"), ("Agent", "log"), ("PyDiscovery", "pyd")]:
        tab = page.get_by_role("tab", name=label, exact=True)
        tab.click()
        expect(tab).to_have_attribute("aria-selected", "true")
        expect(page.locator(f"#{pane}")).to_be_visible()
    expect(page.get_by_role("button", name="Maximise window").last).to_be_visible()


@pytest.mark.parametrize("engine,query", [(None, ""), ("fitted", "?fixture=out_of_range")])
def test_fixture_starts_on_agent(open_gui, engine, query):
    page, expect = open_gui(engine=engine, query=query)
    expect(page.get_by_role("tab", name="Agent", exact=True)).to_have_attribute("aria-selected", "true")
    expect(page.locator("#log")).to_contain_text("fake_formula")
    if engine is None:
        expect(page.locator("#pydTab")).to_be_hidden()
