from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Annotated, ClassVar, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
NonEmpty = Annotated[str, Field(min_length=1)]
State = Literal["pending", "running", "completed", "waiting_for_quota", "blocked", "failed"]


class HostVoices(TypedDict):
    """Two named fields, also representable in providers' strict JSON schemas."""

    host_a: NonEmpty
    host_b: NonEmpty


class HostNames(TypedDict):
    """What the hosts call each other. A voice preset name is not a host identity."""

    host_a: NonEmpty
    host_b: NonEmpty


class SeriesGoal(TypedDict):
    """What the series is for, each aim weighted 0 (not wanted) to 3 (the main aim).

    ``understand`` explains ideas and theories in their own logic, ``evaluate`` tests what holds, ``apply``
    shows how to build or do it. The research plan, the series plan and the reviews weigh their work by it
    (2026-09-30: both series came out as evidence audits, although the user wanted theories explained and,
    for the knowledge-work series, how to build a knowledge base today)."""

    understand: Annotated[int, Field(ge=0, le=3)]
    evaluate: Annotated[int, Field(ge=0, le=3)]
    apply: Annotated[int, Field(ge=0, le=3)]


# Fields added to the brief after runs were recorded; while unset they are left out of every dump, so the hash
# of an unchanged project.yaml, and every binding built from the brief, stays what it was.
LATER_BRIEF_FIELDS = ("series_goal", "recency_months")

ROLE_LABELS = {"host_a": "Host A", "host_b": "Host B"}


def host_labels(config):
    """Spoken-role labels for transcripts and the reading page, never the voice preset."""
    names = getattr(config, "host_names", None) or {}
    return {role: (names.get(role) or ROLE_LABELS[role]) for role in ROLE_LABELS}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class LaterFields(Contract):
    """A contract whose fields listed in ``LATER`` arrived after files were written: at their default they are
    left out of every dump, so stored files, the hashes built from them and the prompts that show them stay as
    they were. A model answer still names them, since strict schemas require every property."""

    LATER: ClassVar[dict] = {}

    @model_serializer(mode="wrap")
    def omit_later_defaults(self, handler):
        data = handler(self)
        for key, default in self.LATER.items():
            if key in data and data[key] == default:
                data.pop(key)
        return data


class ResearchLimits(Contract):
    # Defaults for new projects since 2026-09-27; a saved project keeps the limits in its project.yaml.
    # Measured on the two 18-question runs of 2026-09-26/27 with Opus 5.5: 600 to 750 calls up to the
    # third audit round, 31 and 46 search rounds, 82 and 114 fetched sources.
    search_rounds: int = Field(default=48, gt=0)
    sources: int = Field(default=150, gt=0)
    model_calls: int = Field(default=750, gt=0)


class RuntimeSettings(Contract):
    codex_executable: NonEmpty = "codex"
    codex_model: str | None = None
    text_timeout_seconds: int = Field(default=1800, gt=0)
    tts_python: NonEmpty = Field(default_factory=lambda: sys.executable)
    tts_model: NonEmpty = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    tts_revision: NonEmpty = "main"
    tts_device: Literal["auto", "cuda:0", "mps", "cpu"] = "auto"
    tts_attention: Literal["eager", "sdpa"] = "eager"
    tts_timeout_seconds: int = Field(default=3600, gt=0)
    seed: int = Field(default=42, ge=0, lt=2**32)


class TopicBrief(Contract):
    schema_version: Literal["1.0"] = "1.0"
    topic: NonEmpty
    central_question: str = ""
    language: Literal["de-DE", "en-US"] = "de-DE"
    audience_level: str = "Neugierige Erwachsene ohne spezielles Vorwissen; fachlich anspruchsvoll und auf Augenhöhe"
    prior_knowledge: str = ""
    depth_request: str = ("Fachliche Tiefe von Grund auf entwickeln, klar und auf Augenhöhe. "
                          "Eine zusammenhängende Erklärung aus Problem, Lösungsversuch und weiterführenden "
                          "Konsequenzen aufbauen. Nicht nur sagen, was gilt, sondern erklären, warum es gilt "
                          "und unter welchen Voraussetzungen. Allgemeine Auffassungsgabe voraussetzen, "
                          "nötige Fachbegriffe knapp im Zusammenhang einführen und danach normal verwenden. "
                          "Ein gutes Beispiel nachvollziehbar ausarbeiten; Metaphern gezielt und sparsam einsetzen. "
                          "Keine Formeln oder unerklärten Abkürzungen. Keine belehrenden Vorreden, wiederholten "
                          "Definitionen oder mehrfachen Zusammenfassungen derselben Idee. Grenzen dort einmal benennen, "
                          "wo sie für das Verständnis wichtig sind. Fachliche Tiefe und natürlichen Gesprächsfluss erhalten.")
    focus_questions: list[str] = Field(default_factory=list)
    excluded_topics: list[str] = Field(default_factory=list)
    seed_people: list[str] = Field(default_factory=list)
    seed_urls: list[str] = Field(default_factory=list)
    local_sources: list[str] = Field(default_factory=list)
    target_total_minutes: float | None = Field(default=None, gt=0)
    # Unused since 2026-10-04, when one episode became one MP3; until then the longest audio part a recording was split
    # into. Kept because every run's project hash (storage.project_hash) includes it: dropping it would refuse every
    # resume with inputs_changed. The cap on an episode is script_models.MAX_EPISODE_MINUTES.
    max_episode_minutes: Literal[30] = 30
    research_limits: ResearchLimits = Field(default_factory=ResearchLimits)
    # project.yaml records the CLI defaults. The Studio stores a project's actual provider
    # choices next to it in studio/text.json (text model, effort) and studio/audio.json
    # (Qwen or Gemini voices); those files never hold credentials.
    text_backend: Literal["codex_cli", "claude_code", "auto"] = Field(
        default="codex_cli", description="CLI default; the Studio selection lives in studio/text.json.")
    tts_backend: Literal["qwen3_local"] = Field(
        default="qwen3_local", description="CLI default; the Studio selection lives in studio/audio.json.")
    voice_profile: HostVoices = Field(
        default_factory=lambda: {"host_a": "Ryan", "host_b": "Serena"})
    host_names: HostNames | None = Field(default=None,
        description="Optional names the hosts use for each other; never invented by a model.")
    style_profile_id: Literal["de_calm_deep"] = "de_calm_deep"
    export_context: Literal["private_learning"] = "private_learning"
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    series_goal: SeriesGoal | None = Field(default=None,
        description="Weights 0-3 for understand, evaluate and apply; unset means the pipeline's evaluating default.")
    # A fast field (AI changes within three months, 2026-09-30) prefers practice, tool and benchmark sources
    # published within this many months; standards and foundations may be older and are named with their year.
    recency_months: int | None = Field(default=None, ge=1, le=120,
        description="Prefer practice, tool and benchmark sources from the last N months; unset means no rule.")

    @model_serializer(mode="wrap")
    def omit_unset_later_fields(self, handler):
        data = handler(self)
        for key in LATER_BRIEF_FIELDS:
            if data.get(key) is None:
                data.pop(key, None)
        return data

    @model_validator(mode="after")
    def some_goal(self):
        if self.series_goal is not None and not any(self.series_goal.values()):
            raise ValueError("series_goal braucht mindestens ein Ziel über 0")
        return self

    @model_validator(mode="after")
    def two_voices(self):
        if set(self.voice_profile) != {"host_a", "host_b"}:
            raise ValueError("voice_profile benötigt host_a und host_b")
        if len(set(self.voice_profile.values())) != 2:
            raise ValueError("Die Hörprobe benötigt zwei unterschiedliche Stimmen")
        return self


class Segment(Contract):
    segment_id: Identifier
    scene_id: Identifier
    chapter_id: Identifier
    speaker_id: Literal["host_a", "host_b"]
    text: NonEmpty
    knowledge_refs: list[Identifier] = Field(default_factory=list)
    pause_after_ms: int = Field(default=400, ge=0, le=10000)


class Chapter(Contract):
    chapter_id: Identifier
    title: NonEmpty


class EpisodeScript(Contract):
    schema_version: Literal["1.0"] = "1.0"
    episode_id: Identifier
    title: NonEmpty
    purpose: Literal["technical_probe", "deep_dive"]
    chapters: list[Chapter] = Field(min_length=1)
    segments: list[Segment] = Field(min_length=1)

    @model_validator(mode="after")
    def check_structure(self):
        segment_ids = [s.segment_id for s in self.segments]
        chapter_ids = [c.chapter_id for c in self.chapters]
        if len(segment_ids) != len(set(segment_ids)) or len(chapter_ids) != len(set(chapter_ids)):
            raise ValueError("Segment- und Kapitel-IDs müssen jeweils eindeutig sein")
        order = []
        for segment in self.segments:
            if not order or order[-1] != segment.chapter_id:
                order.append(segment.chapter_id)
        if order != chapter_ids:
            raise ValueError("Kapitel müssen vollständig und zusammenhängend in Skriptreihenfolge vorkommen")
        return self


class TextProbeOutput(Contract):
    topic: NonEmpty
    focus_questions: list[NonEmpty] = Field(min_length=1)
    note: NonEmpty


class Failure(LaterFields):
    code: str
    message: str
    # Since 2026-10-02 the facts a paused stage is resumed by (provider, reset time; runner.failure_details). Unset
    # it is left out of every dump, so manifests written before stay byte-identical.
    details: dict[str, str | int | float | bool | None] | None = None

    LATER: ClassVar[dict] = {"details": None}


class StageRecord(Contract):
    status: State = "pending"
    attempts: int = 0
    outputs: dict[str, str] = Field(default_factory=dict)
    error: Failure | None = None


class RunManifest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    pipeline_version: str = "0.1.0"
    run_id: Identifier
    kind: Literal["text_probe", "audio_probe", "research", "script", "episode_audio", "series_review"]
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    project_hash: str
    input_hash: str
    status: State = "pending"
    audio_approved: bool = False
    stages: dict[str, StageRecord]


SCHEMAS = {
    "topic_brief": TopicBrief,
    "episode_script": EpisodeScript,
    "text_probe_output": TextProbeOutput,
    "run_manifest": RunManifest,
}
