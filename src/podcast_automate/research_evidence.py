"""Deterministic receipt validation around (fallible) semantic model judgements."""
from collections import Counter
import re
import unicodedata

from .prompts import fragment
from .errors import AppError
from .evidence_models import EVIDENCE_VERSION, FindingSupport, SourceAssessment
from .storage import digest

EVIDENCE_INSTRUCTIONS = fragment("evidence_instructions")

SYNTHESIS_INSTRUCTIONS = fragment("synthesis_instructions")
# The rule validate_synthesis enforces, stated to the writer: three dossier drafts of the 20 September
# run were rejected for comparisons without a read passage on every side. Runs that started before
# the rule keep their prompts, so their saved receipts stay valid (question_synthesis.PROMPT_GENERATION).
SYNTHESIS_EVIDENCE_RULE = (" A synthesis relation cites in evidence_refs only references that its compared findings "
                           "themselves cite, at least one for each compared finding; a comparison without a read "
                           "passage on every side is not a relation.")

# Typography that differs between an extracted PDF and a quote typed by a model, never wording. Guillemets
# (2026-10-02): German books quote »so« and ›so‹, a model types "so", and the quote was refused and asked again.
TYPOGRAPHY = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "‚": "'",
                            "»": '"', "«": '"', "›": "'", "‹": "'",
                            "–": "-", "—": "-", "−": "-", "­": ""})


def quotable(text):
    """Text for the verbatim check, with the typography of extraction and quote made equal.

    Ligatures (``ﬁnancial``), curly quotes, dash variants, soft hyphens and a hyphenation at a line
    break (``reces- sion``) come from the PDF, not from the model. The quote still has to appear in
    the section as the same run of words; a paraphrase or an ellipsis stays a rejected quote.
    """
    text = unicodedata.normalize("NFKC", text).translate(TYPOGRAPHY)
    text = re.sub(r"(\w)-\s*(\w)", r"\1\2", text)
    return " ".join(text.split())


def verbatim(excerpt, text):
    """Whether an excerpt appears in a section as the same run of letters, whatever the spaces between them.

    The text layer of a scan breaks words ("human p otential", "matr ix", "anti - author itarian"), and a quote
    that restores them is still the source's wording (Asimov, 2026-09-30: every quote from the scan of
    Max-Neef's Human Scale Development was rejected before review). Other letters, another word order, a
    paraphrase or an ellipsis still fail.
    """
    def squeezed(value):
        return re.sub(r"(?<=\w)-(?=\w)", "", "".join(quotable(value).split()))
    return squeezed(excerpt) in squeezed(text)

PROFILES = {
    "definition": "Original definition, conceptual scope and distinctions; no artificial empirical test.",
    "theory": "Original theoretical account, assumptions and predictions; distinguish exposition from validation.",
    "mechanism": "Prerequisite causal steps, assumptions and limits; separate proposed from tested mechanisms.",
    "empirical": "Design, population, measurements, results and limitations; seek counterevidence and shared-data checks.",
    "example": "Source-grounded example; label illustrative extrapolation and its limits.",
    "boundaries": "Evidence of applicability and exceptions, including counterevidence where contested.",
    "synthesis": "Compare verified answers on a common dimension; retain conditional differences and unresolved tensions.",
}
# Added to a task's profile by its aim (2026-09-30: every task was answered as an evidence audit).
AIM_PROFILES = {
    "explain": ("Explain the account in its own logic from its author's own work (book, chapter, article, lecture): the "
                "question it answers, its assumptions, its picture of people or society, its mechanism and how the parts "
                "fit, its key concepts; attribute it to its author. Tests and critiques belong to evaluate tasks and do "
                "not replace the exposition."),
    "evaluate": "",
    "build": ("How it is done in practice now: concrete steps, the options and when to choose which, current tools and "
              "standards, pitfalls, effort and cost. Documentation, standards, maintained repositories and practitioner "
              "reports are valid sources for how it is done, not for whether it works; date every practice claim."),
}


def evidence_profile(task):
    """What a task's answer must contain: its kind's profile and, for explain and build tasks, its aim's."""
    return " ".join(part for part in (PROFILES[task.kind], AIM_PROFILES[task.aim]) if part)


def evidence_error(message):
    raise AppError(message, code="invalid_evidence_review", status="blocked")


def identity(value):
    value = " ".join(value.casefold().split()).rstrip("/")
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)


SUPPORT_RANK = {"supported": 0, "partially_supported": 1, "insufficient_context": 2, "contradicted": 3}
INDEPENDENCE_RANK = {"independent": 0, "unknown": 1, "shared": 2}
BLOCKING_VERDICTS = {"contradicted", "insufficient_context"}


def blocks(row):
    """Whether one support receipt fails the answer on its own; anything milder is a limitation."""
    return row.verdict in BLOCKING_VERDICTS or row.suitability != "suitable" or not row.contract_preserved


def collapse_support(rows):
    """One receipt per finding: a repeated finding keeps its most conservative receipt."""
    merged = {}
    for row in rows:
        key = (SUPPORT_RANK[row.verdict], row.suitability != "suitable", not row.contract_preserved)
        if row.finding_id not in merged or key > merged[row.finding_id][0]:
            merged[row.finding_id] = (key, row)
    return [row for _, row in merged.values()]


def collapse_assessments(rows):
    """One assessment per source: a repeated source keeps the one claiming the least independence."""
    merged = {}
    for row in rows:
        if row.source_id not in merged or INDEPENDENCE_RANK[row.independence] > INDEPENDENCE_RANK[merged[row.source_id].independence]:
            merged[row.source_id] = row
    return list(merged.values())


def scope_assessments(review, findings, context):
    """Drop, in place, source assessments of sources none of the reviewed findings cites. They lie outside
    the review's scope (another part assesses them, or nothing needs them); every cited source still needs
    its one assessment (Ontologies, 2026-09-27: two extra sources copied from the outline refused a part)."""
    source_of = {section["reference"]: source["source_id"] for source in context for section in source["sections"]}
    cited = {source_of[e.reference] for f in findings for e in f.evidence if e.reference in source_of}
    review.source_assessments = [a for a in review.source_assessments if a.source_id in cited]
    return review


def named(ids):
    return ", ".join(sorted(ids))


def coverage_defects(expected, given, extra_label):
    """What a list of ids lacks and what it names beyond ``expected``, for a correction that names them."""
    missing, extra = set(expected) - set(given), set(given) - set(expected)
    return " ".join(part for part in (f"Missing: {named(missing)}." if missing else "",
                                      f"{extra_label}: {named(extra)}." if extra else "") if part)


def user_notes(document):
    """A read source that is the editor's notes: typed ``idea``, or user material without an address. A copy of a
    published work the editor provided names it in ``citation`` and is that work (research_models.is_idea)."""
    return document.get("type") == "idea" or ("url" in document and not document["url"] and not document.get("citation"))


def support_errors(findings, review, context, *, require_contract=True, limitations=None, per_finding=None):
    """Malformed coverage raises; blocking non-passes return actionable feedback.

    Blocking is a contradicted or insufficient_context verdict, an unsuitable source, a broken claim
    contract or independence that the receipts do not establish. A partially supported finding whose
    contract and source hold, and a receipt that assessed only some of a finding's cited passages,
    are limitations of a passing answer: they are appended to ``limitations`` when the caller
    supplies a list and count as passes otherwise (the stored-verification check on resume).
    Repeated finding or source entries collapse to their most conservative receipt; a missing or
    unknown one is still a shape defect, and its message names the finding or source ids, so the
    repeated call can correct exactly them (Ontologies, 2026-10-01: t02's review was refused seven times
    for "every cited source" without learning which). ``per_finding``, when a dict, collects each
    blocking message under the id of its finding.
    """
    support = collapse_support(review.finding_support)
    assessments = collapse_assessments(review.source_assessments)
    if Counter(r.finding_id for r in support) != Counter(f.id for f in findings):
        evidence_error("Evidence review must assess every finding exactly once. " + coverage_defects(
            [f.id for f in findings], [r.finding_id for r in support], "Unknown findings"))
    passages = {p["reference"]: s for s in context for p in s["sections"]}
    cited = {e.reference for f in findings for e in f.evidence}
    source_ids = {passages[r]["source_id"] for r in cited if r in passages}
    if not cited <= passages.keys():
        evidence_error("Evidence review contains unread or unknown passages: " + named(cited - passages.keys()) + ".")
    if Counter(a.source_id for a in assessments) != Counter(source_ids):
        evidence_error("Assess the suitability and identity of every cited source exactly once. "
                       + coverage_defects(source_ids, [a.source_id for a in assessments], "Not cited by any finding"))
    assessments = {a.source_id: a for a in assessments}
    for assessment in assessments.values():
        if any(r not in passages or passages[r]["source_id"] != assessment.source_id for r in assessment.evidence_refs):
            evidence_error("Source assessment must be grounded in that source's supplied passages. "
                           f"{assessment.source_id} names: " + named(r for r in assessment.evidence_refs if r not in passages
                                                                    or passages[r]["source_id"] != assessment.source_id) + ".")
        if assessment.independence != "unknown" and not assessment.evidence_family:
            evidence_error("Known independence requires a source-grounded study or dataset identity. "
                           f"Source: {assessment.source_id}.")
    by_id = {f.id: f for f in findings}
    errors = []

    def blocking(finding, message):
        errors.append(message)
        if per_finding is not None:
            per_finding.setdefault(finding.id, []).append(message)

    for row in support:
        finding = by_id[row.finding_id]
        refs = {e.reference for e in finding.evidence}
        assessed = set(row.references)
        # A receipt over some of the cited passages is a partial check, not a wasted call: extra
        # references to other read passages are ignored, omitted ones become a stated limitation.
        if not assessed & refs:
            evidence_error("A support receipt must cover at least one of the finding's cited passages. "
                           f"Finding {finding.id} cites: {named(refs)}.")
        if not assessed <= passages.keys():
            evidence_error("A support receipt names passages that were not supplied to this review. "
                           f"Finding {finding.id}: {named(assessed - passages.keys())}.")
        omitted = sorted(refs - assessed)
        if omitted and limitations is not None:
            limitations.append({"finding_id": finding.id, "kind": "unassessed_references",
                                "text": f"{finding.id}: Belegstellen nicht einzeln geprüft: {', '.join(omitted)}."})
        if not set(row.independent_evidence_refs) <= refs:
            evidence_error("Independent evidence must be cited by the finding and actually supplied. "
                           f"Finding {finding.id}: {named(set(row.independent_evidence_refs) - refs)}.")
        if row.empirical_status == "independently_tested":
            sources = [assessments[s] for s in {passages[r]["source_id"] for r in row.independent_evidence_refs}]
            source_context = {s["source_id"]: s for s in context}
            documents = [source_context[s.source_id] for s in sources]
            hashes = [s["text_hash"] for s in documents if s.get("text_hash")]
            # A provided copy of a published work has no address but a citation: it is that work, not notes (2026-10-02).
            if len(set(hashes)) != len(hashes) or any(user_notes(s) for s in documents):
                blocking(finding, f"{finding.id}: duplicate text or user notes cannot establish independent testing.")
            families = {identity(s.evidence_family) for s in sources if s.independence == "independent" and s.evidence_family}
            works = {identity(s.work_id) for s in sources if s.work_id}
            independent_tests = [s for s in sources if s.independence == "independent" and s.evidence_family
                                 and {"empirical_test", "replication"} & set(s.roles)]
            theoretical = [s for s in sources if "theory_exposition" in s.roles and s.work_id]
            theory_test = any(test.work_id and identity(test.work_id) != identity(theory.work_id)
                              for test in independent_tests for theory in theoretical)
            if not theory_test and (len(families) < 2 or len(works) < 2 or not independent_tests):
                blocking(finding, f"{finding.id}: independent testing is not established by distinct evidence families and works.")
        if require_contract and finding.claim_contract is None:
            blocking(finding, f"{finding.id}: missing claim contract (basis, relation, scope and qualifications).")
        if blocks(row):
            blocking(finding, f"{finding.id}: {row.verdict}; {row.reason}; {row.suitability_reason}; "
                     + "; ".join(row.unsupported_clauses))
        elif row.verdict == "partially_supported" and limitations is not None:
            limitations.append({"finding_id": finding.id, "kind": "partial_support",
                                "text": f"{finding.id}: nicht vollständig gestützt: {'; '.join(row.unsupported_clauses)} "
                                        f"({row.reason})"})
    return errors


# What a final review attempt left out, filled so that it can never pass on its own: an unassessed finding is not
# supported, an unassessed source has unknown suitability and independence.
UNREVIEWED_FINDING = "The review returned no support receipt for this finding."
UNREVIEWED_SOURCE = "The review returned no assessment of this source; its suitability and independence are unknown."


def settle_receipts(review, findings, context):
    """Normalise, in place, the receipt shape defects of a review's final attempt that have a conservative reading,
    and return ``(limitations, unreviewed finding ids)``. Before, such a defect stopped the run after three refused
    calls (Ontologies, 2026-10-01: t02 review_037; all four fresh-attempt stops of the allowance logs).

    A receipt or assessment of something no finding cites is dropped, as ``scope_assessments`` drops extra sources;
    references outside the supplied passages or outside the finding are dropped, and independence claimed without a
    study or dataset identity becomes unknown. A finding without a receipt (or with one that covers none of its
    passages) gets an ``insufficient_context`` receipt, so it blocks the answer instead of passing unchecked; a cited
    source without an assessment gets one with unknown roles and independence, recorded as a limitation. A finding
    citing a passage that was never supplied is not normalised: that is ledger corruption, and ``support_errors``
    still raises for it. A well-formed review comes back unchanged.
    """
    passages = {p["reference"]: s["source_id"] for s in context for p in s["sections"]}
    by_id = {f.id: f for f in findings}
    notes, unreviewed, support = [], [], []

    def note(finding_id, text):
        notes.append({"finding_id": finding_id, "kind": "review_normalised", "text": text})

    for row in collapse_support(review.finding_support):
        finding = by_id.get(row.finding_id)
        if finding is None:
            note("", f"Prüfbeleg zu einem unbekannten Befund ({row.finding_id}) verworfen.")
            continue
        refs = [e.reference for e in finding.evidence]
        references = [r for r in row.references if r in passages]
        if not set(references) & set(refs):
            continue  # covers none of the finding's passages: treated as missing below
        update = {}
        if references != row.references:
            update["references"] = references
            note(finding.id, f"{finding.id}: Prüfbeleg nannte nicht vorgelegte Stellen; sie wurden verworfen.")
        independent = [r for r in row.independent_evidence_refs if r in refs]
        if independent != row.independent_evidence_refs:
            update["independent_evidence_refs"] = independent
            if row.empirical_status == "independently_tested" and not independent:
                update["empirical_status"] = "unknown"
            note(finding.id, f"{finding.id}: Belege einer unabhängigen Prüfung, die der Befund nicht zitiert, wurden verworfen.")
        support.append(row.model_copy(update=update) if update else row)
    present = {row.finding_id for row in support}
    for finding in findings:
        if finding.id in present:
            continue
        unreviewed.append(finding.id)
        refs = list(dict.fromkeys(e.reference for e in finding.evidence))
        support.append(FindingSupport(finding_id=finding.id, verdict="insufficient_context", references=refs,
                                      reason=UNREVIEWED_FINDING, unsupported_clauses=[], suitability="unknown",
                                      suitability_reason="Not assessed by the review.", contract_preserved=False,
                                      empirical_status="unknown", independent_evidence_refs=[]))
    review.finding_support = support
    cited = {}
    for finding in findings:
        for evidence in finding.evidence:
            if evidence.reference in passages:
                cited.setdefault(passages[evidence.reference], []).append(evidence.reference)
    assessments = []
    for assessment in collapse_assessments(review.source_assessments):
        if assessment.source_id not in cited:
            continue
        update = {}
        grounded = [r for r in assessment.evidence_refs if passages.get(r) == assessment.source_id]
        if grounded != assessment.evidence_refs:
            update["evidence_refs"] = grounded or list(dict.fromkeys(cited[assessment.source_id]))
            note("", f"Quellenbewertung {assessment.source_id}: Stellen außerhalb dieser Quelle wurden verworfen.")
        if assessment.independence != "unknown" and not assessment.evidence_family:
            update["independence"] = "unknown"
            note("", f"Quellenbewertung {assessment.source_id}: Unabhängigkeit ohne Studien- oder Datenbasis gilt als unbekannt.")
        assessments.append(assessment.model_copy(update=update) if update else assessment)
    for source_id in sorted(cited.keys() - {a.source_id for a in assessments}):
        assessments.append(SourceAssessment(source_id=source_id, roles=["unknown"],
                                            evidence_refs=list(dict.fromkeys(cited[source_id])), rationale=UNREVIEWED_SOURCE,
                                            work_id="", version="", evidence_family="", independence="unknown", method="",
                                            research_group="", population="", geography="", period="",
                                            limitations=[UNREVIEWED_SOURCE]))
        note("", f"Quelle {source_id} wurde von der Prüfung nicht bewertet; Eignung und Unabhängigkeit bleiben unbekannt.")
    review.source_assessments = assessments
    return notes, unreviewed


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
            # The retry is told what fixes it: the commonest cases are a missing passage cited alone and a
            # wording defect with unrelated passages attached.
            evidence_error("Review objection evidence must relate to the affected findings. "
                           f"For the objection on {', '.join(objection.finding_ids)}: put at least one passage those "
                           "findings cite into evidence_refs (a missing or contradicting passage may stand beside it), or, "
                           "for a wording defect, leave evidence_refs empty and name the defect in missing_evidence.")
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
