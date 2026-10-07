"""Best-case remaining calls, calibrated per-task cost and the plan projection shown before approval.

Two measured rates size a plan. ``expected_calls_per_task`` is the median number of model calls a
verified task needed: measured in this run once three tasks are verified, else the value the
project's last published research run recorded in ``research/calibration.json``, else the default.
``seconds_per_call`` follows the same order with the wall-clock timings the engine records in the
ledger (``call_timings``). The plan projection also expects source candidates and search rounds per
open sub-question (``search_rates``: the project's last published run, else the defaults) and names the
limits that would carry the plan (``raise_to``), so the plan gate asks once, at the right size, instead of
the run stopping on those limits later. Neither rate loosens the hard gate: the minimum in ``remaining_calls``
still counts identifiable mandatory calls, and a plan is only carried out after its projection was
approved (``plan_approval.json``, see :mod:`run_budget`).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from statistics import median

from .errors import AppError
from .cost_estimate import money_view
from .storage import digest, read_optional_json, read_text, write_json

# Measured cost of an ordinary task with evidence contracts: reading decisions, an answer, one or
# two reviews, the rejected shapes in between and its share of audit reworks. The earlier 5 came from
# the Codex pipeline without claim contracts; the 30 verified tasks of the two Opus 5.5 runs of
# 2026-09-26/27 needed a median of 15.5 (10 to 55 without the one outlier).
DEFAULT_CALLS_PER_TASK = 16
# Wall-clock time of one call when neither this run nor the project measured one yet.
DEFAULT_SECONDS_PER_CALL = 240
# Dossier, grounding review, assessment and one repair, kept out of the task allowance.
CLOSING_RESERVE = 4
# The in-run rates replace the fallbacks only from this many samples on.
MIN_SAMPLES = 3
SOURCE_LABELS = {"run": "in diesem Lauf gemessen", "project": "Erfahrungswert des Projekts", "default": "Standardwert"}
# Source candidates and search rounds an open sub-question is expected to use while the project has no published
# research run that measured them (search_rates). The three final research runs of 2026-09-30 used 4.4 to 4.5 sources
# and 1.5 to 1.7 search rounds per sub-question (Asimov, Ontologies; Transformer 2.5 and 0.8), counted over the whole
# run. Rounded up, so the plan gate asks once for enough instead of 9 of the series' 20 stops for the user coming
# later from these two limits (D-155).
DEFAULT_SOURCES_PER_TASK = 5
DEFAULT_SEARCH_ROUNDS_PER_TASK = 2


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


def search_counts(work):
    """(sources, search rounds) the run has used so far: the source candidates it attempted, which the source limit
    counts (question_sources.restore_attempts keeps them in ``source_attempts.json``), and its reserved search rounds
    (``budget.json``, which another worker's reservation may be rewriting, hence storage.read_text)."""
    saved = read_optional_json(work / "question_research" / "source_attempts.json", {}) or {}
    attempts = saved.get("value") if isinstance(saved, dict) else None
    path = work / "budget.json"
    try:
        budget = json.loads(read_text(path)) if path.exists() else {}
    except (OSError, ValueError):
        budget = {}
    rounds = budget.get("search_rounds", 0) if isinstance(budget, dict) else 0
    return (len(attempts) if isinstance(attempts, list) else 0,
            rounds if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds >= 0 else 0)


def search_rates(root):
    """(sources per sub-question, search rounds per sub-question, source): what the project's last published research
    run measured (``research/calibration.json``), else DEFAULT_SOURCES_PER_TASK and DEFAULT_SEARCH_ROUNDS_PER_TASK."""
    data = read_calibration(root)
    rates = (data.get("sources_per_task"), data.get("search_rounds_per_task"))
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 for value in rates):
        return float(rates[0]), float(rates[1]), "project"
    return DEFAULT_SOURCES_PER_TASK, DEFAULT_SEARCH_ROUNDS_PER_TASK, "default"


def write_calibration(root, work, run_id, state=None):
    """Record what this run measured, for the projections of the project's next research run. Sources and search
    rounds count per sub-question what the run used after its plan gate (``plan_projection.json`` keeps what discovery
    and planning had used; a run projected before it counts the whole run)."""
    state = _saved_state(work, state)
    if state is None:
        return None
    calls = measured_calls_per_task(state)
    timings = [r["seconds"] for r in state.get("call_timings", []) if isinstance(r.get("seconds"), (int, float))]
    tasks = len(state.get("tasks", {}))
    sources, rounds = search_counts(work)
    planned = read_optional_json(work / "question_research" / "plan_projection.json", {}) or {}

    def per_task(total, key):
        before = planned.get(key) if isinstance(planned, dict) else None
        before = before if isinstance(before, int) and not isinstance(before, bool) and 0 <= before <= total else 0
        return round((total - before) / tasks, 2) if tasks else None
    data = {"run_id": run_id, "date": datetime.now(timezone.utc).isoformat(),
            "tasks": tasks, "verified_tasks": len(calls),
            "calls_per_task": round(median(calls), 2) if calls else None,
            "seconds_per_call": round(median(timings), 1) if timings else None,
            "measured_calls": len(timings),
            "sources_per_task": per_task(sources, "sources_used"),
            "search_rounds_per_task": per_task(rounds, "search_rounds_used")}
    path = root / "research" / "calibration.json"
    write_json(path, data)
    return path


def remaining_calls(state, folder):
    """Return identifiable mandatory calls; repairs/rejections can require more.

    Presence is only an estimate of reuse: cached_call still validates the exact
    prompt and schema before a receipt can actually be used. Blocked tasks receive
    no further calls, whether they wait for an explicit gap approval or a new run.
    """
    # Imported here: question_answering reaches research modules that import this one.
    from .question_answering import search_folder
    from .question_sources import attempt_folder
    questions, closing = set(), set()
    if state["phase"] == "completed":
        return questions, closing
    for task_id, row in state["tasks"].items():
        if row["status"] in {"verified", "blocked"}:
            continue
        attempt = attempt_folder(folder, task_id, row)
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
                # A step that already searched for other queries keeps this search in a folder of its own.
                search = search_folder(reader.parent, pending.get("web_queries") or []) / "search.json"
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
    # From prompt generation 3 on the dossier is assembled from the answers and their reviews carry its receipts:
    # the closing calls are the assessment and the routing (question_synthesis.ASSEMBLED_GENERATION).
    assembled = int(state.get("prompt_generation", 1)) >= 3
    names = ([] if assembled else [f"batch_{start:03d}" for start in range(0, len(state["dirty_tasks"]), 4)]
             if state["seed_dossier"] else ["dossier"])
    for name in names:
        path = synthesis / f"{name}.json"
        if checkpoint(path) is None:
            closing.add(path)
    for revision in range(0 if assembled else 3):
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
    # A run billed to the user's key also shows its money (D-146); every other projection stays as it was.
    money = money_view(root, work, "research", limits, max(minimum, expected))
    return {"used": used, "limit": limits.model_calls, "remaining": remaining,
            "minimum_remaining_calls": minimum, "question_calls": len(questions),
            "closing_calls": len(closing), "headroom": remaining - minimum,
            "shortfall": max(0, minimum - remaining), "feasible": minimum <= remaining,
            "expected_remaining_calls": max(minimum, expected), "expected_calls_per_task": per_task,
            "expected_calls_source": source, "open_tasks": open_tasks, **({"cost": money} if money else {})}


# From this many tasks on, the dossier no longer fits one model window: composition and review run in
# parts, and every audit round reviews the whole dossier again. Measured on the 20 September run:
# 16 tasks, 82 findings, 23 review parts of about 4 minutes per round, plus assessment and routing.
LARGE_RUN_TASKS = 6
REVIEW_PARTS_PER_TASK = 1.4


def review_parts_per_round(tasks):
    return math.ceil(tasks * REVIEW_PARTS_PER_TASK) if tasks >= LARGE_RUN_TASKS else 0


def plan_projection(work, root, state, limits):
    """What carrying out the current plan is expected to cost, in calls and hours, before any task call, and the source
    candidates and search rounds it is expected to use by the run's end. ``raise_to`` names the limits that carry the
    plan when one of the three does not: what a single explicit approval would raise them to (approve_model_call_limit,
    the user's click, never an allowance), each that does not fit with a tenth more than projected, the others as they
    are; None while all three fit."""
    budget = budget_projection(work, state, limits, root=root)
    seconds, seconds_source = seconds_per_call(work, root, state)
    projected = budget["expected_remaining_calls"]
    tasks = len(state["plan"]["tasks"])
    sources_used, rounds_used = search_counts(work)
    per_source, per_round, rates_source = search_rates(root)
    sources = sources_used + math.ceil(budget["open_tasks"] * per_source)
    rounds = rounds_used + math.ceil(budget["open_tasks"] * per_round)
    fits = (projected <= budget["remaining"], rounds <= limits.search_rounds, sources <= limits.sources)

    def raised(limit, used, expected, fit):
        # A limit that does not fit rises to what is used plus the expectation and a tenth more for reading and
        # correction steps, as the plan card raised the calls before (web/app.js renderPlanReview); one that fits stays.
        return limit if fit else max(limit, used + math.ceil(expected * 11 / 10))
    raise_to = None if all(fits) else {
        "model_calls": raised(limits.model_calls, budget["used"], projected, fits[0]),
        "search_rounds": raised(limits.search_rounds, rounds_used, rounds - rounds_used, fits[1]),
        "sources": raised(limits.sources, sources_used, sources - sources_used, fits[2])}
    return {"tasks": tasks, "tasks_pending": budget["open_tasks"],
            "large_run": tasks >= LARGE_RUN_TASKS, "review_parts_per_round": review_parts_per_round(tasks),
            "expected_calls_per_task": budget["expected_calls_per_task"],
            "expected_calls_source": budget["expected_calls_source"],
            "closing_reserve": CLOSING_RESERVE, "closing_calls": budget["closing_calls"],
            "projected_calls": projected, "used": budget["used"], "approved_limit": budget["limit"],
            **({"cost": budget["cost"]} if budget.get("cost") else {}),
            "within_limit": fits[0],
            "sources_used": sources_used, "sources_limit": limits.sources, "sources_per_task": per_source,
            "projected_sources": sources, "sources_within_limit": fits[2],
            "search_rounds_used": rounds_used, "search_rounds_limit": limits.search_rounds,
            "search_rounds_per_task": per_round, "projected_search_rounds": rounds, "search_rounds_within_limit": fits[1],
            "search_rates_source": rates_source, "raise_to": raise_to,
            "seconds_per_call": seconds, "seconds_per_call_source": seconds_source,
            "projected_hours": round(projected * seconds / 3600, 1),
            "plan_hash": digest(state["plan"]), "plan_caps": list(state.get("plan_caps", [])),
            # The scope review still split tasks in its last pass; the plan stands as it left it (plan_tasks).
            **({"scope_note": state["scope_unresolved"]["note"]} if state.get("scope_unresolved") else {}),
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
    if "projected_sources" in projection:
        # Projections written before 2026-10-07 have no sources and search rounds; their message stays as it was.
        text += (f" Bis zum Ende des Laufs erwartet: etwa {projection['projected_sources']} Quellen und "
                 f"{projection['projected_search_rounds']} Suchrunden ({german_number(projection['sources_per_task'])} "
                 f"Quellen und {german_number(projection['search_rounds_per_task'])} Suchrunden je Teilfrage, "
                 f"{SOURCE_LABELS[projection['search_rates_source']]}; Limits {projection['sources_limit']} Quellen und "
                 f"{projection['search_rounds_limit']} Suchrunden, davon {projection['sources_used']} und "
                 f"{projection['search_rounds_used']} verbraucht).")
        short = [name for key, name in (("search_rounds_within_limit", "Suchrundenlimit"),
                                        ("sources_within_limit", "Quellenlimit")) if not projection[key]]
        if short:
            text += (" Das " + " und das ".join(short) + (" reichen" if len(short) > 1 else " reicht")
                     + " dafür voraussichtlich nicht.")
    raise_to = projection.get("raise_to") or {}
    # Only the limits that rise are named; raise_to keeps the others at their current value for the approval.
    rising = [f"das {name} auf {raise_to[key]}" for key, current, name in (
        ("model_calls", projection.get("approved_limit"), "Aufruflimit"),
        ("search_rounds", projection.get("search_rounds_limit"), "Suchrundenlimit"),
        ("sources", projection.get("sources_limit"), "Quellenlimit")) if key in raise_to and raise_to[key] > (current or 0)]
    if rising:
        text += (" Dafür mit der Freigabe " + (", ".join(rising[:-1]) + " und " if len(rising) > 1 else "") + rising[-1]
                 + " anheben oder eine Obergrenze der Teilfragen setzen. Auf der Kommandozeile: pla approve <projekt> "
                 f"--run-id {run_id or '<run_id>'} --model-calls {raise_to['model_calls']} "
                 f"--search-rounds {raise_to['search_rounds']} --sources {raise_to['sources']}, danach die Planfreigabe.")
    if projection.get("large_run"):
        text += (f" Ab {LARGE_RUN_TASKS} Teilfragen passt das Dossier nicht mehr in ein Modellfenster: Zusammenstellung und "
                 "Gesamtprüfung laufen in Teilen, und jede Prüfrunde prüft das ganze Dossier neu, hier etwa "
                 f"{projection.get('review_parts_per_round')} Prüfteile je Runde zu je etwa 4 Minuten, zusätzlich zu Bewertung "
                 "und Zuordnung der Einwände; jede Nachbesserung wiederholt die Runde. Weniger Teilfragen je Lauf halten das im Rahmen.")
    caps = projection.get("plan_caps") or []
    if caps and projection["tasks"] > min(caps):
        text += (f" Eine Obergrenze von {min(caps)} Teilfragen wurde bereits angefordert; die Planung konnte den Plan "
                 "nicht weiter bündeln, ohne Verpflichtungen wegzulassen.")
    if projection.get("scope_note"):
        text += " " + projection["scope_note"]
    command = f"pla approve <projekt> --research-plan {run_id or '<run_id>'} [--max-tasks N]"
    return (text + " Der Rechercheplan wartet auf Freigabe: im Studio „Rechercheplan freigeben“ oder "
            + command + ", danach fortsetzen. Bis dahin wird kein weiterer Modellaufruf verbraucht.")
