from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import yaml

from .errors import AppError
from .models import TopicBrief


def digest(data: object) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pla-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, data: object) -> None:
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_yaml(path: Path, data: object) -> None:
    atomic_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AppError(f"Datei nicht lesbar: {path}", code="invalid_project", status="blocked") from exc
    if not isinstance(data, dict):
        raise AppError(f"Erwartete strukturierte Daten in {path}", code="invalid_project", status="blocked")
    return data


def load_project(root: Path) -> TopicBrief:
    return TopicBrief.model_validate(read_yaml(root / "project.yaml"))


def inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise AppError("Artefaktpfad verlässt den Projektordner.", code="invalid_path", status="blocked")
    return candidate


@contextmanager
def project_lock(root: Path):
    """OS locks are released even after process death; the lock file may remain."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".pla.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AppError("Für dieses Projekt läuft bereits ein Auftrag.",
                           code="project_busy", status="blocked") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)


def init_project(root: Path, config: TopicBrief) -> None:
    with project_lock(root):
        if (root / "project.yaml").exists():
            raise AppError("Das Projekt existiert bereits; project.yaml bleibt erhalten.",
                           code="project_exists", status="blocked")
        write_yaml(root / "project.yaml", config.model_dump(mode="json"))
        for directory in ("sources/raw", "sources/processed", "research", "models",
                          "episodes", "reports", "runs", "probes", "cache/audio"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        if not (root / ".gitignore").exists():
            atomic_text(root / ".gitignore",
                        "# Personal project inputs and outputs\n*\n!.gitignore\n")
