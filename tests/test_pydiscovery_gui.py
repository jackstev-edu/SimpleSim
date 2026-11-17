"""GUI hookup: a run becomes the three contract files, served from a copy of src/ui as ?fixture=pydiscovery."""

import pytest

from contracts.models import Prediction, Result, StudySpec
from src.pydiscovery import App
from src.pydiscovery.demo import main as demo_main
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.gui import UI, add_menu_entry, prepare_site, read_run, write_run


@pytest.fixture
def run(tmp_path):
    part = App().open(write_demo_part(tmp_path / "lb.dsco"))
    part.confirm()
    result = part.optimize(constraints=[("safety_factor", ">=", 1.5), ("max_disp_mm", "<=", 0.5)])
    return result.spec, part.last_prediction, result


def test_run_files_round_trip(tmp_path, run):
    paths = write_run(tmp_path / "out", *run)
    assert [p.name for p in paths] == ["study_spec.json", "prediction.json", "result.json"]
    spec, pred, res = read_run(tmp_path / "out")
    assert isinstance(spec, StudySpec) and isinstance(pred, Prediction) and isinstance(res, Result)
    assert {f.design_id for f in res.finalists} <= {c.design_id for c in pred.shortlist}


def test_site_copy_adds_the_run_without_touching_src_ui(tmp_path, run):
    before = (UI / "index.html").read_text(encoding="utf-8")
    site = prepare_site(*run, root=tmp_path)
    for stem in ("study_spec", "prediction", "result"):
        assert (site / "fixtures" / f"{stem}_pydiscovery.json").exists()
    assert (site / "fixtures" / "result.json").exists()  # the team's own fixtures still load
    page = (site / "index.html").read_text(encoding="utf-8")
    assert 'value="pydiscovery"' in page and 'pydiscovery: "_pydiscovery"' in page
    assert (UI / "index.html").read_text(encoding="utf-8") == before
    assert not list((UI / "fixtures").glob("*pydiscovery*"))


def test_menu_patch_leaves_an_unfamiliar_page_alone():
    assert add_menu_entry("<html>changed</html>") == "<html>changed</html>"


def test_missing_run_explains_how_to_make_one(tmp_path):
    with pytest.raises(FileNotFoundError, match="demo --yes"):
        read_run(tmp_path)


def test_demo_writes_what_the_gui_reads(tmp_path):
    part = write_demo_part(tmp_path / "lb.dsco")
    assert demo_main(["--part", str(part), "--yes", "--out", str(tmp_path / "out")]) == 0
    spec, pred, res = read_run(tmp_path / "out")
    assert res.recommended is not None and pred.model_name == "power_law_v0"
