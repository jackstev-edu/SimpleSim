"""Material catalog read from ANSYS's own Engineering Data sample libraries.

Decision 2026-10-31: materials come from ANSYS's library; default Structural Steel.
Values are read from the MatML files shipped with the install (SI units in the files:
Pa, kg/m^3), so the agent never invents material properties. These sample libraries
carry no prices (checked 2026-10-31).
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path

DEFAULT = "Structural Steel"
LIBRARIES = ("General_Materials.xml", "Granta_Design_Typical_Materials.xml")
_WANTED = {
    "Density": ("density_kg_m3", 1.0),
    "Young's Modulus": ("youngs_modulus_GPa", 1e-9),
    "Poisson's Ratio": ("poissons_ratio", 1.0),
    "Tensile Yield Strength": ("yield_MPa", 1e-6),
    "Tensile Ultimate Strength": ("ultimate_MPa", 1e-6),
}


def library_dir(version: int = 261) -> Path:
    root = os.environ.get(f"AWP_ROOT{version}") or rf"C:\Program Files\ANSYS Inc\ANSYS Student\v{version}"
    return Path(root) / "Addins" / "EngineeringData" / "Samples"


def _first_number(text: str | None) -> float | None:
    """Data may be a temperature table ('7850,7850'); use the first entry (room temperature)."""
    if not text:
        return None
    try:
        return float(text.split(",")[0])
    except ValueError:
        return None


def _parse(path: Path) -> dict[str, dict]:
    root = ET.parse(path).getroot()
    names = {p.get("id"): p.findtext("Name") for p in root.iter("ParameterDetails")}
    out = {}
    for mat in root.iter("Material"):
        bulk = mat.find("BulkDetails")
        if bulk is None:
            continue
        props = {}
        for pv in bulk.iter("ParameterValue"):
            key = _WANTED.get(names.get(pv.get("parameter")))
            value = _first_number(pv.findtext("Data"))
            if key and value is not None and key[0] not in props:
                props[key[0]] = round(value * key[1], 4)
        # Only solids usable in a structural solve: need density, stiffness and a yield strength.
        # Brittle entries without a yield (e.g. Concrete, Gray Cast Iron: 0 in the file) are left
        # out, because the safety factor here is yield / von Mises peak, a ductile criterion.
        if {"density_kg_m3", "youngs_modulus_GPa", "yield_MPa"} <= props.keys() and props["yield_MPa"] > 0:
            out[bulk.findtext("Name")] = {**props, "library": path.name}
    return out


@cache
def catalog(version: int = 261) -> dict[str, dict]:
    """{material name: properties + library file}, first library wins on duplicate names."""
    out: dict[str, dict] = {}
    for lib in LIBRARIES:
        for name, props in _parse(library_dir(version) / lib).items():
            out.setdefault(name, props)
    return out


def get(name: str) -> dict:
    mats = catalog()
    if name not in mats:
        raise KeyError(f"Unknown material {name!r}. Available: {sorted(mats)}")
    return {"name": name, **mats[name]}
