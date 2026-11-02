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


def _xyz(p) -> list[float]:
    """A Point3D as [x, y, z] in mm."""
    return [_mm(p.x), _mm(p.y), _mm(p.z)]


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(a):
    n = _dot(a, a) ** 0.5
    return [c / n for c in a]


def _in_planar_face(face, q: list[float], normal: list[float], samples: int = 16) -> bool:
    """Is point q (mm, on the face's plane) inside the planar face? Even-odd ray cast in 2D
    over all boundary edges (outer loop and holes), arcs sampled as polylines."""
    drop = max(range(3), key=lambda i: abs(normal[i]))  # project onto the best-fit axis plane
    keep = [i for i in range(3) if i != drop]
    qx, qy = q[keep[0]], q[keep[1]]
    inside = False
    for e in face.edges:
        n = 1 if e.curve_type.name == "CURVETYPE_LINE" else samples
        pts = [_xyz(e.shape.evaluate_proportion(k / n).position) for k in range(n + 1)]
        for p0, p1 in zip(pts, pts[1:]):
            x0, y0, x1, y1 = p0[keep[0]], p0[keep[1]], p1[keep[0]], p1[keep[1]]
            if (y0 > qy) != (y1 > qy) and qx < x0 + (qy - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
    return inside


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
        bodies = list(self.design.bodies)
        if not bodies:  # STEP assemblies put the body inside (nested) components
            stack = list(self.design.components)
            while stack:
                comp = stack.pop()
                bodies.extend(comp.bodies)
                stack.extend(comp.components)
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

    def inside_corners(self) -> list[dict]:
        """Concave edges between two planar faces: the sharp inside corners that cause
        stress singularities, and the candidates for a fillet.

        Test: w is the direction from the edge into face B (in B's plane, perpendicular to
        the edge, checked with a point-in-face test). The edge is concave when w points
        to the outward side of face A, i.e. B rises from A on A's open side.
        """
        out = []
        for e in self.body.edges:
            fs = e.faces
            if (len(fs) != 2 or e.curve_type.name != "CURVETYPE_LINE"
                    or any(f.surface_type.name != "SURFACETYPE_PLANE" for f in fs)):
                continue
            a, b = fs
            n_a, n_b = list(a.normal()), list(b.normal())
            s, t = _xyz(e.start), _xyz(e.end)
            d = _unit(_sub(t, s))
            w = _unit(_cross(n_b, d))
            mid = [(s[i] + t[i]) / 2 for i in range(3)]
            eps = 0.05  # mm
            if not _in_planar_face(b, [mid[i] + eps * w[i] for i in range(3)], n_b):
                w = [-c for c in w]
            if _dot(w, n_a) > 1e-6:
                out.append({
                    "face_ids": [a.id, b.id],
                    "length_mm": round(e.length.to(UNITS.mm).m, 3),
                    "midpoint_mm": [round((s[i] + t[i]) / 2, 3) for i in range(3)],
                })
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

    def fillet_between(self, face_a: str, face_b: str, radius_mm: float) -> None:
        """Round the edge shared by two faces, e.g. a sharp inside corner (needs ANSYS 25R2+)."""
        fa, fb = self._face(face_a), self._face(face_b)
        ids_b = {e.id for e in fb.edges}
        shared = [e for e in fa.edges if e.id in ids_b]
        if not shared:
            raise ValueError(f"Faces {face_a} and {face_b} do not share an edge")
        if not self.modeler.geometry_commands.fillet(shared, Distance(radius_mm, UNITS.mm)):
            raise RuntimeError(f"SpaceClaim could not fillet the {face_a}/{face_b} edge with r = {radius_mm} mm")

    def name_faces(self, name: str, face_ids: list[str]) -> None:
        """Tag faces so Mechanical can find them (supports, loads)."""
        self.design.create_named_selection(name, faces=[self._face(i) for i in face_ids])
