"""Best-case remaining calls, calibrated per-task cost and the plan projection shown before approval.

Two measured rates size a plan. ``expected_calls_per_task`` is the median number of model calls a
verified task needed: measured in this run once three tasks are verified, else the value the
project's last published research run recorded in ``research/calibration.json``, else the default.
``seconds_per_call`` follows the same order with the wall-clock timings the engine records in the
ledger (``call_timings``). Neither rate loosens the hard gate: the minimum in ``remaining_calls``
still counts identifiable mandatory calls, and a plan is only carried out after its projection was
approved (``plan_approval.json``, see :mod:`run_budget`).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from statistics import median

from .errors import AppError
from .storage import digest, read_optional_json, read_text, write_json

# Measured cost of an ordinary task with evidence contracts: reading decisions, an answer, one or
# two reviews and the rejected shapes in between. The earlier 5 came from the Codex pipeline
# without claim contracts; a task of the first Claude run needed at least 6.
DEFAULT_CALLS_PER_TASK = 8
# Wall-clock time of one call when neither this run nor the project measured one yet.
DEFAULT_SECONDS_PER_CALL = 240
# Dossier, grounding review, assessment and one repair, kept out of the task allowance.
CLOSING_RESERVE = 4
# The in-run rates replace the fallbacks only from this many samples on.
MIN_SAMPLES = 3
SOURCE_LABELS = {"run": "in diesem Lauf gemessen", "project": "Erfahrungswert des Projekts", "default": "Standardwert"}


def affordable_tasks(used, limit, calls_per_task=DEFAULT_CALLS_PER_TASK):
    """How many tasks the approved allowance is expected to carry at the given per-task cost."""
    return max(1, (limit - used - CLOSING_RESERVE) // max(1, calls_per_task))


def checkpoint(path):
    if not path.exists():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("sha256") != digest(saved.get("value")):
        raise AppError("Gespeicherter Rechercheaufruf wurde verändert.",
                       code="invalid_research_checkpoint", status="blocked")
    return saved["value"]


def _saved_state(work, state):
    if state is not None:
        return state
    path = work / "question_research" / "state.json"
    if not path.exists():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = saved.get("value") if isinstance(saved, dict) else None
    return value if isinstance(value, dict) and saved.get("sha256") == digest(value) else None


def run_timings(work):
    """Timings of this run's calls so far, from the receipts ``research.invoke`` writes per call."""
    rows = []
    for path in sorted((work / "calls").glob("call_*/timing.json")):
        row = read_optional_json(path, {}) or {}
        seconds = row.get("seconds")
        if isinstance(seconds, (int, float)) and seconds >= 0:
            rows.append({"name": row.get("schema", path.parent.name), "task": None, "seconds": round(float(seconds), 3)})
    return rows


def measured_calls_per_task(state):
    """Calls each verified task spent, from the ledger's call timings; tasks without a record are skipped."""
    if not state:
        return []
    counts = {}
    for row in state.get("call_timings", []):
        task = row.get("task")
        if task:
            counts[task] = counts.get(task, 0) + 1
    return [n for task, n in sorted(counts.items()) if state.get("tasks", {}).get(task, {}).get("status") == "verified"]


def read_calibration(root):
    if root is None:
        return {}
    data = read_optional_json(root / "research" / "calibration.json", {}) or {}
    return data if isinstance(data, dict) else {}


def expected_calls_per_task(work, root, state=None):
    """(calls, source): the run's median once three tasks are verified, else the project's, else the default."""
    samples = measured_calls_per_task(_saved_state(work, state))
    if len(samples) >= MIN_SAMPLES:
        return max(1, round(median(samples))), "run"
    value = read_calibration(root).get("calls_per_task")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 1:
        return max(1, round(value)), "project"
    return DEFAULT_CALLS_PER_TASK, "default"


def seconds_per_call(work, root, state=None):
    """(seconds, source): the run's median call duration, else the project's, else the default."""
    rows = (_saved_state(work, state) or {}).get("call_timings", [])
    samples = [r["seconds"] for r in rows if isinstance(r.get("seconds"), (int, float)) and r["seconds"] >= 0]
    if len(samples) >= MIN_SAMPLES:
        return round(median(samples), 1), "run"
    value = read_calibration(root).get("seconds_per_call")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return round(float(value), 1), "project"
    return DEFAULT_SECONDS_PER_CALL, "default"


def write_calibration(root, work, run_id, state=None):
    """Record what this run measured, for the projections of the project's next research run."""
    state = _saved_state(work, state)
    if state is None:
        return None
    calls = measured_calls_per_task(state)
    timings = [r["seconds"] for r in state.get("call_timings", []) if isinstance(r.get("seconds"), (int, float))]
    data = {"run_id": run_id, "date": datetime.now(timezone.utc).isoformat(),
            "tasks": len(state.get("tasks", {})), "verified_tasks": len(calls),
            "calls_per_task": round(median(calls), 2) if calls else None,
            "seconds_per_call": round(median(timings), 1) if timings else None,
            "measured_calls": len(timings)}
    path = root / "research" / "calibration.json"
    write_json(path, data)
    return path


def remaining_calls(state, folder):
    """Return identifiable mandatory calls; repairs/rejections can require more.

    Presence is only an estimate of reuse: cached_call still validates the exact
    prompt and schema before a receipt can actually be used. Blocked tasks receive
    no further calls, whether they wait for an explicit gap approval or a new run.
    """
    questions, closing = set(), set()
    if state["phase"] == "completed":
        return questions, closing
    for task_id, row in state["tasks"].items():
        if row["status"] in {"verified", "blocked"}:
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


def budget_projection(work, state, limits, request=None, *, root=None):
    path = work / "budget.json"
    # Another worker's call reservation may be rewriting the file at this instant.
    used = json.loads(read_text(path)).get("model_calls", 0) if path.exists() else 0
    questions, closing = remaining_calls(state, work / "question_research")
    minimum = len(questions | closing | ({request} if request else set()))
    remaining = max(0, limits.model_calls - used)
    open_tasks = sum(row["status"] not in {"verified", "blocked"} for row in state["tasks"].values())
    per_task, source = expected_calls_per_task(work, root, state)
    expected = (open_tasks * per_task + len(closing)) if state["phase"] != "completed" else 0
    return {"used": used, "limit": limits.model_calls, "remaining": remaining,
            "minimum_remaining_calls": minimum, "question_calls": len(questions),
            "closing_calls": len(closing), "headroom": remaining - minimum,
            "shortfall": max(0, minimum - remaining), "feasible": minimum <= remaining,
            "expected_remaining_calls": max(minimum, expected), "expected_calls_per_task": per_task,
            "expected_calls_source": source, "open_tasks": open_tasks}


# From this many tasks on, the dossier no longer fits one model window: composition and review run in
# parts, and every audit round reviews the whole dossier again. Measured on the 20 September run:
# 16 tasks, 82 findings, 23 review parts of about 4 minutes per round, plus assessment and routing.
LARGE_RUN_TASKS = 6
REVIEW_PARTS_PER_TASK = 1.4


def review_parts_per_round(tasks):
    return math.ceil(tasks * REVIEW_PARTS_PER_TASK) if tasks >= LARGE_RUN_TASKS else 0


def plan_projection(work, root, state, limits):
    """What carrying out the current plan is expected to cost, in calls and hours, before any task call."""
    budget = budget_projection(work, state, limits, root=root)
    seconds, seconds_source = seconds_per_call(work, root, state)
    projected = budget["expected_remaining_calls"]
    tasks = len(state["plan"]["tasks"])
    return {"tasks": tasks, "tasks_pending": budget["open_tasks"],
            "large_run": tasks >= LARGE_RUN_TASKS, "review_parts_per_round": review_parts_per_round(tasks),
            "expected_calls_per_task": budget["expected_calls_per_task"],
            "expected_calls_source": budget["expected_calls_source"],
            "closing_reserve": CLOSING_RESERVE, "closing_calls": budget["closing_calls"],
            "projected_calls": projected, "used": budget["used"], "approved_limit": budget["limit"],
            "within_limit": projected <= budget["remaining"],
            "seconds_per_call": seconds, "seconds_per_call_source": seconds_source,
            "projected_hours": round(projected * seconds / 3600, 1),
            "plan_hash": digest(state["plan"]), "plan_caps": list(state.get("plan_caps", [])),
            "projected_at": datetime.now(timezone.utc).isoformat()}


def german_number(value, decimals=1):
    """German decimal notation without a trailing ,0: 4.5 -> 4,5 and 11.0 -> 11."""
    text = f"{float(value):.{decimals}f}".replace(".", ",")
    return text.rstrip("0").rstrip(",") if "," in text else text


def plan_summary(projection):
    hours = projection["projected_hours"]
    hours_text = german_number(round(hours), 0) if hours >= 10 else german_number(hours, 1)
    return (f"{projection['tasks']} Teilfragen, voraussichtlich {projection['projected_calls']} Aufrufe, "
            f"etwa {hours_text} Stunden bei {german_number(projection['seconds_per_call'] / 60, 1)} Minuten je Aufruf")


def plan_review_message(projection, run_id=None):
    """The block message of the plan gate: the projection, then what an approval looks like."""
    text = plan_summary(projection) + (
        f" ({projection['expected_calls_per_task']} Aufrufe je Teilfrage, {SOURCE_LABELS[projection['expected_calls_source']]}; "
        f"genehmigtes Limit {projection['approved_limit']} Aufrufe, {projection['used']} verbraucht).")
    if not projection["within_limit"]:
        text += " Das genehmigte Aufruflimit reicht dafür voraussichtlich nicht; bei der Freigabe eine Obergrenze setzen oder das Limit erhöhen."
    if projection.get("large_run"):
        text += (f" Ab {LARGE_RUN_TASKS} Teilfragen passt das Dossier nicht mehr in ein Modellfenster: Zusammenstellung und "
                 "Gesamtprüfung laufen in Teilen, und jede Prüfrunde prüft das ganze Dossier neu, hier etwa "
                 f"{projection.get('review_parts_per_round')} Prüfteile je Runde zu je etwa 4 Minuten, zusätzlich zu Bewertung "
                 "und Zuordnung der Einwände; jede Nachbesserung wiederholt die Runde. Weniger Teilfragen je Lauf halten das im Rahmen.")
    caps = projection.get("plan_caps") or []
    if caps and projection["tasks"] > min(caps):
        text += (f" Eine Obergrenze von {min(caps)} Teilfragen wurde bereits angefordert; die Planung konnte den Plan "
                 "nicht weiter bündeln, ohne Verpflichtungen wegzulassen.")
    command = f"pla approve <projekt> --research-plan {run_id or '<run_id>'} [--max-tasks N]"
    return (text + " Der Rechercheplan wartet auf Freigabe: im Studio „Rechercheplan freigeben“ oder "
            + command + ", danach fortsetzen. Bis dahin wird kein weiterer Modellaufruf verbraucht.")
