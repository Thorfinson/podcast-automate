"""Prompt texts live in ``prompts/*.txt``; code only composes them and fills placeholders.

A file holds one paragraph, wrapped for reading; loading joins its lines with single spaces.
Checkpoint signatures hash the composed prompt, so editing a file changes which saved drafts
and reviews are reused. Bump the ``prompt_version`` tag at the call site when the meaning changes.
"""
from __future__ import annotations

from functools import cache
from importlib.resources import files
from string import Formatter

PLACEHOLDERS = {"language", "maximum"}


def _folder():
    return files("podcast_automate").joinpath("prompts")


@cache
def text(name: str) -> str:
    raw = _folder().joinpath(f"{name}.txt").read_text(encoding="utf-8")
    return " ".join(line.strip() for line in raw.splitlines() if line.strip())


def fragment(name: str) -> str:
    """A reusable rule block that further text follows directly; ends with one space."""
    return text(name) + " "


def instructions(name: str, **values) -> str:
    """Task-specific instructions without trailing whitespace; the caller appends the JSON payload."""
    content = text(name)
    return content.format(**values) if values else content


def placeholders(name: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(text(name)) if field is not None}


def available() -> list[str]:
    return sorted(entry.name[:-4] for entry in _folder().iterdir() if entry.name.endswith(".txt"))
