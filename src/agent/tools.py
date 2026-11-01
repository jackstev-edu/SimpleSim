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
import math
import re
import queue
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.agent import screen
from src.sim import materials, mechanical, optimizer, prices
from src.sim.geometry import GeometrySession

FIXED, LOAD, FILLETS = "NS_FIXED", "NS_LOAD", "NS_FILLETS"
FILLET_ELEMENTS_PER_RADIUS = 4  # local mesh size on fillet faces = r / 4
DEFAULT_SAFETY_FACTOR = 1.5  # decision 2026-10-31: default 1.5, user can change it
DESIGN_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Responses the optimizer can use. cost_usd exists only once the user has given a price.
RESPONSES = ("mass_kg", "max_stress_MPa", "max_def_mm", "safety_factor", "cost_usd")
MAX_BUDGET = 30
DEFAULT_SECONDS_PER_DESIGN = 30.0  # used for time estimates until a design has been timed

TOOLS = [
    {
        "name": "describe_part",
        "description": (
            "List every face of the loaded part: id, type (plane/cylinder/...), area, bounding box in mm, "
            "normal for planes, radius for cylinders, plus the part volume and mass. Also lists inside_corners: "
            "sharp concave edges between two flat faces (given by their two face ids). Sharp inside corners "
            "cause stress singularities and are candidates for a fillet. Use it to work out which faces "
            "are walls, holes, mounting faces and load faces. Units: mm, kg."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_materials",
        "description": (
            "List the materials in ANSYS's own material library with density (kg/m^3), Young's modulus (GPa), "
            "Poisson's ratio, tensile yield and ultimate strength (MPa). Use it when the user names or asks "
            "about a material, and to find the exact library name for set_material."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_material",
        "description": (
            "Set the part material (exact name from list_materials; default Structural Steel), the required "
            "safety factor on yield (default 1.5), and optionally a material price. Changing the material clears "
            "earlier results, since stresses, deflections and mass all depend on it. Only pass "
            "price_usd_per_kg when the user has given a price, with price_source saying where it came from; "
            "never estimate a price. With a price, every design also reports cost_usd = price x mass."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "material": {"type": "string"},
                "safety_factor": {"type": "number", "exclusiveMinimum": 0},
                "price_usd_per_kg": {"type": "number", "exclusiveMinimum": 0},
                "price_source": {"type": "string"},
            },
        },
    },
    {
        "name": "get_material_price",
        "description": (
            "Look up a material's price (USD per kg) in the local price table, with its source, basis and access "
            "date. Use it before searching the web for a price. Returns cached: false if there is no entry. "
            "reviewed: false means no person has checked the entry yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"material": {"type": "string"}},
            "required": ["material"],
        },
    },
    {
        "name": "record_material_price",
        "description": (
            "Store a price you found with web_search in the local price table and use it for cost_usd. "
            "source_url must be a page from this session's web_search results, and the price must be stated on "
            "that page; never compute or guess a price. basis says what the price is for, e.g. '6061-T6 plate, "
            "bulk, US, 2026'. Convert to USD per kg and say so in basis if the page used other units. The entry is "
            "stored as unreviewed: show the user the price and its source and get their OK before relying on it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "material": {"type": "string"},
                "usd_per_kg": {"type": "number", "exclusiveMinimum": 0},
                "source_url": {"type": "string"},
                "source_title": {"type": "string"},
                "basis": {"type": "string"},
            },
            "required": ["material", "usd_per_kg", "source_url", "source_title", "basis"],
        },
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
            "Mechanical with the load case and the current material. Returns volume, mass, max equivalent stress "
            "(MPa) and where it is "
            "(max_stress_at_mm, x/y/z of the peak node; compare with face bounding boxes to name the feature), max deformation (mm), "
            "support reaction (N), the reaction-balance error, safety_factor = yield / peak stress (meets_safety_factor "
            "compares it with the required one), and cost_usd if a price is set. An empty edit list evaluates the original "
            "part (the baseline). Edits: 'offset' moves a planar face by value_mm along its outward normal "
            "(positive = adds material, e.g. a thicker wall; negative = removes material); 'radius' sets a "
            "cylindrical face's radius to value_mm (e.g. a hole); 'fillet' rounds the sharp edge between face_id "
            "and other_face_id (an inside corner from describe_part) with radius value_mm. Fillets are applied "
            "after the other edits, and the fillet faces get a local mesh of r/4. Takes about 30 s."
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
                            "action": {"type": "string", "enum": ["offset", "radius", "fillet"]},
                            "value_mm": {"type": "number"},
                            "other_face_id": {"type": "string", "description": "Second face, fillet only"},
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
        "name": "first_look",
        "description": (
            "Quick screen of 1-4 candidate features: vary ONE feature at a time to its low and high end while the "
            "others stay at their reference value (the part's current value, else the middle of the range). That is "
            "2n + 1 solves for n features (seconds_per_design each), run on the 2 mm mesh. Keep sharp inside corners filleted through fixed_edits. Returns per-feature effects on every "
            "response, a ranking by effect on the objective, the best feasible design, and the error of every design that failed (errors, failed; a feature whose designs all failed is unranked, see note). The values are indicative "
            "only (one-at-a-time, coarse mesh, no interactions): use them to choose the variables and bounds for "
            "optimize, then run check_mesh on whatever you recommend. Before calling it, show the user the features, "
            "their ranges and the estimated time (2n + 1 designs x seconds_per_design) and wait for their OK. "
            "Designs are stored as look_ref, look_<k>_low, look_<k>_high. The user can stop it between designs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "features": {
                    "type": "array", "minItems": 1, "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "face_id": {"type": "string"},
                            "action": {"type": "string", "enum": ["offset", "radius", "fillet"]},
                            "other_face_id": {"type": "string", "description": "Second face, fillet only"},
                            "low": {"type": "number"},
                            "high": {"type": "number"},
                            "label": {"type": "string", "description": "Plain-language name for the user"},
                        },
                        "required": ["face_id", "action", "low", "high"],
                    },
                },
                "fixed_edits": {"type": "array", "items": {"type": "object"}, "description": "Edits applied to every design and not varied (same form as evaluate_design edits), e.g. a fillet at each sharp inside corner in the load path so the peak stress is not a singularity"},
                "objective": {"type": "string", "enum": list(RESPONSES)},
                "sense": {"type": "string", "enum": ["min", "max"], "description": "Default min"},
                "limits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "response": {"type": "string", "enum": list(RESPONSES)},
                            "op": {"type": "string", "enum": ["<=", ">="]},
                            "value": {"type": "number"},
                        },
                        "required": ["response", "op", "value"],
                    },
                },
            },
            "required": ["features", "objective", "limits"],
        },
    },
    {
        "name": "optimize",
        "description": (
            "Run the classical optimizer (optiSLang ARSM, an adaptive response-surface method) over 1-3 design "
            "variables. Each variable drives one edit (same actions as evaluate_design; for 'offset' the value is "
            "the offset in mm from the original face, for 'radius' and 'fillet' it is the radius). fixed_edits are "
            "applied to every design (e.g. a fillet you are not varying). Minimises (or, with sense 'max', maximises) "
            "the objective response subject to every limit (response, '<=' or '>=', value). To respect the required "
            "safety factor, add the limit safety_factor >= that value. Every design is stored like evaluate_design results "
            "(names prefix_01, prefix_02, ...), so check_mesh and finalize_design work on them. Uses the 2 mm "
            "mesh: run check_mesh on the best design before trusting it. Each evaluation takes about seconds_per_design, so "
            "agree the budget with the user first (default 15, max 30)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "Name prefix for the designs, e.g. 'opt'"},
                "variables": {
                    "type": "array", "minItems": 1, "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "face_id": {"type": "string"},
                            "action": {"type": "string", "enum": ["offset", "radius", "fillet"]},
                            "other_face_id": {"type": "string", "description": "Second face, fillet only"},
                            "lower": {"type": "number"},
                            "upper": {"type": "number"},
                            "start": {"type": "number"},
                        },
                        "required": ["face_id", "action", "lower", "upper", "start"],
                    },
                },
                "fixed_edits": {"type": "array", "items": {"type": "object"}, "description": "Edits applied to every design and not varied (same form as evaluate_design edits), e.g. a fillet at each sharp inside corner in the load path so the peak stress is not a singularity"},
                "objective": {"type": "string", "enum": list(RESPONSES)},
                "sense": {"type": "string", "enum": ["min", "max"], "description": "Default min"},
                "limits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "response": {"type": "string", "enum": list(RESPONSES)},
                            "op": {"type": "string", "enum": ["<=", ">="]},
                            "value": {"type": "number"},
                        },
                        "required": ["response", "op", "value"],
                    },
                },
                "budget": {"type": "integer", "minimum": 4, "maximum": MAX_BUDGET},
            },
            "required": ["prefix", "variables", "objective", "limits"],
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
        self.material = materials.get(materials.DEFAULT)
        self.safety_factor = DEFAULT_SAFETY_FACTOR
        self.price: dict | None = None  # {"usd_per_kg", "source"}: from the user or the price table
        self.seen_urls: set[str] = set()  # URLs from web_search results, filled in by the agent
        self.faces: list[dict] = []  # describe_faces() of the original part, read at load time
        self.stop_event = threading.Event()  # set by the GUI Stop button; checked between designs
        self.progress: queue.Queue[dict] = queue.Queue()  # events for the GUI, one per finished design/solve
        self._timings: list[float] = []  # wall seconds per evaluated design

    def request_stop(self) -> None:
        """Ask the running first_look/optimize to end after the design in progress."""
        self.stop_event.set()

    def seconds_per_design(self) -> float:
        """Mean measured wall time of a design (build + solve), or a default before any was timed."""
        return round(sum(self._timings) / len(self._timings), 1) if self._timings else DEFAULT_SECONDS_PER_DESIGN

    def _emit(self, tool: str, k: int, n: int, design: str, responses: dict | None = None,
              error: str | None = None, best: str | None = None) -> None:
        ev = {"tool": tool, "k": k, "n": n, "design": design, "best_feasible": best}
        ev |= {"error": error} if error else {"responses": responses}
        self.progress.put(ev)

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
        self.material, self.safety_factor, self.price = materials.get(materials.DEFAULT), DEFAULT_SAFETY_FACTOR, None
        desc = self._run(self._describe)
        self.faces = desc["faces"]
        return desc

    def _describe(self) -> dict:
        geo = self._session()
        geo.open(self.part)
        volume = geo.volume_mm3()
        return {"volume_mm3": round(volume, 1), "material": self.material["name"],
                "mass_kg": round(volume * self.material["density_kg_m3"] * 1e-9, 4),
                "faces": geo.describe_faces(), "inside_corners": geo.inside_corners()}

    def _responses(self, d: dict) -> dict:
        """Derived design measures of a stored design: mass, safety factor and cost."""
        r, mat = d["results"], d["material"]
        mass = d["volume_mm3"] * mat["density_kg_m3"] * 1e-9
        out = {"mass_kg": round(mass, 4), "max_stress_MPa": r["max_stress_MPa"], "max_def_mm": r["max_def_mm"],
               "safety_factor": round(mat["yield_MPa"] / r["max_stress_MPa"], 3),
               "required_safety_factor": self.safety_factor}
        out["meets_safety_factor"] = out["safety_factor"] >= self.safety_factor
        if self.price:
            out["cost_usd"] = round(mass * self.price["usd_per_kg"], 2)
        return out

    def _build(self, edits: list[dict]):
        """Reopen the original part, tag the load-case faces, apply the edits.
        Returns the session and the local mesh sizing for the fillet faces (or None)."""
        geo = self._session()
        geo.open(self.part)
        geo.name_faces(FIXED, self.load_case["fixed_face_ids"])
        geo.name_faces(LOAD, self.load_case["load_face_ids"])
        faces = {f["id"]: f for f in geo.describe_faces()}
        radii = []
        # Fillets last: they add new faces and must round the edges of the already-edited walls.
        for e in sorted(edits, key=lambda e: e["action"] == "fillet"):
            face = faces.get(e["face_id"])
            if face is None:
                raise KeyError(f"Unknown face id {e['face_id']}")
            if e["action"] == "fillet":
                other = faces.get(e.get("other_face_id", ""))
                if other is None:
                    raise KeyError(f"fillet needs other_face_id, a known face id (got {e.get('other_face_id')!r})")
                if face["type"] != "plane" or other["type"] != "plane":
                    raise ValueError("fillet needs two planar faces that meet at an inside corner")
                if e["value_mm"] <= 0:
                    raise ValueError("fillet radius must be positive")
                geo.fillet_between(e["face_id"], e["other_face_id"], e["value_mm"])
                radii.append(e["value_mm"])
                continue
            if e["action"] == "offset":
                if face["type"] != "plane":
                    raise ValueError(f"offset needs a planar face; {e['face_id']} is {face['type']}")
                geo.offset_face(e["face_id"], e["value_mm"], face["normal"])
            elif e["action"] == "radius":
                if face["type"] != "cylinder":
                    raise ValueError(f"radius needs a cylindrical face; {e['face_id']} is {face['type']}")
                geo.set_radius(e["face_id"], e["value_mm"])
        if not radii:
            return geo, None
        new = [f["id"] for f in geo.describe_faces() if f["id"] not in faces and f["type"] == "cylinder"]
        geo.name_faces(FILLETS, new)
        return geo, (FILLETS, round(min(radii) / FILLET_ELEMENTS_PER_RADIUS, 4))

    def _evaluate(self, name: str, edits: list[dict], element_size_mm: float) -> dict:
        t0 = time.time()
        out = self._evaluate_untimed(name, edits, element_size_mm)
        self._timings.append(time.time() - t0)
        return out

    def _evaluate_untimed(self, name: str, edits: list[dict], element_size_mm: float) -> dict:
        geo, local = self._build(edits)
        volume = geo.volume_mm3()
        pmdb = geo.save(self.out_dir / f"{name}.pmdb")
        res = mechanical.solve(pmdb, FIXED, LOAD, self.load_case["force_N"], element_size_mm, local, self.material)
        d = {"name": name, "edits": edits, "volume_mm3": round(volume, 1), "material": self.material,
             "pmdb": pmdb, "local": local, "results": res}
        self.designs[name] = d
        return {"name": name, "volume_mm3": d["volume_mm3"], **res, **self._responses(d)}

    def _optimize(self, args: dict) -> dict:
        """Run ARSM; every design goes through _evaluate (we are already on the ANSYS thread)."""
        variables = args["variables"]
        for v in variables:
            if not v["lower"] <= v["start"] <= v["upper"]:
                raise ValueError(f"start must lie within [lower, upper] for {v['face_id']}")
        names = [f"x{i}" for i in range(len(variables))]
        fixed = args.get("fixed_edits", [])
        prefix, count = args["prefix"], [0]
        budget = min(args.get("budget", 15), MAX_BUDGET)
        sign = -1 if args.get("sense", "min") == "max" else 1
        best = [None, None]  # [design name, objective value] of the best feasible design so far
        self.stop_event.clear()
        limits = args.get("limits", [])
        used = {args["objective"], *(lim["response"] for lim in limits)}
        if "cost_usd" in used and not self.price:
            raise ValueError("cost_usd needs a material price: ask the user for one, then call set_material.")

        def evaluate(values: dict[str, float]) -> dict[str, float]:
            count[0] += 1
            edits = fixed + [{k: v[k] for k in ("face_id", "action", "other_face_id") if k in v}
                             | {"value_mm": round(values[n], 4)} for n, v in zip(names, variables)]
            name = f"{prefix}_{count[0]:02d}"
            try:
                r = self._evaluate(name, edits, 2.0)
            except Exception as e:  # noqa: BLE001 - arsm records it; the GUI still wants the event
                self._emit("optimize", count[0], budget, name, error=f"{type(e).__name__}: {e}", best=best[0])
                raise
            if screen.feasible(r, limits) and (best[1] is None or sign * r[args["objective"]] < sign * best[1]):
                best[:] = [name, r[args["objective"]]]
            self._emit("optimize", count[0], budget, name, {k: r[k] for k in RESPONSES if k in r}, best=best[0])
            return {k: r[k] for k in used}

        run = optimizer.arsm([{"name": n, "lower": v["lower"], "upper": v["upper"], "start": v["start"]}
                              for n, v in zip(names, variables)],
                             evaluate, args["objective"], limits, budget,
                             sense=args.get("sense", "min"), should_stop=self.stop_event.is_set)
        rows = []
        for k, e in enumerate(run["evaluations"], 1):
            name = f"{prefix}_{k:02d}"
            row = {"name": name, "values": [round(e["values"][n], 3) for n in names]}
            if e["error"]:
                row |= {"error": e["error"], "feasible": False}
            else:  # full results (optiSLang only saw the objective and the limited responses)
                full = self._responses(self.designs[name])
                row |= {k: round(v, 4) if isinstance(v, float) else v for k, v in full.items()}
                row["feasible"] = screen.feasible(full, limits)
            rows.append(row)
        best_row = min((r for r in rows if r["feasible"]), key=lambda r: sign * r[args["objective"]], default=None)
        return {"stopped": run["stopped"], "evaluations": len(rows), "variable_order": names,
                "best_feasible": best_row, "designs": rows, "seconds_per_design": self.seconds_per_design()}

    def _first_look(self, args: dict) -> dict:
        """One-at-a-time screen: 2n + 1 designs via _evaluate (we are on the ANSYS thread)."""
        features, objective = args["features"], args["objective"]
        sense, limits = args.get("sense", "min"), args.get("limits", [])
        if not 1 <= len(features) <= 4:
            raise ValueError("first_look takes 1-4 features.")
        for f in features:
            if not f["low"] < f["high"]:
                raise ValueError(f"low must be below high for {f['face_id']}")
        if "cost_usd" in {objective, *(lim["response"] for lim in limits)} and not self.price:
            raise ValueError("cost_usd needs a material price: ask the user for one, then call set_material.")
        faces = {f["id"]: f for f in self.faces}
        currents = [{"offset": 0.0, "radius": faces.get(f["face_id"], {}).get("radius_mm")}.get(f["action"])
                    for f in features]  # a fillet has no current value
        plan = screen.screen_plan(features, currents)
        fixed, sign = args.get("fixed_edits", []), -1 if sense == "max" else 1
        self.stop_event.clear()
        rows, stopped, best = {}, "done", [None, None]
        for k, (name, values) in enumerate(plan, 1):
            if self.stop_event.is_set():
                stopped = "user"
                break
            edits = fixed + [{c: f[c] for c in ("face_id", "action", "other_face_id") if c in f}
                             | {"value_mm": round(v, 4)} for f, v in zip(features, values)]
            try:
                r = self._evaluate(name, edits, 2.0)
            except Exception as e:  # noqa: BLE001 - one failed design must not end the screen
                err = f"{type(e).__name__}: {e}"
                rows[name] = {"values": values, "error": err}
                self._emit("first_look", k, len(plan), name, error=err, best=best[0])
                continue
            resp = {c: r[c] for c in RESPONSES if c in r}
            rows[name] = {"values": values, "responses": resp}
            if screen.feasible(resp, limits) and (best[1] is None or sign * resp[objective] < sign * best[1]):
                best[:] = [name, resp[objective]]
            self._emit("first_look", k, len(plan), name, resp, best=best[0])
        summary = screen.summarize(features, rows, objective, sense, limits)
        return {**summary, "stopped": stopped, "solves": len(rows) - summary["failed"],
                "seconds_per_design": self.seconds_per_design()}

    # --- tool dispatch ------------------------------------------------------
    def call(self, name: str, args: dict) -> str:
        """Run one tool and return its result as a JSON string (errors raise)."""
        if self.part is None:
            raise RuntimeError("No part loaded yet. Ask the user to upload a part file.")
        # Design names become file names, so keep them to a safe character set.
        if "name" in args and not DESIGN_NAME.fullmatch(str(args["name"])):
            raise ValueError("Design names may only use letters, digits, '_' and '-' (max 64 characters).")
        if name == "describe_part":
            out = self._run(self._describe)
        elif name == "list_materials":
            out = {"default": materials.DEFAULT, "current": self.material["name"],
                   "materials": {n: {k: v for k, v in m.items() if k != "library"}
                                 for n, m in materials.catalog().items()}}
        elif name == "set_material":
            out = self._set_material(args)
        elif name == "get_material_price":
            entry = prices.get(args["material"])
            out = {"cached": False, "material": args["material"]} if entry is None else {"cached": True, **entry}
            if entry is not None and args["material"] == self.material["name"]:
                self._use_price(entry)
        elif name == "record_material_price":
            materials.get(args["material"])  # only library materials; KeyError lists them
            if args["source_url"] not in self.seen_urls:
                raise ValueError("source_url is not among this session's web_search results; search first and "
                                 "use the URL of the page that states the price.")
            entry = prices.record(args["material"], args["usd_per_kg"], args["source_url"], args["source_title"],
                                  args["basis"])
            if args["material"] == self.material["name"]:
                self._use_price(entry)
            out = {"recorded": entry, "used_for_cost": self.price is not None
                   and self.price["source"] == entry["source_url"]}
        elif name == "set_load_case":
            self.load_case = args
            self.designs = {}
            out = {"ok": True, "note": "Load case set; earlier results were cleared.",
                   "seconds_per_design": self.seconds_per_design()}
        elif name == "evaluate_design":
            if self.load_case is None:
                raise RuntimeError("Set the load case first (set_load_case).")
            out = self._run(self._evaluate, args["name"], args["edits"], args.get("element_size_mm", 2.0))
            out = {**out, "seconds_per_design": self.seconds_per_design()}
        elif name == "check_mesh":
            d = self.designs[args["name"]]
            size = d["results"]["element_size_mm"]

            def progress(which: str, res: dict) -> None:
                self._emit("check_mesh", 1 if which == "coarse" else 2, 2, args["name"],
                           {"max_stress_MPa": res["max_stress_MPa"], "element_size_mm": res["element_size_mm"]})

            chk = self._run(mechanical.refinement_check, d["pmdb"], FIXED, LOAD, self.load_case["force_N"], size,
                            0.10, d["local"], d["material"], progress)
            out = {"name": args["name"], "stress_change": round(chk["stress_change"], 4),
                   "converged": chk["converged"],
                   "coarse": {k: chk["coarse"][k] for k in ("element_size_mm", "local_size_mm", "max_stress_MPa", "max_stress_at_mm", "max_def_mm",
                                                   "nodes")},
                   "fine": {k: chk["fine"][k] for k in ("element_size_mm", "local_size_mm", "max_stress_MPa", "max_stress_at_mm", "max_def_mm",
                                                   "nodes")}}
            d["mesh_check"] = out
            out = {**out, "seconds_per_design": self.seconds_per_design()}
        elif name == "optimize":
            if self.load_case is None:
                raise RuntimeError("Set the load case first (set_load_case).")
            if not DESIGN_NAME.fullmatch(str(args.get("prefix", ""))) or len(args["prefix"]) > 40:
                raise ValueError("prefix may only use letters, digits, '_' and '-' (max 40 characters).")
            out = self._run(self._optimize, args)
        elif name == "first_look":
            if self.load_case is None:
                raise RuntimeError("Set the load case first (set_load_case).")
            out = self._run(self._first_look, args)
        elif name == "finalize_design":
            d = self.designs[args["name"]]
            path = self.out_dir / f"final_{args['name']}.scdocx"
            self._run(lambda: self._build(d["edits"])[0].save(path))
            self.final_part = path
            out = {"saved": str(path)}
        else:
            raise ValueError(f"Unknown tool {name}")
        return json.dumps(out, default=str)

    def _use_price(self, entry: dict) -> None:
        self.price = {"usd_per_kg": entry["usd_per_kg"], "source": entry["source_url"],
                      "title": entry["source_title"], "basis": entry["basis"], "accessed": entry["accessed"],
                      "reviewed": entry.get("reviewed", False)}

    def _set_material(self, args: dict) -> dict:
        note = []
        if "material" in args and args["material"] != self.material["name"]:
            self.material = materials.get(args["material"])  # KeyError lists the valid names
            self.designs, self.price = {}, None
            note.append("Material changed; earlier results and the price were cleared.")
        if "safety_factor" in args:
            sf = float(args["safety_factor"])
            if not (math.isfinite(sf) and sf >= 1.0):
                raise ValueError("safety_factor must be a finite number >= 1 (below 1 the part is designed to yield).")
            self.safety_factor = sf
        if "price_usd_per_kg" in args:
            if not (math.isfinite(float(args["price_usd_per_kg"])) and args["price_usd_per_kg"] > 0):
                raise ValueError("price_usd_per_kg must be a positive finite number.")
            if not args.get("price_source"):
                raise ValueError("price_usd_per_kg needs price_source (where the user's price comes from).")
            self.price = {"usd_per_kg": float(args["price_usd_per_kg"]), "source": args["price_source"]}
        return {"material": self.material, "required_safety_factor": self.safety_factor,
                "allowable_stress_MPa": round(self.material["yield_MPa"] / self.safety_factor, 1),
                "price": self.price, "note": " ".join(note) or "ok"}

    def table(self) -> list[list]:
        """Rows for the GUI's design comparison table."""
        rows = []
        for d in list(self.designs.values()):  # snapshot: the ANSYS thread may add designs meanwhile
            r = d["results"]
            mc = d.get("mesh_check")
            m = self._responses(d)
            rows.append([d["name"], d["material"]["name"], m["mass_kg"], round(r["max_stress_MPa"], 1),
                         m["safety_factor"], round(r["max_def_mm"], 4), m.get("cost_usd", ""),
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
