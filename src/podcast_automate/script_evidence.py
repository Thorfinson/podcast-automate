"""Check complete, anchored semantic-preservation receipts for spoken scripts."""
from collections import Counter

from .prompts import fragment
from .errors import AppError
from .script_models import ScriptIssue
from .sources import clean

SCRIPT_EVIDENCE_INSTRUCTIONS = fragment("script_evidence_instructions")


def settle_receipts(review, script):
    """Read two receipt slips the way they were meant, instead of asking the whole review again.

    A segment that cites findings but was judged ``no_research_claim`` holds nothing the review found drifting:
    it counts as ``preserved`` against exactly the segment's findings. A ``preserved`` or ``drift`` receipt naming
    other finding ids than the segment cites is bound to the segment's own. Each re-asked review cost Astra at xhigh
    about eight minutes (Asimov ep_010 and ep_014, 2026-09-29: five of the evening's review calls were such
    re-asks). What the review judged is unchanged, and the settled segments are named in its limitations."""
    segments = {s.segment_id: s for s in script.segments}
    settled = []
    for check in review.claim_checks:
        segment = segments.get(check.segment_id)
        if segment is None or not segment.knowledge_refs:
            continue
        refs = list(segment.knowledge_refs)
        if check.verdict == "no_research_claim":
            check.verdict, check.finding_ids, check.changed_fields = "preserved", refs, []
            settled.append(check.segment_id)
        elif set(check.finding_ids) != set(refs):
            check.finding_ids = refs
            settled.append(check.segment_id)
    if settled:
        review.limitations.append("Receipts read as preserved against the segment's own findings: " + ", ".join(settled) + ".")
    return review


def receipt_defects(check, segment, known):
    """Why one claim check does not fit its segment, one sentence per broken rule."""
    refs, sid = sorted(segment.knowledge_refs), check.segment_id
    defects = []
    if clean(check.quote) not in clean(segment.text):
        defects.append(f"{sid}: the quote must be a verbatim excerpt of the segment's text.")
    if set(check.finding_ids) != set(refs):
        defects.append(f"{sid}: finding_ids must be exactly the segment's knowledge_refs {refs}.")
    elif not set(check.finding_ids) <= known:
        defects.append(f"{sid}: finding_ids {sorted(set(check.finding_ids) - known)} are not findings of this episode.")
    if check.verdict == "no_research_claim" and refs:
        defects.append(f"{sid}: no_research_claim is only for a segment without knowledge_refs; this segment cites "
                       f"{refs}, so judge it preserved or drift against those findings.")
    if check.verdict == "preserved" and check.changed_fields:
        defects.append(f"{sid}: preserved takes no changed_fields; use drift when a field changed.")
    if check.verdict == "preserved" and not refs:
        defects.append(f"{sid}: preserved needs findings; a segment without knowledge_refs is no_research_claim, or "
                       "drift when it asserts a research fact.")
    if check.verdict == "drift" and not check.changed_fields:
        defects.append(f"{sid}: drift must name its changed_fields.")
    return defects


def validate_claim_checks(review, script, findings, *, required=True):
    if not required and not review.claim_checks:
        return []
    segments = {s.segment_id: s for s in script.segments}
    if Counter(c.segment_id for c in review.claim_checks) != Counter(segments.keys()):
        raise AppError("Script review must check every segment's claim preservation exactly once.",
                       code="invalid_script_evidence_review", status="blocked")
    known = {f.id for f in findings}
    issues, defects = [], []
    for check in review.claim_checks:
        defects += receipt_defects(check, segments[check.segment_id], known)
    if defects:
        # Named per segment and rule, so the correction can be acted on (Ontologies ep_006, 2026-09-29: three
        # answers judged an illustration citing findings no_research_claim, and the bare message never said why).
        raise AppError("Script preservation receipt is inconsistent with its segment: " + " ".join(defects[:12]) +
                       (f" ({len(defects) - 12} more.)" if len(defects) > 12 else ""),
                       code="invalid_script_evidence_review", status="blocked")
    for check in review.claim_checks:
        if check.verdict == "drift":
            issues.append(ScriptIssue(category="grounding", segment_ids=[check.segment_id],
                reason="Claim drift (" + ", ".join(check.changed_fields) + "): " + check.reason))
    return issues
