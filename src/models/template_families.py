"""Role 1's ten bracket templates (data/processed/formal_multifamily_v1): dimension names, ranges,
baselines, geometry rules and closed-form volumes, with nothing beyond numpy so the emulator and
the power law can use them without Role 1's CAD packages.

The tables mirror src/data/multifamily_v1/templates.py and additional_templates.py (COMMON, EXTRA,
BASE_PARAMS and the clearance checks inside the builders); tests/test_multifamily_models.py
compares them whenever that module imports. Every template keeps the v0 frame: the horizontal
plate along +X of length L, the vertical leg of height H at X = 0, width W along Z, thickness t,
an inside fillet r, 500 N down on the free end. The fitted power laws live in template_fits.json
next to this file, written by `python -m src.models.multifamily --fit`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

K90 = 1 - math.pi / 4  # area a concave fillet adds (or a convex one removes) at a 90 degree corner, over R^2
K135 = 1 / math.tan(3 * math.pi / 8) - math.pi / 8  # same at the 135 degree edge where a gusset tip meets a leg
RIB_JUNCTION_FILLET_MM = 2.0  # Role 1 rounds the eight edges where each rib meets the legs
NOTCH_MOUTH_ROUND_MM = 0.8  # T05: the four vertical edges where a notch meets the side face
FITS_PATH = Path(__file__).with_name("template_fits.json")

COMMON = ("thickness_mm", "flange_length_mm", "arm_width_mm", "fillet_radius_mm", "vertical_height_mm")
COMMON_RANGES = {"thickness_mm": (4.0, 10.0), "flange_length_mm": (75.0, 120.0), "arm_width_mm": (32.0, 60.0),
                 "fillet_radius_mm": (2.0, 8.0), "vertical_height_mm": (50.0, 90.0)}
COMMON_BASE = {"thickness_mm": 6.0, "flange_length_mm": 80.0, "arm_width_mm": 40.0, "fillet_radius_mm": 4.0, "vertical_height_mm": 60.0}
HOLE = ("hole_diameter_mm", (6.0, 10.0))

# key: (title, extra dimensions with their ranges, baseline of the extras)
TEMPLATES: dict[str, tuple[str, dict[str, tuple[float, float]], dict[str, float]]] = {
    "t01_double_hole_v1": ("Double-hole bracket T01", dict([HOLE]), {"hole_diameter_mm": 8.0}),
    "t02_single_slot_v1": ("Single-slot bracket T02", {"slot_width_mm": (6.0, 10.0), "slot_straight_length_mm": (8.0, 18.0)},
                           {"slot_width_mm": 8.0, "slot_straight_length_mm": 13.0}),
    "t03_twin_slot_v1": ("Twin-slot bracket T03", {"slot_width_mm": (6.0, 10.0), "slot_straight_length_mm": (8.0, 18.0)},
                         {"slot_width_mm": 8.0, "slot_straight_length_mm": 13.0}),
    "t04_rounded_window_v1": ("Rounded-window bracket T04",
                              {"window_length_mm": (18.0, 30.0), "window_width_mm": (12.0, 20.0), "window_radius_mm": (2.0, 4.0)},
                              {"window_length_mm": 24.0, "window_width_mm": 16.0, "window_radius_mm": 3.0}),
    "t05_side_notches_v1": ("Side-notch bracket T05", dict([HOLE, ("notch_radius_mm", (3.0, 6.0))]), {"hole_diameter_mm": 8.0, "notch_radius_mm": 4.5}),
    "t06_tapered_v1": ("Tapered bracket T06", dict([HOLE, ("tip_trim_mm", (3.0, 6.0))]), {"hole_diameter_mm": 8.0, "tip_trim_mm": 4.5}),
    "t07_stepped_v1": ("Stepped bracket T07", dict([HOLE, ("step_trim_mm", (3.0, 6.0))]), {"hole_diameter_mm": 8.0, "step_trim_mm": 4.5}),
    "t08_twin_gusset_v1": ("Twin-gusset bracket T08", dict([HOLE, ("gusset_thickness_mm", (3.0, 5.0)), ("gusset_length_mm", (16.0, 28.0))]),
                           {"hole_diameter_mm": 8.0, "gusset_thickness_mm": 4.0, "gusset_length_mm": 22.0}),
    "t09_perforated_gusset_v1": ("Perforated-gusset bracket T09",
                                 dict([HOLE, ("gusset_thickness_mm", (5.0, 9.0)), ("gusset_length_mm", (28.0, 35.0)), ("rib_hole_diameter_mm", (5.0, 8.0))]),
                                 {"hole_diameter_mm": 8.0, "gusset_thickness_mm": 7.0, "gusset_length_mm": 31.5, "rib_hole_diameter_mm": 6.5}),
    "t10_offset_planform_v1": ("Offset-planform bracket T10", dict([HOLE, ("offset_trim_mm", (6.0, 12.0))]), {"hole_diameter_mm": 8.0, "offset_trim_mm": 9.0}),
}
RIBBED = ("t08_twin_gusset_v1", "t09_perforated_gusset_v1")  # Role 1 starts their fillet range at 2.5 mm, as in the v1 gusset family
LABELS = {  # contract name -> the name Discovery files and the screen use
    "thickness_mm": "Thickness", "flange_length_mm": "Flange length", "arm_width_mm": "Arm width", "hole_diameter_mm": "Hole diameter",
    "fillet_radius_mm": "Fillet radius", "vertical_height_mm": "Vertical height", "slot_width_mm": "Slot width",
    "slot_straight_length_mm": "Slot straight length", "window_length_mm": "Window length", "window_width_mm": "Window width",
    "window_radius_mm": "Window corner radius", "notch_radius_mm": "Notch radius", "tip_trim_mm": "Tip trim", "step_trim_mm": "Step trim",
    "gusset_thickness_mm": "Gusset thickness", "gusset_length_mm": "Gusset length", "rib_hole_diameter_mm": "Rib hole diameter",
    "offset_trim_mm": "Offset trim",
}


def template_params(key: str) -> tuple[str, ...]:
    """Dimension names in model order: the v0 five (hole included only where the template has one), height, then the extras."""
    _, extras, _ = TEMPLATES[key]
    names = ["thickness_mm", "flange_length_mm", "arm_width_mm"]
    if "hole_diameter_mm" in extras:
        names.append("hole_diameter_mm")
    names += ["fillet_radius_mm", "vertical_height_mm"] + [e for e in extras if e != "hole_diameter_mm"]
    return tuple(names)


def template_ranges(key: str) -> dict[str, tuple[float, float]]:
    _, extras, _ = TEMPLATES[key]
    common = dict(COMMON_RANGES)
    if key in RIBBED:
        common["fillet_radius_mm"] = (2.5, 8.0)
    return {n: {**common, **extras}[n] for n in template_params(key)}


def template_baseline(key: str) -> dict[str, float]:
    _, _, base = TEMPLATES[key]
    return {n: {**COMMON_BASE, **base}[n] for n in template_params(key)}


TEMPLATE_PARAMS = {k: template_params(k) for k in TEMPLATES}


# ---------------------------------------------------------------- geometry rules (Role 1's clearance checks, by name)


def _tip_width(key: str, p: dict[str, float]) -> float:
    w = p["arm_width_mm"]
    if key == "t06_tapered_v1":
        return w - 2 * p["tip_trim_mm"]
    if key == "t07_stepped_v1":
        return w - 2 * p["step_trim_mm"]
    if key == "t10_offset_planform_v1":
        return w - p["offset_trim_mm"]
    return w


def _opening_rules(key: str, p: dict[str, float]) -> list[str]:
    """T01 to T04: each opening keeps 3 mm from the fillet, the free end, both sides and the other opening."""
    t, L, W, r = p["thickness_mm"], p["flange_length_mm"], p["arm_width_mm"], p["fillet_radius_mm"]
    if key == "t01_double_hole_v1":
        d = p["hole_diameter_mm"]
        boxes = [(f * L - d / 2, f * L + d / 2, W / 2 - d / 2, W / 2 + d / 2) for f in (0.50, 0.78)]
    elif key in ("t02_single_slot_v1", "t03_twin_slot_v1"):
        w, s = p["slot_width_mm"], p["slot_straight_length_mm"]
        centers = (0.5,) if key == "t02_single_slot_v1" else (0.28, 0.72)
        boxes = [(0.65 * L - s / 2 - w / 2, 0.65 * L + s / 2 + w / 2, f * W - w / 2, f * W + w / 2) for f in centers]
    else:
        l, w = p["window_length_mm"], p["window_width_mm"]
        boxes = [(0.65 * L - l / 2, 0.65 * L + l / 2, 0.5 * W - w / 2, 0.5 * W + w / 2)]
    out = []
    for x0, x1, z0, z1 in boxes:
        if min(x0 - (t + r), L - x1, z0, W - z1) < 3 - 1e-6:
            out.append("opening_clearance: an opening comes within 3 mm of the fillet, the free end or a side")
            break
    if len(boxes) == 2:
        (ax0, ax1, az0, az1), (bx0, bx1, bz0, bz1) = boxes
        if max(bx0 - ax1, ax0 - bx1, bz0 - az1, az0 - bz1) < 3 - 1e-6:
            out.append("opening_spacing: the two openings come within 3 mm of each other")
    return out


def _flange_hole_rules(key: str, p: dict[str, float]) -> list[str]:
    """T05 to T10: the flange hole at 0.76 L keeps 3 mm from the fillet, the free end and the sides of the tip."""
    t, L, r, d = p["thickness_mm"], p["flange_length_mm"], p["fillet_radius_mm"], p["hole_diameter_mm"]
    hx = 0.76 * L
    out = []
    if hx - d / 2 < t + r + 3 or L - hx - d / 2 < 3 or _tip_width(key, p) / 2 - d / 2 < 3:
        out.append("flange_hole_clearance: the flange hole comes within 3 mm of the fillet, the free end or the sides of the tip")
    return out


def failed_rules(key: str, p: dict[str, float]) -> list[str]:
    """Why Role 1's builder would refuse this design, by rule name, or an empty list."""
    if key in ("t01_double_hole_v1", "t02_single_slot_v1", "t03_twin_slot_v1", "t04_rounded_window_v1"):
        return _opening_rules(key, p)
    t, L, W, r, H = (p[n] for n in COMMON)
    d = p["hole_diameter_mm"]
    hx = 0.76 * L
    out = []
    if key == "t05_side_notches_v1":
        n, x = p["notch_radius_mm"], 0.47 * L
        if x - n < t + r + 3 or hx - d / 2 - x - n < 3 or W - 2 * n < 3:
            out.append("notch_clearance: a notch comes within 3 mm of the fillet, the hole or the other notch")
    elif key in ("t06_tapered_v1", "t07_stepped_v1", "t10_offset_planform_v1"):
        if hx - d / 2 < 0.5 * L + 3 or _tip_width(key, p) / 2 - d / 2 < 3:
            out.append("tip_hole_clearance: the hole comes within 3 mm of the trim or the sides of the tip")
    else:  # ribbed
        g, gt = p["gusset_length_mm"], p["gusset_thickness_mm"]
        centres = (0.27 * W, 0.73 * W) if key == "t08_twin_gusset_v1" else (0.5 * W,)
        if hx - d / 2 - (t + g + 2) < 3:
            out.append("rib_to_hole_clearance: the rib comes within 3 mm of the flange hole")
        if g < r + 4 or t + g + 2 > H - 3:
            out.append("rib_reach: the rib must clear the fillet by 4 mm and stop 3 mm below the top of the leg")
        if min(c - gt / 2 - 2 for c in centres) < 3 or min(W - c - gt / 2 - 2 for c in centres) < 3:
            out.append("rib_side_clearance: a rib comes within 5 mm of a side face")
        if len(centres) > 1 and centres[1] - centres[0] - gt - 4 < 3:
            out.append("rib_spacing: the two ribs come within 7 mm of each other")
        if key == "t09_perforated_gusset_v1" and min(0.30 * g - 2, 0.40 * g / math.sqrt(2)) - p["rib_hole_diameter_mm"] / 2 < 2:
            out.append("rib_hole_clearance: the rib hole comes within 2 mm of the edges of the rib")
    return out + _flange_hole_rules(key, p)


# ---------------------------------------------------------------- closed-form volumes


def _base(t, L, W, r, H):
    """Both legs with the inside fillet, no openings."""
    return W * (H * t + (L - t) * t + K90 * r**2)


def _rib(r, g, gt):
    """One 45 degree rib of length g and thickness gt, its overlap with the fillet fill removed, plus the junction fillets."""
    jf = RIB_JUNCTION_FILLET_MM
    return 0.5 * g * g * gt - K90 * r**2 * gt + K90 * jf**2 * (4 * (g - r) + math.pi * r) + 2 * K135 * jf**2 * gt


def template_volume_mm3(key: str, X: np.ndarray) -> np.ndarray:
    """Solid volume for rows of X in template_params(key) order."""
    X = np.asarray(X, dtype=float)
    names = TEMPLATE_PARAMS[key]
    c = {n: X[:, i] for i, n in enumerate(names)}
    t, L, W, r, H = (c[n] for n in COMMON)
    v = _base(t, L, W, r, H)
    hole = math.pi * (c["hole_diameter_mm"] / 2) ** 2 * t if "hole_diameter_mm" in c else 0.0
    if key == "t01_double_hole_v1":
        return v - 2 * hole
    if key in ("t02_single_slot_v1", "t03_twin_slot_v1"):
        w, s = c["slot_width_mm"], c["slot_straight_length_mm"]
        return v - (2 if key == "t03_twin_slot_v1" else 1) * t * (w * s + math.pi * (w / 2) ** 2)
    if key == "t04_rounded_window_v1":
        l, w, rw = c["window_length_mm"], c["window_width_mm"], c["window_radius_mm"]
        return v - t * (l * w - (4 - math.pi) * rw**2)
    if key == "t05_side_notches_v1":  # two half-round notches, their four convex mouth edges rounded
        n = c["notch_radius_mm"]
        return v - math.pi * n**2 * t - 4 * K90 * NOTCH_MOUTH_ROUND_MM**2 * t - hole
    if key == "t06_tapered_v1":  # a smoothstep taper from 0.5 L to 0.94 L then straight: 0.28 a L per side
        return v - 0.56 * c["tip_trim_mm"] * L * t - hole
    if key == "t07_stepped_v1":  # a step on each side from 0.5 L; each convex and concave rounding pair cancels
        return v - c["step_trim_mm"] * L * t - hole
    if key == "t10_offset_planform_v1":
        return v - 0.5 * c["offset_trim_mm"] * L * t - hole
    g, gt = c["gusset_length_mm"], c["gusset_thickness_mm"]
    if key == "t08_twin_gusset_v1":
        return v + 2 * _rib(r, g, gt) - hole
    return v + _rib(r, g, gt) - math.pi * (c["rib_hole_diameter_mm"] / 2) ** 2 * gt - hole  # T09


TEMPLATE_VOLUME = {k: (lambda X, k=k: template_volume_mm3(k, X)) for k in TEMPLATES}


# ---------------------------------------------------------------- the fitted constants


def load_fits(path: Path = FITS_PATH) -> dict[str, dict]:
    """The shipped power-law constants per template, or {} before `python -m src.models.multifamily --fit` has run."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
