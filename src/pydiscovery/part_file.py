"""Read a part file into one setup the agent can show the user to confirm.

Discovery files (.dsco) carry parameters, supports, loads, material, goals and study results.
SpaceClaim files (.scdocx, .scdoc) carry geometry and named groups, but no physics.
STEP files carry geometry only. STL is refused: it has no faces, units or dimensions.
Whatever the file lacks is listed in `missing`, so the agent asks instead of assuming.

Run `python -m src.pydiscovery <part>` to print the setup, or add `--json`.
"""

from __future__ import annotations

import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field

DOC = "SpaceClaim/document.xml"
DISCO = "SpaceClaim/Discovery/"
PHYSICS = "Ansys.Disco.Physics.Nominal.Persistence."
BOM = b"\xef\xbb\xbf"

# Discovery stores SI; convert at the boundary to the repo's units.
UNITS = {
    "Pascal": (1e-6, "MPa"), "Meter": (1e3, "mm"), "Millimeter": (1.0, "mm"),
    "Newton": (1.0, "N"), "Kilogram": (1.0, "kg"), "Gram": (1e-3, "kg"),
    "KilogramPerCubicMeter": (1.0, "kg/m3"), "DecimalFraction": (1.0, ""),
    "Radian": (180 / math.pi, "deg"), "Degree": (1.0, "deg"),
}
# Load types we can't model yet; listed so the agent flags them.
OTHER_LOADS = ("Pressure", "Moment", "Torque", "Displacement", "Temperature", "Bearing", "Bolt", "Acceleration")


class UnsupportedPartError(ValueError):
    """The file can't become a part we can resize, load and solve."""


class Parameter(BaseModel):
    name: str
    value: float  # mm for lengths, degrees for angles
    kind: Literal["length", "angle"]
    edit_script: str = ""  # the recorded Discovery edit that applies a new value


class Support(BaseModel):
    name: str
    kind: str  # Discovery's own name, e.g. "Fixed"
    face_count: int


class Force(BaseModel):
    name: str
    magnitude_n: float
    direction: tuple[float, float, float]
    arrow_at_mm: tuple[float, float, float] | None = None  # where Discovery draws the arrow
    face_count: int


class Material(BaseModel):
    name: str
    youngs_mpa: float | None = None
    poisson: float | None = None
    yield_mpa: float | None = None
    ultimate_mpa: float | None = None
    density_kg_m3: float | None = None


class Goal(BaseModel):
    monitor: str
    sense: Literal["minimize", "maximize"]


class Variant(BaseModel):
    name: str
    inputs: dict[str, float]  # "Thickness [mm]" -> 5.0
    results: dict[str, float] = Field(default_factory=dict)  # "Mass [kg]" -> 0.21


class PartSetup(BaseModel):
    path: str
    file_type: Literal["discovery", "spaceclaim", "step"]
    parameters: list[Parameter] = Field(default_factory=list)
    named_groups: list[str] = Field(default_factory=list)
    supports: list[Support] = Field(default_factory=list)
    forces: list[Force] = Field(default_factory=list)
    other_conditions: list[str] = Field(default_factory=list)  # loads or materials we can't model
    notes: list[str] = Field(default_factory=list)  # read but not used, e.g. unread study criteria
    gravity: bool = False
    material: Material | None = None
    monitors: list[str] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)

    def setup_text(self) -> str:
        """Plain-language summary for the confirmation card."""
        lines = [f"Part: {Path(self.path).name} ({self.file_type} file)"]
        lines.append("Fixed: " + (", ".join(f"{s.name} ({s.face_count} faces, {s.kind})" for s in self.supports) or "not set"))
        for f in self.forces:
            lines.append(f"Load: {f.name}, {f.magnitude_n:g} N {_direction_text(f.direction)} on {f.face_count} face(s)")
        if not self.forces:
            lines.append("Load: not set")
        lines += [f"Also in file, not modelled yet: {c}" for c in self.other_conditions]
        if self.material:
            m = self.material
            props = [f"E {m.youngs_mpa:,.0f} MPa" if m.youngs_mpa else None, f"yield {m.yield_mpa:g} MPa" if m.yield_mpa else None,
                     f"density {m.density_kg_m3:g} kg/m3" if m.density_kg_m3 else None]
            lines.append(f"Material: {m.name} (" + ", ".join(p for p in props if p) + ")")
        else:
            lines.append("Material: not set")
        lines.append(f"Gravity: {'on' if self.gravity else 'off'}")
        unit = {"length": "mm", "angle": "deg"}
        lines.append("Can change: " + (", ".join(f"{p.name} = {p.value:g} {unit[p.kind]}" for p in self.parameters) or "nothing named yet"))
        if self.named_groups:
            lines.append("Named groups: " + ", ".join(self.named_groups))
        if self.goals:
            lines.append("Goals saved in file: " + ", ".join(f"{g.sense} {g.monitor}" for g in self.goals))
        if self.variants:
            solved = sum(1 for v in self.variants if v.results)
            lines.append(f"Study in file: {len(self.variants)} variants, {solved} solved")
        lines += [f"Note: {n}" for n in self.notes]
        lines.append("Still needed: " + (", ".join(self.missing) or "nothing"))
        return "\n".join(lines)


def open_part(path: str | Path) -> PartSetup:
    """Read any supported part file; refuse ones we can't resize or load."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".stl":
        raise UnsupportedPartError("STL is a triangle mesh with no faces, units or dimensions to change. Upload the Discovery file or a STEP export.")
    if not p.exists():
        raise FileNotFoundError(p)
    if ext in (".step", ".stp"):
        setup = PartSetup(path=str(p), file_type="step")  # geometry only; everything else comes from the user
    elif ext in (".dsco", ".scdocx", ".scdoc"):
        setup = _read_container(p)
    else:
        raise UnsupportedPartError(f"{ext or 'This file type'} isn't supported. Use .dsco, .scdocx, .scdoc or STEP.")
    setup.missing = _missing(setup)
    return setup


def save_with_parameters(src: str | Path, dst: str | Path, values: dict[str, float]) -> Path:
    """Copy a part file with new parameter values (mm or degrees); every other byte is unchanged.

    Untested in Discovery itself: check on vLab that opening the copy rebuilds the shape.
    """
    kinds = {p.name: p.kind for p in open_part(src).parameters}
    with zipfile.ZipFile(src) as zin:
        raw = zin.read(DOC)
        doc = raw.decode("utf-8-sig")
        for name, value in values.items():
            if name not in kinds:
                raise KeyError(f"No parameter named {name!r}. Known: {sorted(kinds)}")
            si = math.radians(value) if kinds[name] == "angle" else value / 1e3  # back to SI for Discovery
            pattern = rf"(<name>{re.escape(escape(name))}</name>\s*<ScriptToolProxyDef\b(?:(?!</ScriptToolProxyDef>).)*?<desiredValue>)[^<]*(</desiredValue>)"
            doc, hits = re.subn(pattern, rf"\g<1>{si!r}\g<2>", doc, count=1, flags=re.S)
            if hits != 1:
                raise KeyError(f"Couldn't locate the stored value for {name!r}")
        new = (BOM if raw.startswith(BOM) else b"") + doc.encode("utf-8")
        with zipfile.ZipFile(dst, "w") as zout:
            for info in zin.infolist():
                zout.writestr(info, new if info.filename == DOC else zin.read(info))  # keeps names, dates, compression
    return Path(dst)


# ---------------------------------------------------------------- internals


def _read_container(p: Path) -> PartSetup:
    with zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        if DOC not in names:
            raise UnsupportedPartError(f"{p.name} isn't a Discovery or SpaceClaim document.")
        root = ET.fromstring(z.read(DOC).decode("utf-8-sig"))
        for e in root.iter():  # drop XML namespaces so lookups use plain tag names
            if "}" in e.tag:
                e.tag = e.tag.split("}", 1)[1]
        is_disco = any(n.startswith(DISCO) for n in names)
        setup = PartSetup(path=str(p), file_type="discovery" if is_disco else "spaceclaim")
        setup.parameters, setup.named_groups = _parameters(root)
        if not is_disco:
            return setup
        ents = _entities(root)
        monitor_json = _json(z, DISCO + "MonitorResults.json")
        _physics(setup, ents, lambda tid: _table(z, tid))
        _study(setup, ents, monitor_json)
    return setup


def _parameters(root: ET.Element) -> tuple[list[Parameter], list[str]]:
    """Named groups with a stored value are parameters; the rest are just names."""
    params, groups = [], []
    for sel in root.iter("NamedSelectionDef"):
        name = (sel.findtext("name") or "").strip()
        if not name or re.fullmatch(r"\d+:\d+", name):  # skip internal ids like "0:33"
            continue
        proxy = sel.find("ScriptToolProxyDef")
        stored = proxy.findtext("desiredValue") if proxy is not None else None
        if not stored:
            groups.append(name)
            continue
        script = (proxy.findtext("modifyScript") or "").strip()
        kind = "angle" if re.search(r"Rotat|Angle", script) else "length"  # scripts reveal the edit type
        value = math.degrees(float(stored)) if kind == "angle" else float(stored) * 1e3
        params.append(Parameter(name=name, value=round(value, 6), kind=kind, edit_script=script))
    return params, groups


def _entities(root: ET.Element) -> list[tuple[str, ET.Element]]:
    """Every Discovery physics object as (short kind, element), skipping suppressed ones."""
    out = []
    for e in root.iter("entity"):
        st = (e.get("sctype") or "").split(",")[0]
        if st.startswith(PHYSICS) and e.findtext("suppressed") != "True":
            out.append((re.sub(r"(Entity)?Surrogate(V\d+)?$", "", st[len(PHYSICS):]), e))
    return out


def _physics(setup: PartSetup, ents, table) -> None:
    materials = {e.get("id"): e for k, e in ents if k == "Material"}
    for kind, e in ents:
        label = e.findtext("label") or kind
        if kind == "Support":
            setup.supports.append(Support(name=label, kind=e.findtext("supportType") or "Unknown", face_count=_faces(e)))
        elif kind == "Force" and e.findtext("forceSpecification") == "Distributed":
            tf = e.find("distributedForceOptions/totalForce")
            mag = _q(tf.find("magnitude"))[0]
            d = tuple(float(x) for x in (tf.findtext("loadDirection/direction") or "0 0 0").split()[:3])
            pt = tuple(_q(e.find(k))[0] for k in ("originX", "originY", "originZ")) if e.find("originX") is not None else None
            setup.forces.append(Force(name=label, magnitude_n=mag, direction=d, arrow_at_mm=pt, face_count=_faces(e)))
        elif kind == "Force" or any(w in kind for w in OTHER_LOADS):
            setup.other_conditions.append(f"{label} ({kind})")  # e.g. force per area, pressure, moment
        elif kind == "Gravity":
            mag = e.find("value/magnitude")  # Discovery keeps a gravity object even when it is zero
            setup.gravity = e.findtext("includeSelfWeight") == "True" or (mag is not None and _q(mag)[0] != 0)
        elif kind == "MaterialAssignment" and setup.material is None:
            setup.material = _material(materials.get(e.findtext("material")), table, label)
        elif kind == "MaterialAssignment":
            setup.other_conditions.append(f"second material: {label}")


def _material(e: ET.Element | None, table, label: str) -> Material:
    if e is None:
        return Material(name=label)
    found = lambda tag: next(e.iter(tag), None)  # properties sit under different groups per version
    get = lambda tag: _q(found(tag))[0] if found(tag) is not None else None
    ids = {c.tag: (c.text or "").strip() for c in e.iter() if c.tag.endswith("TableId")}
    return Material(
        name=e.findtext("name") or label,
        youngs_mpa=table(ids.get("youngsModulusTableId")),
        poisson=get("poissonsRatio"),
        yield_mpa=get("tensileYieldStrength"),
        ultimate_mpa=get("tensileUltimateYieldStrength"),
        density_kg_m3=table(ids.get("densityTableId")),
    )


def _study(setup: PartSetup, ents, monitor_json: dict | None) -> None:
    """Monitors, saved goals, and the variations table with any solved results."""
    names, by_variant = {}, {}
    for group in (monitor_json or {}).get("items", []):
        for m in group.get("monitors", []):
            unit = None
            for stage in m.get("results", []):
                for v in stage.get("values", []):
                    val, unit = _convert(v["data"]["value"], v["data"].get("unit"))
                    key = f"{m['name']} [{unit}]" if unit else m["name"]
                    by_variant.setdefault(v["variation"]["name"], {}).setdefault(key, val)  # keep first if names repeat
            names[m["monitor"]["value"]] = m["name"]
    for kind, e in ents:
        if kind == "Monitor":
            names.setdefault(e.get("id"), e.findtext("label"))
    setup.monitors = list(dict.fromkeys(names.values()))
    column_to_monitor = {e.findtext("id"): e.findtext("monitorId") for k, e in ents if k.endswith("MonitorColumn")}
    for kind, e in ents:
        if not kind.endswith("OptimizationAnalysis"):
            continue
        for item in e.findall("criteriaList/item"):
            op = item.findtext("objective/operation")
            if item.findtext("criteriaType") == "OBJECTIVE" and op in ("MINIMUM", "MAXIMUM"):
                monitor = names.get(column_to_monitor.get(item.findtext("columnId/id")), "unknown monitor")
                setup.goals.append(Goal(monitor=monitor, sense="minimize" if op == "MINIMUM" else "maximize"))
            elif item.findtext("criteriaType"):
                setup.notes.append(f"Discovery {item.findtext('criteriaType').lower()} in the file was not read")
    headers = {e.findtext("id"): e.findtext("header/value") for k, e in ents if k.endswith(("DrivingDimensionColumn", "PhysicsParameterColumn"))}
    rows = {}
    for kind, e in ents:
        if kind.endswith("ParameterStudy"):
            for cell in e.findall("inputCells/item"):
                q = cell.find("value/quantity")
                if q is None:
                    continue
                val, unit = _convert(q.findtext("value"), q.findtext("unit"))
                rows.setdefault(cell.findtext("key/rowId"), {})[f"{headers.get(cell.findtext('key/columnId'), 'input')} [{unit}]"] = val
    for kind, e in ents:
        if kind.endswith("DesignVariant") and e.findtext("id") in rows:
            name = e.findtext("header/value")
            setup.variants.append(Variant(name=name, inputs=rows[e.findtext("id")], results=by_variant.get(name, {})))


def _missing(s: PartSetup) -> list[str]:
    """What the agent must ask for before it can optimize."""
    out = []
    if not s.parameters:
        out.append("dimensions to change")
    if not s.supports:
        out.append("supports")
    if not s.forces:
        out.append("loads")
    if s.material is None:
        out.append("material")
    elif None in (s.material.youngs_mpa, s.material.yield_mpa):
        out.append("material properties")
    return out


def _faces(e: ET.Element) -> int:
    faces = e.find("scope/faces")
    return len(faces) if faces is not None else 0


def _q(e: ET.Element) -> tuple[float, str]:
    """A <value>/<unit> pair converted to repo units."""
    return _convert(e.findtext("value"), e.findtext("unit"))


def _convert(value, unit: str | None) -> tuple[float, str]:
    factor, label = UNITS.get(unit or "", (1.0, unit or ""))
    return float(value) * factor, label


def _json(z: zipfile.ZipFile, name: str) -> dict | None:
    return json.loads(z.read(name).decode("utf-8-sig")) if name in z.namelist() else None


def _table(z: zipfile.ZipFile, table_id: str | None) -> float | None:
    """First non-temperature value in a Discovery property table, in repo units."""
    name = f"{DISCO}table__{table_id}__dt1.xml"
    if not table_id or name not in z.namelist():
        return None
    for unit, value in re.findall(r'quantity="(?!Temperature)\w+" unit="(\w+)" value="([^"]+)"', z.read(name).decode("utf-8-sig")):
        return _convert(value, unit)[0]
    return None


def _direction_text(d: tuple[float, float, float]) -> str:
    axis = [i for i, c in enumerate(d) if abs(c) > 1e-9]
    if len(axis) == 1:
        return f"along {'-' if d[axis[0]] < 0 else '+'}{'XYZ'[axis[0]]}"
    return "along (" + ", ".join(f"{c:g}" for c in d) + ")"
