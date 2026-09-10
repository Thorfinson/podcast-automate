from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
NonEmpty = Annotated[str, Field(min_length=1)]
State = Literal["pending", "running", "completed", "waiting_for_quota", "blocked", "failed"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class ResearchLimits(Contract):
    search_rounds: int = Field(default=3, gt=0)
    sources: int = Field(default=30, gt=0)
    model_calls: int = Field(default=40, gt=0)


class RuntimeSettings(Contract):
    codex_executable: NonEmpty = "codex"
    codex_model: str | None = None
    text_timeout_seconds: int = Field(default=600, gt=0)
    tts_python: NonEmpty = Field(default_factory=lambda: sys.executable)
    tts_model: NonEmpty = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    tts_revision: NonEmpty = "main"
    tts_device: Literal["cuda:0", "cpu"] = "cuda:0"
    tts_attention: Literal["eager", "sdpa"] = "eager"
    tts_timeout_seconds: int = Field(default=3600, gt=0)
    seed: int = Field(default=42, ge=0, lt=2**32)


class TopicBrief(Contract):
    schema_version: Literal["1.0"] = "1.0"
    topic: NonEmpty
    central_question: str = ""
    language: Literal["de-DE"] = "de-DE"
    audience_level: str = "Anspruchsvoll, mit verständlich erklärten Voraussetzungen"
    prior_knowledge: str = ""
    depth_request: str = "Mechanismen, ausgearbeitete Beispiele, Belege und Grenzen"
    focus_questions: list[str] = Field(default_factory=list)
    excluded_topics: list[str] = Field(default_factory=list)
    seed_people: list[str] = Field(default_factory=list)
    seed_urls: list[str] = Field(default_factory=list)
    local_sources: list[str] = Field(default_factory=list)
    target_total_minutes: float | None = Field(default=None, gt=0)
    max_episode_minutes: Literal[30] = 30
    research_limits: ResearchLimits = Field(default_factory=ResearchLimits)
    text_backend: Literal["codex_cli"] = "codex_cli"
    tts_backend: Literal["qwen3_local"] = "qwen3_local"
    voice_profile: dict[Literal["host_a", "host_b"], NonEmpty] = Field(
        default_factory=lambda: {"host_a": "Ryan", "host_b": "Serena"})
    style_profile_id: Literal["de_calm_deep"] = "de_calm_deep"
    export_context: Literal["private_learning"] = "private_learning"
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)

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


class Failure(Contract):
    code: str
    message: str


class StageRecord(Contract):
    status: State = "pending"
    attempts: int = 0
    outputs: dict[str, str] = Field(default_factory=dict)
    error: Failure | None = None


class RunManifest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    pipeline_version: str = "0.1.0"
    run_id: Identifier
    kind: Literal["text_probe", "audio_probe"]
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
