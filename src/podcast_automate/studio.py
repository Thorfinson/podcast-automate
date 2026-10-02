"""Local browser workspace. Reuses the production pipelines in isolated workers."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
from typing import Literal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, unquote
from urllib.request import urlopen

from pydantic import Field

from .errors import AppError
from . import attachments
from .execution import (ExecutionChoice, MAX_PARALLEL, default_jev_probe, jev_probe_enabled, jev_probe_state,
                        selected_execution, set_jev_probe,
                        settings_execution)
from .logs import configure_logging, logger
from .models import (Contract, EpisodeScript, Failure, RunManifest, RuntimeSettings, SeriesGoal, TopicBrief,
                     host_labels, now)
from .episode_audio import saved_approval, saved_expression
from .expression import TAG
from .runner import manifest_path
from .run_budget import (approve_criterion_gap, approve_model_call_limit, approve_research_gap, approve_research_plan,
                         approve_fresh_attempts, approve_research_retry, approve_residual_finish, approve_text_switch,
                         decide_review_disagreement, fresh_attempts_available, request_teaching_redesign, switch_choice,
                         text_switch)
from .subscriptions import parse_iso
from .scripting import outline_hash, script_metrics, style_notes
from .script_pipeline import failed_teaching
from .speech import (AudioChoice, GEMINI_VOICES, QWEN_VOICES, audio_catalog, audio_generation_record, selected_audio,
                     same_audio_generation)
from .storage import (atomic_text, digest, file_hash, file_lock, init_project, inside, load_project, project_hash,
                      project_lock, read_text, read_yaml, write_json, write_yaml)
from .voice_samples import SAMPLE_TEXTS, ready_sample, sample_inventory, sample_path
from .spoken_forms import (SpokenForms, apply as apply_spoken_forms, load_forms, report as pronunciation_report,
                           spoken_text)
from .studio_progress import memo
from .studio_messages import clean, paths_only, user_text
from . import provided_works, studio_allowances
from .sources import core_usage
from .production_report import production_report
from .studio_scripts import review_notes, script_previews
from .downloads import disposition, podcast_download, podcast_zip
from .studio_trash import has_artifacts, move_contents
from .platforms import configure_path, venv_python
from .process import stop_process_tree
from .text_settings import (CLAUDE_EFFORTS, CLAUDE_MODELS, CODEX_MODELS, DEFAULT_CLAUDE_EFFORT, DEFAULT_CLAUDE_MODEL,
                            DEFAULT_CODEX_MODEL, DEFAULT_REASONING_EFFORT, EFFORT_EQUIVALENTS, OPENROUTER_EFFORTS,
                            OPENROUTER_MODELS, PROVIDER_NOTES, REASONING_EFFORTS, TEXT_PRESETS, TEXT_PROVIDERS,
                            auto_candidates, provider_model, text_preset, validate_model, validate_reasoning)

VOICES = QWEN_VOICES
# A job paused by a subscription limit is resumed automatically at its named reset, at most this often.
MAX_AUTO_RESUMES = 3
SCHEDULER_INTERVAL_SECONDS = 30
# Each project runs one main job (text work, or local Qwen audio); this many projects work at once.
# Local Qwen needs the graphics card, so only one project renders with it at a time.
MAX_PROJECT_JOBS = 3
# A paused text job in one of these states stays the project's job even when a later audio job exists.
ATTENTION = {"blocked", "failed", "interrupted", "waiting_for_quota", "pending", "review_ready"}
# Empty credit and an expired login do not come back by waiting, so the scheduler never resumes them.
NO_AUTO_RESUME = {"openrouter_credits", "authentication_required"}
# Technical stops of a research or script run that a later attempt usually passes: a call that ran out of time or
# went silent, a failed provider turn, a connection that dropped. The scheduler resumes them by itself, MAX_AUTO_RESUMES
# times after these pauses (2026-10-02: about 186 resumes by hand against 8 automatic ones, which covered quota waits
# only). Editorial decisions, limits, credit and logins are never among them.
TRANSIENT_STOPS = {"timeout", "stall", "claude_failed", "codex_failed", "claude_structured_output",
                   "openrouter_connection", "openrouter_unavailable"}
TRANSIENT_BACKOFF_MINUTES = (10, 30, 90)
# Each lane keeps its own parked job (park); paused_job.json is the single slot of Studios before 2026-10-02.
LANES = ("research", "script", "audio")
# Jobs that never write exports: while one of them holds the project lock, downloads read without it.
TEXT_ACTIONS = {"assistant", "research", "plan", "replan", "script", "revise", "check", "audio_sample", "audio_samples"}
# Names start with a word character, so no segment can be "..".
DIAGNOSTIC_FILES = re.compile(r"(runs/\w[\w.-]*/(?:\w[\w.-]*/)*\w[\w.-]*\.(?:txt|md|json)|studio/failures/\w[\w.-]*\.txt"
                              r"|studio/stderr/[a-f0-9]{32}\.log)")
CHAT_LIMIT_STEP = 50
# What a project card needs of a research question: its state and what the resume and stop decisions count
# (app.js blockedSettled, unadvised, canResume). Findings, sources and review notes made the list 2 MB, read
# again on every poll (2026-10-01); the project page still gets the whole rows.
OVERVIEW_QUESTION_FIELDS = ("id", "question", "kind", "status", "outcome", "depends_on", "accepted_gap", "reopened",
                            "reopenable", "retries", "auto_retries", "retry_requested", "access_gap_requested",
                            "activity", "steps", "web_attempts")


def overview_job(job):
    """The job as a project card sees it: the research ledger with short question rows."""
    ledger = ((job or {}).get("progress") or {}).get("research_questions")
    if not isinstance(ledger, dict) or not ledger.get("questions"):
        return job
    rows = [{**{field: row[field] for field in OVERVIEW_QUESTION_FIELDS if field in row},
             **({"advice": {"key": row["advice"].get("key"), "recommendation": row["advice"].get("recommendation")}}
                if isinstance(row.get("advice"), dict) else {})} for row in ledger["questions"]]
    return {**job, "progress": {**job["progress"], "research_questions": {**ledger, "questions": rows}}}


def stop_code(job):
    """The code and stage of a stopped job: the worker's own error first, it is newer than stage records."""
    stages = ((job or {}).get("run") or {}).get("stages") or {}
    stage, error = next(((name, record["error"]) for name, record in stages.items()
                         if isinstance(record, dict) and record.get("error")), (None, None))
    if (job or {}).get("error_code"):
        return job["error_code"], None
    return (error or {}).get("code"), stage


def lane(job):
    """The work a job belongs to; a new job of the same lane replaces a paused one, another lane parks it."""
    action = (job or {}).get("action")
    kind = ((job or {}).get("run") or {}).get("kind")
    if action == "research" or kind == "research":
        return "research"
    if action in {"plan", "replan", "script", "revise"} or kind == "script":
        return "script"
    if action == "audio" or kind == "episode_audio":
        return "audio"
    return None


def parked_paths(root):
    """The project's parked job files: one per lane, then the single slot older Studios wrote."""
    return [root / "studio" / f"paused_{name}.json" for name in LANES] + [root / "studio/paused_job.json"]


def park(root, new_job):
    """Keep a paused text or audio job visible while a job of another lane uses studio/job.json. Each lane parks in
    its own file, so a chat, a check, a voice sample or a run of another lane never displaces a parked run (2026-10-02:
    a chat parked over a stopped research run, whose stop card and scheduled resume then disappeared)."""
    current = read_json(root / "studio/job.json", {}) or {}
    resumed_run = (new_job.get("run") or {}).get("run_id") if new_job.get("action") == "resume" else None
    for parked in parked_paths(root):
        if not parked.exists():
            continue
        # A resume of the parked run continues it; a new job of the same lane supersedes it.
        old = read_json(parked, {}) or {}
        resumed = resumed_run is not None and resumed_run == (old.get("run") or {}).get("run_id")
        if resumed or (new_job.get("action") != "resume" and lane(new_job) is not None and lane(old) == lane(new_job)):
            parked.unlink()
    if (current.get("run") and current.get("status") in ATTENTION and lane(current)
            and lane(current) != lane(new_job) and not (resumed_run is not None
                and resumed_run == current["run"].get("run_id"))):
        write_json(root / "studio" / f"paused_{lane(current)}.json", current)


def chat_limits(root, limits):
    """The editorial conversation's call allowance: the project's research limit or a raise approved in the Studio."""
    approved = read_json(root / "studio/assistant/limit.json", {}) or {}
    calls = approved.get("model_calls")
    if type(calls) is int and calls > limits.model_calls:
        return limits.model_copy(update={"model_calls": calls})
    return limits


def worker_stderr(root, job):
    """The tail of a worker's error output, when it wrote any."""
    job_id = (job or {}).get("id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return None
    path = root / "studio/stderr" / (job_id + ".log")
    try:
        if not path.is_file() or not path.stat().st_size:
            return None
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return None
    return "\n".join(lines[-12:])[-1500:] or None


def worker_record(root, job_id):
    return root / "studio/workers" / (job_id + ".json")


def record_worker(root, job_id, process):
    """Note a started worker's pid and creation time beside the job: a later Studio, after a restart, still knows
    whether that worker runs and may stop it. The worker never writes this file, so no write of its job file races."""
    if type(getattr(process, "pid", None)) is not int:
        return
    try:
        import psutil
        started = psutil.Process(process.pid).create_time()
        write_json(worker_record(root, job_id), {"pid": process.pid, "process_started_at": started})
    except Exception:  # Optional: without the record a busy lock still tells a live worker (Studio.vanished).
        logger("studio").warning("Arbeitsprozess %s nicht vermerkt.", job_id, exc_info=True)


def worker_identity(root, job):
    """The pid and start time of a job's worker: the record the Studio writes when it starts one, or the fields the
    worker writes into its job file at start, ``pid`` and ``started_process_at`` (psutil creation time, seconds since
    the epoch). None when neither is there."""
    job_id = (job or {}).get("id")
    record = read_json(worker_record(root, job_id), {}) if isinstance(job_id, str) and re.fullmatch(r"[a-f0-9]{32}", job_id) else {}
    for source in (record or {}, job or {}):
        pid = source.get("pid")
        started = source.get("started_process_at", source.get("process_started_at"))
        if type(pid) is int and pid > 0 and isinstance(started, (int, float)):
            return {"pid": pid, "started": float(started)}
    return None


def process_alive(identity):
    """True while the recorded worker runs, False once it ended or its pid belongs to a later process, None when
    there is no record to tell. The start time may be the creation time or one the worker took itself, a little
    later; a reused pid starts after the recorded worker ended."""
    if identity is None:
        return None
    import psutil
    try:
        process = psutil.Process(identity["pid"])
        created = process.create_time()
        if not (created <= identity["started"] + 2 and identity["started"] - created <= 120):
            return False
        return process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return None


class ExternalWorker:
    """A worker an earlier Studio started, verified by pid and start time: enough of Popen for stop_process_tree."""

    def __init__(self, pid):
        import psutil
        self.pid, self._process = pid, psutil.Process(pid)

    def poll(self):
        import psutil
        try:
            return None if self._process.is_running() and self._process.status() != psutil.STATUS_ZOMBIE else 0
        except psutil.Error:
            return 0

    def wait(self, timeout=None):
        import psutil
        try:
            self._process.wait(timeout)
        except psutil.NoSuchProcess:
            pass


def auto_resume(job):
    """When the scheduler resumes this stopped main job by itself: ``(kind, ISO time)``, ``(kind, None)`` once its
    automatic resumes are used up, or None when it never does. ``quota`` waits for the named reset; ``transient``
    waits TRANSIENT_BACKOFF_MINUTES after the stop."""
    run = (job or {}).get("run") or {}
    if not run.get("run_id") or stop_code(job)[0] in NO_AUTO_RESUME:
        return None
    count = job.get("auto_resume_count", 0)
    count = count if type(count) is int and count >= 0 else 0
    if job.get("status") == "waiting_for_quota" and job.get("retry_at"):
        return "quota", job["retry_at"] if count < MAX_AUTO_RESUMES else None
    if (job.get("status") in {"failed", "blocked"} and stop_code(job)[0] in TRANSIENT_STOPS
            and run.get("kind") in {"research", "script"}):
        stopped = parse_iso(job.get("finished_at"))
        if stopped is None:
            return None
        if count >= MAX_AUTO_RESUMES:
            return "transient", None
        return "transient", datetime.fromtimestamp(stopped.timestamp() + 60 * TRANSIENT_BACKOFF_MINUTES[count],
                                                   timezone.utc).isoformat()
    return None


def latest_provider_choice(work):
    """The decision of the newest model call, with the switch receipt when that call changed provider."""
    calls = sorted((work / "calls").glob("call_*/provider_choice.json"))
    if not calls:
        return None
    choice = read_json(calls[-1], {})
    if not isinstance(choice, dict) or not choice:
        return None
    switch = read_json(calls[-1].with_name("provider_switch.json"))
    keys = ("provider", "model", "reasoning_effort", "mode", "reason", "decided_at", "search")
    return {"call": calls[-1].parent.name, **{key: choice.get(key) for key in keys},
            "snapshots": choice.get("snapshots") if isinstance(choice.get("snapshots"), dict) else {},
            "switch": {key: switch.get(key) for key in ("from", "to", "error_code", "switched_at")}
            if isinstance(switch, dict) and switch else None}


def audio_job_path(root, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise AppError("Ungültiger Audioauftrag.", code="invalid_job")
    return root / "studio/audio_jobs" / (job_id + ".json")


def record_interruption(root, *, expected_job_id=None, audio_job_id=None,
                        message="Angehalten. Fertige Arbeit bleibt gespeichert.", shared=None, crash_detail=None):
    """Persist an interruption after the owned worker and its children have exited."""
    with project_lock(root, shared=bool(audio_job_id) if shared is None else shared):
        job_path = audio_job_path(root, audio_job_id) if audio_job_id else root / "studio/job.json"
        job = read_json(job_path)
        if expected_job_id is not None and job.get("id") != expected_job_id:
            raise AppError("Der Studio-Auftrag hat sich geändert.", code="job_changed")
        if job.get("status") not in {"running", "interrupted"}:
            return job  # A worker may have saved its final result just before exiting.
        run = job.get("run")
        if run:
            path = manifest_path(root, run["run_id"])
            if path.is_file():
                manifest = RunManifest.model_validate(read_yaml(path))
                if manifest.status == "running":
                    manifest.status = "pending"
                    for stage in manifest.stages.values():
                        if stage.status == "running":
                            stage.status = "pending"
                            stage.error = Failure(code="interrupted", message="Angehalten. Gespeicherten Stand fortsetzen.")
                    manifest.updated_at = now()
                    write_yaml(path, manifest.model_dump(mode="json"))
                job["run"] = manifest.model_dump(mode="json")
        job.update(status="interrupted", finished_at=now(), message=message)
        if crash_detail:
            job["crash_detail"] = crash_detail
        from .studio_progress import safe_script_progress
        progress = safe_script_progress(root, job.get("run"))
        if progress:
            job["progress"] = progress
        write_json(job_path, job)
        return job


def read_json(path, default=None):
    # Through storage.read_text: the server and the workers read files another process is replacing, which on
    # Windows is a sharing violation for a few milliseconds (2026-10-01: Asimov's worker died reading progress.json).
    return json.loads(read_text(path)) if path.exists() else default


def code_updated_at():
    """When the package's code or prompts last changed: a run that stopped earlier on a checkpoint that no longer
    fitted may fit the corrected code (2026-10-01: Asimov's morris_evaluate, fixed after its stop)."""
    package = Path(__file__).parent
    times = [path.stat().st_mtime for path in [*package.glob("*.py"), *package.glob("prompts/*.txt")] if path.exists()]
    return datetime.fromtimestamp(max(times), timezone.utc).isoformat() if times else None


def code_fingerprint():
    """Names, sizes and times of the package's code and prompts. The page is served fresh per request, but the
    server and its scheduler keep the code they started with; a changed fingerprint means an update waits for a
    restart (2026-09-29: five restarts by hand in one day, twice with a running server on old code)."""
    package = Path(__file__).parent
    rows = []
    for path in sorted([*package.glob("*.py"), *package.glob("prompts/*.txt")]):
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append([path.relative_to(package).as_posix(), stat.st_size, stat.st_mtime_ns])
    return digest(rows)


def relaunch(workspace, port, lan):
    """Start the Studio again in the background, on the same workspace and port, without opening a browser."""
    command = [sys.executable, "-m", "podcast_automate", "studio", str(Path(workspace).resolve()), "--port", str(port),
               "--no-browser", *(["--lan"] if lan else [])]
    options = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
               else {"start_new_session": True})
    # A new server that fails before its own log starts leaves its reason here (2026-10-02: a failed relaunch left
    # no trace at all).
    log = Path(workspace) / ".studio/relaunch.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab" if not log.is_file() or log.stat().st_size < 1_000_000 else "wb") as errors:
        errors.write(f"--- {now()} Neustart: {' '.join(command[1:])}\n".encode("utf-8"))
        errors.flush()
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors,
                         close_fds=True, cwd=str(workspace), **options)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value):
        raise AppError("Ungültige Projektauswahl.", code="invalid_project")
    return value


class BriefProposal(Contract):
    message: str = Field(min_length=1, max_length=12000)
    topic: str = Field(min_length=1, max_length=500)
    central_question: str = Field(min_length=1, max_length=2000)
    prior_knowledge: str = Field(max_length=3000)
    depth_request: str = Field(min_length=1, max_length=6000)
    focus_questions: list[str] = Field(max_length=20)
    excluded_topics: list[str] = Field(max_length=20)
    language: Literal["de-DE", "en-US"] | None = None
    target_total_minutes: int | None = Field(default=None, ge=1, le=10000)
    seed_urls: list[str] | None = Field(default=None, max_length=50)
    text: TextChoice | None = None
    audio_settings: AudioChoice | None = None
    execution: ExecutionChoice | None = None
    # None keeps the saved value. recency_months=0 removes a saved rule.
    series_goal: SeriesGoal | None = None
    recency_months: int | None = Field(default=None, ge=0, le=120)
    suggested_replies: list[str] = Field(default_factory=list, max_length=4)
    setup_complete: bool = False


class TextChoice(Contract):
    provider: str = "codex_cli"
    model: str | None = Field(default=None, max_length=200)
    max_output_tokens: int = Field(default=32768, ge=1024, le=200000)
    reasoning_effort: str | None = None

    def kwargs(self):
        if self.provider not in TEXT_PROVIDERS:
            raise AppError("Textanbieter auswählen.", code="invalid_backend")
        defaults = {"codex_cli": (DEFAULT_CODEX_MODEL, DEFAULT_REASONING_EFFORT),
                    "claude_code": (DEFAULT_CLAUDE_MODEL, DEFAULT_CLAUDE_EFFORT)}
        default_model, default_effort = defaults.get(self.provider, (None, None))
        model = provider_model(self.provider, self.model) or default_model
        effort = self.reasoning_effort or default_effort
        validate_model(model)
        validate_reasoning(effort, provider=self.provider, model=model)
        return {"backend": self.provider, "model": model, "reasoning_effort": effort,
                "max_output_tokens": self.max_output_tokens if self.provider == "openrouter" else None}

    def normalized(self):
        selected = self.kwargs()
        return {**self.model_dump(), "model": selected["model"], "reasoning_effort": selected["reasoning_effort"]}


BriefProposal.model_rebuild()


def read_queue(root):
    """Approved Gemini recordings of a project waiting for a free place (Studio.enqueue_audio), in approval order."""
    rows = read_json(root / "studio/audio_queue.json", [])
    return rows if isinstance(rows, list) else []


def write_queue(root, rows):
    write_json(root / "studio/audio_queue.json", rows)


def queue_view(rows, key_available=True):
    """The queue as the audio page shows it. A recording waits for a place, or for the OpenRouter key, which lives
    only in the server's memory and is gone after a restart (2026-10-02: the queue still said it waited for a place)."""
    return [{"episode": row.get("episode"), "position": number, "queued_at": row.get("queued_at"),
             **({"error": row["error"]} if row.get("error") else {"waiting": "place" if key_available else "key"})}
            for number, row in enumerate(rows, 1)]


def kept_local_sources(root, saved, requested):
    """The project's local sources after a save from the browser. Paths outside the project come only from disk, as
    the runtime does; the browser may name files inside the project's inputs/, the Studio's own uploads. A device in
    the home network can otherwise point a research run at any file on this computer (2026-10-02). An unchanged list
    stays exactly as saved, so no brief hash moves."""
    inputs = (root / "inputs").resolve()

    def own(value):
        if not isinstance(value, str) or not value.strip() or Path(value).is_absolute() or Path(value).drive:
            return False
        try:
            return (root / value).resolve().is_relative_to(inputs)
        except (OSError, ValueError):
            return False
    if list(requested) == list(saved):
        return list(saved)
    kept = [value for value in requested if value in saved or own(value)]
    return list(dict.fromkeys(kept + [value for value in saved if value not in kept and not own(value)]))


def reading_expression(root, episode, script_hash):
    """The inline tags placed for an episode's current script (episode_audio.tag_episode), for reading and approval."""
    saved = saved_expression(root, episode, script_hash)
    if saved is None:
        return None
    rows = [{"segment_id": key, "text": text, "spoken": (saved.get("spoken") or {}).get(key, "")}
            for key, text in saved["segments"].items() if isinstance(text, str)]
    return {"tags": sum(len(TAG.findall(row["text"])) for row in rows), "segments": rows,
            "rejected": saved.get("rejected", ""), "placed_at": saved.get("placed_at"),
            "hash": file_hash(root / "episodes" / episode / "expression.json")}


def project_job(main, jobs, parked):
    """The job a project shows. A running or paused main job stays the project's job: a later audio job must not hide
    its decision, and a parked run waits behind a finished chat, check or job of another lane."""
    candidates = [job for job in [main, *jobs] if job]
    latest = max(candidates, key=lambda job: (job["status"] == "running", job.get("started_at", "")), default=None)
    if main and (main["status"] == "running" or (main["status"] in ATTENTION and main.get("run"))):
        return main
    if parked:
        return parked
    if main and main["status"] in ATTENTION:
        return main
    return latest


def episode_view(folder, table, table_key, language):
    """What a published episode's page shows from its script and its audio decision, kept while script.yaml,
    script.md and audio_review.yaml are unchanged: parsing and hashing every script on each poll was much of a page's
    cost (2026-10-02). Read-only for callers."""
    script_file, decision_file = folder / "script.yaml", folder / "audio_review.yaml"

    def compute():
        script = EpisodeScript.model_validate(read_yaml(script_file))
        # The audio decision, not the last run report, holds what the operator set by hand.
        decision = read_yaml(decision_file) if decision_file.is_file() else {}
        overrides = {k: v for k, v in (decision.get("spoken_overrides") or {}).items()
                     if isinstance(k, str) and isinstance(v, str) and v.strip()}
        metrics = script_metrics(script)
        return {"script": script.model_dump(), "hash": file_hash(script_file),
                "readable_hash": file_hash(folder / "script.md"), "metrics": metrics,
                "spoken_overrides": overrides,
                # Computed from the published text, the table and the overrides, with no model
                # call, so a reader sees the difficult tokens before the first audio run.
                "pronunciation": pronunciation_report(script, table, language=language, overrides=overrides),
                # What a recording of this text sends to speech: the characters the voices hear (table and overrides
                # applied, without tags) and the estimated length, shown before a paid recording. No price: it
                # cannot be checked offline.
                "speech": {"characters": sum(len(spoken_text(segment, table, overrides)) for segment in script.segments),
                           "minutes": metrics["estimated_minutes"]},
                "listening_note": decision.get("listening_note", ""),
                "human_listening_reviewed": bool(decision.get("human_listening_reviewed"))}
    return memo(("episode", str(folder), table_key, language),
                [script_file, folder / "script.md", decision_file], compute)


class Studio:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.projects = self.workspace / "projects"
        self.token = secrets.token_urlsafe(32)
        self.key = ""
        self.mutex = threading.RLock()
        self.workers = {}  # project root -> (process, uses the GPU) of its main job
        self.audio_processes = {}
        self.scheduler = None
        # Reachable from the home network too (``pla studio --lan``); the port is the one actually bound.
        self.lan, self.port = False, None
        # Restart when idle (restart_when_idle): the code this server started with, the user's request, and the
        # hook serve() sets to stop the server so it can start again.
        self.instance = uuid.uuid4().hex
        self.code_stamp = code_fingerprint()
        self.restart_requested = self.restarting = False
        self.restart_hook = None
        configure_path(self.workspace)

    def root(self, project):
        root = inside(self.projects, identifier(project))
        if not (root / "project.yaml").is_file():
            raise AppError("Projekt nicht gefunden.", code="unknown_project")
        return root

    def runtime(self):
        settings = RuntimeSettings()
        python = venv_python(self.workspace / ".venv-tts")
        if python.exists():
            settings.tts_python = str(python)
        local = read_json(self.workspace / ".studio/tts-runtime.json", {})
        if local:
            settings = RuntimeSettings.model_validate({**settings.model_dump(), **local})
        if re.fullmatch(r"[a-f0-9]{40}", settings.tts_revision):
            return settings
        # Use the installed, pinned model revision, without copying a pilot's topic.
        for path in sorted(self.projects.glob("*/project.yaml")):
            try:
                runtime = load_project(path.parent).runtime
                if runtime.tts_model == settings.tts_model and re.fullmatch(r"[a-f0-9]{40}", runtime.tts_revision):
                    settings.tts_revision = runtime.tts_revision
                    break
            except (AppError, ValueError):
                continue
        return settings

    def project_list(self):
        projects = []
        for path in sorted(self.projects.glob("*/project.yaml")):
            try:
                identifier(path.parent.name)
                config = load_project(path.parent)
                projects.append({"id": path.parent.name, "topic": config.topic})
            except (AppError, ValueError):
                continue
        return projects

    def voice_samples(self):
        """The shared Gemini sample library (sample_inventory), hashed again only after a sample file changed: the
        inventory hashes every recording, and every project page asked for it on each poll."""
        files = []
        for language in SAMPLE_TEXTS:
            for voice in GEMINI_VOICES:
                try:
                    path = sample_path(self.projects, voice, language)
                except AppError:
                    continue
                files += [path, path.with_suffix(".json")]
        return memo(("samples", str(self.projects)), files, lambda: sample_inventory(self.projects))

    def bootstrap(self):
        projects = self.project_list()
        return {"app": "podcast-studio", "workspace": str(self.workspace),
                "capabilities": {"text_reasoning_selection": True, "parallel_audio": True,
                                 "project_execution": True, "conversational_setup": True, "project_overview": True,
                                 "podcast_downloads": True, "project_attachments": True, "subscription_auto": True},
                "attachment_limits": {"files": attachments.MAX_FILES, "file_bytes": attachments.MAX_FILE_BYTES,
                                      "docx_bytes": attachments.MAX_DOCX_BYTES, "transfer_bytes": attachments.MAX_TRANSFER_BYTES,
                                      "total_bytes": attachments.MAX_TOTAL_BYTES},
                "parallel_limit": MAX_PARALLEL,
                # New projects start with the automatic subscription choice; saved projects keep studio/text.json.
                "text_defaults": TextChoice(**text_preset("auto_subscriptions")).normalized(),
                "text_catalog": {"codex_models": CODEX_MODELS, "openrouter_models": OPENROUTER_MODELS,
                                 "openrouter_efforts": OPENROUTER_EFFORTS, "presets": TEXT_PRESETS,
                                 "reasoning_efforts": REASONING_EFFORTS, "claude_models": CLAUDE_MODELS,
                                 "claude_efforts": CLAUDE_EFFORTS, "effort_equivalents": EFFORT_EQUIVALENTS,
                                 "provider_notes": PROVIDER_NOTES, "auto_candidates": auto_candidates()},
                "token": self.token, "projects": projects, "voices": VOICES,
                "lan": {"enabled": self.lan,
                        "urls": [f"http://{address}:{self.port}" for address in lan_addresses()] if self.lan else []},
                "audio_catalog": audio_catalog(),
                "voice_samples": self.voice_samples(),
                "key_available": self.key_available(),
                "server": self.server_state(),
                "defaults": TopicBrief(topic="Neues Podcast-Projekt", runtime=self.runtime(),
                                      voice_profile={"host_a": "Aiden", "host_b": "Vivian"}).model_dump(mode="json")}

    def key_available(self):
        return bool(self.key or os.environ.get("OPENROUTER_API_KEY"))

    def job(self, root, *, audio_job_id=None, path=None, light=False):
        """A job as the pages show it. ``light`` is the project card's view (overview): no live output, assignment,
        previews or provider details."""
        path = path or (audio_job_path(root, audio_job_id) if audio_job_id else root / "studio/job.json")
        data = read_json(path)
        if data and data["status"] == "running":
            owner = self.audio_processes.get(audio_job_id) if audio_job_id else None
            owned = (owner is not None and owner[1] == root and owner[0].poll() is None) if audio_job_id else (
                self.worker(root) is not None)
            if not owned:
                # The worker may have written its final result just before poll() observed exit.
                data = read_json(path)
                if data["status"] == "running":
                    data = self.vanished(root, path, data, audio_job_id)
            run = data.get("run")
            if run:
                work = manifest_path(root, run["run_id"]).parent
                text_run = run.get("kind") in {"script", "research"}
                # A research or script view is built from the run folder below; its progress.json, megabytes for a
                # research run, is read only when that view is unavailable.
                progress = None if text_run else read_json(work / "progress.json")
                nested_path = inside(root, progress["chapter_progress"]) if (progress or {}).get("chapter_progress") else None
                if (owned or data.get("external")) and data["status"] == "running":
                    # Research and script workers rewrite progress.json when it changes and at least once a minute;
                    # audio writes it per chapter and the chapter's own file per segment. The newest is the heartbeat.
                    stamps = [started.timestamp()] if (started := parse_iso(data.get("started_at"))) else []
                    for candidate in filter(None, (work / "progress.json", nested_path)):
                        try:
                            stamps.append(candidate.stat().st_mtime)
                        except OSError:
                            pass
                    if stamps:
                        data["heartbeat_age_seconds"] = max(0, int(time.time() - max(stamps)))
                if progress and "total_segments" in progress:
                    if nested_path:
                        nested = read_json(nested_path, {}) or {}
                        progress["completed_segments"] = min(progress["total_segments"],
                            progress["completed_segments"] + nested.get("completed_segments", nested.get("completed", 0)))
                        if nested.get("status"):
                            progress["tts_status"] = nested["status"]
                    data["progress"] = progress
        if data and (data.get("run") or {}).get("kind") in {"script", "research"}:
            from .studio_progress import safe_script_progress
            # Calls an earlier, stopped worker left open are not the running job's calls.
            progress = safe_script_progress(root, data["run"], data.get("started_at") if data.get("status") == "running" else None,
                                            light=light)
            if progress:
                data["progress"] = progress
            elif data.get("status") == "running":
                # The last snapshot the worker published, while the run folder is momentarily unreadable.
                saved = read_json(manifest_path(root, data["run"]["run_id"]).parent / "progress.json")
                if isinstance(saved, dict) and "total_segments" in saved:
                    data["progress"] = saved
        if data and (data.get("run") or {}).get("kind") in {"script", "research"} and not light:
            run = data["run"]
            work = manifest_path(root, run["run_id"]).parent
            request = "script_request.json" if run["kind"] == "script" else "research_request.json"
            saved = read_json(work / request, {}).get("text_generation")
            data["text_generation"] = text_switch(work, run.get("input_hash"), saved)
            # Every text run may continue with another provider (approve_text_switch); the page names the current one.
            data["text_switched"] = data["text_generation"] != saved
            data["text_switchable"] = True
            data["text_switch_choice"] = switch_choice(data["text_generation"])
            data["provider_choice"] = latest_provider_choice(work)
            if data.get("status") in {"blocked", "failed"}:
                # Whether "Mit neuen Anläufen fortsetzen" would be accepted now: a step spent its corrections and no
                # approval reset them yet. Kept while the run and its records of fresh attempts are unchanged.
                data["fresh_attempts"] = memo(("fresh_attempts", str(work)),
                    [work / name for name in ("run_manifest.yaml", "fresh_attempts.json", "series_repair.json")],
                    lambda: fresh_attempts_available(root, run["run_id"]))
        if data and audio_job_id is None:
            # Announced only where the scheduler acts: the project's main job with a run to resume.
            plan = auto_resume(data)
            if plan:
                data["auto_resume_kind"] = plan[0]
                if plan[1]:
                    data["auto_resume_at"] = plan[1]
                else:
                    data["auto_resume_exhausted"] = True
        if data and audio_job_id is None and data.get("status") == "blocked":
            # Announced where the scheduler acts on it: an allowance the user set ahead (studio_allowances).
            data["allowance"] = studio_allowances.pending(root, data, stop_code(data)[0])
        return self.readable(root, data) if data else data

    def parked_jobs(self, root, light=False):
        """Paused jobs moved aside by jobs of other lanes while their runs are still open, newest stop first."""
        rows, seen = [], set()
        for path in parked_paths(root):
            data = read_json(path)
            run_id = ((data or {}).get("run") or {}).get("run_id")
            if not run_id or run_id in seen or not manifest_path(root, run_id).is_file():
                continue
            if read_yaml(manifest_path(root, run_id)).get("status") == "completed":
                continue
            seen.add(run_id)
            rows.append(self.job(root, path=path, light=light))
        return sorted(rows, key=lambda job: job.get("finished_at") or "", reverse=True)

    def parked_job(self, root, light=False):
        """The newest parked job (parked_jobs), or None."""
        return next(iter(self.parked_jobs(root, light)), None)

    def vanished(self, root, path, data, audio_job_id):
        """A job still marked running whose worker this server does not own. A worker an earlier Studio started may
        still run: it holds the project lock, or its recorded pid still runs. Then the job is shown as running
        elsewhere and nothing is rewritten (2026-10-02: it was written as interrupted on every poll, "Fortsetzen" met
        the busy project and "Anhalten" found no job). Otherwise it ended without a result and its run is reset."""
        identity = worker_identity(root, data)
        alive = process_alive(identity)
        if alive:
            return self.external(data, stoppable=True)
        detail = worker_stderr(root, data)
        message = ("Der Arbeitsprozess wurde unerwartet beendet." if detail else
                   "Der Arbeitsprozess läuft nicht mehr, etwa nach einem Neustart des Studios.") + \
            " Fertige Arbeit bleibt gespeichert."
        try:
            # The exclusive lock proves that no worker of this project is left; a dead recorded worker needs only the
            # shared one beside the project's other recordings, as a stop does. Only then is the run reset.
            return record_interruption(root, expected_job_id=data.get("id"), audio_job_id=audio_job_id,
                                       message=message, shared=bool(audio_job_id) and alive is False,
                                       crash_detail=detail)
        except AppError as exc:
            if exc.code == "job_changed":
                return read_json(path)
            if exc.code == "project_busy" and alive is None:
                # Without a record of its worker, a held lock is the sign that it still runs.
                return self.external(data, stoppable=False)
            # The recorded worker has ended, but another process holds the project: shown as interrupted, written
            # once the lock is free.
            view = {**data, "status": "interrupted", "message": message}
            if detail:
                view["crash_detail"] = detail
            return view
        except (OSError, ValueError):
            data.update(status="interrupted", message=message)
            if detail:
                data["crash_detail"] = detail
            write_json(path, data)
            return data

    @staticmethod
    def external(data, *, stoppable):
        """A running job whose worker this Studio did not start: shown as running elsewhere, never rewritten."""
        return {**data, "external": True, "external_stoppable": stoppable}

    @staticmethod
    def readable(root, data):
        """The job as a reader sees it: a stop names its code and a German message without CLI advice or paths."""
        if data.get("status") not in {"running", "completed"}:
            code, stage = stop_code(data)
            data["stop"] = {"code": code, "stage": stage, "run_kind": (data.get("run") or {}).get("kind"),
                            **user_text(data.get("message") or "", root),
                            "crash_detail": paths_only(data.get("crash_detail"), root)}
        if data.get("message"):
            data["message"] = clean(data["message"], root)
        for gap in data.get("research_gaps") or []:
            if isinstance(gap, dict):
                gap.update({key: clean(gap.get(key), root) for key in ("question", "why_needed")})
        run_id = (data.get("run") or {}).get("run_id")
        if (data.get("stop") or {}).get("code") == "teaching_design_failed" and isinstance(run_id, str):
            # The episode a new design with the editor's note is for; read from the run, so older jobs have it too.
            try:
                data["teaching_failure"] = failed_teaching(manifest_path(root, run_id).parent)
            except (AppError, OSError, ValueError):
                data["teaching_failure"] = None
        return data

    def approve(self, project, data):
        """Explicit run-bound approvals; each writes one receipt and never touches counters or state."""
        root = self.root(project)
        kind = data.get("kind")
        if kind == "chat_calls":
            # The conversation has no run; its allowance is a project setting outside the brief's hash.
            current = chat_limits(root, load_project(root).research_limits).model_calls
            calls = data.get("model_calls")
            if type(calls) is not int or not current < calls <= current + 500:
                raise AppError("Das neue Gesprächslimit muss eine ganze Zahl über dem bisherigen Limit sein.",
                               code="invalid_budget_approval")
            write_json(root / "studio/assistant/limit.json", {"model_calls": calls, "approved_at": now()})
            return {"chat_calls": calls}
        run_id = data.get("run_id") or ((self.job(root) or {}).get("run") or {}).get("run_id")
        if not isinstance(run_id, str):
            raise AppError("Kein Lauf für diese Freigabe vorhanden.", code="no_run")
        if kind == "model_calls":
            approval = approve_model_call_limit(root, run_id, data.get("model_calls"), search_rounds=data.get("search_rounds"),
                                                sources=data.get("sources"))
            return {"approval": approval.model_dump(mode="json")}
        if kind == "gap":
            approval = approve_research_gap(root, run_id, data.get("task_id"), data.get("reason", ""))
            return {"gap": approval.model_dump(mode="json")}
        if kind == "retry":
            request = approve_research_retry(root, run_id, data.get("task_id"), data.get("hint", ""))
            return {"retry": request.model_dump(mode="json")}
        if kind == "fresh_attempts":
            if self.worker(root) is not None:
                raise AppError("Der Auftrag läuft gerade; neue Anläufe erst, wenn er angehalten hat.", code="studio_busy")
            return {"fresh_attempts": approve_fresh_attempts(root, run_id)}
        if kind == "residual":
            finish = approve_residual_finish(root, run_id, data.get("note", ""))
            return {"residual": finish.model_dump(mode="json")}
        if kind == "dispute":
            choice = decide_review_disagreement(root, run_id, data.get("objection_id"), data.get("decision"),
                                                data.get("note", ""))
            return {"dispute": choice.model_dump(mode="json")}
        if kind == "access_gap":
            approval = approve_criterion_gap(root, run_id, data.get("task_id"), data.get("criterion"), data.get("source"),
                                             data.get("reason", ""))
            return {"access_gap": approval.model_dump(mode="json")}
        if kind == "text_switch":
            return {"text_switch": approve_text_switch(root, run_id, data.get("choice", "claude_first"), model=data.get("model"))}
        if kind == "teaching_redesign":
            request = request_teaching_redesign(root, run_id, data.get("episode_id"), data.get("note", ""))
            return {"teaching_redesign": request.model_dump(mode="json")}
        if kind == "plan":
            # The receipt binds to the projected plan; a cap asks the next resume to plan again and present anew.
            approval = approve_research_plan(root, run_id, max_tasks=data.get("max_tasks"), source="studio")
            return {"plan": approval.model_dump(mode="json")}
        raise AppError("Unbekannte Freigabe.", code="invalid_action")

    def enqueue_audio(self, root, episode, data):
        """Keep an approved Gemini recording until a place is free; a newer approval of the episode replaces it."""
        rows = [row for row in read_queue(root) if row.get("episode") != episode]
        request = {key: value for key, value in data.items() if key not in {"from_queue", "api_key"}}
        rows.append({"episode": episode, "queued_at": now(), "data": request})
        write_queue(root, rows)
        return {"queued": True, "episode": episode, "position": len(rows)}

    def audio_queue(self, project, data):
        """Take an episode out of the recording queue."""
        root = self.root(project)
        episode = data.get("episode")
        if not isinstance(episode, str):
            raise AppError("Folge angeben.", code="unknown_episode")
        rows = [row for row in read_queue(root) if row.get("episode") != episode]
        write_queue(root, rows)
        return {"audio_queue": queue_view(rows)}

    def start_queued(self):
        """Approved Gemini recordings waiting in a project's queue start in approval order once a place is free.
        Each start checks the approval again (start's own rules): one that no longer holds stays in the queue with
        its reason instead of starting, and one waiting for a place or a key simply waits."""
        started = []
        for path in sorted(self.projects.glob("*/studio/audio_queue.json")):
            root, project = path.parents[1], path.parents[1].name
            with self.mutex:
                for row in [row for row in read_queue(root) if not row.get("error")]:
                    if not (self.key or os.environ.get("OPENROUTER_API_KEY")):
                        break
                    try:
                        self.start(project, {**row["data"], "from_queue": True})
                    except AppError as exc:
                        if exc.code in {"audio_capacity", "project_busy", "studio_capacity", "episode_busy"}:
                            break
                        rows = read_queue(root)
                        for saved in rows:
                            if saved.get("episode") == row["episode"]:
                                saved.update(error=str(exc), error_code=exc.code)
                        write_queue(root, rows)
                        logger("studio").warning("Vertonung aus der Warteschlange nicht gestartet (%s): %s", exc.code, exc)
                        continue
                    started.append((project, row["episode"]))
                    logger("studio").info("Vertonung aus der Warteschlange gestartet: %s %s", project, row["episode"])
        return started

    def due_resumes(self, now_seconds=None):
        """Paused main jobs whose automatic resume is due (auto_resume): a quota wait past its named reset, a
        transient technical stop past its pause, either with automatic resumes left."""
        current = time.time() if now_seconds is None else now_seconds
        due, seen = [], set()
        for path in sorted([*self.projects.glob("*/studio/job.json"), *self.projects.glob("*/studio/paused_*.json")]):
            job = read_json(path)
            if not isinstance(job, dict):
                continue
            plan = auto_resume(job)
            at = parse_iso(plan[1]) if plan and plan[1] else None
            if at is None or at.timestamp() > current:
                continue
            run_id, project = job["run"]["run_id"], path.parents[1].name
            if (project, run_id) in seen:
                continue
            seen.add((project, run_id))
            count = job.get("auto_resume_count", 0)
            due.append((project, run_id, count if type(count) is int and count >= 0 else 0))
        return due

    def resume_due(self, now_seconds=None):
        started = []
        for project, run_id, count in self.due_resumes(now_seconds):
            with self.mutex:
                try:
                    if not self.ready_to_start(self.root(project)):
                        continue  # Due again on the next pass, once the project and a place are free.
                    self.start(project, {"action": "resume", "run_id": run_id, "auto_resume_count": count + 1})
                    started.append(project)
                    logger("studio").info("Auftrag automatisch fortgesetzt (Versuch %s): %s", count + 1, project)
                except AppError as exc:
                    logger("studio").warning("Automatische Fortsetzung nicht möglich (%s): %s", exc.code, exc)
        return started

    def ready_to_start(self, root, gpu=False):
        """Whether a main job of this project could start now by start's own rules (no restart pending, the project
        idle and unlocked, a place free), checked before the scheduler commits to anything."""
        if self.restarting:
            return False
        try:
            self.idle(root)
        except AppError:
            return False
        workers = self.active_workers()
        return len(workers) < MAX_PROJECT_JOBS and not (gpu and any(uses_gpu for _, uses_gpu in workers.values()))

    def stopped_jobs(self, root):
        """The project's stopped main job and its parked jobs, as saved (job.json first)."""
        rows, seen = [], set()
        for path in [root / "studio/job.json", *parked_paths(root)]:
            job = read_json(path, {}) or {}
            run_id = (job.get("run") or {}).get("run_id")
            if job.get("status") == "blocked" and run_id and run_id not in seen:
                seen.add(run_id)
                rows.append(job)
        return rows

    def start_scheduler(self):
        if self.scheduler is None:
            self.scheduler = threading.Thread(target=self._schedule_loop, daemon=True)
            self.scheduler.start()

    def apply_allowances(self):
        """Stopped runs an allowance covers, in job.json or parked: write its approval and resume (studio_allowances).
        The project must be ready to start first, so an approval is never spent on a resume that cannot start; one
        granted whose resume still failed is resumed on a later pass without granting again (2026-10-02: a Gemini
        recording held the project, the allowance was used up and the run never resumed)."""
        started = []
        for path in sorted(self.projects.glob("*/studio/allowances.json")):
            root, project = path.parents[1], path.parents[1].name
            with self.mutex:
                for job in self.stopped_jobs(root):
                    code = stop_code(job)[0]
                    granted = studio_allowances.awaiting_resume(root, job)
                    if not granted and studio_allowances.pending(root, job, code) is None:
                        continue
                    if not self.ready_to_start(root):
                        break
                    if not granted and not studio_allowances.apply(root, job, code):
                        continue
                    try:
                        self.start(project, {"action": "resume", "run_id": job["run"]["run_id"]})
                    except AppError as exc:
                        logger("studio").warning("Fortsetzen nach Vorab-Erlaubnis nicht möglich (%s): %s", exc.code, exc)
                        break
                    studio_allowances.mark_resumed(root, job.get("id"))
                    started.append(project)
                    logger("studio").info("Vorab-Erlaubnis angewendet und fortgesetzt: %s", project)
                    break
        return started

    def server_state(self):
        return {"instance": self.instance, "stale": code_fingerprint() != self.code_stamp,
                "restart_requested": self.restart_requested, "code_updated_at": code_updated_at(),
                # Today's calls against CORE's daily allowance, once the user has set a key (sources.core_usage).
                "core": core_usage() if os.environ.get("PLA_CORE_API_KEY") else None}

    def request_restart(self, data):
        """Restart once nothing runs, so the scheduler and every new job use the current code."""
        if self.restart_hook is None:
            raise AppError("Dieses Studio kann sich nicht selbst neu starten.", code="restart_unavailable")
        self.restart_requested = data.get("cancel") is not True
        return {"server": self.server_state()}

    def restart_when_idle(self):
        with self.mutex:
            if (not self.restart_requested or self.restarting or self.restart_hook is None
                    or self.active_workers() or self.active_audio()):
                return False
            # From here on no job starts (start refuses), so the check above stays true until the server stops.
            self.restarting = True
        logger("studio").info("Studio startet mit dem aktuellen Code neu; kein Auftrag läuft.")
        self.restart_hook()
        return True

    def _schedule_loop(self):
        while True:
            time.sleep(SCHEDULER_INTERVAL_SECONDS)
            for step, failure in ((self.resume_due, "Automatische Fortsetzung übersprungen."),
                                  (self.apply_allowances, "Vorab-Erlaubnisse übersprungen."),
                                  (self.start_queued, "Warteschlange der Vertonung übersprungen."),
                                  (self.restart_when_idle, "Neustart übersprungen.")):
                try:
                    step()
                except Exception:  # The scheduler must outlive any single project's trouble.
                    logger("studio").warning(failure, exc_info=True)

    def active_audio(self):
        return {key: value for key, value in self.audio_processes.items() if value[0].poll() is None}

    def active_workers(self):
        return {root: value for root, value in self.workers.items() if value[0].poll() is None}

    def worker(self, root):
        """This server's running main-job process for the project, or None."""
        value = self.active_workers().get(root)
        return value[0] if value else None

    def own_text_job(self, root):
        """True while this server's worker for the project runs a job that never writes exports."""
        if self.worker(root) is None:
            return False
        job = read_json(root / "studio/job.json", {}) or {}
        return job.get("action") in TEXT_ACTIONS or (
            job.get("action") == "resume" and (job.get("run") or {}).get("kind") in {"research", "script"})

    def diagnostic(self, project, relative):
        """A failure receipt, worker output or run report the Studio names in a stop message, as plain text."""
        root = self.root(project)
        if not isinstance(relative, str) or not DIAGNOSTIC_FILES.fullmatch(relative):
            raise AppError("Diese Datei kann das Studio nicht anzeigen.", code="not_found")
        path = inside(root, relative)
        if not path.is_file() or path.stat().st_size > 2_000_000:
            raise AppError("Datei nicht gefunden.", code="not_found")
        text = path.read_text(encoding="utf-8", errors="replace")
        return text.replace(self.key, "[Key verborgen]") if self.key else text

    def audio_jobs(self, root):
        latest = {}
        for path in (root / "studio/audio_jobs").glob("*.json"):
            job = self.job(root, audio_job_id=path.stem)
            episode = job["episode"]
            if episode not in latest or job["started_at"] > latest[episode]["started_at"]:
                latest[episode] = job
        return sorted(latest.values(), key=lambda job: job["episode"])

    def overview(self):
        """Every project as a card. A card is built from cheap reads (card), not from the project page's detail."""
        projects = []
        for item in self.project_list():
            try:
                projects.append(self.card(item["id"]))
            except (AppError, ValueError, OSError, KeyError):
                projects.append({**item, "unavailable": True, "episodes": []})
        trash = []
        for receipt in (self.workspace / ".studio/trash").glob("*/receipt.json"):
            item = read_json(receipt, {})
            if (receipt.parent / "project/project.yaml").is_file():
                trash.append({"id": receipt.parent.name, "topic": item.get("topic", "Projekt"), "deleted_at": item.get("deleted_at")})
        return {"projects": projects, "trash": trash, "server": self.server_state()}

    def card(self, project):
        """What the overview shows of a project: its job in the light view, its recordings and the step it reached.
        2026-10-02: each poll built every project's full page instead, about 65 MB read and 0.6 s with the mutex held."""
        root = self.root(project)
        config = load_project(root)
        audio = selected_audio(root, config)
        jobs = self.audio_jobs(root)
        main = self.job(root, light=True)
        job = project_job(main, jobs, self.parked_job(root, light=True))
        episodes = self.episode_rows(root, config, audio, full=False)
        planned = self.outline_episodes(root)
        return {"id": project, "topic": config.topic, "config_hash": project_hash(config), "job": overview_job(job),
                "audio_jobs": jobs, "has_research": self.has_research(root), "has_outline": planned is not None,
                "episode_count": len({e["script"]["episode_id"] for e in episodes} | set(planned or ())),
                "script_count": len(episodes),
                "episodes": [{"episode_id": e["script"]["episode_id"], "title": e["script"]["title"],
                              "audio": e["audio"], "audio_current": e["audio_current"]} for e in episodes]}

    @staticmethod
    def has_research(root):
        for name in ("research/research_briefing.md", "research/dossier.yaml"):
            try:
                if (root / name).stat().st_size:
                    return True
            except OSError:
                continue
        return False

    @staticmethod
    def research_text(root):
        briefing, dossier = root / "research/research_briefing.md", root / "research/dossier.yaml"

        def compute():
            if briefing.is_file():
                return briefing.read_text(encoding="utf-8")
            if dossier.exists():
                return dossier.read_text(encoding="utf-8")
            return None
        return memo(("research_text", str(root)), [briefing, dossier], compute)

    @staticmethod
    def outline_work(root):
        """The run folder of the current outline, or None without one. The pointer's run holds an outline only while its
        planning stage is completed: a revision or a new draft keeps the replaced plan in series_plan.json until it
        finishes. 2026-10-02: a revision stopped at the output limit kept that plan on the page as if it were current."""
        pointer = read_json(root / "studio/outline.json")
        if not pointer:
            return None
        path = manifest_path(root, pointer["run_id"])
        if not path.is_file() or not (path.parent / "series_plan.json").exists():
            return None
        planning = memo(("outline_planning", str(path)), [path],
                        lambda: ((read_yaml(path) or {}).get("stages") or {}).get("planning") or {})
        return path.parent if planning.get("status") == "completed" else None

    @staticmethod
    def outline_episodes(root):
        """The episode ids of the current outline, or None without one."""
        work = Studio.outline_work(root)
        if work is None:
            return None
        plan = work / "series_plan.json"
        return memo(("outline_episodes", str(plan)), [plan],
                    lambda: [e["episode_id"] for e in (read_json(plan, {}) or {}).get("episodes", [])])

    @staticmethod
    def outline_hash(work):
        """outline_hash, computed again only after one of the files it hashes changed (inputs.json alone is 12-16 MB)."""
        return memo(("outline_hash", str(work)), [work / name for name in
                    ("series_plan.json", "knowledge_model.json", "inputs.json", "script_request.json")],
                    lambda: outline_hash(work))

    def episode_rows(self, root, config, audio, *, full=True):
        """The published episodes; the parts read from the script and its decision are kept while those files are
        unchanged (episode_view). ``full=False`` is the card's view."""
        table = load_forms(root)
        table_key = digest(table.model_dump())
        quality_path = root / "reports/script_quality.yaml"
        reported = None
        if full:
            quality = memo(("quality", str(quality_path)), [quality_path],
                           lambda: read_yaml(quality_path) if quality_path.is_file() else {})
            reported = quality.get("episodes") if isinstance(quality, dict) else None
        rows = []
        for folder in sorted((root / "episodes").glob("ep_*")):
            if not (folder / "script.yaml").exists():
                continue
            row = dict(episode_view(folder, table, table_key, config.language))
            report = read_json(folder / "audio_latest.json", {})
            row["audio"] = [part["audio"] for part in report.get("parts", []) if inside(root, part["audio"]).is_file()]
            row["audio_current"] = (report.get("script_sha256") == row["hash"] and report.get("voices") == audio.voices
                and same_audio_generation(report.get("audio_generation",
                    {"provider": "qwen3_local", "voices": report.get("voices")}), audio.model_dump()))
            if full:
                row["review_notes"] = review_notes((reported or {}).get(folder.name))
                row["expression"] = reading_expression(root, folder.name, row["hash"])
            rows.append(row)
        return rows

    def delete(self, project, data):
        root = self.root(project)
        if data.get("confirm_id") != project:
            raise AppError("Das Löschen dieses Projekts bitte ausdrücklich bestätigen.", code="delete_confirmation")
        if self.worker(root) is not None or any(value[1] == root for value in self.active_audio().values()):
            raise AppError("Laufende Aufträge dieses Projekts zuerst abschließen oder anhalten.", code="project_busy")
        with project_lock(root):
            config = load_project(root)
            if data.get("config_hash") != project_hash(config):
                raise AppError("Projekt inzwischen geändert. Übersicht neu laden.", code="inputs_changed")
            trash_id = uuid.uuid4().hex
            destination = inside(self.workspace / ".studio/trash", trash_id)
            content = destination / "project"
            content.mkdir(parents=True)
            write_json(destination / "receipt.json", {"project": project, "topic": config.topic, "deleted_at": now()})
            try:
                # Keep the open OS lock in place on Windows; move every user
                # artifact into the local trash while CLI writers remain locked out.
                move_contents(root, content, exclude={".pla.lock"})
            except AppError as exc:
                if exc.code == "project_files_locked":
                    content.rmdir()
                    (destination / "receipt.json").unlink()
                    destination.rmdir()
                raise
        return {"deleted": True, "trash_id": trash_id}

    def restore(self, data):
        trash_id = data.get("trash_id")
        if not isinstance(trash_id, str) or not re.fullmatch(r"[a-f0-9]{32}", trash_id):
            raise AppError("Ungültiger Papierkorbeintrag.", code="invalid_project")
        destination = inside(self.workspace / ".studio/trash", trash_id)
        receipt = read_json(destination / "receipt.json", {})
        root = inside(self.projects, identifier(receipt.get("project")))
        content = destination / "project"
        if not (content / "project.yaml").is_file():
            raise AppError("Projekt nicht im Papierkorb gefunden.", code="unknown_project")
        with project_lock(root):
            if has_artifacts(root):
                raise AppError("Am ursprünglichen Speicherort liegt bereits ein Projekt.", code="project_exists")
            move_contents(content, root)
        return {"id": root.name, "restored": True}

    def detail(self, project):
        root = self.root(project)
        config = load_project(root)
        audio = selected_audio(root, config)
        execution = settings_execution(root)
        jobs = self.audio_jobs(root)
        main = self.job(root)
        parked = self.parked_jobs(root)
        latest_job = project_job(main, jobs, parked[0] if parked else None)
        chat_budget = read_json(root / "studio/assistant/budget.json", {}) or {}
        active_audio = self.active_audio()
        active_here = sum(value[1] == root for value in active_audio.values())
        limit = MAX_PARALLEL if execution.audio == "parallel" and audio.remote else 1
        table = load_forms(root)
        data = {"id": project, "config": config.model_dump(mode="json"),
                "config_hash": project_hash(config), "host_labels": host_labels(config),
                "text": read_json(root / "studio/text.json", TextChoice().model_dump()),
                "audio_settings": audio.model_dump(), "audio_hash": digest(audio.model_dump()),
                "style_notes": style_notes(root), "style_notes_hash": digest(style_notes(root)),
                "spoken_forms": table.model_dump(),
                "spoken_forms_hash": digest(table.model_dump()),
                "voice_samples": self.voice_samples(),
                "execution": execution.model_dump(), "execution_hash": digest(execution.model_dump()),
                "jev_probe": jev_probe_enabled(root), "jev_default": jev_probe_state(root)[1],
                "allowances": studio_allowances.summary(root, ((latest_job or {}).get("run") or {}).get("run_id")),
                "server": self.server_state(),
                "audio_queue": queue_view(read_queue(root), self.key_available()),
                # When the tags a Gemini recording speaks are placed for reading (studio_worker.tag_episodes).
                "expression_progress": read_json(root / "studio/expression/progress.json", None),
                "audio_jobs": jobs, "audio_capacity": {"limit": limit, "active": active_here,
                    "available": max(0, min(limit - active_here, MAX_PARALLEL - len(active_audio)))},
                "chat": read_json(root / "studio/chat.json", []), "job": latest_job, "main_job": main,
                # Every paused run of another lane, so each lane's page keeps its own stop card.
                "parked_jobs": parked,
                "chat_budget": {"used": chat_budget.get("model_calls", 0),
                                "limit": chat_limits(root, config.research_limits).model_calls},
                "attachments": attachments.inventory(root),
                # The books and articles blocked questions lack, and the copies the editor provided.
                "works": self.works(root),
                "outline": None, "episodes": [], "research": None, "run": None}
        proposal = next((item for item in reversed(data["chat"]) if item.get("role") == "assistant"), None)
        data["proposal_hash"] = digest(proposal) if proposal else None
        data["proposal_current"] = attachments.proposal_current(root, proposal)
        applied = read_json(root / "studio/applied_proposal.json", {})
        data["proposal_applied"] = bool(proposal and data["proposal_current"] and all(applied.get(key) == data[key] for key in
            ("proposal_hash", "config_hash", "audio_hash", "execution_hash")) and applied.get("text_hash") == digest(data["text"]))
        work = self.outline_work(root)
        if work is not None:
            data["outline"] = {"run_id": work.name, "plan": read_json(work / "series_plan.json"),
                               "hash": self.outline_hash(work), "approval": read_json(work / "plan_approval.json")}
        data["research"] = self.research_text(root)
        if (root / "runs/latest.json").exists():
            data["run"] = read_yaml(manifest_path(root))
        data["episodes"] = self.episode_rows(root, config, audio)
        run = (main or {}).get("run") or data.get("run")
        data["script_previews"] = script_previews(root, run)
        return data

    @staticmethod
    def works(root):
        """provided_works.overview, read again only after a ledger, the newest run's state or the works changed."""
        ledgers = sorted(root.glob("runs/run_*/research_questions.json"))
        files = [*ledgers, *(path.parent / "question_research/state.json" for path in ledgers[-1:]),
                 root / provided_works.MANIFEST]
        return memo(("works", str(root), tuple(map(str, ledgers))), files,
                    lambda: provided_works.overview(root, provided_works.latest_ledger(root)))

    def idle(self, root=None):
        """No job of this project runs, and nobody holds its lock; without a project, no job runs at all."""
        audio = self.active_audio().values()
        busy = (self.worker(root) is not None or any(value[1] == root for value in audio)) if root else (
            bool(self.active_workers()) or bool(audio))
        if busy:
            raise AppError("In diesem Projekt läuft bereits ein Auftrag. Erst fertigstellen oder anhalten.", code="project_busy")
        if root:
            with project_lock(root):
                pass

    def create(self, data):
        config = TopicBrief.model_validate(data["config"])
        choice = TextChoice.model_validate(data.get("text", {}))
        choice.kwargs()
        if choice.provider == "openrouter":
            from .openrouter import OpenRouterAdapter
            OpenRouterAdapter(config.runtime, model=choice.kwargs()["model"], api_key=self.key or None,
                              max_output_tokens=choice.max_output_tokens, reasoning_effort=choice.reasoning_effort)
        slug = re.sub(r"[^a-z0-9]+", "-", config.topic.lower()).strip("-")[:40] or "podcast"
        slug += "-" + uuid.uuid4().hex[:6]
        root = inside(self.projects, slug)
        # Runtime/executable paths come only from the local server, never a web form or LLM; so do local sources
        # outside the project (kept_local_sources). A new project has none on disk yet.
        config.runtime = self.runtime()
        config.local_sources = kept_local_sources(root, [], config.local_sources)
        self.validate_voices(config)
        audio = AudioChoice.model_validate(data.get("audio_settings", {"voices": config.voice_profile}))
        execution = ExecutionChoice.model_validate(data.get("execution", {}))
        init_project(root, config)
        write_json(root / "studio/text.json", choice.normalized())
        write_json(root / "studio/audio.json", audio.model_dump())
        write_json(root / "studio/execution.json", execution.model_dump())
        default_jev_probe(root, config.language)
        return {"id": slug}

    @staticmethod
    def validate_voices(config):
        if any(v not in VOICES for v in config.voice_profile.values()):
            raise AppError("Bitte eine verfügbare Qwen-Stimme wählen.", code="invalid_voice")

    def save_text(self, root, data):
        choice = TextChoice.model_validate(data)
        choice.kwargs()
        if choice.provider == "openrouter":
            from .openrouter import OpenRouterAdapter
            OpenRouterAdapter(load_project(root).runtime, model=choice.kwargs()["model"], api_key=self.key or None,
                              max_output_tokens=choice.max_output_tokens, reasoning_effort=choice.reasoning_effort)
        write_json(root / "studio/text.json", choice.normalized())

    def save(self, project, data):
        root = self.root(project)
        self.idle(root)
        with project_lock(root):
            old = load_project(root)
            if data.get("config_hash") != project_hash(old):
                raise AppError("Projekt wurde inzwischen geändert. Ansicht neu laden.", code="inputs_changed")
            config = TopicBrief.model_validate(data["config"])
            config.runtime = old.runtime
            config.local_sources = kept_local_sources(root, old.local_sources, config.local_sources)
            self.validate_voices(config)
            current_audio = selected_audio(root, old)
            if "audio_settings" in data and data.get("audio_hash") != digest(current_audio.model_dump()):
                raise AppError("Audioauswahl inzwischen geändert. Ansicht neu laden.", code="inputs_changed")
            audio = AudioChoice.model_validate(data.get("audio_settings", current_audio.model_dump()))
            execution = settings_execution(root)
            if "execution" in data:
                if data.get("execution_hash") != digest(execution.model_dump()):
                    raise AppError("Ausführungsmodus inzwischen geändert. Ansicht neu laden.", code="inputs_changed")
                # The Jev probe has its own switch (jev_probe); a proposal or settings save never sets it.
                execution = ExecutionChoice.model_validate(data["execution"]).model_copy(update={"jev_probe": False})
            if "style_notes" in data:
                notes = data["style_notes"]
                if not isinstance(notes, str) or len(notes) > 20000:
                    raise AppError("Redaktionelle Notizen sind zu lang.", code="invalid_request")
                if data.get("style_notes_hash") != digest(style_notes(root)):
                    raise AppError("Notizen inzwischen geändert. Ansicht neu laden.", code="inputs_changed")
                atomic_text(root / "style_notes.md", notes.strip() + "\n" if notes.strip() else "")
            forms = None
            if "spoken_forms" in data:
                if data.get("spoken_forms_hash") != digest(load_forms(root).model_dump()):
                    raise AppError("Sprechformen inzwischen geändert. Ansicht neu laden.", code="inputs_changed")
                forms = SpokenForms.model_validate(data["spoken_forms"])
            self.save_text(root, data["text"])
            write_yaml(root / "project.yaml", config.model_dump(mode="json"))
            write_json(root / "studio/audio.json", audio.model_dump())
            write_json(root / "studio/execution.json", execution.model_dump())
            if forms is not None:
                # Only a request that carried the table writes it; a notes or voice save leaves it alone.
                write_json(root / "studio/spoken_forms.json", forms.model_dump())
        return {"saved": True}

    def production(self, project, run_id):
        """Calls, time, stops and approvals of one research or script run (production_report)."""
        root = self.root(project)
        work = manifest_path(root, run_id).parent
        manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml")) if (work / "run_manifest.yaml").is_file() else None
        if manifest is None or manifest.kind not in {"research", "script"}:
            raise AppError("Einen Recherche- oder Skriptlauf wählen.", code="no_run")
        return {"run_id": run_id, "kind": manifest.kind,
                "report": production_report(work, allowance_rows=studio_allowances.allowance_log(root))}

    def allowances(self, project, data):
        """Fresh attempts and extra calls the scheduler may grant this project's runs without asking."""
        return {"allowances": studio_allowances.set_allowances(self.root(project), data)}

    def jev_probe(self, project, data):
        """Whether new script runs of this project also ask Jev in the gap probe (jev.py). A running job keeps its
        choice; the switch applies from the next new script run."""
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise AppError("Jev-Lückenprobe ein- oder ausschalten.", code="invalid_request")
        set_jev_probe(self.root(project), enabled)
        return {"jev_probe": enabled}

    def spoken_override(self, project, data):
        """A per-segment spoken form. It changes only how a segment is read aloud."""
        root = self.root(project)
        episode, segment_id = data.get("episode"), data.get("segment_id")
        spoken = data.get("spoken", "")
        if not isinstance(episode, str) or not re.fullmatch(r"ep_[a-z0-9_]+", episode):
            raise AppError("Unbekannte Folge.", code="invalid_request")
        if not isinstance(segment_id, str) or not isinstance(spoken, str) or len(spoken) > 4000:
            raise AppError("Ungültige Sprechform.", code="invalid_request")
        folder = inside(root / "episodes", episode)
        script_file = folder / "script.yaml"
        if not script_file.is_file():
            raise AppError("Für diese Folge gibt es noch keinen veröffentlichten Text.", code="unknown_episode")
        script = EpisodeScript.model_validate(read_yaml(script_file))
        segment = next((s for s in script.segments if s.segment_id == segment_id), None)
        if segment is None:
            raise AppError("Dieser Abschnitt kommt in der Folge nicht vor.", code="invalid_request")
        with self.decision_lock(root, folder):
            decision = read_yaml(folder / "audio_review.yaml") if (folder / "audio_review.yaml").is_file() else {}
            overrides = dict(decision.get("spoken_overrides") or {})
            # The text the table already produces is not an override; storing it would freeze the
            # segment against later table changes without changing what is heard today.
            if spoken.strip() and spoken.strip() != apply_spoken_forms(segment.text, load_forms(root)):
                overrides[segment_id] = spoken.strip()
            else:
                overrides.pop(segment_id, None)
            write_yaml(folder / "audio_review.yaml", {**decision, "spoken_overrides": overrides})
        return {"episode": episode, "spoken_overrides": overrides}

    @staticmethod
    @contextmanager
    def decision_lock(root, folder):
        """The locks an episode's audio decision is written under: the project's shared lock, beside the recordings of
        other episodes, and the episode's own recording lock, which a recording of this episode holds throughout
        (episode_audio). 2026-10-02: the exclusive project lock refused every listening review and spoken form while
        any episode was being recorded."""
        with project_lock(root, shared=True), ExitStack() as locks:
            try:
                locks.enter_context(file_lock(folder / ".audio.lock"))
            except AppError as exc:
                if exc.code != "project_busy":
                    raise
                raise AppError("Diese Folge wird gerade vertont. Die Eingabe geht, sobald ihre Vertonung fertig ist "
                               "oder angehalten wurde.", code="episode_busy") from None
            yield

    def listening_review(self, project, data):
        """Record that a human listened, with their note. Nothing sets this automatically."""
        root = self.root(project)
        episode, note = data.get("episode"), data.get("note", "")
        if not isinstance(episode, str) or not re.fullmatch(r"ep_[a-z0-9_]+", episode):
            raise AppError("Unbekannte Folge.", code="invalid_request")
        if not isinstance(note, str) or len(note) > 4000 or not isinstance(data.get("reviewed"), bool):
            raise AppError("Ungültige Hörprüfung.", code="invalid_request")
        folder = inside(root / "episodes", episode)
        if not (folder / "audio_review.yaml").is_file():
            raise AppError("Für diese Folge gibt es noch keine Audioentscheidung.", code="unknown_episode")
        with self.decision_lock(root, folder):
            decision = read_yaml(folder / "audio_review.yaml")
            write_yaml(folder / "audio_review.yaml", {**decision, "human_listening_reviewed": data["reviewed"],
                                                     "listening_note": note.strip()})
        return {"episode": episode, "human_listening_reviewed": data["reviewed"]}

    def apply_proposal(self, project, data):
        root = self.root(project)
        self.idle(root)
        proposal = next((item for item in reversed(read_json(root / "studio/chat.json", []))
                         if item.get("role") == "assistant"), None)
        if not proposal or data.get("proposal_hash") != digest(proposal):
            raise AppError("Der Vorschlag hat sich geändert. Bitte die aktuelle Zusammenfassung prüfen.", code="proposal_changed")
        if not attachments.proposal_current(root, proposal):
            raise AppError("Die Anhänge haben sich geändert. Bitte den Partner die Zusammenfassung aktualisieren lassen.",
                           code="proposal_changed")
        chosen = BriefProposal.model_validate({key: value for key, value in proposal.items() if key != "role"})
        config = load_project(root).model_dump(mode="json")
        for key in ("topic", "central_question", "prior_knowledge", "depth_request", "focus_questions", "excluded_topics"):
            config[key] = getattr(chosen, key)
        for key in ("language", "seed_urls", "series_goal"):
            if getattr(chosen, key) is not None:
                config[key] = getattr(chosen, key)
        if chosen.recency_months is not None:
            if chosen.recency_months:
                config["recency_months"] = chosen.recency_months
            else:
                config.pop("recency_months", None)
        config["target_total_minutes"] = chosen.target_total_minutes
        text_choice = chosen.text or TextChoice.model_validate(read_json(root / "studio/text.json", {}))
        audio = chosen.audio_settings or selected_audio(root, load_project(root))
        execution = (chosen.execution or settings_execution(root)).model_copy(update={"jev_probe": False})
        if audio.provider == "qwen3_local":
            config["voice_profile"] = audio.voices
        result = self.save(project, {"config": config, "config_hash": data.get("config_hash"),
            "text": text_choice.model_dump(), "audio_settings": audio.model_dump(), "audio_hash": data.get("audio_hash"),
            "execution": execution.model_dump(), "execution_hash": data.get("execution_hash")})
        # The audio hash of the choice as saved: selected_audio adds the expression layer a Gemini choice records,
        # and the detail compares against exactly that.
        write_json(root / "studio/applied_proposal.json", {"proposal_hash": digest(proposal),
            "config_hash": project_hash(load_project(root)),
            "audio_hash": digest(selected_audio(root, load_project(root)).model_dump()),
            "execution_hash": digest(execution.model_dump()),
            "text_hash": digest(read_json(root / "studio/text.json"))})
        return result

    def upload(self, project, data):
        root = self.root(project)
        self.idle(root)
        with project_lock(root):
            rows = attachments.add(root, data.get("files"),
                                   secrets=(self.key, os.environ.get("OPENROUTER_API_KEY", "")))
        return {"attachments": rows}

    def upload_work(self, project, raw, query):
        """A copy of a published work the editor provides for blocked questions (provided_works). Unlike other
        project inputs it may arrive while a research run works: the files are written whole, and the run reads
        the work at its next pass, without the project lock its worker holds."""
        root = self.root(project)
        ledger = provided_works.latest_ledger(root) or {}
        known = {row.get("id") for row in ledger.get("questions") or []}
        tasks = [task for task in query.get("task", []) if task in known]
        row = provided_works.add(root, raw, citation=(query.get("citation") or [""])[0], tasks=tasks)
        return {"work": row, "works": provided_works.overview(root, ledger)}

    def remove_attachment(self, project, data):
        root = self.root(project)
        self.idle(root)
        with project_lock(root):
            rows = attachments.remove(root, data.get("id"))
        return {"attachments": rows}

    def start(self, project, data):
        root = self.root(project)
        if self.restarting:
            raise AppError("Das Studio startet gerade mit neuem Code neu. In einigen Sekunden erneut versuchen.",
                           code="studio_restarting")
        action = data.get("action")
        if action not in {"assistant", "research", "plan", "replan", "script", "revise", "audio", "audio_sample", "audio_samples",
                          "resume", "check", "expression"}:
            raise AppError("Unbekannter Arbeitsschritt.", code="invalid_action")
        payload = {"action": action, "message": str(data.get("message", ""))[:12000]}
        if action == "research" and data.get("seed_corpus") is True:
            payload["seed_corpus"] = True
        if action == "assistant" and data.get("text_preset") is not None:
            payload["requested_text"] = text_preset(data["text_preset"])
        if (self.key and self.key in payload["message"]) or re.search(r"sk-or-[A-Za-z0-9_-]{12,}", payload["message"]):
            raise AppError("Den OpenRouter-Key bitte über den geschützten Key-Eingang hinterlegen, nicht im Chat.", code="credential_in_prompt")
        remote_episode = None
        if action == "audio_sample":
            if data.get("voice") not in GEMINI_VOICES or data.get("language") not in {"de-DE", "en-US"}:
                raise AppError("Gemini-Stimme und Sprache für die Hörprobe auswählen.", code="invalid_voice")
            if data.get("approve_sample") is not True:
                raise AppError("Gemini-Hörprobe ausdrücklich erzeugen lassen.", code="audio_approval_required")
            payload.update(voice=data["voice"], language=data["language"])
        if action == "audio_samples":
            if data.get("language") not in {"de-DE", "en-US"}:
                raise AppError("Sprache für die Hörproben auswählen.", code="invalid_voice")
            if data.get("approve_samples") is not True:
                raise AppError("Fehlende Gemini-Hörproben ausdrücklich erzeugen lassen.", code="audio_approval_required")
            payload["language"] = data["language"]
        if action == "expression":
            # Inline audio tags for reading before approval (episode_audio.tag_episode); none named means every episode.
            episodes = data.get("episodes") or []
            if not isinstance(episodes, list) or any(not isinstance(e, str) or not re.fullmatch(r"ep_[a-z0-9_]+", e)
                                                     or not (root / "episodes" / e / "script.yaml").is_file() for e in episodes):
                raise AppError("Folgen mit veröffentlichtem Skript auswählen.", code="unknown_episode")
            payload["episodes"] = episodes
        if action in {"assistant", "replan", "revise"} and not payload["message"].strip():
            raise AppError("Bitte deinen Änderungswunsch eingeben.", code="missing_feedback")
        if action in {"replan", "script"}:
            # A stopped draft has no outline to revise or approve; the worker would only stop again (scripting.outline_revision).
            work = self.outline_work(root)
            if work is None:
                raise AppError("Zuerst das Inhaltsverzeichnis erstellen.", code="plan_required")
            payload["run_id"] = work.name
            payload["plan_hash"] = data.get("plan_hash")
            if action == "script" and payload["plan_hash"] != outline_hash(work):
                raise AppError("Bitte das aktuelle Inhaltsverzeichnis lesen und freigeben.", code="plan_changed")
        if action in {"audio", "revise"}:
            episode = data.get("episode")
            if not isinstance(episode, str) or not re.fullmatch(r"ep_[a-z0-9_]+", episode):
                raise AppError("Folge auswählen.", code="unknown_episode")
            payload["episode"] = episode
            if action == "audio":
                rerender = data.get("rerender") is True
                if not rerender and data.get("approve_audio") is not True:
                    raise AppError("Das gelesene Skript ausdrücklich für Audio freigeben.", code="audio_approval_required")
                for key, name in (("script_hash", "script.yaml"), ("readable_hash", "script.md")):
                    if data.get(key) != file_hash(root / "episodes" / episode / name):
                        raise AppError("Skript inzwischen geändert. Bitte erneut lesen.", code="script_edited")
                    payload[key] = data[key]
                payload["config_hash"] = data.get("config_hash")
                if payload["config_hash"] != project_hash(load_project(root)):
                    raise AppError("Stimmen oder Auftrag geändert. Bitte die aktuelle Ansicht erneut prüfen.", code="inputs_changed")
                audio = selected_audio(root, load_project(root))
                if data.get("audio_hash") != digest(audio.model_dump()):
                    raise AppError("Audioanbieter oder Stimmen geändert. Bitte erneut freigeben.", code="inputs_changed")
                if rerender:
                    # A re-render changes only spoken forms. It starts on the saved approval by the
                    # rule the pipeline applies; without one the reader must approve the text first.
                    if not saved_approval(root, episode, payload["script_hash"], audio_generation_record(audio)):
                        raise AppError("Für diesen Stand liegt keine Audio-Freigabe vor. Bitte auf der Audioseite freigeben.",
                                       code="audio_approval_required")
                    payload["rerender"] = True
                payload["audio_settings"] = audio.model_dump()
                payload["audio_hash"] = data["audio_hash"]
                if audio.remote and audio.expression:
                    # The approval covers the tags the reader saw with the script (tag_episode), or none. A re-render
                    # speaks them too, so it is bound to the tags on the reader's page as well (2026-10-02: after
                    # "Ausdruck neu setzen" a re-render spoke tags nobody had read).
                    tags_file = root / "episodes" / episode / "expression.json"
                    if data.get("expression_hash", "") != (file_hash(tags_file) if tags_file.is_file() else ""):
                        raise AppError("Der Ausdruck wurde seit dem Lesen neu gesetzt. Bitte das Skript mit den aktuellen "
                                       "Tags lesen und erneut freigeben.", code="script_edited")
                    payload["expression_hash"] = data.get("expression_hash", "")
                if audio.remote:
                    remote_episode = episode
        if action == "resume":
            job = self.job(root)
            run_id = data.get("run_id") or ((job or {}).get("run") or {}).get("run_id")
            if not run_id:
                raise AppError("Kein fortsetzbarer Lauf vorhanden.", code="no_run")
            path = manifest_path(root, run_id)
            saved_run = read_yaml(path) if path.is_file() else {}
            if saved_run.get("kind") == "episode_audio":
                saved_inputs = read_json(path.parent / "inputs.json")
                if saved_inputs.get("audio_generation", {}).get("provider") == "openrouter_gemini_tts":
                    remote_episode = saved_inputs["episode_id"]
            payload["run_id"] = run_id
        if remote_episode:
            active = self.active_audio()
            if any(value[1:] == (root, remote_episode) for value in active.values()):
                raise AppError("Diese Folge wird bereits vertont.", code="episode_busy")
            limit = MAX_PARALLEL if selected_execution(root).audio == "parallel" else 1
            busy = self.worker(root) is not None
            full = len(active) >= MAX_PARALLEL or sum(value[1] == root for value in active.values()) >= limit
            if (busy or full) and action == "audio" and not data.get("from_queue"):
                # A new approval waits its turn instead of being refused (start_queued starts it when a place is free).
                return self.enqueue_audio(root, remote_episode, data)
            if busy:
                raise AppError("Zuerst den laufenden Auftrag abschließen oder anhalten.", code="project_busy")
            if full:
                raise AppError(f"Alle {limit} Plätze für die Vertonung sind belegt. Eine laufende Folge fertigstellen oder anhalten.", code="audio_capacity")
            with project_lock(root, shared=True):
                pass
            payload["parallel_remote"] = True
        else:
            self.idle(root)
            # Outside the remote lane every audio job is local Qwen: a new one or a resumed one.
            gpu = action == "audio" or (action == "resume" and saved_run.get("kind") == "episode_audio")
            workers = self.active_workers()
            if len(workers) >= MAX_PROJECT_JOBS:
                raise AppError(f"In {MAX_PROJECT_JOBS} Projekten laufen bereits Aufträge. Einen davon fertigstellen "
                               "oder anhalten.", code="studio_capacity")
            if gpu and any(uses_gpu for _, uses_gpu in workers.values()):
                raise AppError("Qwen vertont gerade in einem anderen Projekt auf der Grafikkarte. Diese Vertonung "
                               "zuerst fertigstellen oder anhalten.", code="gpu_busy")
        payload["text"] = read_json(root / "studio/text.json", TextChoice().model_dump())
        payload["api_key"] = self.key or None
        job = {"id": uuid.uuid4().hex, "action": action, "status": "running", "started_at": now(), "run": None}
        if action == "resume":
            # A resume refused before its first stage keeps showing the paused run and its decisions.
            job["run"] = saved_run or None
        if action == "resume" and type(data.get("auto_resume_count")) is int:
            job["auto_resume_count"] = data["auto_resume_count"]
        if remote_episode:
            job.update(episode=remote_episode, provider="openrouter_gemini_tts")
            payload["audio_job_id"] = job["id"]
            if action == "audio":
                # Started now, whether from the queue or directly: it no longer waits.
                write_queue(root, [row for row in read_queue(root) if row.get("episode") != remote_episode])
        job_path = audio_job_path(root, job["id"]) if remote_episode else root / "studio/job.json"
        if not remote_episode:
            park(root, job)
        write_json(job_path, job)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        errors = self.stderr_file(root, job["id"])
        try:
            # A crash before the worker's own logging starts leaves its reason in this file.
            with errors.open("wb") as stderr:
                process = subprocess.Popen([sys.executable, "-m", "podcast_automate.studio_worker", str(root)],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr,
                    text=True, encoding="utf-8", env=env, start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if remote_episode:
                self.audio_processes[job["id"]] = (process, root, remote_episode)
            else:
                self.workers[root] = (process, gpu)
            record_worker(root, job["id"], process)
            process.stdin.write(json.dumps(payload, ensure_ascii=False))
            process.stdin.close()
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            job.update(status="failed", error_code="worker_start",
                       message=f"Auftrag konnte nicht gestartet werden ({reason}). Studio beenden und neu öffnen, "
                               "dann erneut versuchen; fertige Arbeit bleibt gespeichert.")
            write_json(job_path, job)
            raise AppError(job["message"], code="worker_start") from None
        return job

    def stderr_file(self, root, job_id):
        """The worker's error output file. Files of finished jobs older than an hour are removed with it: their logs,
        the worker records (record_worker) and the status monitors' locks a killed monitor left behind."""
        folder = root / "studio/stderr"
        folder.mkdir(parents=True, exist_ok=True)
        keep = {job_id, (read_json(root / "studio/job.json", {}) or {}).get("id"), *self.audio_processes}
        cutoff = time.time() - 3600
        studio = root / "studio"
        leftovers = [(old, old.stem) for old in folder.glob("*.log")]
        leftovers += [(old, old.stem) for old in (studio / "workers").glob("*.json")]
        leftovers += [(old, old.name[len(".status-"):-len(".lock")]) for old in studio.glob(".status-*.lock")]
        for old, owner in leftovers:
            try:
                if owner in keep or old.stat().st_mtime >= cutoff:
                    continue
                if old.parent.name == "workers" and re.fullmatch(r"[a-f0-9]{32}", owner) and (
                        read_json(audio_job_path(root, owner), {}) or {}).get("status") == "running":
                    continue  # A recording an earlier Studio started may still run; its record identifies it.
                old.unlink()
            except OSError:
                pass  # A file another worker still holds open goes next time.
        return folder / (job_id + ".log")

    def external_worker(self, root, path):
        """The running worker of a job an earlier Studio started, verified by pid and start time, or None."""
        data = read_json(path) or {}
        identity = worker_identity(root, data) if data.get("status") == "running" else None
        if not process_alive(identity):
            return None
        try:
            return ExternalWorker(identity["pid"])
        except Exception:  # psutil: the process ended in between, or may not be inspected.
            return None

    def stop(self, project, job_id=None):
        root = self.root(project)
        if job_id:
            owner = self.audio_processes.get(job_id)
            process = (owner[0] if owner[1] == root else None) if owner else \
                self.external_worker(root, audio_job_path(root, job_id))
            if owner is None and process is None:
                raise AppError("Audioauftrag nicht gefunden.", code="no_active_job")
        else:
            # A worker that outlived the Studio that started it is stopped as well, once its identity is verified.
            process = self.worker(root) or self.external_worker(root, root / "studio/job.json")
        if process is None or process.poll() is not None:
            raise AppError("Kein aktiver Studio-Auftrag für dieses Projekt.", code="no_active_job")
        stop_process_tree(process)
        return record_interruption(root, expected_job_id=job_id, audio_job_id=job_id)

    def stop_all(self):
        for job_id, (_, root, _) in list(self.active_audio().items()):
            self.stop(root.name, job_id)
        for root in list(self.active_workers()):
            self.stop(root.name)

    def media(self, project, relative):
        root = self.root(project)
        path = inside(root, relative)
        if path.suffix.lower() != ".mp3" or not relative.startswith(("exports/", "studio/samples/")) or not path.is_file():
            raise AppError("Audiodatei nicht gefunden.", code="missing_audio")
        return path


def client_scope(address, lan):
    """``local`` for this computer; ``lan`` for a device in the home network, only when the Studio was started
    for it (``pla studio --lan``); None for everything else, such as an address from the internet. The home
    network counts as this computer: the user asked for exactly that, with protection only against outside."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "local"
    return "lan" if lan and (ip.is_private or ip.is_link_local) else None


def lan_addresses():
    """This computer's addresses in the home network, the one on the default route first."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(("192.0.2.1", 9))  # Chooses the outgoing interface; a UDP connect sends nothing.
            found.append(route.getsockname()[0])
    except OSError:
        pass
    try:
        found += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    return [address for address in dict.fromkeys(found) if client_scope(address, True) == "lan"]


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "PodcastStudio/1.0"

    def log_message(self, *_):
        pass  # No credentials, chat text or local paths in request logs.

    def send_data(self, status, body, kind="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def guard(self, mutation=False):
        port = self.server.server_port
        scope = client_scope(self.client_address[0], self.server.studio.lan)
        if scope is None:
            raise AppError("Zugriff nur von diesem Computer oder, mit der WLAN-Startdatei, aus dem Heimnetz.", code="forbidden")
        # A device in the home network names this computer by the address it connected to. Any other name is
        # refused as before, which keeps a web page from steering the Studio through a rebound domain.
        allowed = ({f"127.0.0.1:{port}", f"localhost:{port}"} if scope == "local"
                   else {f"{self.connection.getsockname()[0]}:{port}"})
        if self.headers.get("Host") not in allowed:
            raise AppError("Zugriff nur über die lokale Studio-Adresse.", code="forbidden")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in allowed}:
            raise AppError("Fremde Webseiten dürfen das Studio nicht steuern.", code="forbidden")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise AppError("Zugriff von einer fremden Webseite abgewiesen.", code="forbidden")
        if mutation and not secrets.compare_digest(self.headers.get("X-Studio-Token", ""), self.server.studio.token):
            raise AppError("Studio-Sitzung neu laden.", code="forbidden")

    def drain(self):
        """Read the body a refused request already sent. On Windows, closing a socket with unread data resets
        the connection, and the browser sees an abort instead of the refusal it can act on (a stale session
        token renews itself only on the 403)."""
        remaining, self.unread = getattr(self, "unread", 0), 0
        if remaining <= 0:
            return
        previous = self.connection.gettimeout()
        try:
            # A client that sends less than it declared must not hold this thread.
            self.connection.settimeout(5)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.connection.settimeout(previous)

    def dispatch(self, mutation=False):
        app = self.server.studio
        try:
            path = unquote(urlsplit(self.path).path)
            work = re.fullmatch(r"/api/projects/([^/]+)/work", path)
            limit = (provided_works.MAX_WORK_BYTES if work else
                     attachments.MAX_BODY_BYTES if re.fullmatch(r"/api/projects/[^/]+/upload", path) else 128000)
            length = self.headers.get("Content-Length", "0")
            # Only a body within the endpoint's limit is ever read, also to refuse it; an oversized one never is.
            self.unread = int(length) if mutation and length.isdigit() and 0 < int(length) <= limit else 0
            self.guard(mutation)
            if mutation and work:
                # A book as raw bytes, its citation in the query. Only application/octet-stream is taken: like JSON
                # it needs the browser's preflight, so no other page can post a file here.
                if self.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream":
                    raise AppError("Datei als application/octet-stream erwartet.", code="invalid_request")
                size = int(length) if length.isdigit() else 0
                if not 0 < size <= limit:
                    raise AppError(f"Das Werk darf höchstens {limit // (1024 * 1024)} MB groß sein.", code="invalid_work")
                raw = self.rfile.read(size)
                self.unread = 0
                with app.mutex:
                    result = app.upload_work(work[1], raw, parse_qs(urlsplit(self.path).query))
            elif mutation:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise AppError("JSON-Anfrage erwartet.", code="invalid_request")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= limit:
                    raise AppError("Anfrage zu groß oder leer.", code="invalid_request")
                raw = self.rfile.read(size)
                self.unread = 0
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise AppError("Ungültige Anfrage.", code="invalid_request")
                with app.mutex:
                    if path == "/api/quit":
                        app.stop_all()
                        self.send_data(200, b'{"stopped":true}')
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                        return
                    elif path == "/api/key":
                        value = data.get("key", "")
                        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                            raise AppError("Ungültiger API-Key.", code="invalid_key")
                        app.key = value
                        result = {"key_available": bool(value or os.environ.get("OPENROUTER_API_KEY"))}
                    elif path == "/api/projects":
                        result = app.create(data)
                    elif path == "/api/server/restart":
                        result = app.request_restart(data)
                    elif path == "/api/restore":
                        result = app.restore(data)
                    else:
                        match = re.fullmatch(r"/api/projects/([^/]+)/(save|start|stop|apply_proposal|delete|upload"
                                             r"|remove_attachment|approve|spoken_override|listening_review|jev_probe"
                                             r"|audio_queue|allowances)", path)
                        if not match:
                            raise AppError("Seite nicht gefunden.", code="not_found")
                        project, action = match.groups()
                        result = app.stop(project, data.get("job_id")) if action == "stop" else getattr(app, action)(project, data)
            elif path in {"/", "/app.js", "/style.css"}:
                name = "index.html" if path == "/" else path[1:]
                body = files("podcast_automate").joinpath("web", name).read_bytes()
                self.send_data(200, body, {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[name] + "; charset=utf-8")
                return
            elif path == "/api/bootstrap":
                result = {**app.bootstrap(), "client": client_scope(self.client_address[0], app.lan)}
            elif path == "/api/projects":
                with app.mutex:
                    result = app.overview()
            elif (match := re.fullmatch(r"/api/projects/([^/]+)", path)):
                with app.mutex:
                    result = app.detail(match[1])
            elif (match := re.fullmatch(r"/api/projects/([^/]+)/report", path)):
                result = app.production(match[1], parse_qs(urlsplit(self.path).query).get("run_id", [None])[0])
            elif (match := re.fullmatch(r"/api/projects/([^/]+)/file", path)):
                relative = parse_qs(urlsplit(self.path).query).get("path", [None])[0]
                self.send_data(200, app.diagnostic(match[1], relative).encode("utf-8"), "text/plain; charset=utf-8")
                return
            elif (match := re.fullmatch(r"/media/([^/]+)/(.*)", path)):
                self.send_audio(app.media(match[1], match[2]))
                return
            elif (match := re.fullmatch(r"/download/([^/]+)/podcast.zip", path)):
                root = app.root(match[1])
                # The Studio's own research or script worker holds the project lock for hours but never
                # writes exports; published recordings are then read without the shared lock.
                with podcast_zip(root, locked=not app.own_text_job(root)) as (archive, filename):
                    self.send_file(archive, "application/zip", filename, allow_ranges=False)
                return
            elif (match := re.fullmatch(r"/download/([^/]+)/file/(.*)", path)):
                root = app.root(match[1])
                with project_lock(root, shared=True) if not app.own_text_job(root) else nullcontext():
                    recording = next((r for r in podcast_download(root, selected_path=match[2]).recordings if r.relative == match[2]), None)
                    if recording is None:
                        raise AppError("Aufnahme nicht mehr in der aktuellen Auswahl. Übersicht neu laden.", code="missing_audio")
                    self.send_file(recording.path, "audio/mpeg", recording.filename)
                return
            elif (match := re.fullmatch(r"/samples/(de-DE|en-US)/([a-z_]+)", path)):
                if match[2] not in {v.lower() for v in VOICES}:
                    raise AppError("Stimme nicht gefunden.", code="not_found")
                audio = inside(app.projects, f"voice-samples/{match[1]}/{match[2]}/audio.mp3")
                self.send_audio(audio)
                return
            elif (match := re.fullmatch(r"/samples/gemini/(de-DE|en-US)/([A-Za-z]+)", path)):
                audio = ready_sample(app.projects, match[2], match[1])
                if audio is None:
                    raise AppError("Diese Hörprobe wurde noch nicht erstellt.", code="not_found")
                self.send_audio(audio)
                return
            else:
                raise AppError("Seite nicht gefunden.", code="not_found")
            self.send_data(200, json.dumps(result, ensure_ascii=False).encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return  # A cancelled download still exits its context and cleans up.
        except (AppError, ValueError, KeyError, TypeError, OSError) as exc:
            code = exc.code if isinstance(exc, AppError) else "invalid_request"
            message = str(exc) if isinstance(exc, AppError) else "Daten konnten nicht verarbeitet werden. Eingaben prüfen und Ansicht neu laden."
            if app.key:
                message = message.replace(app.key, "[Key verborgen]")
            if not isinstance(exc, AppError):
                # Request bodies and paths stay out of the log; the traceback names the failing code.
                logger("studio").warning("Anfrage %s abgewiesen: %s", urlsplit(self.path).path, type(exc).__name__, exc_info=exc)
            self.drain()
            self.send_data(403 if code == "forbidden" else 404 if code == "not_found" else 400,
                           json.dumps({"error": message, "code": code}, ensure_ascii=False).encode("utf-8"))

    def send_audio(self, path):
        self.send_file(path, "audio/mpeg")

    def send_file(self, path, kind, filename=None, *, allow_ranges=True):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        headers = {"Accept-Ranges": "bytes" if allow_ranges else "none"}
        if filename:
            headers["Content-Disposition"] = disposition(filename)
        requested = self.headers.get("Range") if allow_ranges else None
        if requested:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
            if not match or int(match[1]) >= size:
                self.send_data(416, b"", extra={"Content-Range": f"bytes */{size}"})
                return
            start, end = int(match[1]), min(int(match[2]) if match[2] else end, end)
            if end < start:
                self.send_data(416, b"")
                return
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        self.send_response(status)
        for key, value in {"Content-Type": kind, "Content-Length": str(end - start + 1),
                           "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", **headers}.items():
            self.send_header(key, value)
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(True)


def make_server(workspace, port=8765, *, lan=False):
    """``lan`` listens on every interface, so devices in the home network reach the Studio (client_scope)."""
    server = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), StudioHandler)
    server.studio = Studio(Path(workspace))
    server.studio.lan, server.studio.port = lan, server.server_port
    server.daemon_threads = True
    return server


def serve(workspace, port=8765, open_browser=True, *, lan=False):
    url = f"http://127.0.0.1:{port}"
    # A second double-click reopens this workspace instead of starting another server.
    try:
        with urlopen(url + "/api/bootstrap", timeout=2) as response:
            existing = json.load(response)
        if isinstance(existing, dict) and existing.get("app") == "podcast-studio" and existing.get("workspace") == str(Path(workspace).resolve()):
            if lan and not (existing.get("lan") or {}).get("enabled"):
                print("Podcast Studio läuft bereits, aber nur auf diesem Computer. Zuerst im Studio „Studio beenden“ "
                      "klicken, sobald kein Auftrag mehr läuft, dann die WLAN-Startdatei erneut öffnen.", flush=True)
                return
            if open_browser:
                webbrowser.open(url)
            print(f"Podcast Studio läuft bereits: {url}", flush=True)
            return
    except (OSError, ValueError):
        pass
    with project_lock(Path(workspace) / ".studio"):
        configure_logging(Path(workspace) / ".studio/studio.log")
        server = make_server(workspace, port, lan=lan)
        # Called from the scheduler once nothing runs; shutdown returns serve_forever below.
        server.studio.restart_hook = server.shutdown
        server.studio.start_scheduler()
        url = f"http://127.0.0.1:{server.server_port}"
        logger("studio").info("Studio gestartet: %s%s", url, " (auch im Heimnetz)" if lan else "")
        print(f"Podcast Studio: {url}\nDieses Fenster geöffnet lassen. Beenden mit Strg+C.", flush=True)
        if lan:
            addresses = [f"http://{address}:{server.server_port}" for address in lan_addresses()]
            print("Im WLAN, etwa vom Handy: " + (" oder ".join(addresses) if addresses else
                  "keine Heimnetz-Adresse gefunden; die Adresse zeigt „ipconfig“ unter IPv4-Adresse"), flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        finally:
            with server.studio.mutex:
                app = server.studio
                app.stop_all()
            server.server_close()
    if server.studio.restarting:
        # The port and the workspace lock are free again; the new server takes both.
        relaunch(workspace, server.server_port, lan)
        print("Podcast Studio startet mit dem neuen Code im Hintergrund neu. Dieses Fenster kann geschlossen werden.",
              flush=True)
