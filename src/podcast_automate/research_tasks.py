"""Fixed research questions, explicit reader actions and independently checked answers."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .models import Contract, Identifier, NonEmpty
from .research_models import Finding, SourceCandidate
from .evidence_models import BRIEF, FindingSupport, ResearchObjection, SourceAssessment


class QuestionTask(Contract):
    id: Identifier = Field(max_length=32, pattern=r"^task_[a-z0-9_]+$")
    requirement_ids: list[Identifier] = Field(min_length=1)
    question_ids: list[Identifier] = Field(min_length=1)
    question: NonEmpty
    kind: Literal["definition", "theory", "mechanism", "empirical", "example", "boundaries", "synthesis"]
    depends_on: list[Identifier] = Field(default_factory=list)
    acceptance: list[NonEmpty] = Field(min_length=1, max_length=6)
    queries: list[NonEmpty] = Field(min_length=1, max_length=4)
    key_terms: list[NonEmpty] = Field(max_length=6)
    finding_ids: list[Identifier]
    gap_ids: list[Identifier]


class QuestionPlan(Contract):
    tasks: list[QuestionTask] = Field(min_length=1, max_length=128)


class CriterionAnswer(Contract):
    index: int = Field(ge=0)
    explanation: NonEmpty = Field(description="One sentence naming the findings that meet this criterion; "
                                              "at most 300 characters.")
    finding_ids: list[Identifier] = Field(min_length=1)


class QuestionAnswer(Contract):
    summary: NonEmpty
    findings: list[Finding] = Field(min_length=1, max_length=6)
    criteria: list[CriterionAnswer] = Field(min_length=1, max_length=6)
    limits: list[NonEmpty]
    outcome: Literal["supported_answer", "supported_uncertainty"] = "supported_answer"


class ReaderSearch(Contract):
    query: NonEmpty
    source_id: str  # empty = all sources; local notes are a separate, explicit lane
    offset: int = Field(ge=0, le=1_000_000)
    include_notes: bool


class ReaderWindow(Contract):
    reference: NonEmpty
    before: int = Field(ge=0, le=3)
    after: int = Field(ge=0, le=5)


class ResearchDecision(Contract):
    action: Literal["read", "search_local", "search_web", "answer", "blocked"]
    reason: NonEmpty = Field(description="One sentence naming the next step and the criterion it serves; "
                                         "at most 300 characters.")
    searches: list[ReaderSearch] = Field(max_length=4)
    windows: list[ReaderWindow] = Field(max_length=8)
    web_queries: list[NonEmpty] = Field(max_length=4)
    answer: QuestionAnswer | None
    block_kind: Literal["access", "extraction", "search", "budget", "evidence"] | None = None

    @model_validator(mode="after")
    def action_payload(self):
        expected = {"read": "windows", "search_local": "searches", "search_web": "web_queries", "answer": "answer"}
        for name in ("windows", "searches", "web_queries", "answer"):
            if bool(getattr(self, name)) != (expected.get(self.action) == name):
                raise ValueError("Supply only the payload of the selected reader action")
        return self


class CriterionVerdict(Contract):
    index: int = Field(ge=0)
    passed: bool
    reason: NonEmpty = Field(description=BRIEF)


class AnswerReview(Contract):
    criteria: list[CriterionVerdict] = Field(min_length=1)
    supported: bool
    source_adequacy: bool
    issues: list[NonEmpty] = Field(description="Defects of the answer that fail no criterion; each entry: " + BRIEF)
    limitations: list[NonEmpty] = Field(default_factory=list,
                                        description="What this review itself could not check; each entry: " + BRIEF)
    finding_support: list[FindingSupport] = Field(default_factory=list)
    source_assessments: list[SourceAssessment] = Field(default_factory=list)


class QuestionSearch(Contract):
    candidates: list[SourceCandidate] = Field(max_length=4)
    limitations: list[NonEmpty]
    executed_queries: list[NonEmpty] = Field(default_factory=list)
    counterevidence: str = ""
    excluded_candidates: list[SourceCandidate] = Field(default_factory=list, max_length=8)


class ObjectionRoute(Contract):
    index: int = Field(ge=0)
    task_ids: list[Identifier] = Field(min_length=1)
    reason: NonEmpty
    anchors: list[ResearchObjection] = Field(default_factory=list)


class ReopenPlan(Contract):
    routes: list[ObjectionRoute] = Field(min_length=1)
