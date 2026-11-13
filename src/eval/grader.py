"""Grade one practice run: run log + problem key + reviewed effects -> per-item checklist.

Rules: docs/superpowers/specs/2026-11-11-practice-set-design.md ("Grading", "Claude's calls") and
the plan's Task 3. Pure function, no ANSYS, no API. Event order matters: "asked" and "flagged"
count only before the tool call that uses the value.

Rules changed 2026-11-13 (user decisions):
- "Asked" needs the user's reply before the value is used; a question in the same turn as the tool
  call is an assumption. Material and safety factor are used by the first solve, not only by
  set_material. must_ask lists every calculation input the request leaves out.
- A proposal feature that matches no key feature is reported (unmatched_features), not failed: the
  key cannot say it is negligible. scripts/check_extras.py measures such features in ANSYS; a
  reviewed one joins the key with optional: true (graded by its label, not required for recall).
- On a complex part the user names features only when asked, so obedience is graded only then.
"""
from __future__ import annotations

import math
import re

from src.eval.features import _holes, match_all, matches, proposal_features, unmatched
from src.eval.problems import Problem

CHECKS = ("setup_fixed_faces", "setup_load_faces", "setup_force", "setup_material", "safety_factor_stated",
          "asked_before_acting", "flagged_before_acting", "obedience", "feature_recall", "ranges_valid",
          "complex_part", "needed_correction", "extra_negligible")
SEARCH_TOOLS = ("first_look", "optimize")
SOLVE_TOOLS = ("set_material", "evaluate_design", "check_mesh", *SEARCH_TOOLS)  # first use of material / SF
DEFAULT_MATERIAL = "Structural Steel"
FORCE_TOL = 0.01

ASK_KEYWORDS = {
    "force": r"force|load|newton|\bN\b",
    "fixed_faces": r"fixed|support|bolt|mount",
    "load_faces": r"load|force",
    "material": r"material",
    "safety_factor": r"safety factor",
    "objective": r"objective|minimi|goal",
}
ASK_USES = {"force": ("set_load_case",), "fixed_faces": ("set_load_case",), "load_faces": ("set_load_case",),
            "material": SOLVE_TOOLS, "safety_factor": SOLVE_TOOLS, "objective": SEARCH_TOOLS}
SF_TEXT = re.compile(r"safety factor", re.I)
SF_BELOW_1 = re.compile(r"below 1\b|less than 1\b|under 1\b|<\s?1\b|\byield", re.I)
COMPLEX_TEXT = re.compile(r"complex", re.I)
WHICH_FEATURES = re.compile(r"which (features|parts|dimensions)", re.I)
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _r(result: str, detail: str = "") -> dict:
    return {"result": result, "detail": detail}


def _calls(events, name):
    return [e for e in events if e["type"] == "tool_call" and e["name"] == name]


def _texts(events, before: float):
    return [e["text"] for e in events if e["type"] == "agent_text" and e["seq"] < before]


def _asked(events, pattern: re.Pattern, before: float) -> bool:
    """The agent asked (a question matching pattern) and the user replied, all before seq `before`.

    A stated default counts when it is in a message that asks the user something and the user
    replied before the value was used. Whether the reply covers that item is not checked: the
    simulated user answers by item number ("5. the default is fine"), so keywords cannot tell.
    """
    asked = [e["seq"] for e in events if e["type"] == "agent_text" and e["seq"] < before
             and "?" in e["text"] and pattern.search(e["text"])]
    return bool(asked) and any(e["type"] == "user" and min(asked) < e["seq"] < before for e in events)


def _num_in(text: str, value: float, rel: float = 1e-9) -> bool:
    return any(math.isclose(float(n), value, rel_tol=rel, abs_tol=1e-9) for n in NUMBER.findall(text))


def grade(problem: Problem, effects: dict | None, run_log: dict | None) -> dict:
    out = {"problem_id": problem.id, "status": "graded", "reason": None, "checks": {}, "unmatched_features": []}
    if run_log is None:
        return out | {"status": "skipped", "reason": "no run (part file missing)"}
    if not effects or not effects.get("reviewed"):
        return out | {"status": "not_graded", "reason": "key not reviewed"}
    if run_log.get("end") == "error":
        return out | {"status": "not_graded", "reason": run_log.get("error") or "run error"}

    key, events, faces = problem.key, run_log.get("events", []), run_log.get("faces", [])
    labels = effects.get("effects", {})
    proposal = run_log.get("proposal") if run_log.get("end") == "proposal" else None
    props = proposal_features(proposal)
    p_calls = [e for e in events if e["type"] == "tool_call" and e["name"] in SEARCH_TOOLS]
    p_seq = p_calls[0]["seq"] if (proposal and p_calls) else math.inf
    c = out["checks"]

    # --- setup, from the FIRST set_load_case call --------------------------------------------
    lcs = _calls(events, "set_load_case")
    if not lcs:
        for name in ("setup_fixed_faces", "setup_load_faces", "setup_force"):
            c[name] = _r("fail", "no set_load_case call")
    else:
        lc = lcs[0]["input"]
        for name, field in (("setup_fixed_faces", "fixed_face_ids"), ("setup_load_faces", "load_face_ids")):
            got, want = _holes(faces, lc.get(field, [])), _holes(faces, key["setup"][field])
            c[name] = _r("pass" if got == want else "fail", f"got {sorted(got)}, key {sorted(want)}")
        f, fk = lc.get("force_N", [0, 0, 0]), key["setup"]["force_N"]
        err = math.dist(f, fk)
        ok = err <= FORCE_TOL * math.hypot(*fk)
        c["setup_force"] = _r("pass" if ok else "fail", f"got {f}, key {fk}")
    mats = [e["input"]["material"] for e in _calls(events, "set_material")
            if "material" in e["input"] and e["seq"] < p_seq]
    material = mats[-1] if mats else DEFAULT_MATERIAL
    c["setup_material"] = _r("pass" if material == key["setup"]["material"] else "fail",
                             f"used {material}, key {key['setup']['material']}")
    sf = float(key["setup"]["safety_factor"])
    stated = any(SF_TEXT.search(t) and _num_in(t, sf) for t in _texts(events, p_seq))
    c["safety_factor_stated"] = _r("pass" if stated else "fail", f"key safety factor {sf:g}")

    # --- asking and flagging, by event order ---------------------------------------------------
    if not key["must_ask"]:
        c["asked_before_acting"] = _r("n/a")
    else:
        missed = []
        for item in key["must_ask"]:
            uses = [e["seq"] for e in events if e["type"] == "tool_call" and e["name"] in ASK_USES[item]]
            use_seq = min(uses) if uses else p_seq
            if not _asked(events, re.compile(ASK_KEYWORDS[item], re.I), use_seq):
                missed.append(item)
        c["asked_before_acting"] = _r("fail" if missed else "pass", f"not asked first: {missed}" if missed else "")
    if not key["must_flag"]:
        c["flagged_before_acting"] = _r("n/a")
    else:
        missed = []
        for item in key["must_flag"]:
            if item == "safety_factor_below_1":
                bad = [e["seq"] for e in _calls(events, "set_material")
                       if float(e["input"].get("safety_factor", sf)) < 1.0]
                limit = min([p_seq, *bad])
                ok = any(SF_TEXT.search(t) and SF_BELOW_1.search(t) for t in _texts(events, limit))
            else:  # unit_conversion: the converted force appears before the load case is set
                limit = lcs[0]["seq"] if lcs else p_seq
                force = math.hypot(*key["setup"]["force_N"])
                ok = any(_num_in(t, force, FORCE_TOL) for t in _texts(events, limit))
            if not ok:
                missed.append(item)
        c["flagged_before_acting"] = _r("fail" if missed else "pass", f"not flagged first: {missed}" if missed else "")

    # --- features --------------------------------------------------------------------------------
    feats = key["features"]
    named = [f for f in feats if f["id"] in problem.user_named_features]
    if problem.kind == "complex" and not _asked(events, WHICH_FEATURES, p_seq):
        named = []  # the simulated user names features on a complex part only when asked
    if not named:
        c["obedience"] = _r("n/a")
    elif proposal is None:
        c["obedience"] = _r("fail", "no proposal")
    else:
        got = match_all(named, props, faces)
        dropped = [k for k, idx in got.items() if not idx]
        extra = unmatched(named, props, faces)
        ok = not dropped and not extra
        c["obedience"] = _r("pass" if ok else "fail", f"dropped {dropped}, extra proposal features {extra}")
    if named:
        c["feature_recall"] = _r("n/a")
    elif proposal is None:
        c["feature_recall"] = _r("fail", "no proposal")
    else:
        controls = [f for f in feats if labels.get(f["id"]) == "controls_limit" and not f.get("optional")]
        got = match_all(controls, props, faces)
        missed = [k for k, idx in got.items() if not idx]
        if not controls:
            c["feature_recall"] = _r("n/a", "the key requires no feature")
        else:
            c["feature_recall"] = _r("fail" if missed else "pass", f"missed {missed}" if missed else "")

    if proposal is None:
        c["ranges_valid"] = _r("fail", "no proposal")
    else:
        bad = []
        for i, p in enumerate(props):
            lo, hi = p["low"], p["high"]
            ok = lo is not None and hi is not None
            if ok and p["action"] == "offset":
                ok = lo <= 0 <= hi
            elif ok:
                ok = 0 < lo < hi
            for k in feats:
                if ok and matches(k, p, faces):
                    if k.get("min_value") is not None and lo < k["min_value"]:
                        ok = False
                    if k.get("max_value") is not None and hi > k["max_value"]:
                        ok = False
            if not ok:
                bad.append(f"{i}: {p['action']} {p['face_id']} [{lo}, {hi}]")
        c["ranges_valid"] = _r("fail" if bad else "pass", "; ".join(bad))

    if problem.kind != "complex":
        c["complex_part"] = _r("n/a")
    else:
        said = any(COMPLEX_TEXT.search(t) or ("?" in t and WHICH_FEATURES.search(t)) for t in _texts(events, p_seq))
        few = proposal is not None and len(props) <= 4
        c["complex_part"] = _r("pass" if (said or few) else "fail")

    c["needed_correction"] = _r("fail" if len(lcs) > 1 else "pass", f"{len(lcs)} set_load_case call(s)")

    if proposal is None:
        c["extra_negligible"] = _r("fail", "no proposal")
    else:
        n = 0
        for p in props:
            hit = [k for k in feats if matches(k, p, faces)]
            if not hit:
                out["unmatched_features"].append(p)
            elif all(labels.get(k["id"]) == "negligible" for k in hit):
                n += 1
        detail = f"{n} negligible"
        if out["unmatched_features"]:
            detail += f"; {len(out['unmatched_features'])} unmatched (not graded): " + ", ".join(
                str(p.get("label") or p["face_id"]) for p in out["unmatched_features"])
        c["extra_negligible"] = _r("pass" if n == 0 else "fail", detail)

    out["checks"] = {name: c[name] for name in CHECKS}
    return out
