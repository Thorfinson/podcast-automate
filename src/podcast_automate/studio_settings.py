"""Settings that hold for every project of a Studio workspace (the user's choice of 2026-10-03): the text model, the
audio choice with its voices and pauses, the execution modes, the pre-approvals, the research limits and the time
limit of one model call.

They live next to the projects, in ``projects/.studio-settings.json``, and only the Studio's settings page writes them.
While that file exists, each of its sections replaces the project's own: ``studio/text.json``, ``studio/audio.json``,
``studio/execution.json``, ``studio/allowances.json`` and, read through storage.load_project, ``research_limits`` and
``runtime.text_timeout_seconds`` of ``project.yaml``. Without it every project keeps its own files as before, which is
what the CLI outside a Studio workspace and the tests see. Runs keep what they bound at their start (the text model,
the execution mode, an approved recording's audio); limits and the time limit are operational and apply on resume.

This module has no project dependencies, so storage, speech, execution and studio_allowances can all read it.
"""
from __future__ import annotations

import json
from pathlib import Path

SETTINGS_NAME = ".studio-settings.json"
SECTIONS = ("text", "audio", "execution", "allowances", "research_limits", "text_timeout_seconds")


def path_for(projects: Path) -> Path:
    """The settings file of the projects directory ``projects``."""
    return projects / SETTINGS_NAME


def load(root: Path) -> dict | None:
    """The workspace settings of the project at ``root``, or None while there are none (or they cannot be read)."""
    return load_dir(root.parent)


def load_dir(projects: Path) -> dict | None:
    """The workspace settings of the projects directory ``projects``, or None."""
    path = path_for(projects)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def section(root: Path, name: str):
    """One section of the workspace settings, or None when they do not set it."""
    return (load(root) or {}).get(name)


def text_data(root: Path, default=None):
    """The text choice new runs of this project take: the workspace's, else the project's ``studio/text.json``."""
    chosen = section(root, "text")
    if isinstance(chosen, dict):
        return chosen
    path = root / "studio/text.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        saved = None
    return saved if isinstance(saved, dict) else default
