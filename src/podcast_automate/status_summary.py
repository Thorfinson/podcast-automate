"""Optional, bounded progress digests. Never edits research, approvals or main budgets."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from .prompts import instructions
from .call_activity import clean_status
from .codex import CodexAdapter, subscription_environment
from .errors import AppError
from .models import Contract
from .openrouter import OpenRouterAdapter
from .runner import manifest_path
from .storage import digest, file_lock, load_project, write_json
from .storage import read_optional_json as read

INTERVAL_SECONDS = 180
MAX_CALLS = 100
SUMMARY_TIMEOUT = 90


class ProgressDigest(Contract):
    summary: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def active_job(root, job_id):
    job = read(root / "studio/job.json", {})
    return job if job.get("id") == job_id and job.get("status") == "running" else None


def result_note(result):
    """Use only bounded public results; never arbitrary keys or full scripts."""
    parts = []
    for key, label in (("findings", "Befunde im Entwurf"), ("episodes", "Folgen im Entwurf"),
                       ("segments", "Sprechabschnitte im Entwurf"), ("issues", "Prüfeinwände"),
                       ("open_questions", "offene Fragen"), ("research_gaps", "Recherchelücken")):
        rows = result.get(key)
        if isinstance(rows, list):
            parts.append(f"{len(rows)} {label}")
            if key in {"issues", "open_questions", "research_gaps"}:
                for row in rows[:4]:
                    text = row.get("reason") or row.get("question") or row.get("message") if isinstance(row, dict) else row
                    if isinstance(text, str):
                        parts.append(clean_status(text, 250))
    if isinstance(result.get("scope_note"), str):
        parts.append(clean_status(result["scope_note"], 400))
    if isinstance(result.get("tasks"), list):
        parts.append(f"{len(result['tasks'])} feste Rechercheaufgaben geplant; Antworten noch nicht geprüft")
    if isinstance(result.get("action"), str) and isinstance(result.get("reason"), str):
        parts.append("Angeforderter nächster Leseschritt: " + clean_status(result["reason"], 400))
    return "; ".join(parts)[:2000] or "Strukturierte Modellantwort gespeichert; noch kein Nachweis einer abgeschlossenen Qualitätsprüfung."


def evidence_snapshot(root, run):
    work = manifest_path(root, run["run_id"]).parent
    facts = []

    def add(identifier, text):
        facts.append({"id": identifier, "text": clean_status(text, 2000)})

    activity = read(work / "research_activity.json", {})
    stage = next((key for key, value in run.get("stages", {}).items() if value.get("status") == "running"), "")
    add("stage", f"Arbeitsschritt: {stage}. " + str(activity.get("activity", "")))
    for name, value in run.get("stages", {}).items():
        if value.get("status") == "completed":
            add("stage_" + name, f"Arbeitsschritt {name} abgeschlossen.")
    index = read(work / "source_index.json", {})
    questions = read(work / "research_questions.json", {})
    if questions.get("source_count") is not None:
        add("sources", f"{questions['source_count']} Quellen eingelesen; {questions.get('source_failures', 0)} Abrufprobleme.")
    elif index:
        add("sources", f"{len(index.get('sources', []))} Quellen eingelesen; {len(index.get('failures', []))} Abrufprobleme.")
    if questions:
        add("question_progress", f"{questions.get('closed', 0)} von {questions.get('total', 0)} Teilfragen unabhängig geprüft abgeschlossen. "
            f"Phase: {questions.get('phase')}. Die Gesamtprüfung bleibt zusätzlich erforderlich.")
        for n, row in enumerate(questions.get("questions", [])):
            if row.get("id") == questions.get("active_task") or row.get("status") == "blocked":
                add(f"question_{n}", f"{row.get('question')}: {row.get('status')}. {row.get('activity')}. "
                    f"{row.get('read_sections', 0)} Abschnitte gelesen. {row.get('reason', '')}")
            if len(facts) >= 14:
                break
    quality = read(work / "research_quality_gate.json", {})
    if quality:
        if questions and questions.get("phase") != "completed":
            add("quality", "Die Gesamtprüfung ist noch nicht freigegeben; laufende Einzelabschlüsse stehen separat im Fragenstand.")
        elif quality.get("assessment_status") == "pending_after_source_review":
            add("quality", "Quellenprüfung meldet fehlende Belege; gezielte Nachrecherche hat Vorrang. "
                "Die bisherige Gesamtbewertung wurde noch nicht erneuert.")
            for n, issue in enumerate(quality.get("source_review", {}).get("issues", [])[:4]):
                add(f"source_gap_{n}", issue.get("reason", ""))
        else:
            add("quality", f"{quality.get('closed', 0)} von {quality.get('total', 0)} Leitfragen bestehen die Qualitätsprüfung.")
        current_requirements = [] if questions and questions.get("phase") != "completed" else quality.get("requirements", [])
        for n, row in enumerate(current_requirements):
            if not row.get("passed"):
                add(f"gap_{n}", str(row.get("question", "")) + ": " + str(row.get("reason", "")))
            if len(facts) >= 18:
                break
    schemas = sorted((work / "calls").glob("call_*/output_schema.json"))
    live = False
    latest_at = None
    if run.get("kind") == "research":
        from .research_status import work_insight
        insight = work_insight(work, run)
        if insight:
            add("current_assignment", "Gespeicherter Auftrag, keine Live-Beobachtung: " + insight["question"] + " " + insight["assignment"])
            if insight["last_step"]:
                add("last_reader_result", "Im letzten gespeicherten Leseschritt: " + insight["last_step"])
            for n, issue in enumerate(insight["feedback"]):
                add(f"current_feedback_{n}", "Zuletzt bemängelt: " + issue)
            details = insight["material"]
            add("current_material", f"Für diesen Schritt bereitgestellt: {details.get('section_count', 0)} Textstellen aus "
                f"{details.get('source_count', 0)} Quellen, dazu {insight['candidate_count']} Suchtreffer. "
                "Suchtreffer sind noch keine gelesenen Belege. " + "; ".join(s["title"] for s in details.get("sources", [])))
            if insight["warning"]:
                add("repeated_no_progress", insight["warning"])
    for schema_file in schemas[-6:]:
        directory = schema_file.parent
        schema = read(schema_file, {}).get("title", "Modellantwort")
        response = read(directory / "response.json")
        failure = read(directory / "failure.json")
        log = read(directory / "activity.json", {})
        state = "Antwort gespeichert" if response is not None else "fehlgeschlagen" if failure else "noch ohne gespeicherte Antwort"
        add(directory.name, f"{directory.name}: {schema} · {state}.")
        if log:
            if schema_file == schemas[-1] and response is None and not failure and run.get("status") == "running":
                live = any(event.get("message") not in {"Aufruf gestartet", "Modell bearbeitet den Auftrag"}
                           for event in log.get("events", []))
                from .model_trace import trace_view
                live = live or any(r.get("call") == directory.name and r.get("kind") in {"text", "reasoning"}
                                   for r in (trace_view(work) or {}).get("lines", []))
            for n, event in enumerate(log.get("events", [])[-5:]):
                add(f"{directory.name}_event_{n}", event.get("message", ""))
            latest_at = max(latest_at or "", log.get("updated_at", ""))
        if response is not None and schema_file in schemas[-2:]:
            add(directory.name + "_result", result_note(response))
            latest_at = max(latest_at or "", timestamp((directory / "response.json").stat().st_mtime))
        if failure:
            add(directory.name + "_error", failure.get("code", "Aufruf fehlgeschlagen"))
    add("live_events_available", "Inhaltliche Zwischenmeldungen des laufenden Aufrufs liegen vor." if live else
        "Keine inhaltlichen Zwischenmeldungen des laufenden Aufrufs verfügbar. Startmeldungen und der gespeicherte Arbeitsauftrag belegen keinen weiteren Fortschritt.")
    # Summaries are based on these records, not a claim to observe hidden reasoning.
    return {"run_id": run["run_id"], "stage": stage, "live_events_available": live,
            "last_record_at": latest_at, "facts": facts}


def summary_view(work, job_id=None):
    state = read(work / "status_reports/state.json", {})
    if not state or (job_id is not None and state.get("job_id") != job_id):
        return None
    return {key: state.get(key) for key in ("job_id", "status", "summary", "generated_at", "checked_at",
            "provider", "model", "calls", "call_limit", "live_events_available", "last_record_at", "history")}


def publish(work, state, research=False):
    write_json(work / "status_reports/state.json", state)
    if research:
        # Existing Studio/worker versions already carry arbitrary research progress
        # fields through this envelope. No server restart or key transfer required.
        path = work / "research_activity.json"
        activity = read(path, {})
        if activity:
            questions = read(work / "research_questions.json")
            if questions:
                activity["research_questions"] = questions
            write_json(path, {**activity, "status_summary": summary_view(work)})


def update_summary(root, job, *, api_key=None, clock=time.time):
    run = job.get("run") or {}
    if run.get("kind") not in {"research", "script"}:
        return
    work = manifest_path(root, run["run_id"]).parent
    state = read(work / "status_reports/state.json", {})
    seconds = clock()
    if seconds - state.get("last_attempt", 0) < INTERVAL_SECONDS:
        return
    snapshot = evidence_snapshot(root, run)
    signature = digest(snapshot)
    state.update(job_id=job["id"], checked_at=timestamp(seconds), last_attempt=seconds,
                 live_events_available=snapshot["live_events_available"], last_record_at=snapshot["last_record_at"],
                 call_limit=MAX_CALLS)
    research = run["kind"] == "research"
    if state.get("snapshot_hash") == signature:
        state["status"] = "unchanged"
        publish(work, state, research)
        return
    if state.get("calls", 0) >= MAX_CALLS or state.get("errors", 0) >= 3:
        state["status"] = "paused"
        publish(work, state, research)
        return
    request = read(work / ("research_request.json" if research else "script_request.json"), {})
    choice = request.get("text_generation") or read(root / "studio/text.json", {})
    provider = choice.get("provider", "codex_cli")
    model = "deepseek/deepseek-v4.1-flash" if provider == "openrouter" else "gpt-5.6-luna"
    state.update(provider=provider, model=model, status="summarizing", calls=state.get("calls", 0) + 1)
    publish(work, state, research)
    directory = work / "status_reports" / f"summary_{state['calls']:03d}"
    settings = load_project(root).runtime.model_copy(update={"codex_model": model, "text_timeout_seconds": SUMMARY_TIMEOUT})
    prompt = (
        instructions("studio_status") + "\n" + json.dumps(snapshot, ensure_ascii=False))
    try:
        if provider == "openrouter":
            adapter = OpenRouterAdapter(settings, model=model, api_key=api_key,
                                        max_output_tokens=1500, reasoning_effort="low")
        else:
            adapter = CodexAdapter(settings, reasoning_effort="low", cancel_check=lambda: not active_job(root, job["id"]))
        result, _ = adapter.structured(prompt, ProgressDigest, directory, prompt_version="studio_status.v1", search=False)
        known = {fact["id"] for fact in snapshot["facts"]}
        if not set(result.evidence_ids) <= known:
            raise AppError("Statusbericht nennt unbekannte Belege.", code="invalid_status_summary")
        if not active_job(root, job["id"]):
            return
        generated = timestamp(clock())
        text = clean_status(result.summary, 1000)
        if api_key:
            text = text.replace(api_key, "[Zugangsdaten entfernt]")
        history = state.get("history", [])
        state.update(status="ready", summary=text, generated_at=generated, snapshot_hash=signature, errors=0,
                     history=(history + [{"text": text, "at": generated, "model": model}])[-5:])
    except Exception as exc:
        # A missing key, rate limit or unavailable status model never stops production.
        state.update(status="unavailable", errors=state.get("errors", 0) + 1,
                     error_code=exc.code if isinstance(exc, AppError) else "summary_failed")
    if active_job(root, job["id"]):
        publish(work, state, research)


def watch_summaries(root, job_id, api_key=None):
    root = root.resolve()
    try:
        with file_lock(root / "studio" / (".status-" + job_id + ".lock")):
            while (job := active_job(root, job_id)):
                try:
                    update_summary(root, job, api_key=api_key)
                except (OSError, ValueError, KeyError, TypeError, AppError):
                    pass
                time.sleep(5)
    except AppError:
        return  # Another monitor owns this job; never duplicate its calls.


def start_monitor(root, job_id, api_key=None):
    process = subprocess.Popen([sys.executable, "-m", "podcast_automate.status_summary", str(root), job_id],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", env=subscription_environment(), start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        process.stdin.write(json.dumps({"api_key": api_key}))
        process.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    return process


if __name__ == "__main__":
    credentials = json.loads(sys.stdin.read() or "{}")
    watch_summaries(Path(sys.argv[1]), sys.argv[2], credentials.get("api_key"))
