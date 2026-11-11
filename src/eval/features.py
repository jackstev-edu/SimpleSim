"""Does a feature the agent proposed mean the same physical change as a key feature?

The same change can be named several ways: either half of a split hole, several holes grouped
through also_face_ids, a fillet pair in either order, or an offset of another face of the same
wall (the key lists those as alt_face_ids).
"""
from __future__ import annotations

from src.agent.screen import hole_faces


def proposal_features(proposal: dict | None) -> list[dict]:
    """The proposal's features as {face_id, action, other_face_id, also_face_ids, low, high}.

    first_look uses low/high, optimize uses lower/upper.
    """
    if not proposal:
        return []
    out = []
    for f in proposal.get("input", {}).get("features", []):
        out.append({"face_id": f.get("face_id"), "action": f.get("action"),
                    "other_face_id": f.get("other_face_id"), "also_face_ids": list(f.get("also_face_ids") or []),
                    "low": f.get("low", f.get("lower")), "high": f.get("high", f.get("upper")),
                    "label": f.get("label")})
    return out


def _holes(faces: list[dict], face_ids: list[str]) -> set[str]:
    known = {f["id"] for f in faces}
    out: set[str] = set()
    for fid in face_ids:
        out.update(hole_faces(faces, fid) if fid in known else [fid])
    return out


def matches(key_feature: dict, prop_feature: dict, faces: list[dict]) -> bool:
    if key_feature["action"] != prop_feature.get("action"):
        return False
    action = key_feature["action"]
    if action == "radius":
        key_set = _holes(faces, [key_feature["face_id"]])
        prop_set = _holes(faces, [prop_feature["face_id"], *(prop_feature.get("also_face_ids") or [])])
        return key_set <= prop_set
    if action == "fillet":
        return {key_feature["face_id"], key_feature.get("other_face_id")} == \
            {prop_feature.get("face_id"), prop_feature.get("other_face_id")}
    return prop_feature.get("face_id") in [key_feature["face_id"], *key_feature.get("alt_face_ids", [])]


def match_all(key_features: list[dict], prop_features: list[dict], faces: list[dict]) -> dict[str, list[int]]:
    """Key feature id -> indices of the proposal features that match it."""
    return {k["id"]: [i for i, p in enumerate(prop_features) if matches(k, p, faces)] for k in key_features}


def unmatched(key_features: list[dict], prop_features: list[dict], faces: list[dict]) -> list[int]:
    """Indices of proposal features that match no key feature."""
    return [i for i, p in enumerate(prop_features) if not any(matches(k, p, faces) for k in key_features)]
