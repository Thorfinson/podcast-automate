"""A podcast's music (D-189): one piece the montage plays before the first word, at every chapter change and after the
last word (audio.assemble), and the credit its license asks for, which the show notes and the companion kit carry.

``studio/music.json`` names the piece under ``studio/music/`` with its hash, its tempo and its credit. A recording binds
the record (episode_audio's inputs and the approval's ``music_hash``), so another piece needs a new approval. The music
is mixed in the montage only, so recording an episode again for it takes every take from the cache."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from .content_text import text as wording
from .errors import AppError
from .models import Contract
from .storage import digest, file_hash, inside

MUSIC = "studio/music.json"


class MusicChoice(Contract):
    file: str = Field(pattern=r"^studio/music/[^/\\]+\.(mp3|wav|ogg|flac)$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Four beats a bar; the montage counts its places in bars.
    bpm: float = Field(ge=40, le=240)
    # Where the chapter change's two bars begin in the piece; the opening and the ending begin at its start.
    chapter_from_seconds: float = Field(default=0.0, ge=0)
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=200)
    source: str = Field(pattern=r"^https://\S{1,300}$")
    license: str = Field(min_length=1, max_length=100)

    @property
    def bar_seconds(self):
        return 4 * 60 / self.bpm


def stored_music(root) -> dict | None:
    """``studio/music.json`` as stored, or None when the project has no music."""
    path = Path(root) / MUSIC
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AppError("studio/music.json ist nicht lesbar.", code="music_invalid", status="blocked") from None
    return data if isinstance(data, dict) else None


def music_choice(root) -> MusicChoice | None:
    """The project's music, its file checked against the hash it was chosen with, or None without music."""
    data = stored_music(root)
    if data is None:
        return None
    try:
        choice = MusicChoice.model_validate(data)
    except ValueError:
        raise AppError("studio/music.json beschreibt keine gültige Musik.", code="music_invalid", status="blocked") from None
    path = inside(Path(root), choice.file)
    if not path.is_file() or file_hash(path) != choice.sha256:
        raise AppError(f"Die Musikdatei {choice.file} fehlt oder wurde geändert.", code="music_changed", status="blocked")
    return choice


def music_record(root) -> dict | None:
    """What a recording binds of the project's music: the checked choice, or None."""
    choice = music_choice(root)
    return choice.model_dump() if choice else None


def music_hash(record):
    """The hash an audio approval names for the music beside the audio choice's: empty for a project without music."""
    return digest(record) if record else ""


def montage_music(root, record):
    """What audio.assemble needs of a bound record: the file, the bar and where the chapter change's bars begin."""
    choice = MusicChoice.model_validate(record)
    path = inside(Path(root), choice.file)
    if not path.is_file() or file_hash(path) != choice.sha256:
        raise AppError(f"Die Musikdatei {choice.file} fehlt oder wurde geändert.", code="music_changed", status="blocked")
    return {"path": path, "bar_seconds": choice.bar_seconds, "chapter_from": choice.chapter_from_seconds,
            "credit": {key: record[key] for key in ("title", "author", "source", "license")}}


def credit_line(record, language):
    """The credit the music's license asks for, in the podcast's language (D-153)."""
    return wording(language, "music_credit", title=record["title"], author=record["author"], source=record["source"],
                   license=record["license"])
