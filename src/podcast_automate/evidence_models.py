"""Evidence contracts shared by research and editorial stages.

These describe what was checked, not a claim that an automated reviewer is infallible.
Unknown bibliographic and methodological properties stay unknown.
"""
from typing import Literal

from pydantic import Field, model_validator

from .models import Contract, Identifier, NonEmpty

EVIDENCE_VERSION = "evidence.v1"
# Free-text justifications are records, not essays: a long one costs minutes per review call.
BRIEF = "One sentence naming the passage and the defect; at most 300 characters."


class Quantity(Contract):
    name: NonEmpty
    value: NonEmpty = Field(description="Normalized number or interval; language-independent decimal notation.")
    unit: NonEmpty
    direction: Literal["increase", "decrease", "no_change", "unspecified"]


class ClaimContract(Contract):
    basis: Literal["source_definition", "source_theory", "empirical", "editorial_synthesis"]
    relation: Literal["definition", "description", "association", "causal", "prediction", "uncertainty"]
    scope: list[NonEmpty] = Field(min_length=1, description="Population, setting, time scale or explicit conceptual scope.")
    qualifications: list[NonEmpty]
    quantities: list[Quantity]


class SourceAssessment(Contract):
    source_id: Identifier
    roles: list[Literal["original_definition", "theory_exposition", "empirical_test", "replication",
                        "critical_comparison", "synthesis", "unknown"]] = Field(min_length=1)
    evidence_refs: list[NonEmpty] = Field(min_length=1)
    rationale: NonEmpty
    work_id: str = Field(description="DOI or source-grounded work identity; empty if unknown.")
    version: str
    evidence_family: str = Field(description="Shared study/dataset identity, not a URL; empty if unknown.")
    independence: Literal["independent", "shared", "unknown"]
    method: str
    research_group: str
    population: str
    geography: str
    period: str
    limitations: list[NonEmpty] = Field(description="Each entry: " + BRIEF)


class FindingSupport(Contract):
    finding_id: Identifier
    verdict: Literal["supported", "partially_supported", "contradicted", "insufficient_context"]
    references: list[NonEmpty] = Field(min_length=1)
    reason: NonEmpty = Field(description=BRIEF)
    unsupported_clauses: list[NonEmpty]
    suitability: Literal["suitable", "unsuitable", "unknown"]
    suitability_reason: NonEmpty = Field(description="One sentence on whether this source can carry this assertion; "
                                                     "at most 300 characters.")
    contract_preserved: bool
    empirical_status: Literal["not_applicable", "tested_in_source", "independently_tested", "unknown"]
    independent_evidence_refs: list[NonEmpty]

    @model_validator(mode="before")
    @classmethod
    def clauses_decide_partial_support(cls, data):
        # The clause list is the judgement and the verdict its summary: a "supported" finding that
        # names unsupported clauses is partially supported. Only a contradiction or missing context
        # is a verdict in its own right, so those two stay with the reviewer.
        if isinstance(data, dict) and data.get("verdict") == "supported" and data.get("unsupported_clauses"):
            return {**data, "verdict": "partially_supported"}
        return data

    @model_validator(mode="after")
    def coherent_support(self):
        if self.verdict in {"partially_supported", "contradicted"} and not self.unsupported_clauses:
            raise ValueError("Name the unsupported or contradicted clauses.")
        if self.empirical_status == "independently_tested" and not self.independent_evidence_refs:
            raise ValueError("Independent testing requires original evidence references.")
        return self


class SynthesisRelation(Contract):
    id: Identifier
    finding_ids: list[Identifier] = Field(min_length=2, max_length=6)
    dimension: NonEmpty
    relation: Literal["contradiction", "conditional_difference", "no_material_conflict", "insufficient_overlap"]
    conditions: NonEmpty
    comparability: Literal["same_conditions", "different_conditions", "unknown"] = "unknown"
    evidence_refs: list[NonEmpty] = Field(min_length=1)
    resolution: Literal["resolved", "unresolved", "not_applicable"]
    explanation: NonEmpty
    basis: Literal["editorial_synthesis", "source_comparison"]


class ResearchObjection(Contract):
    id: Identifier
    rule: Literal["support", "source_suitability", "claim_preservation", "synthesis", "scope", "criterion"]
    task_id: str
    criterion_index: int | None = Field(default=None, ge=0)
    finding_ids: list[Identifier]
    evidence_refs: list[NonEmpty]
    missing_evidence: str
    reason: NonEmpty
    correction: NonEmpty
    closure_condition: NonEmpty
    resolution: Literal["research", "revise", "review_disagreement"]

    @model_validator(mode="after")
    def anchored(self):
        if not self.evidence_refs and not self.missing_evidence:
            raise ValueError("An objection needs evidence or a specific missing-evidence statement.")
        if self.rule == "criterion" and (not self.task_id or self.criterion_index is None):
            raise ValueError("A criterion objection must identify the fixed task and criterion.")
        return self


class ExtractionCoverage(Contract):
    pages_total: int | None = None
    pages_with_text: int | None = None
    empty_pages: list[int] = Field(default_factory=list)
    suspected_image_pages: list[int] = Field(default_factory=list)
    suspected_table_pages: list[int] = Field(default_factory=list)
    suspected_equation_pages: list[int] = Field(default_factory=list)
    truncated: bool = False
    notes: list[str] = Field(default_factory=list)


class ObjectionClosure(Contract):
    objection_id: Identifier
    verdict: Literal["closed", "open", "review_disagreement"]
    references: list[NonEmpty]
    reason: NonEmpty


class SegmentClaimCheck(Contract):
    segment_id: Identifier
    finding_ids: list[Identifier]
    verdict: Literal["preserved", "drift", "no_research_claim"]
    quote: NonEmpty
    reason: NonEmpty
    changed_fields: list[Literal["relation", "scope", "qualifications", "quantities", "basis"]]
