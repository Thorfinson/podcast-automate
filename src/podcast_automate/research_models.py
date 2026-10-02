"""Contracts for a bounded research pass; these are not a finished series model."""
from typing import Literal

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from .models import Contract, Identifier, NonEmpty, LaterFields
from .evidence_models import ClaimContract, ExtractionCoverage, SourceAssessment, SynthesisRelation


class ResearchQuestion(Contract):
    id: Identifier
    question: NonEmpty
    search_query: NonEmpty


# What a source is for (2026-09-30). ``primary_work``: the author's own exposition of an idea or theory (book,
# chapter, article, lecture), required to explain it. ``study``: an empirical test. ``critique``: a critique,
# comparison or replication. ``overview``: a review, textbook or encyclopedia entry. ``practice``: documentation,
# a maintained repository or a practitioner report showing how something is done now (not whether it works).
# ``standard``: a specification. ``idea``: a pointer (the user's attachments, LLM-written notes, social posts)
# that names what to research and is never evidence. ``unknown``: recorded before types existed.
SourceType = Literal["primary_work", "study", "critique", "overview", "practice", "standard", "idea", "unknown"]


class SourceCandidate(LaterFields):
    url: NonEmpty
    title: NonEmpty
    authors: list[str]
    published_date: str
    rationale: NonEmpty
    primary_source: bool
    source_type: SourceType = "unknown"

    LATER = {"source_type": "unknown"}


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


class SourceDocument(LaterFields):
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
    source_type: SourceType = "unknown"
    # Where published_date came from: the document's own metadata, a search result, the editor's citation, or nowhere.
    date_basis: Literal["document", "search_result", "citation", "unknown"] = "unknown"
    # The editor's reference for a copy they provided (provided_works): a published work, not their own notes.
    citation: str = ""

    LATER = {"source_type": "unknown", "date_basis": "unknown", "citation": ""}


class SourceIndex(Contract):
    schema_version: Literal["1.0"] = "1.0"
    sources: list[SourceDocument]
    failures: list[dict[str, str]]


# Findings a composed dossier holds. Part of every dossier call's output schema, so of its saved receipts' signatures.
MAX_FINDINGS = 120


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


class AnswerDigest(Contract):
    """One verified answer as an assembled dossier keeps it: its question, summary, limits and the ids its findings
    carry in the dossier."""
    task_id: Identifier
    question: NonEmpty
    question_ids: list[Identifier]
    summary: NonEmpty
    limits: list[NonEmpty]
    finding_ids: list[Identifier]


class ResearchDossier(LaterFields):
    schema_version: Literal["1.0"] = "1.0"
    topic: NonEmpty
    scope_note: NonEmpty
    # MAX_FINDINGS bounds a dossier a model writes; the schema those calls see keeps it (bounded_findings).
    findings: list[Finding] = Field(min_length=1, json_schema_extra={"maxItems": MAX_FINDINGS})
    coverage: list[QuestionCoverage]
    open_questions: list[NonEmpty]
    evidence_version: str = ""
    source_assessments: list[SourceAssessment] = Field(default_factory=list)
    synthesis: list[SynthesisRelation] = Field(default_factory=list, max_length=40)
    # Assembled from the verified answers without a model call (question_synthesis.assemble_dossier, prompt generation
    # 3): every finding of every answer, and the answers' summaries and limits. No model writes these two fields, so
    # they stay out of the schema the composing calls see and their saved receipts keep their signatures.
    assembled: SkipJsonSchema[bool] = False
    answers: SkipJsonSchema[list[AnswerDigest]] = Field(default_factory=list)

    LATER = {"assembled": False, "answers": []}

    @model_validator(mode="after")
    def bounded_findings(self):
        # A composed dossier kept at most MAX_FINDINGS and lost the rest of the verified answers (2026-10-01:
        # 120 of 335 in Ontologies); an assembled one holds all of them.
        if not self.assembled and len(self.findings) > MAX_FINDINGS:
            raise ValueError(f"A composed dossier holds at most {MAX_FINDINGS} findings")
        return self


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


def is_idea(source):
    """An idea source (user material without a URL, or a source typed ``idea``) names what to research and is
    never evidence (the user's choice, 2026-09-30: the LLM-written SYM notes and the curated posts are maps). A copy
    of a published work the editor provided names it in ``citation`` and counts like a downloaded one (2026-10-01)."""
    if getattr(source, "citation", ""):
        return source.source_type == "idea"
    return not source.url or not source.final_url or source.source_type == "idea"


def admissible(candidate):
    """A search result that may join the corpus: any typed source except an idea source, or, from before source
    types existed, one marked primary."""
    if candidate.source_type == "unknown":
        return candidate.primary_source
    return candidate.source_type != "idea"
