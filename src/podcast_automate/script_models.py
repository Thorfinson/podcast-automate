"""A source-bound outline and review contracts for the first editorial pipeline."""
from typing import Literal

from pydantic import Field

from .models import Contract, Identifier, LaterFields, NonEmpty
from .research_models import Finding
from .evidence_models import SegmentClaimCheck, SourceAssessment, SynthesisRelation


class Dependency(Contract):
    before: Identifier = Field(description="Dossier finding ID introduced first; never an episode or scene ID.")
    after: Identifier = Field(description="Dossier finding ID that needs 'before'; first introduced in the same or a later scene.")
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


# An episode takes the time its explanation needs, up to an hour (2026-09-30; the 30-minute cap squeezed four
# grand theories into one episode). Longer recordings are split into parts of at most 30 minutes.
MAX_EPISODE_MINUTES = 60


class EpisodePlan(LaterFields):
    episode_id: Identifier
    title: NonEmpty
    central_question: NonEmpty
    target_minutes: float = Field(gt=0, le=MAX_EPISODE_MINUTES)
    prerequisite_episodes: list[Identifier] = Field(description="Earlier episode IDs; finding dependencies belong in SeriesPlan.dependencies.")
    finding_ids: list[Identifier] = Field(min_length=1)
    scenes: list[ScenePlan] = Field(min_length=1)
    deferred_questions: list[NonEmpty]
    series_role: str = Field(default="", description=(
        "What this episode contributes to the answer to the series' central question, in one or two sentences."))
    recap_finding_ids: list[Identifier] = Field(default_factory=list, description=(
        "Findings introduced in earlier episodes that this episode recalls, e.g. in the finale's synthesis; "
        "they may be cited but need not all be covered."))
    # Two tiers since 2026-10-02: finding_ids are the core the dialogue must develop. An assembled dossier holds every
    # verified answer (Transformer: 327 findings, about 55 per episode, one cited finding per 140 spoken words), and
    # covering all of them pulled episodes into detail instead of the theory-first explanation the user asked for.
    supporting_finding_ids: list[Identifier] = Field(default_factory=list, description=(
        "Study detail this episode may cite where it backs a statement, in any scene, without covering it; "
        "never one of the episode's own finding_ids."))
    research_limit_ids: list[Identifier] = Field(default_factory=list, description=(
        "The limit_id of each entry in research_limits this episode states where the affected statement is used."))

    LATER = {"series_role": "", "recap_finding_ids": [], "supporting_finding_ids": [], "research_limit_ids": []}


def episode_findings(entry):
    """The findings an episode may cite: its core, its supporting ones, then those it recalls from earlier episodes."""
    return list(dict.fromkeys([*entry.finding_ids, *entry.supporting_finding_ids, *entry.recap_finding_ids]))


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
    synthesis: list[SynthesisRelation] = Field(default_factory=list)
    source_assessments: list[SourceAssessment] = Field(default_factory=list)


class ScriptIssue(Contract):
    category: Literal["grounding", "clarity", "depth", "dialogue", "scope", "structure"]
    segment_ids: list[Identifier]
    reason: NonEmpty


# Why a follow-up review lets an issue block: a previous issue still open, a segment the repair changed, or a
# statement the sources show to be wrong (script_pipeline.follow_up_scope).
ScriptIssueBasis = Literal["previous", "changed", "factual_error", "source_contradiction"]


class ScriptReview(Contract):
    issues: list[ScriptIssue]
    limitations: list[str]
    claim_checks: list[SegmentClaimCheck] = Field(default_factory=list)
    # Follow-up reviews only: one basis per issue, in order; the points outside their scope are advisories.
    issue_basis: list[ScriptIssueBasis] = Field(default_factory=list)
    advisories: list[ScriptIssue] = Field(default_factory=list)


SCRIPT_SCHEMAS = {
    "knowledge_model": KnowledgeModel,
    "series_plan": SeriesPlan,
    "episode_plan": EpisodePlan,
    "script_review": ScriptReview,
}
