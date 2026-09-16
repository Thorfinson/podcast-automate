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


def inspect(settings: RuntimeSettings, *, include_tts=True) -> dict:
    checks = []
    checks.append({"name": "python", "ok": True, "detail": platform.python_version()})
    checks.append({"name": "system", "ok": True,
                   "detail": f"{platform.system()} {platform.version()}"})
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        checks.append({"name": name, "ok": path is not None, "detail": path or "Fehlt im PATH"})
    try:
        mode = CodexAdapter(settings).check_login()
        checks.append({"name": "codex_login", "ok": True, "detail": mode})
    except AppError as exc:
        checks.append({"name": "codex_login", "ok": False, "detail": str(exc)})
    if not include_tts:
        return {"ready": all(check["ok"] for check in checks), "checks": checks,
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
        "ready": all(check["ok"] for check in checks),
        "checks": checks, "tts": tts, "model_inference_tested": False,
    }
