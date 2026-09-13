"""Read-only progress derived from saved results, without exposing prompts or credentials."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .runner import manifest_path
from .storage import file_hash, write_json


def read(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


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
    "ListenerReadback": "Verständlichkeit wird anhand des Skripts geprüft",
    "EditorialReview": "Erzählung und Gespräch werden geprüft",
    "TeachingReview": "Lernziele und Erklärungstiefe werden geprüft",
}


def script_progress(root, run):
    if not run or run.get("kind") != "script":
        return None
    work = manifest_path(root, run["run_id"]).parent
    stages = run.get("stages", {})
    stage = next((name for name, state in stages.items() if state.get("status") == "running"), None)
    stage = stage or next((name for name, state in stages.items() if state.get("error")), None)
    stage = stage or ("publish" if run.get("status") == "completed" else "planning")
    plan = read(work / "series_plan.json", {})
    requested = read(work / "script_request.json", {}).get("episode")
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
    issues = []
    if stage == "teaching" and current and run.get("status") == "blocked":
        checkpoint = read(work / "teaching" / current["episode_id"] / "checkpoint.json", {})
        issues = (checkpoint.get("review") or {}).get("issues", [])
    # Keep the existing progress envelope readable by Studio instances already running during an update.
    return {"phase": "script", "unit": "episodes", "stage": stage, "activity": activity,
            "activity_started_at": started, "current_episode": current["episode_id"] if current else None,
            "episode_number": rows.index(current) + 1 if current else None,
            "episode_title": current["title"] if current else None,
            "completed_segments": sum(row["completed"] for row in rows), "total_segments": len(rows),
            "model_calls": read(work / "budget.json", {}).get("model_calls", 0), "episodes": rows,
            "review_issues": issues}


def watch(root, job_id, stop=None):
    """Compatibility publisher for an existing server; exits with its one original job."""
    import time
    root = root.resolve()
    while stop is None or not stop.is_set():
        job = read(root / "studio/job.json", {})
        if job.get("id") != job_id or job.get("status") != "running":
            return
        run = job.get("run")
        progress = script_progress(root, run)
        if progress:
            write_json(manifest_path(root, run["run_id"]).parent / "progress.json", progress)
        if stop is None:
            time.sleep(2)
        elif stop.wait(2):
            return


if __name__ == "__main__":
    import sys
    watch(Path(sys.argv[1]), sys.argv[2])
