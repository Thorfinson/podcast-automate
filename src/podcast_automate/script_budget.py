"""Remaining model calls of a script run: a fail-fast minimum before each paid stage, and the expected count.

The minimum is a lower bound without repairs: every rejected draft, design or review costs further calls. It
alone gates a stage. The expectation scales it by what the project's last completed script run needed per
episode, as research/calibration.json does for research. Both are written to
``runs/<run_id>/budget_projection.json`` so the Studio can show them.
"""
from __future__ import annotations

import json
import math

from .errors import AppError
from .cost_estimate import money_view
from .storage import read_yaml, write_json
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
MINIMUM_NOTE = "Mindestwerte ohne Reparaturen; jede Korrektur oder erneute Prüfung benötigt weitere Aufrufe."


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


def read_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def last_script_run(root, current=None):
    """What the project's last completed script run spent per episode, or None.

    The minimum counts 9 calls per episode; the completed Asimov and Ontologies runs needed 31 and 42.5 (measured
    2026-10-02), because repairs, re-reviews, supplementary research and series corrections come on top. Its calls
    are the charged model calls in its budget.json; its episodes are the one it was asked for or its plan's."""
    rows = []
    for path in (root / "runs").glob("run_*/run_manifest.yaml"):
        work = path.parent
        if work.name == current:
            continue
        try:
            manifest = read_yaml(path)
        except Exception:  # noqa: BLE001 -- an unreadable manifest is no calibration, never a stop
            continue
        if not isinstance(manifest, dict) or manifest.get("kind") != "script" or manifest.get("status") != "completed":
            continue
        calls = read_json(work / "budget.json", {}).get("model_calls")
        request = read_json(work / "script_request.json", {})
        episodes = 1 if request.get("episode") else len(read_json(work / "series_plan.json", {}).get("episodes") or [])
        if isinstance(calls, int) and calls > 0 and episodes > 0:
            rows.append((str(manifest.get("updated_at") or ""), work.name, calls, episodes))
    if not rows:
        return None
    _, run_id, calls, episodes = max(rows)
    return {"run_id": run_id, "model_calls": calls, "episodes": episodes,
            "calls_per_episode": round(calls / episodes, 1)}


def budget_projection(work, manifest, limits, entries, *, series_review: bool, root=None) -> dict:
    """``root`` lets the expectation read the project's last completed script run; without it the expectation is
    the minimum. Only ``minimum_remaining_calls`` decides ``feasible``."""
    path = work / "budget.json"
    used = json.loads(path.read_text(encoding="utf-8")).get("model_calls", 0) if path.exists() else 0
    minimum, breakdown = remaining_calls(work, manifest, entries, series_review=series_review)
    remaining = max(0, limits.model_calls - used)
    calibration = last_script_run(root, work.name) if root is not None else None
    # Each episode stage scaled by the measured calls per episode; planning and the series review stay single calls.
    factor = max(1.0, calibration["calls_per_episode"] / calls_per_episode()) if calibration else 1.0
    expected_breakdown = {stage: math.ceil(count * factor) if stage in STAGE_CALLS else count
                          for stage, count in breakdown.items()}
    expected = sum(expected_breakdown.values())
    # A run billed to the user's key also shows its money (D-146); every other projection stays as it was.
    money = money_view(root, work, "script", limits, expected)
    return {**({"cost": money} if money else {}), "used": used, "limit": limits.model_calls, "remaining": remaining,
            "minimum_remaining_calls": minimum, "breakdown": breakdown,
            "shortfall": max(0, minimum - remaining), "feasible": minimum <= remaining,
            "calls_per_episode": calls_per_episode(), "stage_calls": STAGE_CALLS,
            "note": MINIMUM_NOTE,
            "minimum_label": "Untergrenze: Pflichtaufrufe ohne Korrekturen. Reicht das Limit nicht einmal dafür, hält "
                             "der Lauf vor dem nächsten Schritt an.",
            "expected_remaining_calls": expected, "expected_breakdown": expected_breakdown,
            "expected_shortfall": max(0, expected - remaining),
            "expected_calls_per_episode": calibration["calls_per_episode"] if calibration else calls_per_episode(),
            "expected_source": "project" if calibration else "minimum", "calibration": calibration,
            "expected_label": (f"Erwartung nach dem letzten abgeschlossenen Skriptlauf des Projekts "
                               f"({calibration['run_id']}: {calibration['model_calls']} Aufrufe für "
                               f"{calibration['episodes']} {'Folge' if calibration['episodes'] == 1 else 'Folgen'}, "
                               f"{format(calibration['calls_per_episode'], 'g').replace('.', ',')} je Folge). "
                               "Sie hält nichts an."
                               if calibration else
                               "Noch kein abgeschlossener Skriptlauf im Projekt: die Erwartung ist die Untergrenze.")}


def ensure_script_budget(work, manifest, limits, entries, *, series_review: bool, root=None) -> dict:
    """Block before the next paid stage when the approved allowance cannot finish the selected episodes."""
    projection = budget_projection(work, manifest, limits, entries, series_review=series_review, root=root)
    write_json(work / "budget_projection.json", projection)
    if not projection["feasible"]:
        detail = ", ".join(f"{LABELS.get(stage, stage)} {count}" for stage, count in projection["breakdown"].items())
        expected = (f" Nach dem letzten abgeschlossenen Skriptlauf sind etwa {projection['expected_remaining_calls']} "
                    "zu erwarten." if projection["calibration"] else "")
        raise AppError(
            f"Mindestens {projection['minimum_remaining_calls']} weitere Modellaufrufe erforderlich ({detail}), "
            f"aber nur {projection['remaining']} von {projection['limit']} verfügbar.{expected} Fertige Arbeit bleibt "
            "gespeichert; ein höheres Aufruflimit muss für diesen Lauf ausdrücklich genehmigt werden.",
            code="script_budget_insufficient", status="blocked")
    return projection
