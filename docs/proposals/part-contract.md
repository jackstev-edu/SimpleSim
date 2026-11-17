# Proposal: a fifth contract for the part setup

**From:** Jack (Role 2). **Needs approval from:** Yunus (agent) and Arin (UI). **Status:** proposed, not implemented.

## Why

The agent and the UI both need the confirmation card: what the part file carries, what's missing, and whether the user has confirmed. Today that lives in `src/pydiscovery` as `PartSetup` plus `Part.state()`. If it became a contract, the UI could render the card from a JSON file the same way it renders `result.json`, and the agent could log it in the decision log.

## Proposed model (`contracts/models.py`)

```python
class SetupDimension(BaseModel):
    name: str            # as shown to the user, e.g. "Thickness"
    contract_name: ParamName
    value_mm: float
    low_mm: float
    high_mm: float


class PartSetupCard(Versioned):
    """Agent -> UI: the setup the user confirms before anything is solved."""

    part: str
    file_type: Literal["discovery", "spaceclaim", "step"]
    family: str | None
    engine: str
    dimensions: list[SetupDimension]
    support: str | None
    load_force_n: float | None
    load_direction: tuple[float, float, float] | None
    material: dict | None   # name, youngs_mpa, poisson, yield_mpa, density_kg_m3
    sources: dict[str, str]  # "file", "user" or "family baseline" per item
    missing: list[str]
    problems: list[str]
    confirmed: bool
```

`Part.state()` already returns the same information (with the load as one nested object), so the change is the model, an example `part_setup.json` and a contract test.
