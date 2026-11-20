"""The GUI's PyDiscovery API: handle() as a pure dispatcher, plus one real server from launch_gui.

Everything runs with the root requirements only: tmp_path is the repo root, so there is no runs.csv
and the API picks the power-law predictor. The demo part is written on demand, so nothing under data/.
"""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from contracts.models import Prediction, Result, StudySpec
from src.pydiscovery.demo_part import write_demo_part
from src.pydiscovery.server import PyDiscoveryAPI, safe_name

LIMITS = [{"metric": "safety_factor", "op": ">=", "value": 1.5}, {"metric": "max_disp_mm", "op": "<=", "value": 0.5}]


@pytest.fixture
def api(tmp_path):
    return PyDiscoveryAPI(tmp_path)


def post(api, path, body=None, query=None):
    raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode("utf-8")
    return api.handle("POST", path, query or {}, raw)


def tool(api, name, args=None):
    return post(api, f"/api/pydiscovery/tool/{name}", args)


def test_status_route_lists_the_tools_and_the_predictor_choice(api):
    status, out = api.handle("GET", "/api/pydiscovery", {}, b"")
    assert status == 200 and out["ok"] is True
    assert out["engine"] == "fitted" and out["predictor"] == "power_law"  # no runs.csv under tmp_path, so no GP
    assert "runs.csv" in out["predictor_note"]
    assert len(out["tools"]) == 11 and "optimize_design" in out["tools"]


def test_demo_part_opens_with_nothing_missing(api, tmp_path):
    status, out = post(api, "/api/pydiscovery/demo-part")
    assert status == 200 and out["ok"] is True
    assert out["state"]["missing"] == [] and out["state"]["problems"] == [] and out["state"]["confirmed"] is False
    assert (tmp_path / "data" / "raw" / "l_bracket_demo.dsco").exists()
    assert "Still needed: nothing" in out["message"]


def test_optimize_before_confirm_is_refused(api):
    post(api, "/api/pydiscovery/demo-part")
    status, out = tool(api, "optimize_design", {"constraints": LIMITS})
    assert status == 200 and out["ok"] is False and "onfirm" in out["message"]


def test_confirm_then_optimize_returns_the_three_contract_files(api):
    post(api, "/api/pydiscovery/demo-part")
    status, out = tool(api, "confirm_setup")
    assert status == 200 and out["ok"] is True and out["state"]["confirmed"] is True
    status, out = tool(api, "optimize_design", {"constraints": LIMITS})
    assert status == 200 and out["ok"] is True
    spec, pred, res = StudySpec.model_validate(out["study_spec"]), Prediction.model_validate(out["prediction"]), Result.model_validate(out["result"])
    assert res.recommended is not None and res.recommended.design_id.startswith("pl-0-")
    assert res.recommended.mass_kg == pytest.approx(0.23, abs=0.02)
    assert res.spec == spec and {f.design_id for f in res.finalists} <= {c.design_id for c in pred.shortlist}


def test_save_without_a_path_lands_in_the_gui_folder(api, tmp_path):
    post(api, "/api/pydiscovery/demo-part")
    tool(api, "confirm_setup")
    tool(api, "optimize_design", {"constraints": LIMITS})
    status, out = tool(api, "save_design")
    assert status == 200 and out["ok"] is True
    saved = Path(out["path"])
    assert saved == tmp_path / "data" / "processed" / "pydiscovery_gui" / "l_bracket_demo_optimized.dsco" and saved.exists()


def test_uploading_an_stl_is_refused_with_the_readers_message(api, tmp_path):
    status, out = post(api, "/api/pydiscovery/upload", b"solid x", query={"name": ["mesh.stl"]})
    assert status == 200 and out["ok"] is False and "STL" in out["message"]
    assert not (tmp_path / "data" / "raw" / "uploads").exists()  # nothing written for a refused file


def test_uploaded_spaceclaim_part_asks_for_four_things_then_confirms(api, tmp_path):
    raw = write_demo_part(tmp_path / "src.scdocx").read_bytes()
    status, out = post(api, "/api/pydiscovery/upload", raw, query={"name": ["l_bracket.scdocx"]})
    assert status == 200 and out["ok"] is True and len(out["state"]["missing"]) == 4
    assert tool(api, "confirm_setup")[1]["ok"] is False
    assert tool(api, "use_family", {"family": "l_bracket_v0"})[1]["ok"] is True
    assert tool(api, "set_support")[1]["ok"] is True
    assert tool(api, "set_load", {"force_n": 500})[1]["ok"] is True
    status, out = tool(api, "set_material", {"material": "aluminum_6061"})
    assert out["ok"] is True and out["state"]["missing"] == []
    status, out = tool(api, "confirm_setup")
    assert out["ok"] is True and out["state"]["confirmed"] is True
    # A SpaceClaim part can't be rewritten, so the default save is a .json of values.
    status, out = tool(api, "save_design")
    assert out["ok"] is True and Path(out["path"]).suffix == ".json"


def test_upload_names_stay_inside_the_uploads_folder(api, tmp_path):
    raw = write_demo_part(tmp_path / "src.dsco").read_bytes()
    status, out = post(api, "/api/pydiscovery/upload", raw, query={"name": ["../../evil.dsco"]})
    assert status == 200 and out["ok"] is True
    uploads = tmp_path / "data" / "raw" / "uploads"
    assert Path(out["state"]["part"]).resolve() == (uploads / "evil.dsco").resolve()
    assert not (tmp_path / "evil.dsco").exists() and not (tmp_path.parent / "evil.dsco").exists()
    assert safe_name("..\\..\\x y;z.dsco") == "x_y_z.dsco"


def test_upload_checks_type_name_and_size(api):
    assert post(api, "/api/pydiscovery/upload", b"x", query={"name": ["part.txt"]})[0] == 400
    assert post(api, "/api/pydiscovery/upload", b"x")[0] == 400  # no name
    assert post(api, "/api/pydiscovery/upload", b"", query={"name": ["part.dsco"]})[0] == 400  # empty body
    api_small = api
    api_small_limit = 50 * 1024 * 1024 + 1
    assert post(api_small, "/api/pydiscovery/upload", b"\0" * api_small_limit, query={"name": ["big.dsco"]})[0] == 400


def test_unknown_tool_and_unknown_route(api):
    post(api, "/api/pydiscovery/demo-part")
    status, out = tool(api, "no_such_tool")
    assert status == 200 and out["ok"] is False
    assert api.handle("GET", "/api/nothing", {}, b"")[0] == 404
    assert api.handle("POST", "/api/pydiscovery/nothing", {}, b"")[0] == 404
    assert api.handle("POST", "/api/pydiscovery/tool/confirm_setup", {}, b"not json")[0] == 400
    assert api.handle("GET", "/api/pydiscovery/demo-part", {}, b"")[0] == 405


def test_session_route_restarts_and_rejects_unknown_engines(api):
    post(api, "/api/pydiscovery/demo-part")
    status, out = post(api, "/api/pydiscovery/session", {"engine": "formula", "predictor": "power_law"})
    assert status == 200 and out["ok"] is True and out["engine"] == "formula"
    assert api.tools.part is None  # a fresh session has no part open
    assert post(api, "/api/pydiscovery/session", {"engine": "warp_drive"})[0] == 400
    assert post(api, "/api/pydiscovery/session", {"predictor": "oracle"})[0] == 400


def test_launch_gui_serves_the_page_and_the_api(tmp_path):
    import launch_gui

    server = launch_gui.build_server(PyDiscoveryAPI(tmp_path), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = urllib.request.urlopen(base + "/", timeout=10).read().decode("utf-8")
        assert 'data-atab="pyd"' in page and 'id="pyd"' in page
        status = json.loads(urllib.request.urlopen(base + "/api/pydiscovery", timeout=10).read())
        assert status["ok"] is True and status["predictor"] == "power_law"
        req = urllib.request.Request(base + "/api/pydiscovery/demo-part", data=b"", method="POST")
        opened = json.loads(urllib.request.urlopen(req, timeout=10).read())
        assert opened["ok"] is True and opened["state"]["missing"] == []
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/api/nowhere", timeout=10)
        assert err.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_launch_gui_without_the_api_still_serves_static_files():
    import launch_gui

    server = launch_gui.build_server(None, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert "SimpleSim" in urllib.request.urlopen(base + "/", timeout=10).read().decode("utf-8")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/api/pydiscovery", timeout=10)
        assert err.value.code == 404  # so the GUI hides the PyDiscovery tab
    finally:
        server.shutdown()
        server.server_close()


def test_gusset_part_runs_through_the_same_routes(api, tmp_path):
    from src.pydiscovery import L_BRACKET_GUSSET_V1

    raw = write_demo_part(tmp_path / "src.dsco", family=L_BRACKET_GUSSET_V1).read_bytes()
    status, out = post(api, "/api/pydiscovery/upload", raw, query={"name": ["gusset.dsco"]})
    assert status == 200 and out["ok"] is True and out["state"]["family"] == "l_bracket_gusset_v1"
    assert [d["contract_name"] for d in out["state"]["dimensions"]][-3:] == ["vertical_height_mm", "gusset_thickness_mm", "gusset_length_mm"]
    assert tool(api, "confirm_setup")[1]["ok"] is True
    status, out = tool(api, "optimize_design", {"constraints": LIMITS})
    assert status == 200 and out["ok"] is True
    spec, res = StudySpec.model_validate(out["study_spec"]), Result.model_validate(out["result"])
    assert spec.bracket_family == "l_bracket_gusset_v1" and res.recommended is not None and len(res.recommended.design) == 8
    status, out = tool(api, "save_design")
    assert out["ok"] is True and Path(out["path"]).name == "gusset_optimized.dsco"


def test_spaceclaim_upload_can_pick_the_gusset_family(api, tmp_path):
    raw = write_demo_part(tmp_path / "src.scdocx").read_bytes()
    post(api, "/api/pydiscovery/upload", raw, query={"name": ["shape.scdocx"]})
    status, out = tool(api, "use_family", {"family": "l_bracket_gusset_v1"})
    assert out["ok"] is True and out["state"]["family"] == "l_bracket_gusset_v1" and len(out["state"]["dimensions"]) == 8
