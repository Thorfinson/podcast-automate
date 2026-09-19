from __future__ import annotations

import json
import platform
import shutil
import tempfile
from pathlib import Path

from .audio import worker_path
from .codex import CodexAdapter
from .errors import AppError
from .models import RuntimeSettings
from .process import run_process
from .speech import VOICES_VERIFIED_ON
from .subscriptions import describe_snapshot, quota_overview
from .text_settings import catalog_age

LOGIN_CHECKS = {"codex_login", "claude_login", "subscription_quota"}


def subscription_checks(settings: RuntimeSettings) -> list[dict]:
    """Claude login and both subscription quotas. Informational unless no subscription is usable at all."""
    try:
        overview = quota_overview(settings, refresh=True)
    except (AppError, OSError, ValueError) as exc:
        return [{"name": "claude_login", "ok": False, "detail": str(exc)},
                {"name": "subscription_quota", "ok": False, "detail": "Kontingentabfrage fehlgeschlagen"}]
    claude = overview["claude_code"]
    login = claude.get("login") or {}
    detail = (f"claude.ai · {claude.get('plan') or 'Abo'} · Claude Code {login.get('cli_version')}"
              if claude.get("usable") else describe_snapshot("claude_code", claude))
    return [{"name": "claude_login", "ok": bool(claude.get("usable")), "detail": detail},
            {"name": "subscription_quota", "ok": bool(overview["any_available"]), "detail": " | ".join(overview["lines"])}]


def readiness(checks) -> bool:
    """Everything technical must pass; of the subscriptions, at least one must be usable."""
    return (all(check["ok"] for check in checks if check["name"] not in LOGIN_CHECKS) and
            any(check["ok"] for check in checks if check["name"] in {"codex_login", "claude_login"}))


def catalog_check() -> dict:
    """Informational: hard-coded model and voice catalogs age; a stale list never blocks work."""
    age = catalog_age()
    detail = (f"Textmodelle geprüft am {age['verified_on']} ({age['age_days']} Tage), "
              f"Gemini-Stimmen am {VOICES_VERIFIED_ON}")
    if age["stale"]:
        detail += "; Kataloge gegen die Anbieter prüfen (text_settings.py, speech.py)"
    return {"name": "model_catalog", "ok": True, "detail": detail, "stale": age["stale"]}


def inspect(settings: RuntimeSettings, *, include_tts=True) -> dict:
    checks = []
    checks.append({"name": "python", "ok": True, "detail": platform.python_version()})
    checks.append({"name": "system", "ok": True,
                   "detail": f"{platform.system()} {platform.version()}"})
    checks.append(catalog_check())
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        checks.append({"name": name, "ok": path is not None, "detail": path or "Fehlt im PATH"})
    try:
        mode = CodexAdapter(settings).check_login()
        checks.append({"name": "codex_login", "ok": True, "detail": mode})
    except AppError as exc:
        checks.append({"name": "codex_login", "ok": False, "detail": str(exc)})
    checks.extend(subscription_checks(settings))
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
                raise AppError("TTS-Python-Umgebung ist nicht lauffähig.", code="tts_environment")
            packages_ok = all(tts["packages"].get(name) for name in ("torch", "qwen-tts", "soundfile"))
            device_ok = settings.tts_device in {"auto", "cpu"} or (
                tts.get("mps_available", False) if settings.tts_device == "mps"
                else tts.get("cuda_available", False))
            checks.append({"name": "tts_environment", "ok": packages_ok and device_ok,
                           "detail": "Pakete und Gerät verfügbar" if packages_ok and device_ok
                           else "Qwen/PyTorch-Pakete oder konfiguriertes GPU-Gerät fehlen"})
    except (AppError, OSError, ValueError) as exc:
        checks.append({"name": "tts_environment", "ok": False, "detail": str(exc)})
    return {
        "ready": readiness(checks),
        "checks": checks, "tts": tts, "model_inference_tested": False,
    }
