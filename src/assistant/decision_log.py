"""Decision log for one assistant run (Role 3 backlog item 4).

Records every question, answer, assumption, the spec, the shortlist, every finalist and why it was
rejected, the recommendation and the explanation, in order. Saved next to result.json as
decision_log.jsonl (one entry per line, for tools) and decision_log.md (a table, for people).
"""
from __future__ import annotations

import json
from pathlib import Path

KINDS = ("question", "answer", "assumption", "spec", "shortlist", "verified", "rejected",
         "recommendation", "explanation", "warning")


class DecisionLog:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, kind: str, text: str, **data) -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; use one of {KINDS}")
        entry = {"seq": len(self.entries), "kind": kind, "text": text, "data": data}
        self.entries.append(entry)
        return entry

    def add_result(self, result: dict, shortlist_ids: list[str] | None = None) -> None:
        """Log the shortlist, each verified finalist, why the others lost, and the pick."""
        if shortlist_ids is not None:
            self.add("shortlist", f"{len(shortlist_ids)} candidates shortlisted by the predictor",
                     design_ids=list(shortlist_ids))
        rec = result.get("recommended")
        rec_id = rec["design_id"] if rec else None
        for f in result.get("finalists", []):
            self.add("verified", f"{f['design_id']} re-checked: feasible={f['feasible']}",
                     **{k: f[k] for k in ("design_id", "feasible", "violated", "mass_kg", "safety_factor") if k in f})
            if f["design_id"] == rec_id:
                continue
            if not f["feasible"]:
                why = "violates " + ", ".join(f.get("violated") or ["a constraint"])
            else:
                why = "feasible but worse objective than the recommended design"
            self.add("rejected", f"{f['design_id']} rejected: {why}", design_id=f["design_id"])
        if rec:
            self.add("recommendation",
                     f"{rec_id} recommended; binding constraint: {result.get('binding_constraint') or 'none'}",
                     design_id=rec_id)
        else:
            self.add("recommendation", "No feasible design found; nothing recommended", design_id=None)
        for w in result.get("warnings", []):
            self.add("warning", w)

    def save(self, stem: Path) -> None:
        stem = Path(stem)
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.with_suffix(".jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in self.entries), encoding="utf-8")
        rows = ["| # | kind | decision |", "|---|---|---|"]
        rows += [f"| {e['seq']} | {e['kind']} | {e['text'].replace('|', '/')} |" for e in self.entries]
        stem.with_suffix(".md").write_text("# Decision log\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
