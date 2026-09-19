"""Deterministic receipt validation around (fallible) semantic model judgements."""
from collections import Counter
import re

from .prompts import fragment
from .errors import AppError
from .evidence_models import EVIDENCE_VERSION
from .storage import digest

EVIDENCE_INSTRUCTIONS = fragment("evidence_instructions")

SYNTHESIS_INSTRUCTIONS = fragment("synthesis_instructions")

PROFILES = {
    "definition": "Original definition, conceptual scope and distinctions; no artificial empirical test.",
    "theory": "Original theoretical account, assumptions and predictions; distinguish exposition from validation.",
    "mechanism": "Prerequisite causal steps, assumptions and limits; separate proposed from tested mechanisms.",
    "empirical": "Design, population, measurements, results and limitations; seek counterevidence and shared-data checks.",
    "example": "Source-grounded example; label illustrative extrapolation and its limits.",
    "boundaries": "Evidence of applicability and exceptions, including counterevidence where contested.",
    "synthesis": "Compare verified answers on a common dimension; retain conditional differences and unresolved tensions.",
}


def evidence_error(message):
    raise AppError(message, code="invalid_evidence_review", status="blocked")


def identity(value):
    value = " ".join(value.casefold().split()).rstrip("/")
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)


def support_errors(findings, review, context, *, require_contract=True):
    """Malformed coverage raises; substantive non-passes return actionable feedback."""
    if Counter(r.finding_id for r in review.finding_support) != Counter(f.id for f in findings):
        evidence_error("Evidence review must assess every finding exactly once.")
    passages = {p["reference"]: s for s in context for p in s["sections"]}
    cited = {e.reference for f in findings for e in f.evidence}
    source_ids = {passages[r]["source_id"] for r in cited if r in passages}
    if not cited <= passages.keys():
        evidence_error("Evidence review contains unread or unknown passages.")
    if Counter(a.source_id for a in review.source_assessments) != Counter(source_ids):
        evidence_error("Assess the suitability and identity of every cited source exactly once.")
    assessments = {a.source_id: a for a in review.source_assessments}
    for assessment in assessments.values():
        if any(r not in passages or passages[r]["source_id"] != assessment.source_id for r in assessment.evidence_refs):
            evidence_error("Source assessment must be grounded in that source's supplied passages.")
        if assessment.independence != "unknown" and not assessment.evidence_family:
            evidence_error("Known independence requires a source-grounded study or dataset identity.")
    by_id = {f.id: f for f in findings}
    errors = []
    for row in review.finding_support:
        finding = by_id[row.finding_id]
        refs = {e.reference for e in finding.evidence}
        if set(row.references) != refs or len(row.references) != len(set(row.references)):
            evidence_error("A support receipt must cover precisely the finding's cited passages.")
        if not set(row.independent_evidence_refs) <= refs:
            evidence_error("Independent evidence must be cited by the finding and actually supplied.")
        if row.empirical_status == "independently_tested":
            sources = [assessments[s] for s in {passages[r]["source_id"] for r in row.independent_evidence_refs}]
            source_context = {s["source_id"]: s for s in context}
            documents = [source_context[s.source_id] for s in sources]
            hashes = [s["text_hash"] for s in documents if s.get("text_hash")]
            if len(set(hashes)) != len(hashes) or any("url" in s and not s["url"] for s in documents):
                errors.append(f"{finding.id}: duplicate text or user notes cannot establish independent testing.")
            families = {identity(s.evidence_family) for s in sources if s.independence == "independent" and s.evidence_family}
            works = {identity(s.work_id) for s in sources if s.work_id}
            independent_tests = [s for s in sources if s.independence == "independent" and s.evidence_family
                                 and {"empirical_test", "replication"} & set(s.roles)]
            theoretical = [s for s in sources if "theory_exposition" in s.roles and s.work_id]
            theory_test = any(test.work_id and identity(test.work_id) != identity(theory.work_id)
                              for test in independent_tests for theory in theoretical)
            if not theory_test and (len(families) < 2 or len(works) < 2 or not independent_tests):
                errors.append(f"{finding.id}: independent testing is not established by distinct evidence families and works.")
        if require_contract and finding.claim_contract is None:
            errors.append(f"{finding.id}: missing claim contract (basis, relation, scope and qualifications).")
        if row.verdict != "supported" or row.suitability != "suitable" or not row.contract_preserved:
            errors.append(f"{finding.id}: {row.verdict}; {row.reason}; {row.suitability_reason}; "
                          + "; ".join(row.unsupported_clauses))
    return errors


def validate_synthesis(dossier, context):
    findings = {f.id: f for f in dossier.findings}
    refs = {p["reference"] for s in context for p in s["sections"]}
    if len({r.id for r in dossier.synthesis}) != len(dossier.synthesis):
        evidence_error("Synthesis relation IDs must be unique.")
    for relation in dossier.synthesis:
        if relation.relation == "contradiction" and relation.comparability != "same_conditions":
            evidence_error("A direct contradiction requires comparable populations, measurements and time scales.")
        if len(set(relation.finding_ids)) < 2 or not set(relation.finding_ids) <= findings.keys():
            evidence_error("A synthesis comparison must name at least two actual findings.")
        allowed = {e.reference for fid in relation.finding_ids for e in findings[fid].evidence}
        if not set(relation.evidence_refs) <= (refs & allowed):
            evidence_error("Synthesis relationships must use the compared findings' read evidence.")
        if any(not ({e.reference for e in findings[fid].evidence} & set(relation.evidence_refs))
               for fid in relation.finding_ids):
            evidence_error("A synthesis comparison needs evidence for every compared finding.")


def claim_changes(before, after):
    """Semantic fields are a review receipt, not a language-dependent string heuristic."""
    old = {f.id: f for f in before}
    return [{"finding_id": f.id, "before": old[f.id].model_dump(), "after": f.model_dump(),
             "changed_fields": [k for k in ("statement", "evidence", "claim_contract", "supporting_contracts")
                                if getattr(old[f.id], k) != getattr(f, k)]}
            for f in after if f.id in old and old[f.id] != f]


def concentration(assessments):
    """Descriptive advisories with explicit unknown denominators, never quality gates."""
    rows = list(assessments)
    result = {"sources": len(rows), "advisory_only": True, "dimensions": {}}
    for field in ("evidence_family", "work_id", "method", "research_group", "population", "geography", "period"):
        values = [getattr(a, field) for a in rows if getattr(a, field)]
        counts = Counter(values)
        result["dimensions"][field] = {"known": len(values), "unknown": len(rows) - len(values),
            "counts": dict(counts), "largest_share_of_known": max(counts.values()) / len(values) if values else None}
    return result


def validate_objection(objection, tasks, findings, context, *, target_task=None, owners=None):
    by_task = {t.id: t for t in tasks}
    by_finding = {f.id: f for f in findings}
    refs = {p["reference"] for s in context for p in s["sections"]}
    if not set(objection.finding_ids) <= by_finding.keys() or not set(objection.evidence_refs) <= refs:
        evidence_error("Review objection names unknown findings or unread evidence.")
    if objection.task_id:
        task = by_task.get(objection.task_id)
        if task is None or (objection.criterion_index is not None and objection.criterion_index >= len(task.acceptance)):
            evidence_error("Review objection must name an existing fixed criterion.")
    if objection.rule != "criterion" and not objection.finding_ids:
        evidence_error("A quality-rule objection needs affected findings.")
    if objection.evidence_refs and objection.finding_ids:
        allowed = {e.reference for fid in objection.finding_ids for e in by_finding[fid].evidence}
        if not set(objection.evidence_refs) & allowed:
            evidence_error("Review objection evidence must relate to the affected findings.")
    if target_task:
        owned = any(target_task in owners.get(fid, []) for fid in objection.finding_ids)
        fixed = objection.rule == "criterion" and objection.task_id == target_task
        if not owned and not fixed:
            evidence_error("An objection cannot reopen a task solely because it shares a broad question.")
    # Stable across review rounds; no random model identifier can create a new retry allowance.
    return "obj_" + digest([objection.rule, objection.task_id, objection.criterion_index,
                            sorted(objection.finding_ids), objection.missing_evidence])[:16]


def single_group_findings(findings, assessments):
    """Claims and mechanisms whose every source belongs to one known research group.

    Descriptive, never a gate: 2026 model claims often have no independent test yet, so the
    honest outcome is a stated limitation, not a forced source. Sources whose group is unknown
    are reported as unknown rather than counted as independent.
    """
    groups = {a.source_id: a.research_group for a in assessments}
    rows = []
    for finding in findings:
        if finding.kind not in {"claim", "mechanism"}:
            continue
        sources = list(dict.fromkeys(e.reference.split("#")[0] for e in finding.evidence))
        known = [groups[s] for s in sources if groups.get(s)]
        unknown = [s for s in sources if not groups.get(s)]
        if known and len(set(known)) == 1 and not unknown:
            rows.append({"finding_id": finding.id, "research_group": known[0],
                         "source_ids": sources, "unknown_group_source_ids": []})
        elif not known and sources:
            rows.append({"finding_id": finding.id, "research_group": None,
                         "source_ids": sources, "unknown_group_source_ids": unknown})
        elif known and len(set(known)) == 1:
            rows.append({"finding_id": finding.id, "research_group": known[0],
                         "source_ids": sources, "unknown_group_source_ids": unknown})
    return rows


def evidence_summary(findings, review):
    return {"version": EVIDENCE_VERSION, "findings": [
        {"finding_id": row.finding_id, "passage_present": True,
         "automated_support_checked": True, "support_verdict": row.verdict,
         "empirical_status": row.empirical_status, "finding_hash": digest(next(
             f.model_dump() for f in findings if f.id == row.finding_id))}
        for row in review.finding_support], "concentration": concentration(review.source_assessments),
        "single_group_findings": single_group_findings(findings, review.source_assessments)}
