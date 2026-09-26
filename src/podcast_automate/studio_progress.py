"""Read-only progress derived from saved results, without exposing prompts or credentials."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .runner import manifest_path
from .script_checkpoints import finished, teaching_ready  # noqa: F401  (re-exported for callers)
from .run_budget import accepted_gaps, effective_limits, read_plan_approval, retry_requests
from .errors import AppError
from .models import ResearchLimits
from .research_ledger import reopenable
from .script_checks import MAX_PLAN_REPAIRS
from .storage import read_yaml, write_json
from .storage import read_optional_json as read
from .studio_messages import clean
from .studio_scripts import script_previews


def text_at(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


ACTIVITIES = {
    "SeriesPlan": "Inhaltsverzeichnis wird entworfen",
    "TeachingPlan": "Lehrkonzept wird ausgearbeitet",
    "TeachingPlanReview": "Lehrkonzept wird geprüft",
    "TeachingPlanRepair": "Offene Erklärungsschritte werden gezielt ergänzt",
    "ResearchDiscovery": "Zusätzliche Quellen werden gesucht",
    "FoundationSupplement": "Zusätzliche Belege werden ausgewertet",
    "FoundationReview": "Zusätzliche Belege werden geprüft",
    "DialoguePolishReview": "Dialogüberarbeitung wird geprüft",
    "ScriptReview": "Fakten und Erklärungen werden geprüft",
    "SeriesReview": "Zusammenhang und Vollständigkeit der gesamten Skriptserie werden geprüft",
    "ListenerReadback": "Verständlichkeit wird anhand des Skripts geprüft",
    "EditorialReview": "Erzählung und Gespräch werden geprüft",
    "TeachingReview": "Lernziele und Erklärungstiefe werden geprüft",
}


CALL_NAME = re.compile(r"call_\d+")


def open_calls(work, run, since=None):
    """Every call of the current worker still waiting for its answer, oldest first; several in parallel mode."""
    if run.get("status") != "running":
        return []
    rows = []
    for schema_file in sorted((work / "calls").glob("call_*/output_schema.json"))[-60:]:
        directory = schema_file.parent
        if (directory / "response.json").exists() or (directory / "failure.json").exists():
            continue
        activity = read(directory / "activity.json", {}) or {}
        if activity and activity.get("status") != "running":
            continue
        started = activity.get("started_at") or datetime.fromtimestamp(schema_file.stat().st_mtime, timezone.utc).isoformat()
        # A call an earlier, stopped worker left open is not running now.
        if since and started < since:
            continue
        rows.append({"call": directory.name, "schema": (read(schema_file, {}) or {}).get("title"), "started_at": started})
    return sorted(rows, key=lambda row: row["started_at"])


def call_labels(work, calls, titles):
    """What each call serves: its episode for a script stage, its sub-question for research."""
    labels = {}
    for name in calls:
        if not isinstance(name, str) or not CALL_NAME.fullmatch(name):
            continue
        directory = work / "calls" / name
        subject = (read(directory / "activity.json", {}) or {}).get("subject")
        question = None if subject else (read(directory / "work_context.json", {}) or {}).get("question")
        if subject or question:
            labels[name] = titles.get(subject, subject) if subject else question
    return labels


def script_progress(root, run, since=None):
    if run and run.get("kind") == "research":
        return research_progress(root, run, since)
    if not run or run.get("kind") != "script":
        return None
    work = manifest_path(root, run["run_id"]).parent
    stages = run.get("stages", {})
    stage = next((name for name, state in stages.items() if state.get("status") == "running"), None)
    stage = stage or next((name for name, state in stages.items() if state.get("error")), None)
    stage = stage or ("publish" if run.get("status") == "completed" else "planning")
    plan = read(work / "series_plan.json", {})
    request = read(work / "script_request.json", {})
    requested = request.get("episode")
    entries = [e for e in plan.get("episodes", []) if isinstance(e.get("episode_id"), str)
               and re.fullmatch(r"[a-z][a-z0-9_]*", e["episode_id"])
               and (not requested or e["episode_id"] == requested)]
    rows = []
    for entry in entries:
        identifier = entry["episode_id"]
        ready = finished(work, identifier, stage) if stage in {"teaching", "writing", "polishing", "review"} else stages.get(stage, {}).get("status") == "completed"
        folder = work / "teaching" / identifier
        rows.append({"episode_id": identifier, "title": entry["title"], "completed": ready,
                     "teaching_preview": text_at(folder / "plan.md") if teaching_ready(folder) else ""})
    # Each episode's state in this stage; several are running at once in parallel mode.
    stage_rows = {row["episode_id"]: read(work / "stage_activity" / stage / (row["episode_id"] + ".json"), {}) or {}
                  for row in rows} if stage in {"teaching", "writing", "polishing", "review"} else {}
    running = run.get("status") == "running"
    for row in rows:
        record = stage_rows.get(row["episode_id"], {})
        row["stage_status"] = record.get("status") if running else None
        row["stage_started_at"] = record.get("started_at") if running and record.get("status") == "running" else None
    current = next((row for row in rows if not row["completed"]), None)
    active_episodes = [row["episode_id"] for row in rows if row["stage_status"] == "running"]
    # A failed episode of a parallel stage stops the run only after the others finish their step.
    stopped = [row for row in rows if row["stage_status"] == "interrupted"
               and (not since or (stage_rows[row["episode_id"]].get("finished_at") or "") >= since)]
    calls = sorted((work / "calls").glob("call_*/output_schema.json"))
    schema = read(calls[-1], {}).get("title") if calls else None
    activity = ACTIVITIES.get(schema, "Gespeicherte Ergebnisse werden verarbeitet")
    if schema == "TeachingPlan" and current and (work / "teaching" / current["episode_id"] / "checkpoint.json").exists():
        activity = "Lehrkonzept wird überarbeitet"
    if schema == "EpisodeScript":
        activity = {"polishing": "Dialog wird sprachlich überarbeitet",
                    "review": "Skript wird nach den Prüfeinwänden überarbeitet"}.get(stage, "Skript wird ausgearbeitet")
    plan_repair = None
    if stage == "planning" and schema == "SeriesPlan":
        # A saved draft with findings means the running outline call is one of the automatic corrections.
        checkpoint = read(work / "planning_checkpoint.json", {}) or {}
        if checkpoint and read(work / "plan_errors.json", []):
            plan_repair = {"round": min(MAX_PLAN_REPAIRS, int(checkpoint.get("repairs", 0)) + 1), "limit": MAX_PLAN_REPAIRS}
            activity = f"Inhaltsverzeichnis wird korrigiert · Korrekturrunde {plan_repair['round']} von {MAX_PLAN_REPAIRS}"
    if stage == "publish":
        activity = "Ergebnisse werden bereitgestellt" if run.get("status") == "running" else "Ergebnisse bereit zur Durchsicht"
    started = datetime.fromtimestamp(calls[-1].stat().st_mtime, timezone.utc).isoformat() if calls else None
    responses = list((work / "calls").glob("call_*/response.json"))
    last_result = datetime.fromtimestamp(max(path.stat().st_mtime for path in responses), timezone.utc).isoformat() if responses else None
    pending = open_calls(work, run, since)
    issues = []
    # In a parallel stage the episode that failed is not necessarily the first unfinished one.
    failed = next((row for row in rows if stage_rows.get(row["episode_id"], {}).get("status") == "interrupted"), None)
    focus = failed or current
    if stage == "teaching" and focus and run.get("status") == "blocked":
        checkpoint = read(work / "teaching" / focus["episode_id"] / "checkpoint.json", {})
        issues = (checkpoint.get("review") or {}).get("issues", [])
    elif stage == "review" and focus and run.get("status") == "blocked":
        checkpoint = read(work / "reviews" / f"{focus['episode_id']}_checkpoint.json", {})
        review = checkpoint.get("review") or read(work / "reviews" / f"{focus['episode_id']}.json", {})
        issues = review.get("issues", [])
    model_call_limit = None
    try:
        snapshot = read_yaml(work / "project_snapshot.yaml")
        limits = ResearchLimits.model_validate(snapshot.get("research_limits", {}))
        model_call_limit = effective_limits(work, limits, run.get("input_hash")).model_calls
    except (AppError, ValueError, OSError):
        pass
    # Keep the existing progress envelope readable by Studio instances already running during an update.
    return {"phase": "script", "unit": "episodes", "stage": stage, "activity": activity,
            "updated_at": datetime.now(timezone.utc).isoformat(), "last_result_at": last_result,
            # The time the run itself last changed, unlike updated_at, which only says when this view was read.
            "changed_at": max(filter(None, (started, last_result)), default=None),
            "plan_repair": plan_repair,
            "model_call_started_at": pending[0]["started_at"] if pending else None, "open_calls": pending,
            "stopping": {"episodes": [row["title"] for row in stopped]} if stopped else None,
            "issues_episode": focus["title"] if issues and focus else None,
            "activity_started_at": started, "current_episode": current["episode_id"] if current else None,
            "episode_number": rows.index(current) + 1 if current else None,
            "episode_title": current["title"] if current else None,
            "completed_segments": sum(row["completed"] for row in rows), "total_segments": len(rows),
            "model_calls": read(work / "budget.json", {}).get("model_calls", 0),
            "model_call_limit": model_call_limit, "episodes": rows,
            "budget_projection": read(work / "budget_projection.json"),
            "execution": request.get("execution", {"text": "sequential", "audio": "sequential"}),
            "active_episodes": active_episodes,
            "script_previews": script_previews(root, run),
            "review_issues": issues}


def plan_review_state(work, input_hash, awaiting):
    """The plan gate as the run folder shows it: the projection, whether the run waits, whether it is approved."""
    projection = read(work / "question_research/plan_projection.json")
    approval, approved = None, False
    try:
        approval = read_plan_approval(work)
    except AppError:
        approval = None
    if approval is not None:
        approved = (approval.run_id == work.name and approval.input_hash == input_hash
                    and bool(projection) and approval.plan_hash == projection.get("plan_hash"))
    return {"awaiting": bool(awaiting), "approved": approved, "projection": projection,
            "approval": approval.model_dump(mode="json") if approval else None}


def research_progress(root, run, since=None):
    work = manifest_path(root, run["run_id"]).parent
    data = read(work / "research_activity.json", {})
    if not data:
        return None
    report = read(work / "research_quality_gate.json", data.get("research_quality"))
    questions = read(work / "research_questions.json")
    responses = list((work / "calls").glob("call_*/response.json"))
    pending = open_calls(work, run, since)
    # A failed task of a parallel run: the other tasks finish their current call before the run stops.
    marker = read(work / "question_research/stopping.json", {}) or {}
    stopping = ({"question": marker.get("question"), "code": marker.get("code")}
                if run.get("status") == "running" and marker and (not since or (marker.get("at") or "") >= since) else None)
    budget = read(work / "budget.json", {})
    snapshot = read_yaml(work / "project_snapshot.yaml") if (work / "project_snapshot.yaml").exists() else {}
    limits = ResearchLimits.model_validate(snapshot.get("research_limits", {}))
    effective = effective_limits(work, limits, run.get("input_hash"))
    if questions and isinstance(questions.get("questions"), list):
        # An approval written while no worker runs is shown at once; the ledger adopts it on resume.
        try:
            approved = accepted_gaps(work, run.get("input_hash"))
        except AppError:
            approved = {}
        if approved:
            for row in questions["questions"]:
                if row.get("id") in approved and row.get("status") == "blocked" and not row.get("accepted_gap"):
                    row.update(accepted_gap=True, accepted_reason=approved[row["id"]].get("reason", ""), outcome="accepted_gap")
            questions["blocked"] = sum(r.get("status") == "blocked" and not r.get("accepted_gap") for r in questions["questions"])
            questions["accepted"] = sum(bool(r.get("accepted_gap")) for r in questions["questions"])
            if questions.get("phase") == "blocked" and not questions["blocked"]:
                questions["phase"] = "questions"
        # A requested new attempt is shown at once as well; the row stays blocked until the resume adopts it.
        try:
            retries = retry_requests(work, run.get("input_hash"))
        except AppError:
            retries = {}
        if retries:
            for row in questions["questions"]:
                request = retries.get(row.get("id"))
                if (request and row.get("status") == "blocked" and not row.get("accepted_gap")
                        and row.get("retry_adopted") != request.get("requested_at")):
                    row.update(retry_requested=True, retry_hint=request.get("hint", ""))
            questions["retry_requested"] = sum(bool(r.get("retry_requested")) for r in questions["questions"])
            undecided = [r for r in questions["questions"]
                         if r.get("status") == "blocked" and not r.get("accepted_gap") and not r.get("retry_requested")]
            if questions.get("phase") == "blocked" and not undecided:
                questions["phase"] = "questions"
        if "reopenable" not in questions:
            # A ledger written before the field existed: decide it from the saved rows, as a resume would,
            # so the Studio offers the resume that gives these blocks their web search.
            state = (read(work / "question_research/state.json", {}) or {}).get("value") or {}
            tasks = state.get("tasks") or {}
            for row in questions["questions"]:
                task = tasks.get(row.get("id")) or {}
                row["reopenable"] = bool(task) and reopenable(task, state.get("limits"))
                row.setdefault("web_attempts", task.get("web_attempts", 0))
            questions["reopenable"] = sum(bool(r.get("reopenable")) for r in questions["questions"])
    counts = questions or report or {}
    from .research_status import work_insight
    awaiting = isinstance(questions, dict) and questions.get("phase") == "awaiting_plan_approval"
    request = read(work / "research_request.json", {})
    retrieval = retrieval_view(work, run, snapshot, limits)
    activity = data.get("activity")
    if retrieval and retrieval["running"]:
        activity = (f"Originaltexte werden eingelesen: {retrieval['attempted']} von bis zu {retrieval['total']} Quellen "
                    f"abgerufen, {retrieval['imported']} lesbar")
    if isinstance(questions, dict):
        for row in questions.get("questions") or []:
            if isinstance(row, dict):
                row.update({key: clean(row[key], root) for key in ("reason", "activity") if isinstance(row.get(key), str)})
    insight = work_insight(work, run)
    if isinstance(insight, dict) and isinstance(insight.get("feedback"), list):
        insight["feedback"] = [clean(text, root) for text in insight["feedback"]]
    return {**data, "phase": "research", "unit": "questions", "research_quality": report, "activity": activity,
            # research_activity.json keeps the time of its last real change; updated_at below is the read time.
            "changed_at": data.get("updated_at"), "retrieval": retrieval,
            "work_insight": insight,
            "research_questions": questions,
            "plan_review": plan_review_state(work, run.get("input_hash"), awaiting),
            # The mode the run was started with; older runs without the field ran one task at a time.
            "execution": request.get("execution") or {"text": "sequential", "audio": "sequential"},
            "total_segments": counts.get("total", 0), "completed_segments": counts.get("closed", 0),
            "model_calls": budget.get("model_calls", 0), "search_rounds": budget.get("search_rounds", 0),
            "model_call_limit": effective.model_calls, "search_round_limit": effective.search_rounds,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model_call_started_at": pending[0]["started_at"] if pending else None, "open_calls": pending,
            "stopping": stopping,
            "last_result_at": datetime.fromtimestamp(max(p.stat().st_mtime for p in responses), timezone.utc).isoformat() if responses else None}


def retrieval_view(work, run, snapshot, limits):
    """Per-source progress of the first retrieval and its failures, the report a reader can check."""
    saved = read(work / "retrieval_progress.json")
    if not isinstance(saved, dict):
        return None
    discovery = read(work / "discovery.json", {}) or {}
    offered = (len(discovery.get("candidates") or []) + len(snapshot.get("seed_urls") or [])
               + len(snapshot.get("local_sources") or []))
    stage = ((run.get("stages") or {}).get("retrieval") or {}).get("status")
    failures = [{"source": str(row.get("source", ""))[:200], "reason": clean(row.get("reason", ""))}
                for row in saved.get("failures") or [] if isinstance(row, dict)]
    return {"running": stage == "running", "attempted": int(saved.get("attempted", 0)),
            "imported": int(saved.get("imported", 0)), "total": min(limits.sources, offered) or int(saved.get("attempted", 0)),
            "failed": len(failures), "failures": failures[:12]}


def watch(root, job_id, stop=None):
    """Compatibility publisher for an existing server; exits with its one original job."""
    import time
    root = root.resolve()
    unreadable = 0
    while stop is None or not stop.is_set():
        job = read(root / "studio/job.json")
        if not isinstance(job, dict) or not job.get("id") or not job.get("status"):
            # A temporarily unreadable file is not evidence that the job ended.
            # Bound retries so a standalone publisher exits if the project disappears.
            unreadable += 1
            if unreadable >= 30:
                return
        else:
            unreadable = 0
            if job["id"] != job_id or job["status"] != "running":
                return
            run = job.get("run")
            progress = safe_script_progress(root, run)
            if progress:
                try:
                    write_json(manifest_path(root, run["run_id"]).parent / "progress.json", progress)
                    # Older running Studio servers carry research envelope fields
                    # through unchanged. Keep the live tail visible without restart.
                    if progress.get("phase") == "research":
                        activity_path = manifest_path(root, run["run_id"]).parent / "research_activity.json"
                        activity = read(activity_path, {})
                        if activity:
                            write_json(activity_path, {**activity, "model_trace": progress.get("model_trace"),
                                                      "work_insight": progress.get("work_insight")})
                except OSError:
                    logging.getLogger(__name__).warning("Progress file temporarily unavailable; retrying.")
        if stop is None:
            time.sleep(2)
        elif stop.wait(2):
            return


def safe_script_progress(root, run, since=None):
    """Progress is optional: concurrent file access must not break a job or its API."""
    try:
        progress = script_progress(root, run, since)
        if progress and run:
            from .status_summary import summary_view
            summary = summary_view(manifest_path(root, run["run_id"]).parent)
            if summary:
                progress["status_summary"] = summary
            from .model_trace import trace_view
            work = manifest_path(root, run["run_id"]).parent
            progress["model_trace"] = trace_view(work)
            # Live lines and open calls name their episode or sub-question when several run side by side.
            titles = {row["episode_id"]: row["title"] for row in progress.get("episodes") or [] if isinstance(row, dict)}
            calls = {line.get("call") for line in (progress["model_trace"] or {}).get("lines", []) if isinstance(line, dict)}
            calls |= {row["call"] for row in progress.get("open_calls") or []}
            progress["call_labels"] = call_labels(work, calls, titles)
            for row in progress.get("open_calls") or []:
                row["label"] = progress["call_labels"].get(row["call"])
        return progress
    except (OSError, ValueError, KeyError, TypeError):
        logging.getLogger(__name__).warning("Progress temporarily unavailable; retaining the previous snapshot.")
        return None


if __name__ == "__main__":
    import sys
    watch(Path(sys.argv[1]), sys.argv[2])
