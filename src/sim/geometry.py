"""Edit an existing part in SpaceClaim through PyAnsys Geometry.

The agent never sees CAD history (user parts arrive as plain solids), so features
are found from face descriptions: type, area, bounding box, normal, radius.

Student licence limits (checked 2026-10-30): only .scdocx and .pmdb can be saved,
and a blocked export kills the SpaceClaim session, so `save` refuses other formats.
"""
from __future__ import annotations

import time
from pathlib import Path

from ansys.geometry.core import launch_modeler_with_spaceclaim
from ansys.geometry.core.designer.design import DesignFileFormat
from ansys.geometry.core.math import UnitVector3D
from ansys.geometry.core.misc import UNITS, Distance

SAVE_FORMATS = {".scdocx": DesignFileFormat.SCDOCX, ".pmdb": DesignFileFormat.PMDB}


def _mm(q) -> float:
    return round(q.to(UNITS.mm).m, 6)


class GeometrySession:
    """One SpaceClaim process, kept open for the whole agent session (startup is ~36 s)."""

    def __init__(self, version: int = 261, hidden: bool = True):
        t0 = time.time()
        self.modeler = launch_modeler_with_spaceclaim(version=version, hidden=hidden)
        self.startup_s = time.time() - t0
        self.design = None

    def close(self) -> None:
        self.modeler.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- part I/O -----------------------------------------------------------
    def open(self, path: str | Path) -> None:
        """Open a part (.scdocx, .stp/.step, or another SpaceClaim reader format)."""
        self.design = self.modeler.open_file(str(path), upload_to_server=False)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        fmt = SAVE_FORMATS.get(path.suffix.lower())
        if fmt is None:
            raise ValueError(f"Can only save {sorted(SAVE_FORMATS)} under the Student licence, not {path.suffix}")
        self.design.download(str(path), fmt)
        return path

    # --- inspection ---------------------------------------------------------
    @property
    def body(self):
        bodies = self.design.bodies
        if len(bodies) != 1:
            raise NotImplementedError(f"Only single-body parts are supported so far (found {len(bodies)})")
        return bodies[0]

    def volume_mm3(self) -> float:
        return self.body.volume.to(UNITS.mm**3).m

    def describe_faces(self) -> list[dict]:
        """Face list for the agent to identify features from. Re-read after every edit."""
        out = []
        for f in self.body.faces:
            bb = f.bounding_box
            d = {
                "id": f.id,
                "type": f.surface_type.name.replace("SURFACETYPE_", "").lower(),
                "area_mm2": round(f.area.to(UNITS.mm**2).m, 3),
                "bbox_min_mm": [_mm(bb.min_corner.x), _mm(bb.min_corner.y), _mm(bb.min_corner.z)],
                "bbox_max_mm": [_mm(bb.max_corner.x), _mm(bb.max_corner.y), _mm(bb.max_corner.z)],
            }
            if d["type"] == "plane":
                d["normal"] = [round(c, 6) for c in f.normal()]
            elif d["type"] == "cylinder":
                d["radius_mm"] = _mm(f.shape.geometry.radius)
            out.append(d)
        return out

    def _face(self, face_id: str):
        for f in self.body.faces:
            if f.id == face_id:
                return f
        raise KeyError(f"No face with id {face_id!r}; face ids can change after edits, re-run describe_faces")

    # --- edits --------------------------------------------------------------
    def offset_face(self, face_id: str, distance_mm: float, direction: list[float]) -> None:
        """Push/pull a planar face, e.g. to change a wall thickness."""
        self.modeler.geometry_commands.extrude_faces(
            self._face(face_id), Distance(distance_mm, UNITS.mm), UnitVector3D(direction)
        )

    def set_radius(self, face_id: str, radius_mm: float) -> None:
        """Resize a cylindrical face, e.g. a hole or a fillet."""
        ok = self.modeler.geometry_commands.offset_faces_set_radius(
            self._face(face_id), Distance(radius_mm, UNITS.mm)
        )
        if not ok:
            raise RuntimeError(f"SpaceClaim could not set radius {radius_mm} mm on face {face_id}")

    def name_faces(self, name: str, face_ids: list[str]) -> None:
        """Tag faces so Mechanical can find them (supports, loads)."""
        self.design.create_named_selection(name, faces=[self._face(i) for i in face_ids])
