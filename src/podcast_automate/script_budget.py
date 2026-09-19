"""Minimum remaining model calls of a script run; a fail-fast check before each paid stage.

The numbers are lower bounds without repairs: every rejected draft, design or review costs further
calls. The projection is written to ``runs/<run_id>/budget_projection.json`` so the Studio can show it.
"""
from __future__ import annotations

import json

from .errors import AppError
from .storage import write_json
from .script_checkpoints import finished

# Mandatory calls per episode and stage when nothing is checkpointed yet.
STAGE_CALLS = {
    "teaching": 2,   # teaching design + independent design review
    "writing": 1,    # dialogue draft
    "polishing": 2,  # spoken-language pass + before/after comparison
    "review": 4,     # source review, listener readback, editorial review, teaching review
}
LABELS = {"planning": "Inhaltsverzeichnis", "teaching": "Lehrkonzept", "writing": "Skriptentwurf",
          "polishing": "Dialog-Polishing", "review": "Prüfungen", "series_review": "Serienprüfung"}


def calls_per_episode() -> int:
    return sum(STAGE_CALLS.values())


def remaining_calls(work, manifest, entries, *, series_review: bool) -> tuple[int, dict]:
    stages = manifest.stages
    breakdown = {}
    if stages["planning"].status != "completed" and not (work / "planning_checkpoint.json").exists():
        breakdown["planning"] = 1
    for stage, calls in STAGE_CALLS.items():
        if stage not in stages or stages[stage].status == "completed":
            continue
        pending = [entry.episode_id for entry in entries if not finished(work, entry.episode_id, stage)]
        if pending:
            breakdown[stage] = calls * len(pending)
    if series_review and stages["review"].status != "completed" and not (work / "series_review.json").exists():
        breakdown["series_review"] = 1
    return sum(breakdown.values()), breakdown


def budget_projection(work, manifest, limits, entries, *, series_review: bool) -> dict:
    path = work / "budget.json"
    used = json.loads(path.read_text(encoding="utf-8")).get("model_calls", 0) if path.exists() else 0
    minimum, breakdown = remaining_calls(work, manifest, entries, series_review=series_review)
    remaining = max(0, limits.model_calls - used)
    return {"used": used, "limit": limits.model_calls, "remaining": remaining,
            "minimum_remaining_calls": minimum, "breakdown": breakdown,
            "shortfall": max(0, minimum - remaining), "feasible": minimum <= remaining,
            "calls_per_episode": calls_per_episode(), "stage_calls": STAGE_CALLS,
            "note": "Mindestwerte ohne Reparaturen; jede Korrektur oder erneute Prüfung benötigt weitere Aufrufe."}


def ensure_script_budget(work, manifest, limits, entries, *, series_review: bool) -> dict:
    """Block before the next paid stage when the approved allowance cannot finish the selected episodes."""
    projection = budget_projection(work, manifest, limits, entries, series_review=series_review)
    write_json(work / "budget_projection.json", projection)
    if not projection["feasible"]:
        detail = ", ".join(f"{LABELS.get(stage, stage)} {count}" for stage, count in projection["breakdown"].items())
        raise AppError(
            f"Mindestens {projection['minimum_remaining_calls']} weitere Modellaufrufe erforderlich ({detail}), "
            f"aber nur {projection['remaining']} von {projection['limit']} verfügbar. Fertige Arbeit bleibt "
            "gespeichert; ein höheres Aufruflimit muss für diesen Lauf ausdrücklich genehmigt werden.",
            code="script_budget_insufficient", status="blocked")
    return projection
