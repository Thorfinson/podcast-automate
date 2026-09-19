"""Distinguish missing evidence from edits supported by the current passages."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .prompts import instructions
from .models import Contract, NonEmpty
from .research_models import DossierReview, ReviewIssue
from .evidence_models import FindingSupport, ResearchObjection, SourceAssessment, ObjectionClosure


ROUTING_INSTRUCTIONS = instructions("routing_instructions")


class Resolution(Contract):
    resolution: Literal["research", "revise"]
    search_queries: list[NonEmpty] = Field(max_length=4)

    @model_validator(mode="after")
    def queries_match_resolution(self):
        if bool(self.search_queries) != (self.resolution == "research"):
            raise ValueError("Research requires search queries; a text correction must not include them.")
        return self


class SourceReviewIssue(ReviewIssue, Resolution):
    objection: ResearchObjection | None = None


class SourceReview(DossierReview):
    issues: list[SourceReviewIssue]
    finding_support: list[FindingSupport] = Field(default_factory=list)
    source_assessments: list[SourceAssessment] = Field(default_factory=list)
    objection_checks: list[ObjectionClosure] = Field(default_factory=list)


def needs_research(review):
    return any(i.resolution == "research" for i in review.issues)
