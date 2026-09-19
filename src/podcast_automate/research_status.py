"""Explain saved research inputs and observable signals without another model call."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache

from .model_trace import redact, trace_view
from .research_ledger import active_tasks
from .storage import read_optional_json as read


ASSIGNMENTS = {
    "ResearchDecision": "Die bereitgestellten Textstellen an den Prüfpunkten messen und daraus eine belegte Antwort oder den nächsten gezielten Lese- und Suchschritt bestimmen.",
    "AnswerReview": "Die vorgeschlagene Antwort unabhängig mit den Originalpassagen vergleichen: Sind die Aussagen belegt und alle Prüfpunkte beantwortet?",
    "QuestionSearch": "Zusätzliche Originalquellen für die noch offene Teilfrage suchen.",
    "QuestionPlan": "Die Leitfragen in einzeln beantwortbare Rechercheaufgaben aufteilen.",
    "QuestionScopeReview": "Prüfen, ob jede Rechercheaufgabe einen klar begrenzten Umfang hat.",
    "ResearchDiscovery": "Originalquellen zu den vereinbarten Leitfragen suchen.",
    "ResearchDossier": "Die vorliegenden Belege und Antworten zu einem zusammenhängenden Dossier verbinden.",
    "DossierPatch": "Die zugeordneten Befunde im Dossier gezielt ergänzen oder berichtigen.",
    "SourceReview": "Die Aussagen des Dossiers mit ihren Originalquellen vergleichen.",
    "ResearchAssessment": "Prüfen, ob das Dossier den vereinbarten Erklärumfang erfüllt.",
    "ReopenPlan": "Konkrete Prüfeinwände den betroffenen Recherchefragen zuordnen.",
}
QUESTION_SCHEMAS = {"ResearchDecision", "AnswerReview", "QuestionSearch"}


def clean(value, limit=800):
    if not isinstance(value, str):
        return ""
    value = redact(value)
    value = re.sub(r"\b(?:src|sec|qt|task|finding|gap)_[a-zA-Z0-9_]+(?:#[a-zA-Z0-9_]+)?", "[interner Verweis]", value)
    value = " ".join(value.split())
    return value[:limit] + (" …" if len(value) > limit else "")


def texts(values, maximum=4):
    return list(dict.fromkeys(clean(value) for value in values if isinstance(value, str) and value.strip()))[:maximum]


def material(sources):
    """Titles and counts only; no original text, paths, credentials or model prompts."""
    rows, count = [], 0
    for source in sources:
        sections = source.get("sections", [])
        count += len(sections)
        rows.append({"title": clean(source.get("title"), 220), "sections": len(sections),
                     "pages": sorted({s["page"] for s in sections if type(s.get("page")) is int})[:8]})
    return {"section_count": count, "source_count": len(rows), "sources": rows[:6]}


def request_context(schema, payload):
    """Allowlisted projection of the actual model input; never store the prompt itself."""
    task = payload.get("task") or {}
    sources = payload.get("sources") or []
    actions = payload.get("previous_actions") or []
    return {"schema": schema, "question": clean(task.get("question")),
            "criteria": texts(task.get("acceptance", []), 6),
            "feedback": texts(payload.get("feedback", [])),
            "last_step": clean(actions[-1].get("reason")) if actions else "",
            "queries": texts(payload.get("queries", [])),
            "candidate_count": sum(len(c.get("candidates", [])) for c in payload.get("candidates", [])),
            "material": material(sources)}


def record_request(directory, schema, prompt):
    # Optional observability must never interfere with production or its checkpoints.
    try:
        payload = json.loads(prompt.rsplit("\n", 1)[-1])
        if not isinstance(payload, dict):
            return
        context = request_context(schema, payload)
        from .storage import write_json
        write_json(directory / "work_context.json", context)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return


@lru_cache(maxsize=4)
def _source_metadata(path, modified, size):
    # Source indexes can contain entire books. Keep only metadata in the read cache.
    index = read(path, {})
    index = index.get("value", index)
    lookup = {}
    for source in index.get("sources", []):
        # A manifest row keeps the document's metadata and points at its processed text.
        document = source.get("document") or source
        sections = source.get("sections")
        if sections is None and source.get("processed"):
            root = path.parents[3]
            processed = read(root / source["processed"], {}) if (root / source["processed"]).is_file() else {}
            sections = processed.get("sections", [])
        for section in sections or []:
            lookup[f"{document['id']}#{section['id']}"] = (document["id"], document.get("title", ""), section.get("page"))
    return lookup


def active_questions(state):
    """The tasks being answered right now, in plan order, with what each one is doing."""
    rows = state.get("tasks", {})
    active = set(active_tasks(state))
    return [{"id": spec["id"], "question": clean(spec.get("question")),
             "status": rows.get(spec["id"], {}).get("status"),
             "activity": clean(rows.get(spec["id"], {}).get("activity"), 300)}
            for spec in state.get("plan", {}).get("tasks", []) if spec.get("id") in active]


def saved_context(work, state, schema):
    """Fallback for a worker already running before input receipts were added.

    With several tasks in flight the saved state cannot tell which one the latest call serves;
    the first active task in plan order stands in, and ``active_questions`` lists them all.
    """
    active = active_tasks(state)
    spec = next((s for s in state.get("plan", {}).get("tasks", []) if s.get("id") in active), {})
    row = state.get("tasks", {}).get(spec.get("id"), {})
    if schema not in QUESTION_SCHEMAS or state.get("phase") != "questions":
        return {}, {}
    refs = row.get("current_refs", [])
    if schema == "AnswerReview":
        refs = list(dict.fromkeys(e.get("reference") for f in (row.get("answer") or {}).get("findings", []) for e in f.get("evidence", [])))
    if schema == "QuestionSearch":
        refs = []  # The search sees a catalog, not the full passages.
    signature = state.get("index_hash", "")
    index = work / "question_research/indexes" / (signature + ".json") if re.fullmatch(r"[a-f0-9]{64}", signature) else None
    sources = {}
    if index and index.is_file():
        stat = index.stat()
        lookup = _source_metadata(index, stat.st_mtime_ns, stat.st_size)
        for ref in dict.fromkeys(refs):
            if ref in lookup:
                identifier, title, page = lookup[ref]
                sources.setdefault(identifier, {"title": title, "sections": []})["sections"].append({"page": page})
    payload = {"task": spec, "sources": list(sources.values()), "feedback": row.get("feedback", []),
               "previous_actions": row.get("actions", []), "candidates": row.get("catalog", [])}
    context = request_context(schema, payload)
    context["material"]["unresolved_sections"] = len(set(refs)) - context["material"]["section_count"]
    return context, row


def iso_mtime(path):
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def work_insight(work, run):
    calls = sorted((work / "calls").glob("call_*/output_schema.json"))
    if not calls:
        return None
    directory = calls[-1].parent
    schema = read(calls[-1], {}).get("title", "")
    activity = read(directory / "activity.json", {})
    diagnostics = read(directory / "diagnostics.json", {})
    response = directory / "response.json"
    failure = read(directory / "failure.json", {})
    state = read(work / "question_research/state.json", {}).get("value", {})
    context, row = saved_context(work, state, schema)
    recorded = read(directory / "work_context.json")
    if recorded and recorded.get("schema") == schema:
        context = recorded
    else:
        recorded = None
    trace = trace_view(work) or {}
    content = [r for r in trace.get("lines", []) if r.get("call") == directory.name and r.get("kind") in {"text", "reasoning"}]
    visible_at = max((r["at"] for r in content if r.get("at")), default=None)
    # A delta can contain only JSON punctuation, whitespace or hidden IDs.
    # Receipt is not evidence of a new readable statement or research progress.
    streaming = type(diagnostics.get("stream_deltas")) is int
    content_at = visible_at if streaming else max(
        [visible_at or "", diagnostics.get("last_content_at") or ""], default="") or None
    live = run.get("status") == "running"
    call_state = "completed" if response.exists() else "failed" if failure or activity.get("status") not in {None, "running"} else "running" if live else "stopped"
    phases = {"awaiting_plan_approval": "Wartet auf Freigabe des Rechercheplans; bis dahin wird kein Modellaufruf verbraucht.",
              "synthesis": "Geprüfte Antworten werden zum Dossier verbunden.", "audit": "Das zusammengesetzte Dossier wird abschließend geprüft."}
    no_progress = row.get("no_progress", 0)
    results = list((work / "calls").glob("call_*/response.json"))
    timeout = next((e.get("timeout_seconds") for e in diagnostics.get("events", []) if e.get("kind") == "request"), None)
    return {"basis": "request" if recorded else "saved_state", "question": context.get("question", ""),
            "assignment": ASSIGNMENTS.get(schema, phases.get(state.get("phase"), "Gespeicherte Rechercheergebnisse weiterverarbeiten.")),
            "criteria": context.get("criteria", []), "last_step": context.get("last_step", ""),
            "feedback": context.get("feedback", []), "material": context.get("material", {}),
            "candidate_count": context.get("candidate_count", 0), "queries": context.get("queries", []),
            "active_questions": active_questions(state),
            "no_progress_steps": no_progress, "steps": row.get("step", 0),
            "warning": f"Die letzten {no_progress} Arbeitsschritte brachten keine neuen Belege oder Suchtreffer und keine bestandene Antwortprüfung."
                       if no_progress >= 2 else "",
            "signals": {"call": directory.name, "state": call_state,
                        "timeout_seconds": timeout if type(timeout) is int and timeout > 0 else None,
                        "started_at": activity.get("started_at") or iso_mtime(calls[-1]),
                        "last_event_at": diagnostics.get("last_stream_event_at") or diagnostics.get("last_stdout_at") or activity.get("updated_at"),
                        "last_content_at": content_at,
                        "last_visible_at": visible_at,
                        "last_received_at": (diagnostics.get("last_delta_at") or diagnostics.get("last_content_at")) if streaming else None,
                        "stream_deltas": diagnostics.get("stream_deltas") if streaming else None,
                        "stream_chars": diagnostics.get("stream_chars") if streaming else None,
                        "stream_whitespace_chars": diagnostics.get("stream_whitespace_chars") if streaming else None,
                        "last_result_at": max((iso_mtime(p) for p in results), default=None),
                        "categories": {k: n for k, n in diagnostics.get("categories", {}).items() if k in {"connection", "retry", "timeout", "rate_limit", "server_error", "authentication", "quota"}},
                        "error_code": clean(failure.get("code"), 80) or None}}
