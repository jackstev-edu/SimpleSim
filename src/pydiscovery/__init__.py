"""PyDiscovery emulator: open a part, read its setup, confirm, solve, optimize and save, without Ansys.

    from src.pydiscovery import App
    part = App().open("data/raw/l_bracket_demo.dsco")   # engine: "fitted" by default
    print(part.describe()); part.confirm()               # confirm only after the user agrees
    result = part.optimize(constraints=[("safety_factor", ">=", 1.5)])
    part.save("data/processed/l_bracket_opt.dsco", result.recommended.design)

See docs/pydiscovery.md. Swap the engine (App("calculix"), later App("ansys") or App("discovery"))
without changing the agent, the UI or the contracts.
"""

from .backends import BACKENDS, Backend, BackendUnavailable, get_backend
from .families import FAMILIES, L_BRACKET_GUSSET_V1, L_BRACKET_V0, Family, match_family
from .part_file import (
    Force,
    Goal,
    Material,
    Parameter,
    PartSetup,
    Support,
    UnsupportedPartError,
    Variant,
    open_part,
    save_with_parameters,
)
from .session import App, Part, SetupError
from .tools import TOOL_SPECS, PyDiscoveryTools

__all__ = [
    "App", "Part", "SetupError", "PyDiscoveryTools", "TOOL_SPECS",
    "BACKENDS", "Backend", "BackendUnavailable", "get_backend",
    "FAMILIES", "L_BRACKET_V0", "L_BRACKET_GUSSET_V1", "Family", "match_family",
    "Force", "Goal", "Material", "Parameter", "PartSetup", "Support", "UnsupportedPartError", "Variant",
    "open_part", "save_with_parameters",
]
