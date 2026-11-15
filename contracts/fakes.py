"""Day-one stand-ins so every role can build before the real parts exist.

FakePredictor: beam formula with a fixed 10 percent band, same interface as the GP.
FakeSolver:    the same formula, standing in for fem2d or Ansys.
Both are wrong on purpose where it does not matter and right about the shapes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .models import (
    PARAM_NAMES,
    Candidate,
    Estimate,
    Prediction,
    Result,
    StudySpec,
    Verified,
)

# Nominal structural steel, matching the MVP material library.
MATERIALS = {
    "structural_steel": {"E_mpa": 200_000.0, "yield_mpa": 250.0, "rho_kg_mm3": 7850e-9},
    "aluminum_6061": {"E_mpa": 69_000.0, "yield_mpa": 276.0, "rho_kg_mm3": 2700e-9},
}
MOUNT_HEIGHT_MM = 60.0  # fixed leg height of the v0 family


def beam_formula(design: dict[str, float], force_n: float, material_key: str) -> tuple[float, float, float]:
    """Return (max_stress_mpa, max_disp_mm, mass_kg) from cantilever beam theory."""
    t, L, w, d, r = (design[n] for n in PARAM_NAMES)
    m = MATERIALS[material_key]
    # Fillet concentration factor fitted to the MVP's 2D FEA.
    kt = 1.0 + 0.72 * (w / (2.0 * max(r, 0.25))) ** 0.675
    section_modulus = t * w * w / 6.0
    moment = force_n * (L - w)
    stress = kt * moment / section_modulus
    inertia = t * w**3 / 12.0
    # 1.65 covers corner rotation beam theory misses.
    disp = 1.65 * force_n * (L - w) ** 3 / (3.0 * m["E_mpa"] * inertia)
    # L-plate area minus the hole, times thickness.
    area = w * MOUNT_HEIGHT_MM + (L - w) * w - math.pi * (d / 2) ** 2
    mass = area * t * m["rho_kg_mm3"]
    return stress, disp, mass


def _in_range(spec: StudySpec, design: dict[str, float]) -> bool:
    return all(p.low_mm <= design[p.name] <= p.high_mm for p in spec.parameters)


def _satisfied(spec: StudySpec, metrics: dict[str, float]) -> list[str]:
    """Names of violated constraints, empty when all hold."""
    bad = []
    for c in spec.constraints:
        ok = metrics[c.metric] >= c.value if c.op == ">=" else metrics[c.metric] <= c.value
        if not ok:
            bad.append(f"{c.metric} {c.op} {c.value:g}")
    return bad


def sample_designs(spec: StudySpec, n: int, seed: int = 0) -> list[dict[str, float]]:
    """Uniform random designs inside the spec ranges; fixed params stay fixed."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        out.append({p.name: float(rng.uniform(p.low_mm, p.high_mm)) for p in spec.parameters})
    return out


@dataclass
class FakePredictor:
    """Same call signature the GP will have: predict(spec) -> Prediction."""

    band: float = 0.10  # relative half-width of the fake interval

    def predict(self, spec: StudySpec, n: int | None = None, seed: int = 0) -> Prediction:
        yield_mpa = MATERIALS[spec.material_key]["yield_mpa"]
        cands = []
        for i, design in enumerate(sample_designs(spec, n or min(spec.n_candidates, 2000), seed)):
            s, d, mass = beam_formula(design, spec.load.force_n, spec.material_key)
            est = lambda v: Estimate(value=v, p05=v * (1 - self.band), p95=v * (1 + self.band))  # noqa: E731
            sf = Estimate(value=yield_mpa / s, p05=yield_mpa / (s * (1 + self.band)), p95=yield_mpa / (s * (1 - self.band)))
            # Judge feasibility at the pessimistic end of every interval.
            worst = {"mass_kg": mass, "max_stress_mpa": s * (1 + self.band), "max_disp_mm": d * (1 + self.band), "safety_factor": sf.p05}
            cands.append(
                Candidate(
                    design_id=f"fake-{seed}-{i:05d}",
                    design=design,
                    mass_kg=mass,
                    max_stress_mpa=est(s),
                    max_disp_mm=est(d),
                    safety_factor=sf,
                    feasible_p95=not _satisfied(spec, worst),
                    in_training_range=_in_range(spec, design),
                )
            )
        feasible = [c for c in cands if c.feasible_p95]
        key = spec.objective.metric
        sign = 1 if spec.objective.direction == "minimize" else -1
        feasible.sort(key=lambda c: sign * (c.mass_kg if key == "mass_kg" else getattr(c, key).value))
        return Prediction(
            model_name="fake_formula",
            trained_on_runs=0,
            shortlist=feasible[: spec.n_verify * 4],
            warnings=["fake predictor: beam formula with a fixed 10% band, not a trained model"],
        )


@dataclass
class FakeSolver:
    """Stands in for fem2d or Ansys: solve(spec, design) -> Verified."""

    source: str = "fake_formula"

    def solve(self, spec: StudySpec, design_id: str, design: dict[str, float]) -> Verified:
        s, d, mass = beam_formula(design, spec.load.force_n, spec.material_key)
        sf = MATERIALS[spec.material_key]["yield_mpa"] / s
        metrics = {"mass_kg": mass, "max_stress_mpa": s, "max_disp_mm": d, "safety_factor": sf}
        violated = _satisfied(spec, metrics)
        return Verified(
            design_id=design_id,
            design=design,
            source=self.source,
            mass_kg=mass,
            max_stress_mpa=s,
            max_disp_mm=d,
            safety_factor=sf,
            feasible=not violated,
            violated=violated,
        )


def run_pipeline(spec: StudySpec, baseline: dict[str, float], predictor=None, solver=None) -> Result:
    """Spec -> predict -> verify -> Result. The smoke test and the agent both call this."""
    predictor = predictor or FakePredictor()
    solver = solver or FakeSolver()
    prediction = predictor.predict(spec)
    base = solver.solve(spec, "baseline", baseline)
    finalists = [solver.solve(spec, c.design_id, c.design) for c in prediction.shortlist[: spec.n_verify]]
    feasible = [f for f in finalists if f.feasible]
    best = feasible[0] if feasible else None

    binding = None
    if best is not None:
        # Constraint with the smallest relative margin at the recommended design.
        margins = []
        for c in spec.constraints:
            actual = getattr(best, c.metric)
            margins.append((abs(actual - c.value) / max(abs(c.value), 1e-9), f"{c.metric} {c.op} {c.value:g}"))
        binding = min(margins)[1]

    sens = {}
    if len(prediction.shortlist) >= 4:
        # Pearson correlation of each varied parameter with the objective across the shortlist.
        obj = np.array([c.mass_kg if spec.objective.metric == "mass_kg" else getattr(c, spec.objective.metric).value for c in prediction.shortlist])
        for p in spec.parameters:
            x = np.array([c.design[p.name] for c in prediction.shortlist])
            sens[p.name] = 0.0 if x.std() < 1e-12 or obj.std() < 1e-12 else float(np.corrcoef(x, obj)[0, 1])

    if best is None:
        text = f"None of the {len(finalists)} verified finalists met every constraint. No recommendation yet."
    else:
        change = (best.mass_kg / base.mass_kg - 1) * 100
        text = (
            f"Recommended {best.design_id}: mass {best.mass_kg:.3f} kg ({change:+.0f}% vs baseline), "
            f"safety factor {best.safety_factor:.2f}, displacement {best.max_disp_mm:.3f} mm. "
            f"Binding constraint: {binding}. Verified with {solver.source}."
        )
    return Result(
        spec=spec,
        baseline=base,
        recommended=best,
        finalists=finalists,
        binding_constraint=binding,
        sensitivities=sens,
        explanation=text,
        discovery_values=(best or base).design,
        warnings=prediction.warnings,
    )
