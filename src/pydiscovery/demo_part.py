"""Write small synthetic part files for the demo and the tests.

They copy the layout of a real Discovery save (see part_file.py) but are generated here, not
by Ansys. Once someone saves a real l_bracket.dsco from Discovery, use that instead.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .families import L_BRACKET_V0, Family

P = "Ansys.Disco.Physics.Nominal.Persistence."
STEEL = {"name": "Structural steel", "E_pa": 200e9, "nu": 0.30, "yield_pa": 250e6, "density": 7850.0}


def write_demo_part(path: str | Path, family: Family = L_BRACKET_V0, values_mm: dict[str, float] | None = None,
                    force_n: float = 500.0, direction: tuple[float, float, float] = (0.0, -1.0, 0.0),
                    material: dict | None = STEEL, physics: bool = True) -> Path:
    """A .dsco with named parameters and, optionally, physics; or a bare .scdocx with neither."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    discovery = path.suffix.lower() == ".dsco"
    values = values_mm or family.baseline_mm
    groups = ""
    if discovery:  # parameters are named groups holding an SI value and an edit script
        for i, (contract, label) in enumerate(family.labels.items()):
            groups += (f'<NamedSelectionDef Id="1{i}"><name>{escape(label)}</name>'
                       f'<ScriptToolProxyDef Id="2{i}" xmlns="urn:spaceclaim.core.design"><updateState>0</updateState>'
                       f'<desiredValue>{values[contract] / 1000.0!r}</desiredValue>'
                       f'<modifyScript># synthetic demo parameter\ndistance = val</modifyScript></ScriptToolProxyDef></NamedSelectionDef>')
    body = f'<PartDef><StoredSelectionTableDef xmlns="urn:stored selection">{groups}</StoredSelectionTableDef></PartDef>'
    if discovery and physics:
        body += _physics(force_n, direction, material)
    doc = ('<?xml version="1.0" encoding="utf-8"?><Document version="1.670" xmlns="urn:core">'
           "<!-- Synthetic SimpleSim demo part, not saved by Ansys Discovery -->" + body + "</Document>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SpaceClaim/document.xml", b"\xef\xbb\xbf" + doc.encode("utf-8"))  # real files carry a BOM
        if discovery:
            z.writestr("SpaceClaim/Discovery/discoveryexplore.xml", "<ExploreData />")
            if physics and material:
                z.writestr("SpaceClaim/Discovery/table__E__dt1.xml", _table("Pressure", "Pascal", material["E_pa"]))
                z.writestr("SpaceClaim/Discovery/table__RHO__dt1.xml", _table("Density", "KilogramPerCubicMeter", material["density"]))
    return path


def _physics(force_n: float, direction, material: dict | None) -> str:
    d = " ".join(f"{c:g}" for c in direction)
    xml = (f'<PhysicsObjectDef xmlns="urn:disco.physics">'
           f'<entity sctype="{P}SupportEntitySurrogate, Disco.Physics" id="s1"><label>Fixed Support 1</label>'
           f'<scope><type>Face</type><faces><item /></faces></scope><supportType>Fixed</supportType><suppressed>False</suppressed></entity>'
           f'<entity sctype="{P}ForceEntitySurrogate, Disco.Physics" id="f1"><label>Force 1</label>'
           f'<scope><type>Face</type><faces><item /></faces></scope>'
           f'<distributedForceOptions><totalForce><magnitude><value>{force_n:g}</value><unit>Newton</unit></magnitude>'
           f'<loadDirection><direction>{d}</direction></loadDirection></totalForce></distributedForceOptions>'
           f'<forceSpecification>Distributed</forceSpecification><suppressed>False</suppressed></entity>'
           f'<entity sctype="{P}GravityEntitySurrogate, Disco.Physics" id="g1"><label>Gravity</label>'
           f'<value><magnitude><value>0</value><unit>MeterPerSecondSquared</unit></magnitude></value><includeSelfWeight>False</includeSelfWeight></entity>')
    if material:
        xml += (f'<entity sctype="{P}MaterialEntitySurrogate, Disco.Physics" id="m1"><label>{escape(material["name"])}</label>'
                f'<name>{escape(material["name"])}</name><statePropertiesV2><item><physicalProperties>'
                f'<tensileYieldStrength><value>{material["yield_pa"]:g}</value><unit>Pascal</unit></tensileYieldStrength>'
                f'<poissonsRatio><value>{material["nu"]:g}</value><unit>DecimalFraction</unit></poissonsRatio></physicalProperties>'
                f'<mechanicalPropertiesV2><youngsModulusTableId>E</youngsModulusTableId><densityTableId>RHO</densityTableId>'
                f'</mechanicalPropertiesV2></item></statePropertiesV2></entity>'
                f'<entity sctype="{P}MaterialAssignmentEntitySurrogate, Disco.Physics" id="a1"><label>{escape(material["name"])}</label>'
                f'<material>m1</material></entity>')
    return xml + "</PhysicsObjectDef>"


def _table(quantity: str, unit: str, value: float) -> str:
    return (f'<NewDataSet><t><Temperature quantity="Temperature" unit="DegreeCelsius" value="22" />'
            f'<X quantity="{quantity}" unit="{unit}" value="{value:g}" /></t></NewDataSet>')
