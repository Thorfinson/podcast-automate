"""Local browser workspace. Reuses the production pipelines in isolated workers."""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from contextlib import nullcontext
from typing import Literal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, unquote
from urllib.request import urlopen

from pydantic import Field

from .errors import AppError
from . import attachments
from .execution import ExecutionChoice, MAX_PARALLEL, selected_execution
from .logs import configure_logging, logger
from .models import Contract, EpisodeScript, Failure, RunManifest, RuntimeSettings, TopicBrief, host_labels, now
from .episode_audio import saved_approval
from .runner import manifest_path
from .run_budget import approve_model_call_limit, approve_research_gap, approve_research_plan, approve_research_retry
from .subscriptions import parse_iso
from .scripting import outline_hash, script_metrics, style_notes
from .speech import (AudioChoice, GEMINI_VOICES, QWEN_VOICES, audio_catalog, audio_generation_record, selected_audio,
                     same_audio_generation)
from .storage import (atomic_text, digest, file_hash, init_project, inside, load_project, project_hash, project_lock,
                      read_yaml, write_json, write_yaml)
from .voice_samples import ready_sample, sample_inventory
from .spoken_forms import SpokenForms, apply as apply_spoken_forms, load_forms, report as pronunciation_report
from .studio_messages import clean, paths_only, user_text
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
# Empty credit does not come back by waiting, so the scheduler never resumes it.
NO_AUTO_RESUME = {"openrouter_credits"}
# Jobs that never write exports: while one of them holds the project lock, downloads read without it.
TEXT_ACTIONS = {"assistant", "research", "plan", "replan", "script", "revise", "check", "audio_sample", "audio_samples"}
# Names start with a word character, so no segment can be "..".
DIAGNOSTIC_FILES = re.compile(r"(runs/\w[\w.-]*/(?:\w[\w.-]*/)*\w[\w.-]*\.(?:txt|md|json)|studio/failures/\w[\w.-]*\.txt"
                              r"|studio/stderr/[a-f0-9]{32}\.log)")
CHAT_LIMIT_STEP = 50


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


def park(root, new_job):
    """Keep a paused text or audio job visible while a job of another lane uses studio/job.json."""
    current = read_json(root / "studio/job.json", {}) or {}
    parked = root / "studio/paused_job.json"
    if parked.exists():
        # A resume of the parked run continues it; a new job of the same lane supersedes it.
        old = read_json(parked, {}) or {}
        resumed = (new_job.get("action") == "resume"
                   and (new_job.get("run") or {}).get("run_id") == (old.get("run") or {}).get("run_id"))
        if resumed or (new_job.get("action") != "resume" and lane(new_job) is not None and lane(old) == lane(new_job)):
            parked.unlink()
    if (current.get("run") and current.get("status") in ATTENTION and lane(current)
            and lane(current) != lane(new_job) and not (new_job.get("action") == "resume"
                and (new_job.get("run") or {}).get("run_id") == current["run"].get("run_id"))):
        write_json(parked, current)


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
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


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

    def bootstrap(self):
        projects = []
        for path in sorted(self.projects.glob("*/project.yaml")):
            try:
                identifier(path.parent.name)
                config = load_project(path.parent)
                projects.append({"id": path.parent.name, "topic": config.topic})
            except (AppError, ValueError):
                continue
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
                "audio_catalog": audio_catalog(),
                "voice_samples": sample_inventory(self.projects),
                "key_available": bool(self.key or os.environ.get("OPENROUTER_API_KEY")),
                "defaults": TopicBrief(topic="Neues Podcast-Projekt", runtime=self.runtime(),
                                      voice_profile={"host_a": "Aiden", "host_b": "Vivian"}).model_dump(mode="json")}

    def job(self, root, *, audio_job_id=None, path=None):
        path = path or (audio_job_path(root, audio_job_id) if audio_job_id else root / "studio/job.json")
        data = read_json(path)
        if data and data["status"] == "running":
            owner = self.audio_processes.get(audio_job_id) if audio_job_id else None
            owned = (owner is not None and owner[1] == root and owner[0].poll() is None) if audio_job_id else (
                self.worker(root) is not None)
            if not owned:
                # The worker may have written its final result before poll() observed exit.
                data = read_json(path)
                if data["status"] == "running":
                    data = self.vanished(root, path, data, audio_job_id)
            run = data.get("run")
            if run:
                work = manifest_path(root, run["run_id"]).parent
                progress = read_json(work / "progress.json")
                nested_path = inside(root, progress["chapter_progress"]) if (progress or {}).get("chapter_progress") else None
                if owned:
                    # Research and script workers rewrite progress.json every few seconds; audio writes it per
                    # chapter and the chapter's own file per segment. The newest of them is the heartbeat.
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
            progress = safe_script_progress(root, data["run"], data.get("started_at") if data.get("status") == "running" else None)
            if progress:
                data["progress"] = progress
        if data and (data.get("run") or {}).get("kind") in {"script", "research"}:
            run = data["run"]
            work = manifest_path(root, run["run_id"]).parent
            request = "script_request.json" if run["kind"] == "script" else "research_request.json"
            data["text_generation"] = read_json(work / request, {}).get("text_generation")
            data["provider_choice"] = latest_provider_choice(work)
        if data and data.get("status") == "waiting_for_quota" and data.get("retry_at"):
            # Announced only where the scheduler acts: the project's main job with a run to resume.
            if (audio_job_id is None and (data.get("run") or {}).get("run_id")
                    and stop_code(data)[0] not in NO_AUTO_RESUME):
                if data.get("auto_resume_count", 0) < MAX_AUTO_RESUMES:
                    data["auto_resume_at"] = data["retry_at"]
                else:
                    data["auto_resume_exhausted"] = True
        return self.readable(root, data) if data else data

    def parked_job(self, root):
        """A paused job moved aside by a job of another lane, while its run is still open."""
        path = root / "studio/paused_job.json"
        data = read_json(path)
        run_id = ((data or {}).get("run") or {}).get("run_id")
        if not run_id or not manifest_path(root, run_id).is_file():
            return None
        if read_yaml(manifest_path(root, run_id)).get("status") == "completed":
            return None
        return self.job(root, path=path)

    def vanished(self, root, path, data, audio_job_id):
        """A job still marked running whose worker this server does not own: it ended without a result."""
        detail = worker_stderr(root, data)
        message = ("Der Arbeitsprozess wurde unerwartet beendet." if detail else
                   "Der Arbeitsprozess läuft nicht mehr, etwa nach einem Neustart des Studios.") + \
            " Fertige Arbeit bleibt gespeichert."
        try:
            # The exclusive lock proves that no worker of this project is left; only then is the run reset.
            return record_interruption(root, expected_job_id=data.get("id"), audio_job_id=audio_job_id,
                                       message=message, shared=False, crash_detail=detail)
        except (AppError, OSError, ValueError):
            data.update(status="interrupted", message=message)
            if detail:
                data["crash_detail"] = detail
            write_json(path, data)
            return data

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
        if kind == "plan":
            # The receipt binds to the projected plan; a cap asks the next resume to plan again and present anew.
            approval = approve_research_plan(root, run_id, max_tasks=data.get("max_tasks"), source="studio")
            return {"plan": approval.model_dump(mode="json")}
        raise AppError("Unbekannte Freigabe.", code="invalid_action")

    def due_resumes(self, now_seconds=None):
        """Paused jobs whose named reset has passed and whose automatic resumes are not used up."""
        current = time.time() if now_seconds is None else now_seconds
        due = []
        for path in sorted([*self.projects.glob("*/studio/job.json"), *self.projects.glob("*/studio/paused_job.json")]):
            job = read_json(path)
            if not isinstance(job, dict) or job.get("status") != "waiting_for_quota":
                continue
            retry = parse_iso(job.get("retry_at"))
            run_id = (job.get("run") or {}).get("run_id")
            count = job.get("auto_resume_count", 0)
            if (retry is None or retry.timestamp() > current or not run_id or count >= MAX_AUTO_RESUMES
                    or stop_code(job)[0] in NO_AUTO_RESUME):
                continue
            due.append((path.parents[1].name, run_id, count))
        return due

    def resume_due(self, now_seconds=None):
        started = []
        for project, run_id, count in self.due_resumes(now_seconds):
            with self.mutex:
                try:
                    self.start(project, {"action": "resume", "run_id": run_id, "auto_resume_count": count + 1})
                    started.append(project)
                    logger("studio").info("Auftrag nach Kontingent-Reset automatisch fortgesetzt: %s", project)
                except AppError as exc:
                    logger("studio").warning("Automatische Fortsetzung nicht möglich (%s): %s", exc.code, exc)
        return started

    def start_scheduler(self):
        if self.scheduler is None:
            self.scheduler = threading.Thread(target=self._schedule_loop, daemon=True)
            self.scheduler.start()

    def _schedule_loop(self):
        while True:
            time.sleep(SCHEDULER_INTERVAL_SECONDS)
            try:
                self.resume_due()
            except Exception:  # The scheduler must outlive any single project's trouble.
                logger("studio").warning("Automatische Fortsetzung übersprungen.", exc_info=True)

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
        projects = []
        for item in self.bootstrap()["projects"]:
            try:
                data = self.detail(item["id"])
                projects.append({"id": data["id"], "topic": data["config"]["topic"],
                    "config_hash": data["config_hash"], "job": data["job"], "audio_jobs": data["audio_jobs"],
                    "has_research": bool(data["research"]), "has_outline": bool(data["outline"]),
                    "episode_count": len({e["script"]["episode_id"] for e in data["episodes"]} |
                        {e["episode_id"] for e in (data["outline"] or {}).get("plan", {}).get("episodes", [])}),
                    "script_count": len(data["episodes"]), "episodes": [
                        {"episode_id": e["script"]["episode_id"], "title": e["script"]["title"],
                         "audio": e["audio"], "audio_current": e["audio_current"]} for e in data["episodes"]]})
            except (AppError, ValueError, OSError, KeyError):
                projects.append({**item, "unavailable": True, "episodes": []})
        trash = []
        for receipt in (self.workspace / ".studio/trash").glob("*/receipt.json"):
            item = read_json(receipt, {})
            if (receipt.parent / "project/project.yaml").is_file():
                trash.append({"id": receipt.parent.name, "topic": item.get("topic", "Projekt"), "deleted_at": item.get("deleted_at")})
        return {"projects": projects, "trash": trash}

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
        execution = selected_execution(root)
        jobs = self.audio_jobs(root)
        main = self.job(root)
        candidates = [job for job in [main, *jobs] if job]
        latest_job = max(candidates, key=lambda job: (job["status"] == "running", job.get("started_at", "")), default=None)
        # A running or paused job stays the project's job: a later audio job must not hide its decision,
        # and a parked run waits behind a finished chat, check or job of another lane.
        parked = self.parked_job(root)
        if main and (main["status"] == "running" or (main["status"] in ATTENTION and main.get("run"))):
            latest_job = main
        elif parked:
            latest_job = parked
        elif main and main["status"] in ATTENTION:
            latest_job = main
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
                "voice_samples": sample_inventory(self.projects),
                "execution": execution.model_dump(), "execution_hash": digest(execution.model_dump()),
                "audio_jobs": jobs, "audio_capacity": {"limit": limit, "active": active_here,
                    "available": max(0, min(limit - active_here, MAX_PARALLEL - len(active_audio)))},
                "chat": read_json(root / "studio/chat.json", []), "job": latest_job, "main_job": main,
                "chat_budget": {"used": chat_budget.get("model_calls", 0),
                                "limit": chat_limits(root, config.research_limits).model_calls},
                "attachments": attachments.inventory(root),
                "outline": None, "episodes": [], "research": None, "run": None}
        proposal = next((item for item in reversed(data["chat"]) if item.get("role") == "assistant"), None)
        data["proposal_hash"] = digest(proposal) if proposal else None
        data["proposal_current"] = attachments.proposal_current(root, proposal)
        applied = read_json(root / "studio/applied_proposal.json", {})
        data["proposal_applied"] = bool(proposal and data["proposal_current"] and all(applied.get(key) == data[key] for key in
            ("proposal_hash", "config_hash", "audio_hash", "execution_hash")) and applied.get("text_hash") == digest(data["text"]))
        pointer = read_json(root / "studio/outline.json")
        if pointer:
            work = manifest_path(root, pointer["run_id"]).parent
            if (work / "series_plan.json").exists():
                data["outline"] = {"run_id": pointer["run_id"], "plan": read_json(work / "series_plan.json"),
                                   "hash": outline_hash(work), "approval": read_json(work / "plan_approval.json")}
        if (root / "research/research_briefing.md").is_file():
            data["research"] = (root / "research/research_briefing.md").read_text(encoding="utf-8")
        elif (root / "research/dossier.yaml").exists():
            data["research"] = (root / "research/dossier.yaml").read_text(encoding="utf-8")
        if (root / "runs/latest.json").exists():
            data["run"] = read_yaml(manifest_path(root))
        quality = read_yaml(root / "reports/script_quality.yaml") if (root / "reports/script_quality.yaml").is_file() else {}
        reported = quality.get("episodes") if isinstance(quality, dict) else None
        for folder in sorted((root / "episodes").glob("ep_*")):
            if not (folder / "script.yaml").exists():
                continue
            script = EpisodeScript.model_validate(read_yaml(folder / "script.yaml"))
            report = read_json(folder / "audio_latest.json", {})
            # The audio decision, not the last run report, holds what the operator set by hand.
            decision = read_yaml(folder / "audio_review.yaml") if (folder / "audio_review.yaml").is_file() else {}
            overrides = {k: v for k, v in (decision.get("spoken_overrides") or {}).items()
                         if isinstance(k, str) and isinstance(v, str) and v.strip()}
            audio_paths = [part["audio"] for part in report.get("parts", [])
                           if inside(root, part["audio"]).is_file()]
            data["episodes"].append({"script": script.model_dump(), "hash": file_hash(folder / "script.yaml"),
                "readable_hash": file_hash(folder / "script.md"), "metrics": script_metrics(script),
                "audio": audio_paths, "audio_current": report.get("script_sha256") == file_hash(folder / "script.yaml")
                and report.get("voices") == audio.voices
                and same_audio_generation(report.get("audio_generation",
                    {"provider": "qwen3_local", "voices": report.get("voices")}), audio.model_dump()),
                "review_notes": review_notes((reported or {}).get(folder.name)),
                "spoken_overrides": overrides,
                # Computed from the published text, the table and the overrides, with no model
                # call, so a reader sees the difficult tokens before the first audio run.
                "pronunciation": pronunciation_report(script, table, language=config.language, overrides=overrides),
                "listening_note": decision.get("listening_note", ""),
                "human_listening_reviewed": bool(decision.get("human_listening_reviewed"))})
        run = (main or {}).get("run") or data.get("run")
        data["script_previews"] = script_previews(root, run)
        return data

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
        # Runtime/executable paths come only from the local server, never a web form or LLM.
        config.runtime = self.runtime()
        self.validate_voices(config)
        audio = AudioChoice.model_validate(data.get("audio_settings", {"voices": config.voice_profile}))
        execution = ExecutionChoice.model_validate(data.get("execution", {}))
        init_project(root, config)
        write_json(root / "studio/text.json", choice.normalized())
        write_json(root / "studio/audio.json", audio.model_dump())
        write_json(root / "studio/execution.json", execution.model_dump())
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
            self.validate_voices(config)
            current_audio = selected_audio(root, old)
            if "audio_settings" in data and data.get("audio_hash") != digest(current_audio.model_dump()):
                raise AppError("Audioauswahl inzwischen geändert. Ansicht neu laden.", code="inputs_changed")
            audio = AudioChoice.model_validate(data.get("audio_settings", current_audio.model_dump()))
            execution = selected_execution(root)
            if "execution" in data:
                if data.get("execution_hash") != digest(execution.model_dump()):
                    raise AppError("Ausführungsmodus inzwischen geändert. Ansicht neu laden.", code="inputs_changed")
                execution = ExecutionChoice.model_validate(data["execution"])
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
        with project_lock(root):
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
        with project_lock(root):
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
        for key in ("language", "seed_urls"):
            if getattr(chosen, key) is not None:
                config[key] = getattr(chosen, key)
        config["target_total_minutes"] = chosen.target_total_minutes
        text_choice = chosen.text or TextChoice.model_validate(read_json(root / "studio/text.json", {}))
        audio = chosen.audio_settings or selected_audio(root, load_project(root))
        execution = chosen.execution or selected_execution(root)
        if audio.provider == "qwen3_local":
            config["voice_profile"] = audio.voices
        result = self.save(project, {"config": config, "config_hash": data.get("config_hash"),
            "text": text_choice.model_dump(), "audio_settings": audio.model_dump(), "audio_hash": data.get("audio_hash"),
            "execution": execution.model_dump(), "execution_hash": data.get("execution_hash")})
        write_json(root / "studio/applied_proposal.json", {"proposal_hash": digest(proposal),
            "config_hash": project_hash(load_project(root)),
            "audio_hash": digest(audio.model_dump()), "execution_hash": digest(execution.model_dump()),
            "text_hash": digest(read_json(root / "studio/text.json"))})
        return result

    def upload(self, project, data):
        root = self.root(project)
        self.idle(root)
        with project_lock(root):
            rows = attachments.add(root, data.get("files"),
                                   secrets=(self.key, os.environ.get("OPENROUTER_API_KEY", "")))
        return {"attachments": rows}

    def remove_attachment(self, project, data):
        root = self.root(project)
        self.idle(root)
        with project_lock(root):
            rows = attachments.remove(root, data.get("id"))
        return {"attachments": rows}

    def start(self, project, data):
        root = self.root(project)
        action = data.get("action")
        if action not in {"assistant", "research", "plan", "replan", "script", "revise", "audio", "audio_sample", "audio_samples", "resume", "check"}:
            raise AppError("Unbekannter Arbeitsschritt.", code="invalid_action")
        payload = {"action": action, "message": str(data.get("message", ""))[:12000]}
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
        if action in {"assistant", "replan", "revise"} and not payload["message"].strip():
            raise AppError("Bitte deinen Änderungswunsch eingeben.", code="missing_feedback")
        if action in {"replan", "script"}:
            pointer = read_json(root / "studio/outline.json")
            if not pointer:
                raise AppError("Zuerst das Inhaltsverzeichnis erstellen.", code="plan_required")
            payload["run_id"] = pointer["run_id"]
            payload["plan_hash"] = data.get("plan_hash")
            if action == "script" and payload["plan_hash"] != outline_hash(manifest_path(root, pointer["run_id"]).parent):
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
            if self.worker(root) is not None:
                raise AppError("Zuerst den laufenden Auftrag abschließen oder anhalten.", code="project_busy")
            if any(value[1:] == (root, remote_episode) for value in active.values()):
                raise AppError("Diese Folge wird bereits vertont.", code="episode_busy")
            limit = MAX_PARALLEL if selected_execution(root).audio == "parallel" else 1
            if len(active) >= MAX_PARALLEL or sum(value[1] == root for value in active.values()) >= limit:
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
        """The worker's error output file; logs of finished jobs older than an hour are removed."""
        folder = root / "studio/stderr"
        folder.mkdir(parents=True, exist_ok=True)
        keep = {job_id, (read_json(root / "studio/job.json", {}) or {}).get("id"), *self.audio_processes}
        cutoff = time.time() - 3600
        for old in folder.glob("*.log"):
            try:
                if old.stem not in keep and old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass  # A log another worker still holds open goes next time.
        return folder / (job_id + ".log")

    def stop(self, project, job_id=None):
        root = self.root(project)
        if job_id:
            owner = self.audio_processes.get(job_id)
            if owner is None:
                raise AppError("Audioauftrag nicht gefunden.", code="no_active_job")
            process = owner[0] if owner[1] == root else None
        else:
            process = self.worker(root)
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
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            raise AppError("Zugriff nur über die lokale Studio-Adresse.", code="forbidden")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in allowed}:
            raise AppError("Fremde Webseiten dürfen das Studio nicht steuern.", code="forbidden")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise AppError("Zugriff von einer fremden Webseite abgewiesen.", code="forbidden")
        if mutation and not secrets.compare_digest(self.headers.get("X-Studio-Token", ""), self.server.studio.token):
            raise AppError("Studio-Sitzung neu laden.", code="forbidden")

    def dispatch(self, mutation=False):
        app = self.server.studio
        try:
            self.guard(mutation)
            path = unquote(urlsplit(self.path).path)
            if mutation:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise AppError("JSON-Anfrage erwartet.", code="invalid_request")
                size = int(self.headers.get("Content-Length", "0"))
                limit = attachments.MAX_BODY_BYTES if re.fullmatch(r"/api/projects/[^/]+/upload", path) else 128000
                if not 0 < size <= limit:
                    raise AppError("Anfrage zu groß oder leer.", code="invalid_request")
                data = json.loads(self.rfile.read(size))
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
                    elif path == "/api/restore":
                        result = app.restore(data)
                    else:
                        match = re.fullmatch(r"/api/projects/([^/]+)/(save|start|stop|apply_proposal|delete|upload"
                                             r"|remove_attachment|approve|spoken_override|listening_review)", path)
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
                result = app.bootstrap()
            elif path == "/api/projects":
                with app.mutex:
                    result = app.overview()
            elif (match := re.fullmatch(r"/api/projects/([^/]+)", path)):
                with app.mutex:
                    result = app.detail(match[1])
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


def make_server(workspace, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), StudioHandler)
    server.studio = Studio(Path(workspace))
    server.daemon_threads = True
    return server


def serve(workspace, port=8765, open_browser=True):
    url = f"http://127.0.0.1:{port}"
    # A second double-click reopens this workspace instead of starting another server.
    try:
        with urlopen(url + "/api/bootstrap", timeout=2) as response:
            existing = json.load(response)
        if isinstance(existing, dict) and existing.get("app") == "podcast-studio" and existing.get("workspace") == str(Path(workspace).resolve()):
            if open_browser:
                webbrowser.open(url)
            print(f"Podcast Studio läuft bereits: {url}", flush=True)
            return
    except (OSError, ValueError):
        pass
    with project_lock(Path(workspace) / ".studio"):
        configure_logging(Path(workspace) / ".studio/studio.log")
        server = make_server(workspace, port)
        server.studio.start_scheduler()
        url = f"http://127.0.0.1:{server.server_port}"
        logger("studio").info("Studio gestartet: %s", url)
        print(f"Podcast Studio: {url}\nDieses Fenster geöffnet lassen. Beenden mit Strg+C.", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        finally:
            with server.studio.mutex:
                app = server.studio
                app.stop_all()
            server.server_close()
