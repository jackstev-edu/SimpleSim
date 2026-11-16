"""Part-file reader: what each file type gives the agent, and a safe write-back.

The synthetic files mirror the layout of a real Discovery save. Put a real one at
data/raw/demo_study.dsco (gitignored) to also run the check against Discovery output.
"""

import zipfile
from pathlib import Path

import pytest

from src.pydiscovery import UnsupportedPartError, open_part, save_with_parameters

P = "Ansys.Disco.Physics.Nominal.Persistence."
REAL = Path(__file__).resolve().parents[1] / "data" / "raw" / "demo_study.dsco"

GROUPS = """<PartDef><StoredSelectionTableDef xmlns="urn:stored selection">
<NamedSelectionDef Id="1"><name>Thickness</name><ScriptToolProxyDef Id="2" xmlns="urn:spaceclaim.core.design">
<updateState>1</updateState><desiredValue>0.005</desiredValue><modifyScript>distance = val</modifyScript></ScriptToolProxyDef></NamedSelectionDef>
<NamedSelectionDef Id="3"><name>hole1</name></NamedSelectionDef>
<NamedSelectionDef Id="4"><name>0:33</name></NamedSelectionDef>
</StoredSelectionTableDef></PartDef>"""

PHYSICS = f"""<PhysicsObjectDef xmlns="urn:disco.physics">
<entity sctype="{P}SupportEntitySurrogate, Disco.Physics" id="s1"><label>Fixed Support 1</label>
<scope><type>Face</type><faces><item /><item /></faces></scope><supportType>Fixed</supportType><suppressed>False</suppressed></entity>
<entity sctype="{P}ForceEntitySurrogate, Disco.Physics" id="f1"><label>Force 1</label><scope><type>Face</type><faces><item /></faces></scope>
<originX><value>0.06</value><unit>Meter</unit></originX><originY><value>0</value><unit>Meter</unit></originY><originZ><value>0.02</value><unit>Meter</unit></originZ>
<distributedForceOptions><totalForce><magnitude><value>500</value><unit>Newton</unit></magnitude>
<loadDirection><direction>0 -1 0</direction></loadDirection></totalForce></distributedForceOptions>
<forceSpecification>Distributed</forceSpecification><suppressed>False</suppressed></entity>
<entity sctype="{P}GravityEntitySurrogate, Disco.Physics" id="g1"><label>Gravity</label>
<value><magnitude><value>0</value><unit>MeterPerSecondSquared</unit></magnitude></value><includeSelfWeight>False</includeSelfWeight></entity>
<entity sctype="{P}MaterialEntitySurrogate, Disco.Physics" id="m1"><label>Steel</label><name>Structural steel</name>
<statePropertiesV2><item><physicalProperties><tensileYieldStrength><value>250000000</value><unit>Pascal</unit></tensileYieldStrength>
<poissonsRatio><value>0.3</value><unit>DecimalFraction</unit></poissonsRatio></physicalProperties>
<mechanicalPropertiesV2><youngsModulusTableId>E</youngsModulusTableId><densityTableId>RHO</densityTableId></mechanicalPropertiesV2></item></statePropertiesV2></entity>
<entity sctype="{P}MaterialAssignmentEntitySurrogate, Disco.Physics" id="a1"><label>Steel</label><material>m1</material></entity>
</PhysicsObjectDef>"""


def table(quantity, unit, value):
    return f'<NewDataSet><t><Temperature quantity="Temperature" unit="DegreeCelsius" value="22" /><X quantity="{quantity}" unit="{unit}" value="{value}" /></t></NewDataSet>'


def make_part(path, body, discovery=True):
    """Write a minimal SpaceClaim container with the given document body."""
    doc = f'<?xml version="1.0" encoding="utf-8"?><Document version="1.670" xmlns="urn:core">{body}</Document>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SpaceClaim/document.xml", b"\xef\xbb\xbf" + doc.encode())  # real files start with a BOM
        z.writestr("docProps/thumbnail.png", b"not really a png")
        if discovery:
            z.writestr("SpaceClaim/Discovery/table__E__dt1.xml", table("Pressure", "Pascal", "200000000000"))
            z.writestr("SpaceClaim/Discovery/table__RHO__dt1.xml", table("Density", "KilogramPerCubicMeter", "7850"))
    return path


def test_discovery_file_brings_its_own_physics(tmp_path):
    part = open_part(make_part(tmp_path / "bracket.dsco", GROUPS + PHYSICS))
    assert part.file_type == "discovery"
    assert [(p.name, p.value, p.kind) for p in part.parameters] == [("Thickness", 5.0, "length")]
    assert part.named_groups == ["hole1"]  # internal ids like 0:33 are dropped
    assert [(s.name, s.face_count) for s in part.supports] == [("Fixed Support 1", 2)]
    force = part.forces[0]
    assert (force.magnitude_n, force.direction, force.arrow_at_mm) == (500.0, (0.0, -1.0, 0.0), (60.0, 0.0, 20.0))
    m = part.material
    assert (m.youngs_mpa, m.yield_mpa, m.poisson, m.density_kg_m3) == (200000.0, 250.0, 0.3, 7850.0)
    assert part.gravity is False
    assert part.missing == []
    assert "500 N along -Y" in part.setup_text()


def test_spaceclaim_file_lists_what_the_agent_must_ask(tmp_path):
    part = open_part(make_part(tmp_path / "bracket.scdocx", GROUPS.replace("<desiredValue>0.005</desiredValue>", ""), discovery=False))
    assert part.file_type == "spaceclaim"
    assert part.parameters == [] and part.named_groups == ["Thickness", "hole1"]
    assert part.missing == ["dimensions to change", "supports", "loads", "material"]


def test_step_is_geometry_only_and_stl_is_refused(tmp_path):
    step = tmp_path / "bracket.step"
    step.write_text("ISO-10303-21;")
    assert open_part(step).missing == ["dimensions to change", "supports", "loads", "material"]
    stl = tmp_path / "bracket.stl"
    stl.write_text("solid x")
    with pytest.raises(UnsupportedPartError, match="no faces, units or dimensions"):
        open_part(stl)


def test_save_with_parameters_changes_only_the_value(tmp_path):
    src = make_part(tmp_path / "bracket.dsco", GROUPS + PHYSICS)
    dst = save_with_parameters(src, tmp_path / "bracket_opt.dsco", {"Thickness": 6.5})
    assert open_part(dst).parameters[0].value == 6.5
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
        assert a.namelist() == b.namelist()
        changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
        old, new = a.read("SpaceClaim/document.xml"), b.read("SpaceClaim/document.xml")
    assert changed == ["SpaceClaim/document.xml"]
    assert new.startswith(b"\xef\xbb\xbf") and old.replace(b"0.005", b"0.0065") == new
    with pytest.raises(KeyError):
        save_with_parameters(src, tmp_path / "x.dsco", {"Width": 3.0})


@pytest.mark.skipif(not REAL.exists(), reason="no real Discovery file at data/raw/demo_study.dsco")
def test_real_discovery_file():
    part = open_part(REAL)
    assert [(p.name, p.value) for p in part.parameters] == [("Thickness", 5.0)]
    assert len(part.supports) == 2 and part.forces[0].magnitude_n == 3000.0
    assert part.material.youngs_mpa == pytest.approx(69040.0) and part.material.yield_mpa == pytest.approx(259.2)
    solved = [v for v in part.variants if v.results]
    assert len(part.variants) == 52 and len(solved) == 2
    dv1 = solved[0]  # Discovery's own safety factor should equal yield over peak stress
    assert dv1.results["Factor of Safety"] == pytest.approx(part.material.yield_mpa / dv1.results["Max. Von Mises Stress [MPa]"], rel=1e-3)
