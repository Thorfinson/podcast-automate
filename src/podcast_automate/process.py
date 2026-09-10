from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from .errors import AppError


def _stop_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=10, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.communicate()


def run_process(args: list[str], *, timeout: int, cwd: Path | None = None,
                input_text: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """Never pass prompts or project paths through a shell."""
    try:
        process = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except OSError as exc:
        raise AppError(f"Programm konnte nicht gestartet werden: {args[0]}",
                       code="missing_executable", status="blocked") from exc
    try:
        stdout, stderr = process.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _stop_tree(process)
        raise AppError("Zeitlimit erreicht; der Auftrag kann wiederaufgenommen werden.",
                       code="timeout") from exc
    except KeyboardInterrupt:
        _stop_tree(process)
        raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
