# Ansys Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `App("ansys")` in Jack's PyDiscovery emulator solves any L-bracket v0 design with the Role 3 ANSYS pipeline and returns `{max_stress_mpa, max_disp_mm, mass_kg}`.

**Architecture:** `src/sim/ansys_engine.py` turns the five v0 dimensions into workbench edits on `examples/l_bracket.scdocx` (faces found by geometry), drives a `src.agent.tools.Workbench` through its public `load_part` / `call(...)` API, and maps the result to Jack's engine dict. Jack's `AnsysBackend` imports it lazily and delegates.

**Tech Stack:** Python 3.12, PyAnsys Geometry (SpaceClaim), PyMechanical, pytest. Run from `SimpleSim` with `..\.venv\Scripts\python.exe`.

**Spec:** `docs/superpowers/specs/2026-11-15-ansys-engine-design.md`

## Global Constraints

- Only the `AnsysBackend` class (and its docstring line) changes in `src/pydiscovery/`; no edits to `contracts/`.
- `python -m pytest tests -q` must pass without ANSYS: `src.sim.ansys_engine` is imported only inside `AnsysBackend.solve()`; an `ImportError` there becomes `BackendUnavailable`.
- Baseline (from `docs/bracket_family_v0.md`): thickness 6, flange length 80, arm width 40, hole diameter 8 mm; the CAD corner is sharp, so the fillet is always applied.
- `max_stress_mpa` = the workbench's `stress_for_limit_MPa` (zone-excluded stress), never the raw `max_stress_MPa`.
- Mass = `volume_mm3 × material.density_kg_m3 × 1e-9` (the part's own density).
- Comments: one short line on non-trivial lines; no em dashes or double-dash separators (SimpleSim CLAUDE.md). Log GenAI use in `docs/genai-log.md`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No push without the user's OK.

## Review Focus

1. A design at a range edge (thickness 4: offset −2 on two faces; arm width 32: −8) must still map to the right faces: roles come from the baseline part only, never from a re-read edited part.
2. A material name that differs only in case, spaces, `_` or `-` from an ANSYS library name ("structural_steel") should still use the library material.
3. A material with no `youngs_mpa` or `density_kg_m3` (Jack's `Material` allows None) must raise a clear `ValueError`, not a `TypeError` deep in the arithmetic.
4. Two solves on one engine must not reload the part or reset the load case when force and material are unchanged, and must reset when either changes.
5. A failed solve (workbench raises) must leave the engine usable for the next design.

---

### Task 1: Merge Jack's emulator branch into Yunus

**Files:**
- Modify (conflict resolution only): `CLAUDE.md`, `.gitignore`, `docs/genai-log.md`, anything else git reports

- [ ] **Step 1: Merge.** Run: `git merge --no-ff origin/role2/pydiscovery-emulator -m "Merge Jack's PyDiscovery emulator into Yunus"`. Resolve conflicts by keeping both sides (both CLAUDE.md sections, both genai-log rows in date order, the union of `.gitignore` lines).
- [ ] **Step 2: Run the whole suite.** `..\.venv\Scripts\python.exe -m pytest tests -q`. Expected: all pass (198 + Jack's `test_part_file.py` and `test_pydiscovery.py`). If Jack's tests need a package missing from `.venv`, stop and report it; install nothing without the user's OK.
- [ ] **Step 3: Commit the merge** (after conflicts: `git add` the files, `git commit --no-edit`).

---

### Task 2: `src/sim/ansys_engine.py` with fake-workbench tests

**Files:**
- Create: `src/sim/ansys_engine.py`
- Test: `tests/test_ansys_engine.py`

**Interfaces:**
- Consumes: `src.agent.tools.Workbench` public API: `Workbench(out_dir)`, `.load_part(path) -> dict` (has `"faces"`), `.call(name, args) -> str` (JSON) for `"set_material"` `{"material": str}`, `"set_load_case"` `{"fixed_face_ids", "load_face_ids", "force_N"}`, `"evaluate_design"` `{"name", "edits"}` (returns `volume_mm3`, `stress_for_limit_MPa`, `max_stress_MPa`, `max_def_mm`, `zone_mm`, `zone_settled`, `excluded_at`, `stress_for_limit_at_mm`, `max_stress_at_mm`, `nodes`, `messages`), `"check_mesh"` `{"name"}`; `.close()`. Face dicts: `id`, `type`, `bbox_min_mm`, `bbox_max_mm`, `normal` (plane), `radius_mm` (cylinder). `src.sim.materials.catalog() -> dict[name, {"youngs_modulus_GPa", "yield_MPa", "density_kg_m3", ...}]`. Jack's `Material` (`src.pydiscovery.part_file`: `name`, `youngs_mpa`, `density_kg_m3`, ...) and `Family` (`src.pydiscovery.families`: `key`, `load_direction`).
- Produces:
  - `BASELINE_MM: dict[str, float]` = `{"thickness_mm": 6.0, "flange_length_mm": 80.0, "arm_width_mm": 40.0, "hole_diameter_mm": 8.0}`
  - `PART: Path` = `examples/l_bracket.scdocx`, resolved from this file's location
  - `find_faces(faces: list[dict]) -> dict` with keys `back` (plane, normal −X, max X ≈ 0), `end` (plane, normal +X, largest max X), `side` (plane, normal +Z, largest max Z), `vert_inner` (plane, normal +X, not `end`), `horiz_top` (plane, normal +Y, min Y > 0, smallest such Y), `hole` (list of cylinder ids with radius ≈ 4). Raises `ValueError` naming the missing role.
  - `edits_for(design_mm: dict[str, float], roles: dict) -> list[dict]` in workbench edit form; offsets of 0 and a hole at its baseline radius are skipped; the fillet is always last.
  - `library_material(name: str) -> str | None`, matching ignoring case, spaces, `_` and `-`.
  - `class AnsysEngine(out_dir: Path = Path("data/processed/ansys_engine"), mesh_check: bool = False, workbench_factory=Workbench)` with `solve(family, design_mm, force_n, material) -> dict` (`max_stress_mpa`, `max_disp_mm`, `mass_kg`), attribute `last_details: dict`, and `close()`.

- [ ] **Step 1: Write the failing tests** in `tests/test_ansys_engine.py`. `FACES` is a synthetic baseline L-bracket: `b` back x=0 normal −X; `vi` x=6 normal +X; `e` x=80 normal +X; `ht` y=6 normal +Y; bottom y=0 normal −Y; `s` z=40 normal +Z; side z=0 normal −Z; top of the vertical leg y=60 normal +Y; `h1`, `h2` cylinders r 4. `FakeWorkbench` records `load_part` calls (`loads`), `set_load_case` args (`load_cases`), and returns canned JSON: `evaluate_design` gives `volume_mm3` 30000, `stress_for_limit_MPa` 120, `max_stress_MPa` 400, `max_def_mm` 0.5; `check_mesh` gives `{"converged": true, "stress_change": 0.02}`; class flag `fail_next` makes the next `evaluate_design` raise `RuntimeError`. `STEEL_E = materials.catalog()["Structural Steel"]["youngs_modulus_GPa"] * 1000`.

```python
def test_find_faces_by_geometry():
    r = find_faces(FACES)
    assert (r["back"], r["end"], r["side"], r["vert_inner"], r["horiz_top"]) == ("b", "e", "s", "vi", "ht")
    assert sorted(r["hole"]) == ["h1", "h2"]

def test_find_faces_names_the_missing_role():
    with pytest.raises(ValueError, match="end"):
        find_faces([f for f in FACES if f["id"] != "e"])

def test_edits_for_offsets_from_baseline():
    e = edits_for({"thickness_mm": 4.0, "flange_length_mm": 120.0, "arm_width_mm": 32.0,
                   "hole_diameter_mm": 12.0, "fillet_radius_mm": 2.0}, find_faces(FACES))
    assert {"face_id": "vi", "action": "offset", "value_mm": -2.0} in e
    assert {"face_id": "ht", "action": "offset", "value_mm": -2.0} in e
    assert {"face_id": "e", "action": "offset", "value_mm": 40.0} in e
    assert {"face_id": "s", "action": "offset", "value_mm": -8.0} in e
    assert {"face_id": "h1", "action": "radius", "also_face_ids": ["h2"], "value_mm": 6.0} in e
    assert e[-1] == {"face_id": "vi", "action": "fillet", "other_face_id": "ht", "value_mm": 2.0}

def test_edits_for_baseline_is_only_the_fillet():
    assert [x["action"] for x in edits_for({**BASELINE_MM, "fillet_radius_mm": 4.0}, find_faces(FACES))] == ["fillet"]

def test_library_material_matches_loosely():
    assert library_material("structural_steel") == "Structural Steel"
    assert library_material("unobtainium") is None

def test_solve_library_material_returns_zone_stress_and_part_density():
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    out = eng.solve(FAMILY, DESIGN, 500.0, Material(name="Structural Steel", youngs_mpa=200000, density_kg_m3=7850))
    assert out == {"max_stress_mpa": 120.0, "max_disp_mm": 0.5, "mass_kg": pytest.approx(30000 * 7850e-9)}
    assert eng.last_details["raw_peak_mpa"] == 400.0 and eng.last_details["material_used"] == "Structural Steel"

def test_solve_unknown_material_scales_deflection_by_E():
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    out = eng.solve(FAMILY, DESIGN, 500.0, Material(name="Custom", youngs_mpa=100000, density_kg_m3=2000))
    assert out["max_stress_mpa"] == 120.0
    assert out["max_disp_mm"] == pytest.approx(0.5 * STEEL_E / 100000)
    assert "rescaled" in eng.last_details["material_note"]

def test_solve_rejects_material_without_density():
    with pytest.raises(ValueError, match="density"):
        AnsysEngine(workbench_factory=FakeWorkbench).solve(FAMILY, DESIGN, 500.0, Material(name="X", youngs_mpa=1e5))

def test_force_follows_family_direction_and_load_case_is_reused():
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    m = Material(name="Structural Steel", youngs_mpa=200000, density_kg_m3=7850)
    eng.solve(FAMILY, DESIGN, 500.0, m); eng.solve(FAMILY, DESIGN, 500.0, m)
    wb = eng._wb
    assert wb.loads == 1
    assert wb.load_cases == [{"fixed_face_ids": ["b"], "load_face_ids": ["e"], "force_N": [0.0, -500.0, 0.0]}]
    eng.solve(FAMILY, DESIGN, 800.0, m)
    assert len(wb.load_cases) == 2

def test_failed_solve_leaves_engine_usable():
    eng = AnsysEngine(workbench_factory=FakeWorkbench)
    m = Material(name="Structural Steel", youngs_mpa=200000, density_kg_m3=7850)
    FakeWorkbench.fail_next = True
    with pytest.raises(RuntimeError):
        eng.solve(FAMILY, DESIGN, 500.0, m)
    assert eng.solve(FAMILY, DESIGN, 500.0, m)["max_stress_mpa"] == 120.0

def test_mesh_check_is_opt_in():
    eng = AnsysEngine(mesh_check=True, workbench_factory=FakeWorkbench)
    eng.solve(FAMILY, DESIGN, 500.0, Material(name="Structural Steel", youngs_mpa=200000, density_kg_m3=7850))
    assert eng.last_details["mesh_check"]["converged"] is True
```

- [ ] **Step 2: Run** `..\.venv\Scripts\python.exe -m pytest tests/test_ansys_engine.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `src/sim/ansys_engine.py`.** Face roles come from the baseline part's `load_part` faces, read once. Tolerances: 0.01 mm on coordinates, 0.999 on normal dot products. `solve()`: validate the material (`youngs_mpa` and `density_kg_m3` required, `ValueError` naming the missing field); on first use create the workbench with `workbench_factory(out_dir)` and `load_part(PART)`; call `set_material` (library match, else `"Structural Steel"`) and `set_load_case` only when material or force changed, and cache them only after the call returned; `evaluate_design` with name `pyd_NNNN`; if `mesh_check`, `check_mesh`. Deflection rescale only when the library had no match: `× STEEL_E / material.youngs_mpa`. `last_details` holds design name, edits, `raw_peak_mpa`, zone fields, locations, nodes, messages, `material_used`, `material_note`, `mesh_check`.
- [ ] **Step 4: Run** the test file, then the full suite. Expected: all pass.
- [ ] **Step 5: Commit** `src/sim/ansys_engine.py tests/test_ansys_engine.py` ("Ansys engine: L-bracket v0 dimensions to workbench edits").

---

### Task 3: Wire Jack's `AnsysBackend`

**Files:**
- Modify: `src/pydiscovery/backends.py` (`AnsysBackend` class and the module docstring's `ansys` line only)
- Modify: `docs/pydiscovery.md` (engine row `ansys`: "ready; needs a local licensed ANSYS 2026 R1; see `src/sim/ansys_engine.py`")
- Test: `tests/test_pydiscovery_ansys.py`

**Interfaces:**
- Consumes: `AnsysEngine` from Task 2.
- Produces: `AnsysBackend(mesh_check: bool = False)` with `name = "ansys_mechanical"`, `solve(family, design_mm, force_n, material) -> dict`, `last_details` (property, from the engine, `{}` before the first solve), `close()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_ansys_backend_delegates_lazily(monkeypatch):
    fake = types.ModuleType("src.sim.ansys_engine")
    class E:
        def __init__(self, mesh_check=False): self.last_details = {"k": 1}
        def solve(self, *a): return {"max_stress_mpa": 1.0, "max_disp_mm": 2.0, "mass_kg": 3.0}
        def close(self): pass
    fake.AnsysEngine = E
    monkeypatch.setitem(sys.modules, "src.sim.ansys_engine", fake)
    b = get_backend("ansys")
    assert b.solve(None, {}, 1.0, None)["mass_kg"] == 3.0 and b.last_details == {"k": 1}

def test_ansys_backend_without_ansys_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "src.sim.ansys_engine", None)  # makes the import raise ImportError
    with pytest.raises(BackendUnavailable, match="ANSYS"):
        get_backend("ansys").solve(None, {}, 1.0, None)

def test_backends_module_does_not_import_ansys():
    src = Path("src/pydiscovery/backends.py").read_text(encoding="utf-8")
    top = src.split("class ")[0]
    assert "ansys_engine" not in top  # only imported inside AnsysBackend.solve
```

- [ ] **Step 2: Run** `..\.venv\Scripts\python.exe -m pytest tests/test_pydiscovery_ansys.py -q`. Expected: FAIL (placeholder raises).
- [ ] **Step 3: Replace the placeholder.** Import `AnsysEngine` inside `solve()` on first use; catch `ImportError` and raise `BackendUnavailable("The Ansys engine needs a licensed ANSYS 2026 R1 with PyMechanical and PyAnsys Geometry (Role 3, src/sim). Import failed: ...")`.
- [ ] **Step 4: Run** the new tests, then the full suite. Expected: all pass.
- [ ] **Step 5: Commit** `src/pydiscovery/backends.py docs/pydiscovery.md tests/test_pydiscovery_ansys.py` ("PyDiscovery: wire the Ansys engine (Role 3)").

---

### Task 4: ANSYS check against Can's CalculiX runs

**Files:**
- Create: `scripts/smoke_ansys_engine.py`
- Modify: `docs/genai-log.md` (one row); Project2 `CLAUDE.md` Decisions row (controller)

**Interfaces:**
- Consumes: `AnsysEngine(mesh_check=True)`, `L_BRACKET_V0` from `src.pydiscovery.families`, `table_material("structural_steel")` from `src.pydiscovery.session`.

- [ ] **Step 1: Pre-flight.** Free commit memory ≥ 8 GB (`Win32_OperatingSystem.FreeVirtualMemory`), no leftover `SpaceClaim`, `AnsysWBU` or `ansys` processes. If short, stop and ask the user to close programs.
- [ ] **Step 2: Write the script.** Solve Can's runs (from `data/processed/formal_v0/runs.csv` on `Jin(role--data)`), 500 N, structural steel:
  - `formal_20261111_085`: t 6.347747135119783, L 81.57748854959381, w 38.28145814934185, d 9.281828861374336, r 3.647431728482555; CalculiX 171.69 MPa, 0.4582 mm, 0.25544 kg.
  - `formal_20261111_145`: t 7.609197160950782, L 87.74770382265461, w 39.5535328591916, d 7.371207621505333, r 3.753980266308204; CalculiX 128.75 MPa, 0.3171 mm, 0.32948 kg.
  Print per design: zone stress and raw peak, deflection, mass, % differences, zone fields, mesh-check change, solve time. Pass: mass within 1 %, deflection within 10 %, zone stress within 15 % of CalculiX, mesh check converged. Exit code 1 on any FAIL.
- [ ] **Step 3: Run** in the background: `$env:PYTHONIOENCODING='utf-8'; ..\.venv\Scripts\python.exe -u -m scripts.smoke_ansys_engine *> outputs/smoke_ansys_engine.log` (about 4 to 6 minutes). Expected: PASS on both. On a FAIL, stop and report the numbers; do not tune tolerances.
- [ ] **Step 4: Commit** `scripts/smoke_ansys_engine.py docs/genai-log.md` ("Ansys engine: ANSYS check against Can's CalculiX runs").
