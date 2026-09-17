"""Check complete, anchored semantic-preservation receipts for spoken scripts."""
from collections import Counter

from .errors import AppError
from .script_models import ScriptIssue
from .sources import clean

SCRIPT_EVIDENCE_INSTRUCTIONS = (
    "Preserve each finding's claim_contract and the material unresolved synthesis relationships. "
    "Do not upgrade association to causation, source theory to tested fact, or remove population/time limits. "
    "Compare normalized quantities by value, unit and direction; equivalent spoken numbers, translations and "
    "unit conversions are allowed. Review claim_checks exactly once per segment, including nonfactual segments. "
    "Each check must quote the actual segment, identify all its knowledge_refs, assess preservation against "
    "the research contract and original draft, and name changed semantic fields for drift. A factual assertion "
    "without references is drift, not no_research_claim. No-research-claim is only for genuinely nonfactual framing. "
    "When a qualified finding spans segments, check its scope in the surrounding dialogue. "
)


def validate_claim_checks(review, script, findings, *, required=True):
    if not required and not review.claim_checks:
        return []
    segments = {s.segment_id: s for s in script.segments}
    if Counter(c.segment_id for c in review.claim_checks) != Counter(segments.keys()):
        raise AppError("Script review must check every segment's claim preservation exactly once.",
                       code="invalid_script_evidence_review", status="blocked")
    known = {f.id for f in findings}
    issues = []
    for check in review.claim_checks:
        segment = segments[check.segment_id]
        if (clean(check.quote) not in clean(segment.text) or
                set(check.finding_ids) != set(segment.knowledge_refs) or not set(check.finding_ids) <= known or
                (check.verdict == "no_research_claim" and check.finding_ids) or
                (check.verdict == "preserved" and (not check.finding_ids or check.changed_fields)) or
                (check.verdict == "drift" and not check.changed_fields)):
            raise AppError("Script preservation receipt is inconsistent with its segment.",
                           code="invalid_script_evidence_review", status="blocked")
        if check.verdict == "drift":
            issues.append(ScriptIssue(category="grounding", segment_ids=[check.segment_id],
                reason="Claim drift (" + ", ".join(check.changed_fields) + "): " + check.reason))
    return issues
