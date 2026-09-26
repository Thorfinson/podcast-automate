"""One Studio job per process; secrets arrive through stdin, never command arguments."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from .prompts import instructions
from .codex import CodexAdapter  # noqa: F401  (tests patch podcast_automate.studio_worker.CodexAdapter.structured)
from . import attachments
from .doctor import inspect
from .episode_audio import run_episode_audio
from .errors import AppError
from .execution import selected_execution
from .editorial import TERMINOLOGY
from .logs import configure_logging, logger, record_failure
from .models import now
from .provider_pool import AdapterPool, text_generation_settings
from .research import reserve_call, run_research
from .runner import manifest_path, run_observer
from .scripting import outline_hash, run_script
from .teaching_research import gaps_in
from .speech import GeminiSpeech, audio_catalog, selected_audio
from .storage import digest, load_project, project_lock, read_yaml, write_json
from .subscriptions import quota_retry_at
from .studio import BriefProposal, TextChoice, audio_job_path, chat_limits, read_json
from .studio_progress import safe_script_progress, watch
from .status_summary import start_monitor
from .process import stop_process_tree
from .voice_samples import generate_sample, generate_samples
from .text_settings import (CLAUDE_EFFORTS, CLAUDE_MODELS, CODEX_MODELS, EFFORT_EQUIVALENTS, OPENROUTER_EFFORTS,
                            OPENROUTER_MODELS, PROVIDER_NOTES, REASONING_EFFORTS)


def perform(root, request, sample_progress=None):
    action = request["action"]
    config = load_project(root)
    choice = TextChoice.model_validate(request["text"])
    kwargs = choice.kwargs()
    kwargs["api_key"] = request.get("api_key") if choice.provider == "openrouter" else None
    saved_key = None
    if action in {"script", "replan"}:
        saved = read_json(manifest_path(root, request["run_id"]).parent / "script_request.json", {})
        if saved.get("text_generation", {}).get("provider") == "openrouter":
            saved_key = request.get("api_key")
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
            prompt = (
                TERMINOLOGY +
                instructions("studio_assistant") + " " +
                attachments.MATERIAL_RULES +
                instructions("studio_assistant_attachments") + "\n" +
                json.dumps({"brief": {key: getattr(config, key) for key in
                    ("topic", "central_question", "prior_knowledge", "depth_request", "focus_questions", "excluded_topics",
                     "language", "target_total_minutes", "seed_urls")},
                    "saved_settings": {"text": choice.normalized(), "audio_settings": selected_audio(root, config).model_dump(),
                                       "execution": selected_execution(root).model_dump()},
                    "audio_catalog": audio_catalog(), "attachments": attachments.context(root),
                    "text_catalog": {"codex_models": CODEX_MODELS, "openrouter_models": OPENROUTER_MODELS,
                                     "openrouter_efforts": OPENROUTER_EFFORTS, "claude_models": CLAUDE_MODELS,
                                     "claude_efforts": CLAUDE_EFFORTS, "effort_equivalents": EFFORT_EQUIVALENTS,
                                     "providers": PROVIDER_NOTES},
                    "reasoning_efforts": REASONING_EFFORTS, "requested_text": request.get("requested_text"),
                    "conversation": conversation[-16:], "user_message": request["message"]}, ensure_ascii=False))
            proposal, _ = adapter.structured(prompt, BriefProposal, work / f"call_{number:03d}",
                                              prompt_version="studio_brief.v4-tts-model", search=False)
            if request.get("requested_text"):
                proposal.text = TextChoice.model_validate(request["requested_text"])
            if proposal.text:
                proposal.text = TextChoice.model_validate(proposal.text.normalized())
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
        run = run_research(root, **research_choice, plan_review="required")
    elif action == "plan":
        run = run_script(root, plan_only=True, **kwargs)
    elif action == "replan":
        run = run_script(root, plan_only=True, resume=True, run_id=request["run_id"],
                         outline_feedback=request["message"], api_key=saved_key)
    elif action == "script":
        run = run_script(root, resume=True, run_id=request["run_id"],
                         approved_plan_hash=request["plan_hash"], api_key=saved_key)
    elif action == "revise":
        run = run_script(root, revise=request["episode"], feedback=request["message"], **kwargs)
    elif action == "audio":
        rerender = request.get("rerender") is True
        run = run_episode_audio(root, episode=request["episode"], approve_audio=not rerender,
            approval_note=("Neu gerendert mit der gespeicherten Freigabe; nur Sprechformen geändert." if rerender
                           else "Skript in Podcast Studio gelesen und ausdrücklich für Audio freigegeben."),
            expected_script_hash=request["script_hash"], expected_readable_hash=request["readable_hash"],
            expected_config_hash=request["config_hash"], audio_choice=request.get("audio_settings"),
            expected_audio_hash=request.get("audio_hash"), api_key=request.get("api_key"),
            parallel_remote=request.get("parallel_remote", False))
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
                             api_key=request.get("api_key") if saved.get("text_generation", {}).get("provider") == "openrouter" else None)
        elif manifest["kind"] == "research":
            # Also for the scheduler's automatic resume after a quota reset: the gate is never bypassed.
            run = run_research(root, resume=True, run_id=run_id, plan_review="required")
        elif manifest["kind"] == "episode_audio":
            run = run_episode_audio(root, resume=True, run_id=run_id, api_key=request.get("api_key"),
                                    parallel_remote=request.get("parallel_remote", False))
        else:
            raise AppError("Diesen älteren Probentyp über die vorhandenen Werkzeuge fortsetzen.", code="unsupported_run")
    else:
        raise AppError("Unbekannter Auftrag.", code="invalid_action")
    return {"run": run.model_dump(mode="json")}


def main():
    root = Path(sys.argv[1]).resolve()
    request = json.loads(sys.stdin.read())
    configure_logging(root / "studio/worker.log")
    job_path = audio_job_path(root, request["audio_job_id"]) if request.get("audio_job_id") else root / "studio/job.json"
    job = read_json(job_path)
    logger("worker").info("Auftrag %s gestartet (%s)", job.get("id"), request.get("action"))
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
            # The Studio scheduler resumes a paused job once the named reset has passed.
            quota_error = next((r["error"] for r in run["stages"].values() if r.get("error")), None)
            job["retry_at"] = quota_retry_at(AppError(quota_error["message"] if quota_error else "",
                                                      code=(quota_error or {}).get("code", "quota")))
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
                job["retry_at"] = quota_retry_at(exc)
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


if __name__ == "__main__":
    main()
