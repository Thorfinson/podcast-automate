"""One Studio job per process; secrets arrive through stdin, never command arguments."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from datetime import datetime, timezone
from pathlib import Path

from .prompts import instructions
from .codex import CodexAdapter  # noqa: F401  (tests patch podcast_automate.studio_worker.CodexAdapter.structured)
from . import attachments
from .doctor import inspect
from .episode_audio import run_episode_audio, saved_expression, tag_episode
from .expression import TAG
from .errors import AppError
from .execution import selected_execution
from .editorial import terminology
from .logs import add_secret, configure_logging, logger, record_failure
from .models import now
from .provider_pool import AdapterPool, text_generation_settings
from .research import reserve_call, run_research, latest_research_run
from .run_budget import run_text_generation
from .runner import manifest_path, run_observer
from .scripting import outline_hash, run_script
from .teaching_research import gaps_in
from .speech import GeminiSpeech, selected_audio
from .storage import digest, file_hash, load_project, project_lock, read_yaml, write_json
from .subscriptions import quota_retry_at
from .studio import BriefProposal, TextChoice, audio_job_path, chat_limits, read_json
from .studio_progress import safe_script_progress, watch
from .status_summary import start_monitor
from .process import stop_process_tree
from .voice_samples import generate_sample, generate_samples


def tag_episodes(root, episodes, api_key=None, *, run_id=None, progress=None):
    """Inline audio tags for several published episodes at once (episode_audio.tag_episode). One that fails is
    reported with its reason and does not stop the others; only when every one fails does the first reason stop.

    ``studio/expression/progress.json`` says while it runs how many are done, so the Studio can show when the
    tags are placed, also at the end of a script run (``run_id``); ``progress`` also receives the counts."""
    results, failures = {}, []
    state = {"status": "running", "run_id": run_id, "done": 0, "total": len(episodes), "started_at": now()}
    report = root / "studio" / "expression" / "progress.json"

    def publish():
        write_json(report, {**state, "updated_at": now()})
        if progress:
            progress({"phase": "expression", "completed_segments": state["done"], "total_segments": state["total"]})
    publish()
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(5, len(episodes))), thread_name_prefix="expression") as pool:
            futures = {pool.submit(copy_context().run, tag_episode, root, episode, api_key=api_key): episode
                       for episode in episodes}
            for future in as_completed(futures):
                episode = futures[future]
                try:
                    record = future.result()
                    results[episode] = {"tags": sum(len(TAG.findall(text)) for text in record["segments"].values()),
                                        "rejected": record.get("rejected", "")}
                except AppError as exc:
                    failures.append(exc)
                    results[episode] = {"error": str(exc), "code": exc.code}
                state["done"] += 1
                publish()
    finally:
        state["status"] = "completed" if len(failures) < len(episodes) or not episodes else "stopped"
        publish()
    if episodes and len(failures) == len(episodes):
        raise failures[0]
    return {episode: results[episode] for episode in episodes}


def express_published(root, run, api_key=None):
    """After a script run finished, place the tags of the episodes it published, when the project records with
    Gemini and its expression layer; the reader then sees them before approving audio. A failure here never
    undoes the finished run: the reading page offers "Ausdruck setzen" for any episode still without tags."""
    if run.kind != "script" or run.status != "completed":
        return None
    audio = selected_audio(root, load_project(root))
    if not (audio.remote and audio.expression):
        return None
    missing = []
    for folder in sorted((root / "episodes").glob("ep_*")):
        pointer = read_json(folder / "latest.json", {})
        script = folder / "script.yaml"
        if (pointer.get("run_id") == run.run_id and script.is_file()
                and saved_expression(root, folder.name, file_hash(script)) is None):
            missing.append(folder.name)
    try:
        return tag_episodes(root, missing, api_key, run_id=run.run_id) if missing else None
    except AppError as exc:
        logger("worker").warning("Ausdruck nach dem Skriptlauf nicht gesetzt (%s); auf der Leseseite erneut anstoßen.",
                                 exc.code)
        return None


def probe_key(root, request, run_id=None):
    """The Studio's OpenRouter key reaches a script run only when that run asked for the Jev gap probe."""
    execution = (read_json(manifest_path(root, run_id).parent / "script_request.json", {}).get("execution") if run_id
                 else selected_execution(root).model_dump()) or {}
    return request.get("api_key") if execution.get("jev_probe") else None


def text_key(root, request, run_id):
    """The key goes to a text run only while the run works with OpenRouter, as started or as switched."""
    current = run_text_generation(manifest_path(root, run_id).parent) or {}
    return request.get("api_key") if current.get("provider") == "openrouter" else None


def perform(root, request, sample_progress=None):
    action = request["action"]
    config = load_project(root)
    choice = TextChoice.model_validate(request["text"])
    kwargs = choice.kwargs()
    kwargs["api_key"] = request.get("api_key") if choice.provider == "openrouter" else None
    saved_key = text_key(root, request, request["run_id"]) if action in {"script", "replan"} else None
    if action == "check":
        remote = selected_audio(root, config).remote
        checks = inspect(config.runtime, include_tts=not remote)
        if remote:
            try:
                GeminiSpeech(request.get("api_key")).require_key()
                available = True
            except AppError:
                available = False
            checks["checks"].append({"name": "Gemini-TTS-Key", "ok": available,
                "detail": "Key hinterlegt; noch kein API-Hörtest durchgeführt." if available else "OpenRouter-Key fehlt."})
            checks["ready"] = checks["ready"] and available
        return {"checks": checks}
    if action == "audio_sample":
        return {"sample": generate_sample(root, request["voice"], request["language"], request.get("api_key"))}
    if action == "audio_samples":
        return {"samples": generate_samples(root, request["language"], request.get("api_key"), sample_progress)}
    if action == "assistant":
        with project_lock(root):
            conversation = read_json(root / "studio/chat.json", [])
            user_message = {"role": "user", "message": request["message"]}
            if conversation and conversation[-1] == user_message:
                # Sending an unanswered message again replaces it instead of repeating it.
                conversation = conversation[:-1]
            write_json(root / "studio/chat.json", [*conversation, user_message])
            selection = text_generation_settings(config, **{key: value for key, value in kwargs.items() if key != "api_key"})
            adapter = AdapterPool(config.runtime, selection, api_key=kwargs["api_key"])
            adapter.require_key()
            work = root / "studio/assistant"
            number = reserve_call(work, chat_limits(root, config.research_limits))
            # The machine-learning names only for a machine-learning brief; no receipt binds this prompt's text.
            prompt = (
                terminology(config.language, config.topic, config.central_question) +
                instructions("studio_assistant") + " " +
                attachments.MATERIAL_RULES +
                instructions("studio_assistant_attachments") + "\n" +
                json.dumps({"brief": {key: getattr(config, key) for key in
                    ("topic", "central_question", "prior_knowledge", "depth_request", "focus_questions", "excluded_topics",
                     "language", "target_total_minutes", "seed_urls", "series_goal", "recency_months")},
                    # What the settings page holds, for answers about it; the conversation does not change it.
                    "saved_settings": {"text": choice.normalized(), "audio_settings": selected_audio(root, config).model_dump(),
                                       "execution": selected_execution(root).model_dump()},
                    "attachments": attachments.context(root),
                    "conversation": conversation[-16:], "user_message": request["message"]}, ensure_ascii=False))
            proposal, _ = adapter.structured(prompt, BriefProposal, work / f"call_{number:03d}",
                                              prompt_version="studio_brief.v7-settings-page", search=False)
            # Text model, audio and execution are the settings page's for every project (studio_settings, 2026-10-03):
            # a proposal carries the brief only, whatever the model put there.
            proposal.text = proposal.audio_settings = proposal.execution = None
            conversation.extend([user_message,
                                 {"role": "assistant", **proposal.model_dump()}])
            write_json(root / "studio/chat.json", conversation)
            write_json(root / "studio/proposal_inputs.json", {"proposal_hash": digest(conversation[-1]),
                       "attachments_hash": digest(attachments.inventory(root))})
            return {"proposal": proposal.model_dump()}
    if action == "research":
        if choice.provider == "codex_cli":
            research_choice = {key: kwargs[key] for key in ("model", "reasoning_effort")}
        elif choice.provider in {"claude_code", "auto"}:
            research_choice = {"backend": choice.provider, "model": kwargs["model"], "reasoning_effort": kwargs["reasoning_effort"]}
        else:
            # OpenRouter has no web tools; research runs on the subscriptions with the automatic rule.
            research_choice = {"backend": "auto"}
        # Studio runs always stop for the plan projection; the approval comes from the research page.
        # The earlier research's stored sources as a starting library, when the page asked for it.
        seed = {"seed_corpus": latest_research_run(root)} if request.get("seed_corpus") else {}
        run = run_research(root, **research_choice, plan_review="required", **seed)
    elif action == "plan":
        run = run_script(root, plan_only=True, probe_key=probe_key(root, request), **kwargs)
    elif action == "replan":
        run = run_script(root, plan_only=True, resume=True, run_id=request["run_id"],
                         outline_feedback=request["message"], api_key=saved_key,
                         probe_key=probe_key(root, request, request["run_id"]))
    elif action == "script":
        run = run_script(root, resume=True, run_id=request["run_id"],
                         approved_plan_hash=request["plan_hash"], api_key=saved_key,
                         probe_key=probe_key(root, request, request["run_id"]))
    elif action == "revise":
        run = run_script(root, revise=request["episode"], feedback=request["message"],
                         probe_key=probe_key(root, request), **kwargs)
    elif action == "expression":
        episodes = request.get("episodes") or sorted(folder.name for folder in (root / "episodes").glob("ep_*")
                                                     if (folder / "script.yaml").is_file())
        return {"expression": tag_episodes(root, episodes, request.get("api_key"), progress=sample_progress)}
    elif action == "audio":
        rerender = request.get("rerender") is True
        run = run_episode_audio(root, episode=request["episode"], approve_audio=not rerender,
            approval_note=("Neu gerendert mit der gespeicherten Freigabe; nur Sprechformen geändert." if rerender
                           else "Skript in Podcast Studio gelesen und ausdrücklich für Audio freigegeben."),
            expected_script_hash=request["script_hash"], expected_readable_hash=request["readable_hash"],
            expected_config_hash=request["config_hash"], audio_choice=request.get("audio_settings"),
            expected_audio_hash=request.get("audio_hash"), api_key=request.get("api_key"),
            parallel_remote=request.get("parallel_remote", False), expected_expression_hash=request.get("expression_hash"))
    elif action == "resume":
        run_id = request["run_id"]
        path = manifest_path(root, run_id)
        manifest = read_yaml(path)
        if manifest["kind"] == "script":
            saved = read_json(path.parent / "script_request.json", {})
            # An interrupted outline remains an outline; resume is never an approval.
            approval = read_json(path.parent / "plan_approval.json", {})
            plan_only = saved.get("require_plan_approval", False) and (
                manifest["stages"]["planning"]["status"] != "completed" or not approval or
                approval.get("plan_hash") != outline_hash(path.parent))
            run = run_script(root, resume=True, run_id=run_id, plan_only=plan_only,
                             api_key=text_key(root, request, run_id), probe_key=probe_key(root, request, run_id))
        elif manifest["kind"] == "research":
            # Also for the scheduler's automatic resume after a quota reset: the gate is never bypassed.
            run = run_research(root, resume=True, run_id=run_id, plan_review="required",
                               api_key=text_key(root, request, run_id))
        elif manifest["kind"] == "episode_audio":
            run = run_episode_audio(root, resume=True, run_id=run_id, api_key=request.get("api_key"),
                                    parallel_remote=request.get("parallel_remote", False))
        else:
            raise AppError("Diesen älteren Probentyp über die vorhandenen Werkzeuge fortsetzen.", code="unsupported_run")
    else:
        raise AppError("Unbekannter Auftrag.", code="invalid_action")
    if action in {"script", "revise", "resume"}:
        express_published(root, run, request.get("api_key"))
    return {"run": run.model_dump(mode="json")}


# SetThreadExecutionState flags: keep the system running while this thread asks, until it asks no more.
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def keep_awake():
    """Keep the computer from going to sleep while this worker runs; returns the call that allows sleep again.

    2026-10-02: the PC slept from 23:14 to 09:30 during runs, and a quota reset at 01:20 went unused. On Windows
    the worker's main thread asks for the system to stay on (the display may still turn off); on macOS
    ``caffeinate`` holds it for this process's lifetime. Elsewhere, under ``CI`` and with ``PLA_KEEP_AWAKE=0``
    (tests that start real workers) nothing happens."""
    if os.environ.get("PLA_KEEP_AWAKE") == "0" or os.environ.get("CI"):
        return lambda: None
    if os.name == "nt":
        try:
            import ctypes
            kernel = ctypes.WinDLL("kernel32")
            kernel.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
            kernel.SetThreadExecutionState.restype = ctypes.c_uint32
            if kernel.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                return lambda: kernel.SetThreadExecutionState(ES_CONTINUOUS)
        except (OSError, AttributeError):
            pass
        return lambda: None
    if platform.system() == "Darwin" and shutil.which("caffeinate"):
        try:
            holder = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return holder.terminate
        except OSError:
            pass
    return lambda: None


def process_started_at() -> str:
    """When this worker process was created, so a restarted Studio can tell its worker from a reused process id."""
    try:
        import psutil
        return datetime.fromtimestamp(psutil.Process(os.getpid()).create_time(), timezone.utc).isoformat()
    except Exception:  # noqa: BLE001  (an optional fact; the job starts without it)
        return now()


def main():
    root = Path(sys.argv[1]).resolve()
    request = json.loads(sys.stdin.read())
    add_secret(request.get("api_key"))
    configure_logging(root / "studio/worker.log")
    job_path = audio_job_path(root, request["audio_job_id"]) if request.get("audio_job_id") else root / "studio/job.json"
    job = read_json(job_path)
    logger("worker").info("Auftrag %s gestartet (%s)", job.get("id"), request.get("action"))
    # The worker names itself in its job file, so a restarted Studio finds it again (pid plus creation time).
    job.update(pid=os.getpid(), started_process_at=process_started_at())
    write_json(job_path, job)
    allow_sleep = keep_awake()
    progress_stop = threading.Event()
    progress_thread = None
    summary_process = None
    if request["action"] in {"research", "plan", "replan", "script", "revise", "resume"} and not request.get("audio_job_id"):
        progress_thread = threading.Thread(target=watch, args=(root, job["id"], progress_stop), daemon=True)
        progress_thread.start()
        try:
            summary_process = start_monitor(root, job["id"], request.get("api_key"))
        except OSError:
            pass

    def update(manifest):
        job["run"] = manifest.model_dump(mode="json")
        progress = read_json(manifest_path(root, manifest.run_id).parent / "progress.json", {})
        if progress.get("phase") in {"foundation_research", "research"}:
            job["progress"] = progress
        elif job.get("progress", {}).get("phase") == "foundation_research":
            job.pop("progress", None)
        write_json(job_path, job)
        if request["action"] in {"plan", "replan"}:
            write_json(root / "studio/outline.json", {"run_id": manifest.run_id})

    token = run_observer.set(update)
    def sample_progress(progress):
        job["progress"] = progress
        write_json(job_path, job)

    try:
        result = perform(root, request, sample_progress)
        job.update(result)
        run = result.get("run")
        job["status"] = run["status"] if run else "completed"
        if run and run["status"] == "waiting_for_quota":
            # The Studio scheduler resumes a paused job once the named reset has passed: the reset of the provider
            # that failed, as the stage kept it (runner.failure_details), else a wait that grows with each resume.
            quota_error = next((r["error"] for r in run["stages"].values() if r.get("error")), None) or {}
            job["retry_at"] = quota_retry_at(AppError(quota_error.get("message", ""), code=quota_error.get("code", "quota"),
                                                      details=quota_error.get("details") or {}),
                                             attempt=job.get("auto_resume_count", 0))
        if run and run["kind"] == "script" and run["status"] == "pending" and run["stages"]["planning"]["status"] == "completed":
            job["status"] = "review_ready"
        if run:
            errors = [r["error"]["message"] for r in run["stages"].values() if r.get("error")]
            if errors:
                job["message"] = errors[-1]
            if any(r.get("error", {}).get("code") == "teaching_research_required"
                   for r in run["stages"].values() if r.get("error")):
                job["research_gaps"] = gaps_in(manifest_path(root, run["run_id"]).parent)
    except (Exception, KeyboardInterrupt) as exc:
        job["status"] = exc.status if isinstance(exc, AppError) else "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        job["error_code"] = exc.code if isinstance(exc, AppError) else "interrupted" if isinstance(exc, KeyboardInterrupt) else "processing_failed"
        job["message"] = str(exc) if isinstance(exc, AppError) else "Auftrag unterbrochen oder Verarbeitung fehlgeschlagen. Gespeicherten Stand prüfen."
        if isinstance(exc, AppError):
            logger("worker").warning("Auftrag %s angehalten (%s): %s", job.get("id"), exc.code, exc)
            if exc.status == "waiting_for_quota":
                job["retry_at"] = quota_retry_at(exc, attempt=job.get("auto_resume_count", 0))
        elif not isinstance(exc, KeyboardInterrupt):
            receipt = record_failure(root / "studio", "worker", exc, secrets=(request.get("api_key") or "",))
            logger("worker").error("Auftrag %s fehlgeschlagen: %s", job.get("id"), type(exc).__name__, exc_info=exc)
            if receipt:
                job["message"] += f" Technische Details: {receipt.relative_to(root).as_posix()}"
        if request.get("api_key"):
            job["message"] = job["message"].replace(request["api_key"], "[Key verborgen]")
    finally:
        if summary_process is not None:
            try:
                stop_process_tree(summary_process)
            except Exception:
                pass  # An optional monitor must not prevent saving the final job state.
        progress_stop.set()
        if progress_thread:
            progress_thread.join(timeout=3)
        run_observer.reset(token)
        progress = safe_script_progress(root, job.get("run"))
        if progress:
            job["progress"] = progress
        job["finished_at"] = now()
        write_json(job_path, job)
        allow_sleep()


if __name__ == "__main__":
    main()
