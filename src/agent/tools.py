"""Tools the agent can call, and the ANSYS state behind them.

Face ids are stable across reopening a part and across edits (checked 2026-10-30),
so the agent picks faces once on the original part and reuses those ids for every
design variant. Each variant is rebuilt from the original part, never from the
previous variant, so edits are always absolute and reproducible.

All ANSYS calls run on one worker thread: SpaceClaim and the embedded Mechanical
are started there and must keep being called from there.
"""
from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.sim import mechanical
from src.sim.geometry import GeometrySession

FIXED, LOAD = "NS_FIXED", "NS_LOAD"

TOOLS = [
    {
        "name": "describe_part",
        "description": (
            "List every face of the loaded part: id, type (plane/cylinder/...), area, bounding box in mm, "
            "normal for planes, radius for cylinders, plus the part volume. Use it to work out which faces "
            "are walls, holes, mounting faces and load faces. Units: mm."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_load_case",
        "description": (
            "Define the supports and the load, using face ids from describe_part. Fixed faces get a fixed "
            "support. The force (N, global x/y/z components) is spread over the load faces. Only call this "
            "after the user has confirmed which faces are supported and loaded, and the force."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fixed_face_ids": {"type": "array", "items": {"type": "string"}},
                "load_face_ids": {"type": "array", "items": {"type": "string"}},
                "force_N": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            },
            "required": ["fixed_face_ids", "load_face_ids", "force_N"],
        },
    },
    {
        "name": "evaluate_design",
        "description": (
            "Build a design variant from the ORIGINAL part by applying the edits, then solve it in ANSYS "
            "Mechanical with the load case. Returns volume, max equivalent stress (MPa), max deformation (mm), "
            "support reaction (N) and the reaction-balance error. An empty edit list evaluates the original "
            "part (the baseline). Edits: 'offset' moves a planar face by value_mm along its outward normal "
            "(positive = adds material, e.g. a thicker wall; negative = removes material); 'radius' sets a "
            "cylindrical face's radius to value_mm (e.g. a hole or a fillet). Takes about 30 s."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short unique name, e.g. 'baseline' or 't8_r5'"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "face_id": {"type": "string"},
                            "action": {"type": "string", "enum": ["offset", "radius"]},
                            "value_mm": {"type": "number"},
                        },
                        "required": ["face_id", "action", "value_mm"],
                    },
                },
                "element_size_mm": {"type": "number", "description": "Mesh size, default 2 mm"},
            },
            "required": ["name", "edits"],
        },
    },
    {
        "name": "check_mesh",
        "description": (
            "Re-solve an evaluated design with half the element size. If max stress changes by more than "
            "10%, the peak is mesh-dependent, usually a singularity at a sharp corner, and must not be "
            "trusted as is. Run it on the baseline and on the final design. Takes about 1 minute."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "finalize_design",
        "description": (
            "Save an evaluated design as the deliverable part file (.scdocx, opens in SpaceClaim/ANSYS). "
            "Call when the user accepts a design."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
]


class Workbench:
    """Session state: the original part, the load case and every evaluated design."""

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ansys")
        self._geo: GeometrySession | None = None
        self.part: Path | None = None
        self.load_case: dict | None = None
        self.designs: dict[str, dict] = {}
        self.final_part: Path | None = None

    # Everything that touches ANSYS goes through here.
    def _run(self, fn, *args):
        return self._pool.submit(fn, *args).result()

    def _session(self) -> GeometrySession:
        if self._geo is None:
            self._geo = GeometrySession()
        return self._geo

    def load_part(self, path: str | Path) -> dict:
        src = Path(path)
        self.part = self.out_dir / f"original{src.suffix.lower()}"
        shutil.copy(src, self.part)
        self.load_case, self.designs, self.final_part = None, {}, None
        return self._run(self._describe)

    def _describe(self) -> dict:
        geo = self._session()
        geo.open(self.part)
        return {"volume_mm3": round(geo.volume_mm3(), 1), "faces": geo.describe_faces()}

    def _build(self, edits: list[dict]):
        """Reopen the original part, tag the load-case faces, apply the edits."""
        geo = self._session()
        geo.open(self.part)
        geo.name_faces(FIXED, self.load_case["fixed_face_ids"])
        geo.name_faces(LOAD, self.load_case["load_face_ids"])
        faces = {f["id"]: f for f in geo.describe_faces()}
        for e in edits:
            face = faces.get(e["face_id"])
            if face is None:
                raise KeyError(f"Unknown face id {e['face_id']}")
            if e["action"] == "offset":
                if face["type"] != "plane":
                    raise ValueError(f"offset needs a planar face; {e['face_id']} is {face['type']}")
                geo.offset_face(e["face_id"], e["value_mm"], face["normal"])
            elif e["action"] == "radius":
                if face["type"] != "cylinder":
                    raise ValueError(f"radius needs a cylindrical face; {e['face_id']} is {face['type']}")
                geo.set_radius(e["face_id"], e["value_mm"])
        return geo

    def _evaluate(self, name: str, edits: list[dict], element_size_mm: float) -> dict:
        geo = self._build(edits)
        volume = geo.volume_mm3()
        pmdb = geo.save(self.out_dir / f"{name}.pmdb")
        res = mechanical.solve(pmdb, FIXED, LOAD, self.load_case["force_N"], element_size_mm)
        self.designs[name] = {"name": name, "edits": edits, "volume_mm3": round(volume, 1), "pmdb": pmdb,
                              "results": res}
        return {"name": name, "volume_mm3": round(volume, 1), **res}

    # --- tool dispatch ------------------------------------------------------
    def call(self, name: str, args: dict) -> str:
        """Run one tool and return its result as a JSON string (errors raise)."""
        if self.part is None:
            raise RuntimeError("No part loaded yet. Ask the user to upload a part file.")
        if name == "describe_part":
            out = self._run(self._describe)
        elif name == "set_load_case":
            self.load_case = args
            self.designs = {}
            out = {"ok": True, "note": "Load case set; earlier results were cleared."}
        elif name == "evaluate_design":
            if self.load_case is None:
                raise RuntimeError("Set the load case first (set_load_case).")
            out = self._run(self._evaluate, args["name"], args["edits"], args.get("element_size_mm", 2.0))
        elif name == "check_mesh":
            d = self.designs[args["name"]]
            size = d["results"]["element_size_mm"]
            chk = self._run(mechanical.refinement_check, d["pmdb"], FIXED, LOAD, self.load_case["force_N"], size)
            out = {"name": args["name"], "stress_change": round(chk["stress_change"], 4),
                   "converged": chk["converged"],
                   "coarse": {k: chk["coarse"][k] for k in ("element_size_mm", "max_stress_MPa", "max_def_mm")},
                   "fine": {k: chk["fine"][k] for k in ("element_size_mm", "max_stress_MPa", "max_def_mm")}}
            d["mesh_check"] = out
        elif name == "finalize_design":
            d = self.designs[args["name"]]
            path = self.out_dir / f"final_{args['name']}.scdocx"
            self._run(lambda: self._build(d["edits"]).save(path))
            self.final_part = path
            out = {"saved": str(path)}
        else:
            raise ValueError(f"Unknown tool {name}")
        return json.dumps(out, default=str)

    def table(self) -> list[list]:
        """Rows for the GUI's design comparison table."""
        rows = []
        for d in self.designs.values():
            r = d["results"]
            mc = d.get("mesh_check")
            rows.append([d["name"], d["volume_mm3"], round(r["max_stress_MPa"], 1), round(r["max_def_mm"], 4),
                         f"{r['balance_error']:.2%}",
                         "" if mc is None else ("ok" if mc["converged"] else f"peak not converged "
                                                f"({mc['stress_change']:.0%})")])
        return rows

    def close(self) -> None:
        if self._pool._shutdown:  # already closed (close() and the atexit hook can both call this)
            return
        if self._geo is not None:
            self._run(self._geo.close)
            self._geo = None
        self._run(mechanical.shutdown)  # must run on the ANSYS thread, see mechanical.shutdown
        self._pool.shutdown()
