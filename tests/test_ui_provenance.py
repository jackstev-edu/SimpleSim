"""Browser checks. Set SIMPLESIM_CHROME to a Chrome executable and install Playwright."""

import json
import os
from pathlib import Path

import pytest

from contracts import Prediction, Result, StudySpec

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "ui"


@pytest.fixture(scope="module")
def browser():
    executable = os.environ.get("SIMPLESIM_CHROME")
    if not executable:
        pytest.skip("set SIMPLESIM_CHROME to run the optional browser checks")
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as tools:
        with tools.chromium.launch(executable_path=executable) as chrome:
            yield chrome


@pytest.mark.parametrize(
    "family,source,live,label",
    [
        ("v0", "fake_formula", False, "Formula demo"),
        ("v0", "formula_emulator", True, "Formula demo"),
        ("v0", "power_law_v0", True, "Fitted surrogate check"),
        ("v1", "power_law_v1", True, "Fitted surrogate check"),
        ("v1", "calculix", True, "CalculiX FEA"),
        ("v0", "calculix_preview", True, "Reported result"),
    ],
)
def test_result_provenance(browser, family, source, live, label):
    examples = ROOT / "contracts" / "examples"
    if family == "v1":
        examples /= "l_bracket_gusset_v1"
    spec = StudySpec.model_validate_json((examples / "study_spec.json").read_text())
    prediction = Prediction.model_validate_json((examples / "prediction.json").read_text())
    result = Result.model_validate_json((examples / "result.json").read_text())
    assert result.recommended
    model = "fake_formula" if source == "fake_formula" else "gp_powerlaw_test"
    prediction.model_name = model
    prediction.trained_on_runs = 0 if model == "fake_formula" else 200
    prediction.warnings = ["Shortlist warning."]
    result.warnings = ["Result warning."]
    for design in [result.baseline, result.recommended, *result.finalists]:
        design.source = source
    supplied = "Supplied explanation with engineering context."
    result.explanation = f"{supplied} Verified with {source}."
    payload = [spec.model_dump(), prediction.model_dump(), result.model_dump()]

    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto((UI / "index.html").as_uri())
        page.locator("#pick:visible").wait_for()
        if live:
            page.evaluate("data => accept(...data, PYD_NAMES, true)", payload)
        else:
            page.locator("#files").set_input_files([
                {"name": f"{name}.json", "mimeType": "application/json", "buffer": json.dumps(data).encode()}
                for name, data in zip(("study_spec", "prediction", "result"), payload)
            ])
        row = page.locator("#winVar tr.base .chip")
        row.wait_for()
        assert row.inner_text() == label
        assert row.get_attribute("title") == source
        assert page.locator("#readout .chip").first.inner_text() == label
        if model != "fake_formula":
            assert page.locator("#winVar tr.pred .chip").first.inner_text() == "Trained prediction"
        assert supplied in page.locator("#log").inner_text()

        page.locator('[data-atab="recs"]').click()
        recommendations = page.locator("#recs")
        recommendations.get_by_text("Study explanation and model notes").click()
        text = recommendations.inner_text()
        assert supplied in text and source in text and model in text
        assert "Shortlist warning." in text and "Result warning." in text
        assert ("Verified with" in text) == (source == "calculix")
        assert bool(recommendations.locator("math").count()) == (not live)
        assert bool(recommendations.get_by_text("Fixture beam equations").count()) == (not live)

        page.locator('[data-atab="graphs"]').click()
        assert page.locator("#graphs svg[data-ch]").count() > 0
        assert page.locator("#scenebox svg").count() > 0
        assert not errors
    finally:
        page.close()
