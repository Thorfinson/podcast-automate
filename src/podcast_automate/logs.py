"""Process-wide logging and persisted failure tracebacks; credentials are redacted before writing.

Stage failures keep their user-facing German message in the run manifest. The technical cause goes
to ``runs/<run_id>/failures/<stage>_<timestamp>.txt`` and to the log file of the running process.
"""
from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .model_trace import redact

LOGGER = "podcast_automate"
FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MAX_BYTES = 2_000_000
BACKUPS = 3


class OneLine(logging.Formatter):
    """Terminal output stays a single line; tracebacks belong in the log file."""

    def format(self, record):
        saved = record.exc_info, record.exc_text
        record.exc_info, record.exc_text = None, None
        try:
            return super().format(record)
        finally:
            record.exc_info, record.exc_text = saved


def logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER + ("." + name if name else ""))


def configure_logging(path: Path | None = None, *, level=logging.INFO, stderr_level=logging.ERROR) -> logging.Logger:
    """Idempotent: one rotating file handler per path and one terminal handler per process."""
    root = logging.getLogger(LOGGER)
    root.setLevel(min(level, stderr_level))
    root.propagate = False
    targets = {getattr(handler, "pla_target", None) for handler in root.handlers}
    if "stderr" not in targets:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(stderr_level)
        handler.setFormatter(OneLine("%(levelname)s: %(message)s"))
        handler.pla_target = "stderr"
        root.addHandler(handler)
    if path is not None and str(path) not in targets:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        except OSError:
            root.warning("Protokolldatei nicht beschreibbar: %s", path)
            return root
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(FILE_FORMAT))
        handler.pla_target = str(path)
        root.addHandler(handler)
    return root


def release_logging(path: Path | None) -> None:
    """Close the file handler for ``path`` so short-lived callers do not keep the file open."""
    if path is None:
        return
    root = logging.getLogger(LOGGER)
    for handler in list(root.handlers):
        if getattr(handler, "pla_target", None) == str(path):
            root.removeHandler(handler)
            handler.close()


def record_failure(directory: Path, name: str, exc: BaseException, *, secrets=()) -> Path | None:
    """Persist a redacted traceback for later diagnosis. Never raises; returns the file or None."""
    try:
        text = redact("".join(traceback.format_exception(exc)), secrets)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = directory / "failures" / f"{name}_{stamp}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path
    except OSError:
        return None


def failure_records(directory: Path) -> list[str]:
    folder = directory / "failures"
    if not folder.is_dir():
        return []
    return sorted(entry.name for entry in folder.iterdir() if entry.suffix == ".txt")
