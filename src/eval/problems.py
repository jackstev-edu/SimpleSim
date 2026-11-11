"""Practice-set problems: one YAML file per problem, plus a reviewed effects sidecar.

Schema: docs/superpowers/plans/2026-11-11-practice-set.md ("Problem YAML").
A part is either repo-relative or "external:<path relative to Project2>" (files kept out of the
public repo, e.g. SimJEB); a missing external file makes the problem "skipped", not an error.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO.parent
PROBLEMS_DIR = REPO / "practice" / "problems"

KINDS = {"clean_auto", "clean_named", "missing_info", "units", "bad_requirement", "complex"}
MUST_ASK = {"force", "fixed_faces", "load_faces", "material", "safety_factor", "objective"}
MUST_FLAG = {"safety_factor_below_1", "unit_conversion"}
ACTIONS = {"offset", "radius", "fillet"}
EXTERNAL = "external:"


@dataclass
class Problem:
    id: str
    part: str
    kind: str
    request: str
    hidden_facts: str
    user_named_features: list[str]
    key: dict


def _check(ok: bool, field: str, msg: str, path: Path) -> None:
    if not ok:
        raise ValueError(f"{path.name}: {field}: {msg}")


def load_problem(path: Path) -> Problem:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for field in ("id", "part", "kind", "request", "hidden_facts", "key"):
        _check(field in data, field, "missing", path)
    _check(data["id"] == path.stem, "id", f"{data['id']!r} must match the file name {path.stem!r}", path)
    _check(data["kind"] in KINDS, "kind", f"{data['kind']!r} not in {sorted(KINDS)}", path)
    key = data["key"]
    for field in ("setup", "objective", "limits", "features"):
        _check(field in key, f"key.{field}", "missing", path)
    setup = key["setup"]
    for field in ("fixed_face_ids", "load_face_ids", "force_N", "material", "safety_factor"):
        _check(field in setup, f"key.setup.{field}", "missing", path)
    _check(len(setup["force_N"]) == 3, "key.setup.force_N", "needs 3 components", path)
    key.setdefault("must_ask", [])
    key.setdefault("must_flag", [])
    bad = set(key["must_ask"]) - MUST_ASK
    _check(not bad, "key.must_ask", f"unknown {sorted(bad)}", path)
    bad = set(key["must_flag"]) - MUST_FLAG
    _check(not bad, "key.must_flag", f"unknown {sorted(bad)}", path)
    ids = []
    for f in key["features"]:
        _check(f.get("action") in ACTIONS, "key.features.action", f"{f.get('action')!r} not in {sorted(ACTIONS)}", path)
        _check(bool(f.get("id")) and bool(f.get("face_id")), "key.features", "every feature needs id and face_id", path)
        _check(f["action"] != "fillet" or bool(f.get("other_face_id")), "key.features.other_face_id",
               f"fillet {f['id']} needs other_face_id", path)
        f.setdefault("alt_face_ids", [])
        ids.append(f["id"])
    _check(len(ids) == len(set(ids)), "key.features.id", "feature ids must be unique", path)
    named = data.get("user_named_features") or []
    bad = set(named) - set(ids)
    _check(not bad, "user_named_features", f"{sorted(bad)} are not key feature ids", path)
    return Problem(id=data["id"], part=data["part"], kind=data["kind"], request=data["request"],
                   hidden_facts=data["hidden_facts"], user_named_features=list(named), key=key)


def load_problems(folder: Path = PROBLEMS_DIR) -> list[Problem]:
    files = [p for p in Path(folder).glob("*.yaml") if not p.name.endswith(".effects.yaml")]
    return sorted((load_problem(p) for p in files), key=lambda p: p.id)


def resolve_part(p: Problem) -> Path | None:
    """Absolute path of the problem's part; None when an external file is missing."""
    if p.part.startswith(EXTERNAL):
        path = PROJECT / p.part[len(EXTERNAL):]
        return path if path.is_file() else None
    return REPO / p.part


def load_effects(p: Problem, folder: Path = PROBLEMS_DIR) -> dict | None:
    path = Path(folder) / f"{p.id}.effects.yaml"
    if not path.is_file():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))
