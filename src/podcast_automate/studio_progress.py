"""Read-only progress derived from saved results, without exposing prompts or credentials."""
from __future__ import annotations

import copy
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .runner import manifest_path
from .script_checkpoints import finished, teaching_ready  # noqa: F401  (re-exported for callers)
from .run_budget import (accepted_gaps, criterion_gaps, dispute_decisions, disputed_checks, effective_limits,
                         read_plan_approval, residual_finish, retry_requests)
from .errors import AppError
from .models import ResearchLimits
from .research_ledger import reopenable
from .script_checks import MAX_PLAN_REPAIRS
from .storage import digest, load_project, read_yaml, write_json
from .storage import read_optional_json as read
from .studio_messages import clean
from .studio_scripts import script_previews
from . import studio_text
from .studio_text import t

# The publisher rewrites progress.json only when its content changed, and at least this often while the worker
# lives: the file's age is the Studio's heartbeat (studio.Studio.job, app.js heartbeatNote warns after 300 s).
# 2026-10-02: it rewrote 2-2.6 MB with fsync every 2 s, and research_activity.json beside it.
HEARTBEAT_SECONDS = 60


def text_at(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# Values derived from large run files, kept while the files they read are unchanged. 2026-10-02: one overview poll
# parsed the 16 MB question state, hashed a 12-16 MB inputs.json and every script, about 65 MB and 0.6 s, with the
# Studio's mutex held. A key names what was derived and from where; a file counts as unchanged while its path, file
# id, modification time (ns) and size are. The file id changes with every atomic replace (storage.write_json), also
# within one tick of the clock. Callers treat a cached value as read-only.
_MEMO = {}
_MEMO_LIMIT = 256
_MEMO_LOCK = threading.Lock()


def stamp(path):
    """A file's identity for the caches; None while it is missing."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_ino, info.st_mtime_ns, info.st_size


def memo(key, paths, compute):
    """``compute()``, again only after one of ``paths`` changed. The stamps are taken first, so a file replaced
    while it is read leaves a stale stamp and the next call computes anew."""
    signature = tuple(stamp(path) for path in paths)
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
    if hit is not None and hit[0] == signature:
        return hit[1]
    value = compute()
    with _MEMO_LOCK:
        _MEMO.pop(key, None)
        while len(_MEMO) >= _MEMO_LIMIT:
            _MEMO.pop(next(iter(_MEMO)))
        _MEMO[key] = (signature, value)
    return value


def state_facts(work):
    """What the progress view needs of a research run's question state, without keeping the 16 MB state itself:
    its prompt generation, each task's reopenability and web attempts, and the recorded objections."""
    path = work / "question_research/state.json"

    def compute():
        state = (read(path, {}) or {}).get("value") or {}
        tasks = state.get("tasks") or {}
        return {"prompt_generation": int(state.get("prompt_generation", 1) or 1),
                "tasks": {task_id: {"reopenable": bool(task) and reopenable(task, state.get("limits")),
                                    "web_attempts": task.get("web_attempts", 0)}
                          for task_id, task in tasks.items() if isinstance(task, dict)},
                "objections": state.get("objections") or {}}
    return memo(("state_facts", str(path)), [path], compute)


# The activity line of a script run's newest call, by its output schema; the text is progress.activity.<schema> in the
# interface language (D-152).
ACTIVITIES = ("SeriesPlan", "TeachingPlan", "TeachingPlanReview", "TeachingPlanRepair", "ResearchDiscovery",
              "FoundationSupplement", "FoundationReview", "DialoguePolishReview", "ScriptReview", "SeriesReview",
              "ListenerReadback", "EditorialReview", "TeachingReview")
_PIPELINE = {"catalog": None, "patterns": []}


def pipeline_patterns():
    """The research pipeline's German activity lines (pipeline.activity.* in de.json) as patterns: the longest fixed
    tail first, then the longest template, so "… · Anlauf 2 nach Abweisung" is not read as the tail of a question."""
    german = studio_text.catalog("de")
    if _PIPELINE["catalog"] is not german:
        rows = []
        for key, value in german.items():
            if not key.startswith("pipeline.activity."):
                continue
            for template in (value.values() if isinstance(value, dict) else [value]):
                parts = re.split(r"\{(\w+)\}", template)
                pattern = "".join(re.escape(part) if index % 2 == 0 else f"(?P<{part}>.+?)"
                                  for index, part in enumerate(parts))
                rows.append(((len(parts[-1]), len(template)), key, re.compile(pattern + r"\Z", re.DOTALL)))
        rows.sort(key=lambda row: row[0], reverse=True)
        _PIPELINE.update(catalog=german, patterns=[row[1:] for row in rows])
    return _PIPELINE["patterns"]


def activity_text(text, depth=0):
    """A research pipeline's activity line in the interface language. The pipeline writes it in German, and the
    status brief reads it so; the English interface shows the catalog's English where a German template matches and
    the German line otherwise (D-152)."""
    if not isinstance(text, str) or studio_text.current_language() == "de" or depth > 2:
        return text
    for key, pattern in pipeline_patterns():
        found = pattern.match(text)
        if found:
            params = found.groupdict()
            if "activity" in params:
                params["activity"] = activity_text(params["activity"], depth + 1)
            return str(t(key, **params))
    return text


CALL_NAME = re.compile(r"call_\d+")


def money_fields(work, budget, limits):
    """What a run billed to a key has spent and may spend (D-146); nothing for a run no key pays for, so its view
    stays as it was."""
    from .cost_estimate import spent_usd
    from .run_budget import run_text_generation
    from .text_settings import BILLED_TEXT_PROVIDERS
    spent = any(key in budget for key in ("billed_usd", "estimated_usd", "external_usd"))
    try:
        billed = (run_text_generation(work) or {}).get("provider") in BILLED_TEXT_PROVIDERS
    except (AppError, ValueError, OSError):
        billed = False
    if not (spent or billed):
        return {}
    return {"cost_spent_usd": spent_usd(budget), "cost_estimated_usd": round(budget.get("estimated_usd", 0.0), 4),
            "cost_unpriced_attempts": budget.get("unpriced_attempts", 0),
            "cost_limit_usd": getattr(limits, "cost_usd", None) if limits is not None else None}


def run_limits(root, work, input_hash, snapshot=None):
    """The limits a run works under: the project's current ones with the run's approved raises, as the worker and
    run_budget compute them. Limits are no longer part of a run's hash (storage.bound_brief), so the run's snapshot
    showed outdated ones (2026-10-02); it serves only while project.yaml cannot be read."""
    try:
        limits = load_project(root).research_limits
    except (AppError, ValueError, OSError):
        if snapshot is None:
            snapshot = read_yaml(work / "project_snapshot.yaml")
        limits = ResearchLimits.model_validate((snapshot or {}).get("research_limits", {}))
    return effective_limits(work, limits, input_hash)


def disputed_objections(work, run, questions):
    """Every objection the current audit round disputed, the one the run stopped on first, each with the
    editor's decision once there is one; empty unless the round's stop file exists."""
    if not isinstance(questions, dict):
        return []
    audit_round = int(questions.get("audit_round") or 0)
    path = work / "question_research/synthesis" / f"audit_{audit_round:02d}" / "review_disagreement.json"
    stopped = (read(path, {}) or {}).get("value")
    if not isinstance(stopped, dict) or not stopped.get("objection_id"):
        return []
    checks = {stopped["objection_id"]: stopped}
    for check in disputed_checks(work, audit_round):
        checks.setdefault(check["objection_id"], check)
    known = {oid: check["objection"] for oid, check in checks.items() if check.get("objection")}
    if len(known) < len(checks):
        # Only the stop file carries its objection; the ledger has the others.
        known.update({oid: row for oid, row in state_facts(work)["objections"].items() if oid in checks and oid not in known})
    try:
        decisions = dispute_decisions(work, run.get("input_hash"))
    except AppError:
        decisions = {}
    rows = []
    for oid, check in checks.items():
        objection = known.get(oid) or {}
        question = next((row.get("question") for row in questions.get("questions") or []
                         if row.get("id") == objection.get("task_id")), objection.get("task_id"))
        rows.append({"objection_id": oid, "task_id": objection.get("task_id"), "question": question,
                     "objection": {key: objection.get(key, "") for key in ("reason", "correction", "closure_condition")},
                     "review": {"reason": check.get("reason", ""), "references": check.get("references", [])},
                     "decision": decisions.get(oid)})
    return rows


def disputed_objection(work, run, questions):
    """The objection the current audit round stopped on (the first of ``disputed_objections``), or None."""
    rows = disputed_objections(work, run, questions)
    return rows[0] if rows else None


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


def script_progress(root, run, since=None, light=False):
    """``light`` is the project card's view: no previews, assignment or live output, only what a card decides on."""
    if run and run.get("kind") == "research":
        return research_progress(root, run, since, light=light)
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
    activity = t(f"progress.activity.{schema}" if schema in ACTIVITIES else "progress.activity.default")
    if schema == "TeachingPlan" and current and (work / "teaching" / current["episode_id"] / "checkpoint.json").exists():
        activity = t("progress.activity.teaching_revision")
    if schema == "EpisodeScript":
        activity = t({"polishing": "progress.activity.script_polishing",
                      "review": "progress.activity.script_review"}.get(stage, "progress.activity.script_writing"))
    plan_repair = None
    if stage == "planning" and schema == "SeriesPlan":
        # A saved draft with findings means the running outline call is one of the automatic corrections.
        checkpoint = read(work / "planning_checkpoint.json", {}) or {}
        if checkpoint and read(work / "plan_errors.json", []):
            plan_repair = {"round": min(MAX_PLAN_REPAIRS, int(checkpoint.get("repairs", 0)) + 1), "limit": MAX_PLAN_REPAIRS}
            activity = t("progress.activity.plan_repair", round=plan_repair["round"], limit=MAX_PLAN_REPAIRS)
    if stage == "publish":
        activity = t("progress.activity.publishing" if run.get("status") == "running" else "progress.activity.published")
    if stage == "review" and rows and all(row["completed"] for row in rows) and run.get("status") == "running":
        # Every episode passed its own review: what runs now is the series review or its one bounded correction.
        repaired = ((read(work / "series_repair.json", {}) or {}).get("receipt") or {}).get("episodes") or []
        numbers = ", ".join(str(int(e.rsplit("_", 1)[-1])) for e in sorted(repaired) if e.rsplit("_", 1)[-1].isdigit())
        if schema == "SeriesReview":
            activity = t("progress.activity.series_review_again" if repaired else "progress.activity.series_review")
        elif repaired:
            activity = t("progress.activity.series_repair", episodes=numbers)
    tagging = read(root / "studio" / "expression" / "progress.json", {}) or {}
    if tagging.get("status") == "running" and tagging.get("run_id") == run.get("run_id"):
        # After a finished script run, the tags a Gemini recording speaks are placed for reading (tag_episodes).
        activity = t("progress.activity.expression", done=tagging.get("done", 0), total=tagging.get("total", 0))
    jev = read(work / "jev_probe.json", {}) or {}
    if jev.get("status") == "running" and run.get("status") == "running":
        activity = t("progress.activity.jev", done=jev.get("done", 0), total=jev.get("total", 0))
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
    model_call_limit = limits = None
    try:
        limits = run_limits(root, work, run.get("input_hash"))
        model_call_limit = limits.model_calls
    except (AppError, ValueError, OSError):
        pass
    money = money_fields(work, read(work / "budget.json", {}) or {}, limits)
    # Keep the existing progress envelope readable by Studio instances already running during an update.
    return {**money, "phase": "script", "unit": "episodes", "stage": stage, "activity": str(activity),
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
            "script_previews": [] if light else script_previews(root, run),
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


def ledger_view(root, work, input_hash):
    """The question ledger as the Studio shows it, with the approvals written since the worker last saved it. Kept
    while the ledger, the approval files and the state are unchanged; read-only for its callers."""
    files = ["research_questions.json", "gap_approvals.json", "retry_requests.json", "criterion_gaps.json",
             "residual_finish.json", "question_research/state.json"]
    language = studio_text.current_language()
    return memo(("ledger", str(work), input_hash, str(root), language), [work / name for name in files],
                lambda: _ledger_view(root, work, input_hash, language))


def _ledger_view(root, work, input_hash, language="de"):
    questions = read(work / "research_questions.json")
    if not isinstance(questions, dict):
        return questions
    if isinstance(questions.get("questions"), list):
        # An approval written while no worker runs is shown at once; the ledger adopts it on resume.
        try:
            approved = accepted_gaps(work, input_hash)
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
            retries = retry_requests(work, input_hash)
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
        # So is an accepted access gap; the resume reopens the question with the criterion narrowed.
        try:
            access = criterion_gaps(work, input_hash)
        except AppError:
            access = []
        if access:
            for row in questions["questions"]:
                known = row.get("access_gaps") or []
                requested = [gap for gap in access if gap["task_id"] == row.get("id") and gap not in known]
                if requested and row.get("status") == "blocked" and not row.get("accepted_gap"):
                    row.update(access_gap_requested=True, requested_access_gaps=requested)
            undecided = [r for r in questions["questions"] if r.get("status") == "blocked" and not r.get("accepted_gap")
                         and not r.get("retry_requested") and not r.get("access_gap_requested")]
            if questions.get("phase") == "blocked" and not undecided:
                questions["phase"] = "questions"
        if "keeps_spent_answers" not in questions and questions.get("phase") == "blocked":
            # A ledger saved before the field existed (the runs stopped on 2026-10-01): decided from the run's state,
            # once, since the next save of a resumed run writes it.
            questions["keeps_spent_answers"] = state_facts(work)["prompt_generation"] >= 3
        if "reopenable" not in questions:
            # A ledger written before the field existed: decide it from the saved rows, as a resume would,
            # so the Studio offers the resume that gives these blocks their web search.
            tasks = state_facts(work)["tasks"]
            for row in questions["questions"]:
                task = tasks.get(row.get("id")) or {}
                row["reopenable"] = bool(task.get("reopenable"))
                row.setdefault("web_attempts", task.get("web_attempts", 0))
            questions["reopenable"] = sum(bool(r.get("reopenable")) for r in questions["questions"])
    try:
        questions["residual_finish"] = residual_finish(work, input_hash)
    except AppError:
        questions["residual_finish"] = None
    for row in questions.get("questions") or []:
        if isinstance(row, dict):
            if isinstance(row.get("activity"), str):
                row["activity"] = activity_text(row["activity"])
            row.update({key: clean(row[key], root, language) for key in ("reason", "activity")
                        if isinstance(row.get(key), str)})
    return questions


def insight_view(work, run, responses):
    """The current assignment (research_status.work_insight), kept while the files it reads are unchanged: the
    newest call's records, the trace, the question state and the answers so far."""
    from .research_status import work_insight
    calls = sorted((work / "calls").glob("call_*/output_schema.json"))
    if not calls:
        return None
    last = calls[-1].parent
    files = [calls[-1], *(last / name for name in ("activity.json", "diagnostics.json", "response.json", "failure.json",
                                                    "work_context.json")),
             work / "question_research/state.json", work / "model_trace.json"]
    newest = max((stamp(path) or (0, 0, 0))[1] for path in responses) if responses else 0
    key = ("insight", str(work), run.get("status"), len(calls), len(responses), newest)
    # A copy: the caller cleans the feedback lines in place.
    return copy.deepcopy(memo(key, files, lambda: work_insight(work, run)))


def research_progress(root, run, since=None, light=False):
    work = manifest_path(root, run["run_id"]).parent
    data = read(work / "research_activity.json", {})
    if not data:
        return None
    report = read(work / "research_quality_gate.json", data.get("research_quality"))
    questions = ledger_view(root, work, run.get("input_hash"))
    responses = list((work / "calls").glob("call_*/response.json"))
    pending = open_calls(work, run, since)
    # A failed task of a parallel run: the other tasks finish their current call before the run stops.
    marker = read(work / "question_research/stopping.json", {}) or {}
    stopping = ({"question": marker.get("question"), "code": marker.get("code")}
                if run.get("status") == "running" and marker and (not since or (marker.get("at") or "") >= since) else None)
    budget = read(work / "budget.json", {})
    snapshot = read_yaml(work / "project_snapshot.yaml") if (work / "project_snapshot.yaml").exists() else {}
    effective = run_limits(root, work, run.get("input_hash"), snapshot)
    disputes = disputed_objections(work, run, questions)
    counts = questions or report or {}
    awaiting = isinstance(questions, dict) and questions.get("phase") == "awaiting_plan_approval"
    request = read(work / "research_request.json", {})
    retrieval = retrieval_view(work, run, snapshot, effective)
    activity = activity_text(data.get("activity"))
    if retrieval and retrieval["running"]:
        activity = str(t("progress.activity.retrieval", attempted=retrieval["attempted"], total=retrieval["total"],
                         imported=retrieval["imported"]))
    insight = None if light else insight_view(work, run, responses)
    language = studio_text.current_language()
    if isinstance(insight, dict) and isinstance(insight.get("feedback"), list):
        insight["feedback"] = [clean(text, root, language) for text in insight["feedback"]]
    return {**data, "phase": "research", "unit": "questions", "research_quality": report, "activity": activity,
            # research_activity.json keeps the time of its last real change; updated_at below is the read time.
            "changed_at": data.get("updated_at"), "retrieval": retrieval,
            "work_insight": insight,
            "research_questions": questions, "review_disagreement": disputes[0] if disputes else None,
            "review_disagreements": disputes,
            "plan_review": plan_review_state(work, run.get("input_hash"), awaiting),
            # The mode the run was started with; older runs without the field ran one task at a time.
            "execution": request.get("execution") or {"text": "sequential", "audio": "sequential"},
            "total_segments": counts.get("total", 0), "completed_segments": counts.get("closed", 0),
            "model_calls": budget.get("model_calls", 0), "search_rounds": budget.get("search_rounds", 0),
            "model_call_limit": effective.model_calls, "search_round_limit": effective.search_rounds,
            "source_limit": effective.sources, **money_fields(work, budget or {}, effective),
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
    failures = [{"source": str(row.get("source", ""))[:200],
                 "reason": clean(row.get("reason", ""), None, studio_text.current_language())}
                for row in saved.get("failures") or [] if isinstance(row, dict)]
    return {"running": stage == "running", "attempted": int(saved.get("attempted", 0)),
            "imported": int(saved.get("imported", 0)), "total": min(limits.sources, offered) or int(saved.get("attempted", 0)),
            "failed": len(failures), "failures": failures[:12]}


def watch(root, job_id, stop=None):
    """Compatibility publisher for an existing server; exits with its one original job. It writes progress.json when
    the progress changed and otherwise once a minute, so the file's age stays the worker's heartbeat. It leaves
    research_activity.json to its writer (research.progress): reading and rewriting it from here raced that writer,
    and every Studio since safe_script_progress builds the live output itself."""
    root = root.resolve()
    unreadable = 0
    written, written_at = None, 0.0
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
                # The read time alone is no change.
                signature = digest({key: value for key, value in progress.items() if key != "updated_at"})
                if signature != written or time.monotonic() - written_at >= HEARTBEAT_SECONDS:
                    try:
                        write_json(manifest_path(root, run["run_id"]).parent / "progress.json", progress)
                        written, written_at = signature, time.monotonic()
                    except OSError:
                        logging.getLogger(__name__).warning("Progress file temporarily unavailable; retrying.")
        if stop is None:
            time.sleep(2)
        elif stop.wait(2):
            return


def safe_script_progress(root, run, since=None, light=False):
    """Progress is optional: concurrent file access must not break a job or its API. ``light`` leaves out the live
    output, the call labels and everything else only the opened project shows (script_progress)."""
    try:
        progress = script_progress(root, run, since, light)
        if progress and run:
            from .status_summary import summary_view
            summary = summary_view(manifest_path(root, run["run_id"]).parent)
            if summary:
                progress["status_summary"] = summary
            if light:
                return progress
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
