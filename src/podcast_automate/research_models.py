"""Contracts for a bounded research pass; these are not a finished series model."""
from typing import Literal

from pydantic import Field, model_validator

from .models import Contract, Identifier, NonEmpty
from .evidence_models import ClaimContract, ExtractionCoverage, SourceAssessment, SynthesisRelation


class ResearchQuestion(Contract):
    id: Identifier
    question: NonEmpty
    search_query: NonEmpty


class SourceCandidate(Contract):
    url: NonEmpty
    title: NonEmpty
    authors: list[str]
    published_date: str
    rationale: NonEmpty
    primary_source: bool


class ResearchDiscovery(Contract):
    topic: NonEmpty
    questions: list[ResearchQuestion] = Field(min_length=1, max_length=32)
    candidates: list[SourceCandidate] = Field(min_length=1)
    limitations: list[str]

    @model_validator(mode="after")
    def unique_questions(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Research question IDs must be unique")
        return self


class SourceSection(Contract):
    id: Identifier
    text: NonEmpty
    page: int | None = None


class SourceDocument(Contract):
    schema_version: Literal["1.0"] = "1.0"
    extraction_version: str = ""
    id: Identifier
    type: Literal["html", "pdf", "text"]
    title: NonEmpty
    authors: list[str]
    published_date: str
    imported_at: str
    url: str
    final_url: str
    language: str = "unknown"
    license_status: Literal["unknown"] = "unknown"
    allowed_usage: Literal["private_learning"] = "private_learning"
    private: bool = True
    reliability_note: str
    uncertainties: list[str]
    raw_path: str
    raw_hash: str
    text_hash: str
    sections: list[SourceSection] = Field(min_length=1)
    extraction_coverage: ExtractionCoverage | None = None


class SourceIndex(Contract):
    schema_version: Literal["1.0"] = "1.0"
    sources: list[SourceDocument]
    failures: list[dict[str, str]]


class Evidence(Contract):
    reference: NonEmpty
    excerpt: NonEmpty = Field(max_length=200)


class Finding(Contract):
    id: Identifier
    kind: Literal["definition", "claim", "mechanism", "example", "limitation"]
    statement: NonEmpty
    evidence: list[Evidence] = Field(min_length=1)
    illustration: str = ""
    illustration_limit: str = ""
    claim_contract: ClaimContract | None = None  # Legacy documents remain readable, never implicitly upgraded.
    supporting_contracts: list[ClaimContract] = Field(default_factory=list)

    @model_validator(mode="after")
    def explain_illustration_limits(self):
        if bool(self.illustration) != bool(self.illustration_limit):
            raise ValueError("An illustration and its limits must be supplied together")
        return self


class QuestionCoverage(Contract):
    question_id: Identifier
    status: Literal["answered", "partial", "unanswered"]
    finding_ids: list[Identifier]
    gap: str
    # The corpus probe compares words, so a German gap over an English corpus finds nothing by
    # itself. These words bridge that: the model names them in the sources' language.
    gap_terms: list[NonEmpty] = Field(default_factory=list, description=(
        "Three to eight search words in the language of the stored sources that a section "
        "answering this gap would contain; empty when the question is answered."))


class ResearchDossier(Contract):
    schema_version: Literal["1.0"] = "1.0"
    topic: NonEmpty
    scope_note: NonEmpty
    findings: list[Finding] = Field(min_length=1, max_length=120)
    coverage: list[QuestionCoverage]
    open_questions: list[NonEmpty]
    evidence_version: str = ""
    source_assessments: list[SourceAssessment] = Field(default_factory=list)
    synthesis: list[SynthesisRelation] = Field(default_factory=list, max_length=40)


class ReviewIssue(Contract):
    finding_id: Identifier
    reason: NonEmpty


class DossierReview(Contract):
    issues: list[ReviewIssue]
    limitations: list[str]


RESEARCH_SCHEMAS = {
    "research_discovery": ResearchDiscovery,
    "source_document": SourceDocument,
    "source_index": SourceIndex,
    "research_dossier": ResearchDossier,
    "dossier_review": DossierReview,
}
