from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

from .errors import AppError
from .models import TopicBrief


def digest(data: object) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def project_hash(config: TopicBrief) -> str:
    """The brief's identity for run manifests, input hashes and the Studio's change gates.

    A field added to the brief after runs were recorded is dropped while it is unset, so an
    existing run still matches an unchanged project.yaml. ``host_names`` arrived on 19 September
    2026; a brief without names hashes exactly as it did before the field existed.
    """
    data = config.model_dump(mode="json")
    if data.get("host_names") is None:
        data.pop("host_names", None)
    return digest(data)


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


def read_optional_json(path: Path, default=None):
    """Read a best-effort status snapshot; missing or unreadable data is optional."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


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
def file_lock(path: Path, *, shared=False, timeout=0):
    """Process-safe reader/writer lock, also released when a worker is killed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            class Overlapped(ctypes.Structure):
                _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                            ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                            ("hEvent", wintypes.HANDLE)]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
            kernel.LockFileEx.restype = wintypes.BOOL
            kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.DWORD, ctypes.POINTER(Overlapped)]
            kernel.UnlockFileEx.restype = wintypes.BOOL
            handle, offset = msvcrt.get_osfhandle(stream.fileno()), Overlapped()

            def acquire():
                if not kernel.LockFileEx(handle, 1 | (0 if shared else 2), 0, 1, 0, ctypes.byref(offset)):
                    raise ctypes.WinError(ctypes.get_last_error())

            def release():
                if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(offset)):
                    raise ctypes.WinError(ctypes.get_last_error())
        else:
            import fcntl

            def acquire():
                fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)

            def release():
                fcntl.flock(stream, fcntl.LOCK_UN)

        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise AppError("Für dieses Projekt oder diesen Abschnitt läuft bereits ein Auftrag.",
                                   code="project_busy", status="blocked") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            release()


def project_lock(root: Path, *, shared=False):
    return file_lock(root / ".pla.lock", shared=shared)


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
