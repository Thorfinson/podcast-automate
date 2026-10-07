from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
from pathlib import Path

from .audio import worker_path
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .errors import AppError
from .models import RuntimeSettings
from .process import run_process
from .speech import VOICES_VERIFIED_ON
from .subscriptions import describe_snapshot, quota_overview
from .text_settings import catalog_age

LOGIN_CHECKS = {"codex_login", "claude_login", "claude_api", "openrouter_text", "subscription_quota", "perplexity_search"}
# Checks of which one usable text access suffices: a subscription login, Claude on the user's API key (D-145), or an
# OpenRouter key, which researches with the Perplexity search (D-151).
TEXT_ACCESS = {"codex_login", "claude_login", "claude_api", "openrouter_text"}
# The details written here are English like the rest of the CLI (D-156); a detail taken from a checked module's error
# (the adapters, the quota lines of subscriptions) keeps that module's wording.


def subscription_checks(settings: RuntimeSettings) -> list[dict]:
    """Claude login and both subscription quotas. Informational unless no subscription is usable at all."""
    try:
        overview = quota_overview(settings, refresh=True)
    except (AppError, OSError, ValueError) as exc:
        return [{"name": "claude_login", "ok": False, "detail": str(exc)},
                {"name": "subscription_quota", "ok": False, "detail": "Quota query failed"}]
    claude = overview["claude_code"]
    login = claude.get("login") or {}
    detail = (f"claude.ai · {claude.get('plan') or 'subscription'} · Claude Code {login.get('cli_version')}"
              if claude.get("usable") else describe_snapshot("claude_code", claude))
    return [{"name": "claude_login", "ok": bool(claude.get("usable")), "detail": detail},
            {"name": "subscription_quota", "ok": bool(overview["any_available"]), "detail": " | ".join(overview["lines"])}]


def readiness(checks) -> bool:
    """Everything technical must pass; of the text accesses, at least one must be usable."""
    return (all(check["ok"] for check in checks if check["name"] not in LOGIN_CHECKS) and
            any(check["ok"] for check in checks if check["name"] in TEXT_ACCESS))


def claude_api_check(settings: RuntimeSettings, key=None) -> dict:
    """Claude on the user's Anthropic key: a key (the Studio's, else ANTHROPIC_API_KEY) and a recent enough Claude
    Code. No model call, so nothing is billed; whether the key is accepted shows only at the first call."""
    if not (key or os.environ.get("ANTHROPIC_API_KEY", "")).strip():
        return {"name": "claude_api", "ok": False, "detail": "No ANTHROPIC_API_KEY set (needed only for Claude on your "
                                                             "own API key)."}
    try:
        version = ClaudeCodeAdapter(settings).cli_version()
    except AppError as exc:
        return {"name": "claude_api", "ok": False, "detail": str(exc)}
    return {"name": "claude_api", "ok": True,
            "detail": f"Anthropic key present · Claude Code {version}; no billed call yet"}


def catalog_check() -> dict:
    """Informational: hard-coded model and voice catalogs age; a stale list never blocks work."""
    age = catalog_age()
    detail = (f"Text models checked on {age['verified_on']} ({age['age_days']} days ago), "
              f"Gemini voices on {VOICES_VERIFIED_ON}")
    if age["stale"]:
        detail += "; check the catalogs against the providers (text_settings.py, speech.py)"
    return {"name": "model_catalog", "ok": True, "detail": detail, "stale": age["stale"]}


def inspect(settings: RuntimeSettings, *, include_tts=True, anthropic_key=None, perplexity_key=None,
            openrouter_key=None) -> dict:
    """The installation's checks; the Studio passes the keys it holds in memory, the CLI has the environment's."""
    checks = []
    checks.append({"name": "python", "ok": True, "detail": platform.python_version()})
    checks.append({"name": "system", "ok": True,
                   "detail": f"{platform.system()} {platform.version()}"})
    checks.append(catalog_check())
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        checks.append({"name": name, "ok": path is not None, "detail": path or "Not on PATH"})
    try:
        mode = CodexAdapter(settings).check_login()
        checks.append({"name": "codex_login", "ok": True, "detail": mode})
    except AppError as exc:
        checks.append({"name": "codex_login", "ok": False, "detail": str(exc)})
    checks.extend(subscription_checks(settings))
    checks.append(claude_api_check(settings, anthropic_key))
    # Informational: an OpenRouter key alone serves text without any subscription; research needs Perplexity too.
    routed = bool((openrouter_key or os.environ.get("OPENROUTER_API_KEY", "")).strip())
    checks.append({"name": "openrouter_text", "ok": routed,
                   "detail": "OpenRouter key present; research with it only through the Perplexity web search" if routed
                   else "No OpenRouter key (needed only for an OpenRouter text model)."})
    # Informational: only a run that chose the Perplexity search needs it (D-151); no request is made.
    searchable = bool((perplexity_key or os.environ.get("PERPLEXITY_API_KEY", "")).strip())
    checks.append({"name": "perplexity_search", "ok": searchable,
                   "detail": "Perplexity key present; no billed search yet" if searchable else
                   "No Perplexity key (needed only for the web search through Perplexity)."})
    if not include_tts:
        return {"ready": readiness(checks), "checks": checks,
                "tts": None, "model_inference_tested": False}
    tts = None
    try:
        with tempfile.TemporaryDirectory(prefix="pla-doctor-") as temporary:
            report = Path(temporary) / "report.json"
            result = run_process(
                [settings.tts_python, str(worker_path()), "--doctor", "--output", str(report)],
                timeout=45)
            if report.is_file():
                tts = json.loads(report.read_text(encoding="utf-8"))
            if result.returncode or not tts or "error" in tts:
                raise AppError("The TTS Python environment does not run.", code="tts_environment")
            packages_ok = all(tts["packages"].get(name) for name in ("torch", "qwen-tts", "soundfile"))
            device_ok = settings.tts_device in {"auto", "cpu"} or (
                tts.get("mps_available", False) if settings.tts_device == "mps"
                else tts.get("cuda_available", False))
            checks.append({"name": "tts_environment", "ok": packages_ok and device_ok,
                           "detail": "Packages and device available" if packages_ok and device_ok
                           else "Qwen/PyTorch packages or the configured GPU device are missing"})
    except (AppError, OSError, ValueError) as exc:
        checks.append({"name": "tts_environment", "ok": False, "detail": str(exc)})
    return {
        "ready": readiness(checks),
        "checks": checks, "tts": tts, "model_inference_tested": False,
    }
