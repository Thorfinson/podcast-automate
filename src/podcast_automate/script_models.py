"""A source-bound outline and review contracts for the first editorial pipeline."""
from typing import Literal

from pydantic import Field

from .models import Contract, Identifier, NonEmpty
from .research_models import Finding


class Dependency(Contract):
    before: Identifier
    after: Identifier
    reason: NonEmpty


class Omission(Contract):
    finding_id: Identifier
    reason: NonEmpty


class ScenePlan(Contract):
    scene_id: Identifier
    title: NonEmpty
    question: NonEmpty
    purpose: Literal["orientation", "explanation", "worked_example", "limitation", "synthesis"]
    finding_ids: list[Identifier]
    explanation_steps: list[NonEmpty] = Field(min_length=1)


class EpisodePlan(Contract):
    episode_id: Identifier
    title: NonEmpty
    central_question: NonEmpty
    target_minutes: float = Field(gt=0, le=30)
    prerequisite_episodes: list[Identifier]
    finding_ids: list[Identifier] = Field(min_length=1)
    scenes: list[ScenePlan] = Field(min_length=1)
    deferred_questions: list[NonEmpty]


class SeriesPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    topic: NonEmpty
    central_question: NonEmpty
    explanation_path: NonEmpty
    scope_note: NonEmpty
    dependencies: list[Dependency]
    episodes: list[EpisodePlan] = Field(min_length=1)
    omitted_findings: list[Omission]


class KnowledgeModel(Contract):
    schema_version: Literal["1.0"] = "1.0"
    research_run_id: Identifier
    topic: NonEmpty
    claims: list[Finding]
    key_terms: list[Identifier]
    mechanisms: list[Identifier]
    examples: list[Identifier]
    counterpoints: list[Identifier]
    dependencies: list[Dependency]
    uncertainties: list[str]
    editorial_priorities: str


class ScriptIssue(Contract):
    category: Literal["grounding", "clarity", "depth", "dialogue", "scope", "structure"]
    segment_ids: list[Identifier]
    reason: NonEmpty


class ScriptReview(Contract):
    issues: list[ScriptIssue]
    limitations: list[str]


SCRIPT_SCHEMAS = {
    "knowledge_model": KnowledgeModel,
    "series_plan": SeriesPlan,
    "episode_plan": EpisodePlan,
    "script_review": ScriptReview,
}
