"""Read-only progress derived from saved results, without exposing prompts or credentials."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .runner import manifest_path
from .run_budget import effective_limits
from .errors import AppError
from .models import ResearchLimits
from .storage import file_hash, read_yaml, write_json
from .storage import read_optional_json as read
from .studio_scripts import script_previews


def text_at(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def teaching_ready(folder):
    checkpoint = read(folder / "checkpoint.json", {})
    review = read(folder / "review.json")
    plan = read(folder / "plan.json")
    return bool(plan and review is not None and not review.get("issues") and not review.get("research_gaps")
                and checkpoint.get("design") == plan and checkpoint.get("review") == review)


def finished(work, episode, stage):
    if stage == "teaching":
        return teaching_ready(work / "teaching" / episode)
    if stage == "writing":
        path = work / "drafts" / f"{episode}.json"
        stamp = read(path.with_suffix(".checkpoint.json"), {})
        return bool(path.is_file() and stamp.get("sha256") == file_hash(path))
    if stage == "polishing":
        folder = work / "polishing" / episode
        result = read(folder / "result.json", {})
        checkpoint = read(folder / "checkpoint.json", {})
        return bool(result.get("status") == "passed" and read(folder / "script.json")
                    and read(folder / "script.json") == checkpoint.get("candidate"))
    if stage == "review":
        report = read(work / "reviews" / f"{episode}.json")
        checked = read(work / "reviewed" / f"{episode}.json")
        checkpoint = read(work / "reviews" / f"{episode}_checkpoint.json", {})
        return bool(checked and report is not None and not report.get("issues") and checkpoint.get("draft") == checked)
    return False


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


def script_progress(root, run):
    if run and run.get("kind") == "research":
        return research_progress(root, run)
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
    current = next((row for row in rows if not row["completed"]), None)
    active_episodes = [row["episode_id"] for row in rows if run.get("status") == "running" and
                       read(work / "stage_activity" / stage / (row["episode_id"] + ".json"), {}).get("status") == "running"]
    calls = sorted((work / "calls").glob("call_*/output_schema.json"))
    schema = read(calls[-1], {}).get("title") if calls else None
    activity = ACTIVITIES.get(schema, "Gespeicherte Ergebnisse werden verarbeitet")
    if schema == "TeachingPlan" and current and (work / "teaching" / current["episode_id"] / "checkpoint.json").exists():
        activity = "Lehrkonzept wird überarbeitet"
    if schema == "EpisodeScript":
        activity = "Dialog wird sprachlich überarbeitet" if stage == "polishing" else "Skript wird ausgearbeitet"
    if stage == "publish":
        activity = "Ergebnisse werden bereitgestellt" if run.get("status") == "running" else "Ergebnisse bereit zur Durchsicht"
    started = datetime.fromtimestamp(calls[-1].stat().st_mtime, timezone.utc).isoformat() if calls else None
    responses = list((work / "calls").glob("call_*/response.json"))
    last_result = datetime.fromtimestamp(max(path.stat().st_mtime for path in responses), timezone.utc).isoformat() if responses else None
    call_pending = bool(calls and not calls[-1].with_name("response.json").exists() and run.get("status") == "running")
    issues = []
    if stage == "teaching" and current and run.get("status") == "blocked":
        checkpoint = read(work / "teaching" / current["episode_id"] / "checkpoint.json", {})
        issues = (checkpoint.get("review") or {}).get("issues", [])
    elif stage == "review" and current and run.get("status") == "blocked":
        checkpoint = read(work / "reviews" / f"{current['episode_id']}_checkpoint.json", {})
        review = checkpoint.get("review") or read(work / "reviews" / f"{current['episode_id']}.json", {})
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
            "model_call_started_at": started if call_pending else None,
            "activity_started_at": started, "current_episode": current["episode_id"] if current else None,
            "episode_number": rows.index(current) + 1 if current else None,
            "episode_title": current["title"] if current else None,
            "completed_segments": sum(row["completed"] for row in rows), "total_segments": len(rows),
            "model_calls": read(work / "budget.json", {}).get("model_calls", 0),
            "model_call_limit": model_call_limit, "episodes": rows,
            "execution": request.get("execution", {"text": "sequential", "audio": "sequential"}),
            "active_episodes": active_episodes,
            "script_previews": script_previews(root, run),
            "review_issues": issues}


def research_progress(root, run):
    work = manifest_path(root, run["run_id"]).parent
    data = read(work / "research_activity.json", {})
    if not data:
        return None
    report = read(work / "research_quality_gate.json", data.get("research_quality"))
    questions = read(work / "research_questions.json")
    calls = sorted((work / "calls").glob("call_*/output_schema.json"))
    responses = list((work / "calls").glob("call_*/response.json"))
    pending = bool(calls and not calls[-1].with_name("response.json").exists() and run.get("status") == "running")
    budget = read(work / "budget.json", {})
    snapshot = read_yaml(work / "project_snapshot.yaml") if (work / "project_snapshot.yaml").exists() else {}
    limits = ResearchLimits.model_validate(snapshot.get("research_limits", {}))
    model_call_limit = effective_limits(work, limits, run.get("input_hash")).model_calls
    counts = questions or report or {}
    from .research_status import work_insight
    return {**data, "phase": "research", "unit": "questions", "research_quality": report,
            "work_insight": work_insight(work, run),
            "research_questions": questions,
            "total_segments": counts.get("total", 0), "completed_segments": counts.get("closed", 0),
            "model_calls": budget.get("model_calls", 0), "search_rounds": budget.get("search_rounds", 0),
            "model_call_limit": model_call_limit,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model_call_started_at": datetime.fromtimestamp(calls[-1].stat().st_mtime, timezone.utc).isoformat() if pending else None,
            "last_result_at": datetime.fromtimestamp(max(p.stat().st_mtime for p in responses), timezone.utc).isoformat() if responses else None}


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


def safe_script_progress(root, run):
    """Progress is optional: concurrent file access must not break a job or its API."""
    try:
        progress = script_progress(root, run)
        if progress and run:
            from .status_summary import summary_view
            summary = summary_view(manifest_path(root, run["run_id"]).parent)
            if summary:
                progress["status_summary"] = summary
            from .model_trace import trace_view
            progress["model_trace"] = trace_view(manifest_path(root, run["run_id"]).parent)
        return progress
    except (OSError, ValueError, KeyError, TypeError):
        logging.getLogger(__name__).warning("Progress temporarily unavailable; retaining the previous snapshot.")
        return None


if __name__ == "__main__":
    import sys
    watch(Path(sys.argv[1]), sys.argv[2])
