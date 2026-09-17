"""Best-case remaining calls, including reusable checkpoints and closing work."""
from __future__ import annotations

import json

from .errors import AppError
from .storage import digest


def checkpoint(path):
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("sha256") != digest(saved.get("value")):
        raise AppError("Gespeicherter Rechercheaufruf wurde verändert.",
                       code="invalid_research_checkpoint", status="blocked")
    return saved["value"]


def remaining_calls(state, folder):
    """Return identifiable mandatory calls; repairs/rejections can require more.

    Presence is only an estimate of reuse: cached_call still validates the exact
    prompt and schema before a receipt can actually be used.
    """
    questions, closing = set(), set()
    if state["phase"] == "completed":
        return questions, closing
    for task_id, row in state["tasks"].items():
        if row["status"] == "verified":
            continue
        attempt = folder / "tasks" / task_id / f"attempt_{len(row['reopenings'])}"
        if row.get("dependency_revision"):
            attempt = attempt / f"dependency_{row['dependency_revision']}"
        step = row["step"]
        if row["status"] == "reviewing":
            review = attempt / f"review_{step:03d}.json"
            if checkpoint(review) is None:
                questions.add(review)
            continue
        reader = attempt / f"step_{step:03d}" / "reader.json"
        pending = row.get("pending") or checkpoint(reader)
        if pending:
            if pending["action"] == "search_web":
                search = reader.with_name("search.json")
                if checkpoint(search) is None:
                    questions.add(search)
            if pending["action"] != "answer":
                step += 1
                reader = attempt / f"step_{step:03d}" / "reader.json"
                if checkpoint(reader) is None:
                    questions.add(reader)
        else:
            questions.add(reader)
        review = attempt / f"review_{step + 1:03d}.json"
        if checkpoint(review) is None:
            questions.add(review)
    synthesis = folder / "synthesis" / f"audit_{state['audit_round']:02d}"
    names = ([f"batch_{start:03d}" for start in range(0, len(state["dirty_tasks"]), 4)]
             if state["seed_dossier"] else ["dossier"])
    for name in names:
        path = synthesis / f"{name}.json"
        if checkpoint(path) is None:
            closing.add(path)
    for revision in range(3):
        path = synthesis / f"grounding_{revision}.json"
        review = checkpoint(path)
        if review is None:
            closing.add(path)
            break
        if (not review["issues"] or any(i.get("resolution", "research") == "research" for i in review["issues"])
                or revision == 2):
            break
        correction = synthesis / f"correction_{revision}.json"
        if checkpoint(correction) is None:
            closing.add(correction)
    assessment = synthesis / "assessment.json"
    if checkpoint(assessment) is None:
        closing.add(assessment)
    return questions, closing


def budget_projection(work, state, limits, request=None):
    path = work / "budget.json"
    used = json.loads(path.read_text(encoding="utf-8")).get("model_calls", 0) if path.exists() else 0
    questions, closing = remaining_calls(state, work / "question_research")
    minimum = len(questions | closing | ({request} if request else set()))
    remaining = max(0, limits.model_calls - used)
    return {"used": used, "limit": limits.model_calls, "remaining": remaining,
            "minimum_remaining_calls": minimum, "question_calls": len(questions),
            "closing_calls": len(closing), "headroom": remaining - minimum,
            "shortfall": max(0, minimum - remaining), "feasible": minimum <= remaining}
