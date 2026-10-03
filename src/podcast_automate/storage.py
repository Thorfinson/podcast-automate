from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

from . import studio_settings
from .errors import AppError
from .models import ResearchLimits, RuntimeSettings, TopicBrief

# Brief fields that steer how a run works, not what it says: deadlines, CLI paths and limits. A resumed run hashes
# them as its saved snapshot recorded them (bound_brief).
OPERATIONAL_FIELDS = ("runtime", "research_limits")
# Fields the research lane never reads (checked with grep on 2026-10-02): voices, audio and text defaults, host names.
RESEARCH_OPERATIONAL_FIELDS = ("voice_profile", "tts_backend", "text_backend", "host_names")


def digest(data: object) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def bound_brief(config: TopicBrief, snapshot: dict | None, lane: str = "script") -> TopicBrief:
    """The brief as a resumed run's hash binds it: its operational fields as the run's ``project_snapshot.yaml``
    recorded them at the start, every content field as it is now.

    ``project_hash`` of the result equals the run's recorded hash whenever only operational fields changed, while
    any content edit still changes it. The Studio saves the brief while a run waits for quota, and a longer
    deadline, a raised research limit or, for research, a voice or host-name edit left such a run unresumable
    (2026-10-02). ``runtime`` and ``research_limits`` are operational in every lane; ``lane="research"`` adds
    ``RESEARCH_OPERATIONAL_FIELDS``. A missing snapshot binds the brief as it is.
    """
    if not isinstance(snapshot, dict):
        return config
    update = {}
    for key in OPERATIONAL_FIELDS + (RESEARCH_OPERATIONAL_FIELDS if lane == "research" else ()):
        if key == "host_names":
            # Unset names are left out of the hash, so a snapshot from before the field binds them as unset.
            value = snapshot.get(key)
            update[key] = dict(value) if isinstance(value, dict) else None
        elif key not in snapshot:
            continue
        elif key in {"runtime", "research_limits"}:
            try:
                update[key] = (RuntimeSettings if key == "runtime" else ResearchLimits).model_validate(snapshot[key])
            except ValueError:
                continue
        else:
            update[key] = snapshot[key]
    return config.model_copy(update=update)


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
        replace_file(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


# Only Windows has the momentary sharing violations below. Tests switch the retry on through this flag: patching
# os.name instead turns every Path built meanwhile into a WindowsPath on Linux and macOS.
SHARING_VIOLATIONS = os.name == "nt"


def outlast_sharing_violation(operation, timeout: float = 2.0):
    """Run ``operation``; on Windows, wait out a momentary permission error with doubling backoff.

    Windows refuses to open a file while another handle is renaming onto it, and refuses the rename
    while another handle reads it. Either overlap lasts microseconds, so the loser waits briefly
    instead of failing a run. A lock that stays raises once the timeout has passed; other
    platforms raise at once.
    """
    deadline = time.monotonic() + timeout
    delay = 0.001
    while True:
        try:
            return operation()
        except PermissionError:
            if not SHARING_VIOLATIONS or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


def replace_file(temporary: Path | str, path: Path, *, timeout: float = 2.0) -> None:
    """``os.replace`` that outlasts a concurrent reader, for example a progress poll or another
    worker reading ``budget.json`` at that instant."""
    outlast_sharing_violation(lambda: os.replace(temporary, path), timeout)


def read_text(path: Path, *, timeout: float = 2.0) -> str:
    """``Path.read_text`` that outlasts a concurrent ``replace_file`` onto the same file, for example
    a worker projecting the budget while another worker's call reservation rewrites ``budget.json``.
    Files one worker writes while another may read them go through this; a missing file raises as
    before."""
    return outlast_sharing_violation(lambda: path.read_text(encoding="utf-8"), timeout)


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


# libyaml's safe parser where PyYAML has it: the Studio reads every script and quality report on each poll, and
# the pure-Python parser made a page take seconds (2026-10-01: 2.8 s for the project list, 1.2 s with this).
YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def read_yaml(path: Path) -> dict:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=YAML_LOADER)
    except (OSError, yaml.YAMLError) as exc:
        raise AppError(f"Datei nicht lesbar: {path}", code="invalid_project", status="blocked") from exc
    if not isinstance(data, dict):
        raise AppError(f"Erwartete strukturierte Daten in {path}", code="invalid_project", status="blocked")
    return data


def load_project(root: Path) -> TopicBrief:
    """The project's brief, with the research limits and the time limit of one model call from the workspace settings
    where they set them (studio_settings): those hold for every project of the Studio."""
    config = TopicBrief.model_validate(read_yaml(root / "project.yaml"))
    settings = studio_settings.load(root) or {}
    if isinstance(settings.get("research_limits"), dict):
        config.research_limits = ResearchLimits.model_validate(settings["research_limits"])
    if type(settings.get("text_timeout_seconds")) is int and settings["text_timeout_seconds"] > 0:
        config.runtime = config.runtime.model_copy(update={"text_timeout_seconds": settings["text_timeout_seconds"]})
    return config


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
