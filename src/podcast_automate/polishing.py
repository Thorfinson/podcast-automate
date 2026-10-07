"""A separate spoken-dialogue pass with a before/after fidelity and role review."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .prompts import instructions
from .errors import AppError
from .editorial import CONTINUITY, LISTENABILITY, TERMINOLOGY, EPISODE_FRAMING, terminology
from .models import Contract, EpisodeScript, Identifier, NonEmpty
from .research_patches import corrected_call
from .script_advisories import dialogue_shape
from .storage import digest, write_json
from .teaching import Passage

POLISH_VERSION = "dialogue_polish.v1"
# Keep the run input contract stable; the prompt version and full prompt bind new
# checkpoints. Existing runs retain their approved inputs and historical verdicts.
# v4 (2026-10-02): the composed framing fragment names the final episode as the series' synthesis, and the terminology
# rule is the project's own (editorial.terminology). v5 (2026-10-06): the listenability rules (editorial.LISTENABILITY)
# replace "long coherent monologues are welcome" and "no target ratio of speech between hosts". v6: the design's
# storytelling devices are kept instead of one standard pattern (D-143).
# v7 (2026-10-07): the breathers of the listenability rules.
POLISH_PROMPT_VERSION = "dialogue_polish.v7-breathers"
# v4 (2026-10-02): a comparison after a repair is told the previous round's failing points and the changed segments.
# v5 (2026-10-06): it judges spoken_language by the listenability rules, with the candidate's measured dialogue_shape,
# and a recap or reflection beat that restates the original is no new fact. v6: chapter endings that differ as the
# design planned them are no defect.
POLISH_REVIEW_VERSION = "dialogue_polish_review.v7-breathers"
# The repair repeats the polishing prompt, so its meaning changed with v5 and v6.
POLISH_REPAIR_VERSION = "dialogue_polish_repair.v4-breathers"
DEMANDING_PASSAGES = 3
HOST_ROLES = {
    "host_a": "The expert: calm, precise and analytical. Develop mechanisms and relevant details, "
              "explain why each step follows and acknowledge uncertainty. Respond to the partner's actual objection.",
    "host_b": "The curious, thoughtful conversation partner: voice questions that arise while listening, "
              "test assumptions and connect details to the larger significance. Bring a reasonable alternative "
              "or deduction rather than ask for the next definition. Do not act unintelligent or merely praise the expert.",
}
POLISH_CRITERIA = ("meaning", "completeness", "speaker_roles", "spoken_language", "episode_framing")
# What the polish must keep of the checked draft; a loss blocks in every round (scoped_points).
FIDELITY_CRITERIA = frozenset({"meaning", "completeness"})


class PolishCheck(Contract):
    criterion: Literal["meaning", "completeness", "speaker_roles", "spoken_language", "episode_framing"]
    verdict: Literal["pass", "fail"]
    reason: NonEmpty
    before: list[Passage]
    after: list[Passage]


class Referent(Contract):
    expression: NonEmpty
    resolved_by: Identifier | None = Field(default=None,
        description="Segment ID of the earlier sentence that makes this expression concrete; null if none does.")


class DemandingPassage(Contract):
    segment_id: Identifier
    load: NonEmpty
    referents: list[Referent]


class DialoguePolishReview(Contract):
    checks: list[PolishCheck]
    demanding_passages: list[DemandingPassage] = Field(default_factory=list)
    limitations: list[str]


def unresolved_referents(review):
    """Referents the review itself could not trace back to an earlier sentence."""
    return [(passage, referent) for passage in review.demanding_passages
            for referent in passage.referents if referent.resolved_by is None]


def validate_demanding_passages(review, candidate):
    """Named passages must be real, distinct, and not the greeting or the sign-off."""
    positions = {s.segment_id: i for i, s in enumerate(candidate.segments)}
    named = [p.segment_id for p in review.demanding_passages]
    framing = {candidate.segments[0].segment_id, candidate.segments[-1].segment_id}
    # An episode shorter than five segments has fewer candidates than the rule asks for;
    # it then owes every segment that is neither the greeting nor the sign-off.
    expected = min(DEMANDING_PASSAGES, len(positions) - len(framing))
    if len(named) != expected or len(set(named)) != expected:
        raise AppError(f"Dialogvergleich muss genau {expected} unterschiedliche, besonders "
                       "dichte Passagen benennen.", code="invalid_polish_review", status="blocked")
    if not set(named) <= positions.keys():
        raise AppError("Dialogvergleich benennt eine dichte Passage, die es im Skript nicht gibt.",
                       code="invalid_polish_evidence", status="blocked")
    if set(named) & framing:
        raise AppError("Begrüßung und Verabschiedung zählen nicht als dichteste Passage.",
                       code="invalid_polish_review", status="blocked")
    for passage in review.demanding_passages:
        for referent in passage.referents:
            if referent.resolved_by is None:
                continue
            if positions.get(referent.resolved_by, len(positions)) >= positions[passage.segment_id]:
                raise AppError("Ein Bezug muss durch einen früheren Abschnitt aufgelöst werden.",
                               code="invalid_polish_evidence", status="blocked")


def validate_polish_review(review, original, candidate):
    """Raises on a malformed review; returns the deterministic issues the repair loop must handle."""
    criteria = [c.criterion for c in review.checks]
    if len(criteria) != len(POLISH_CRITERIA) or set(criteria) != set(POLISH_CRITERIA):
        raise AppError("Dialogvergleich muss alle fünf Kriterien einschließlich Intro und Outro genau einmal prüfen.",
                       code="invalid_polish_review", status="blocked")
    before = {s.segment_id: s.text for s in original.segments}
    after = {s.segment_id: s.text for s in candidate.segments}
    for check in review.checks:
        for passages, texts in ((check.before, before), (check.after, after)):
            if any(p.segment_id not in texts or p.quote not in texts[p.segment_id] for p in passages):
                raise AppError("Dialogvergleich zitiert eine nicht vorhandene Textstelle.",
                               code="invalid_polish_evidence", status="blocked")
        if check.verdict == "pass" and (not check.after or
                (check.criterion in {"meaning", "completeness"} and not check.before)):
            raise AppError("Bestandener Dialogvergleich benötigt passende Vorher-/Nachher-Belege.",
                           code="invalid_polish_evidence", status="blocked")
        if check.criterion == "episode_framing" and check.verdict == "pass":
            chapters = {segment.segment_id: segment.chapter_id for segment in candidate.segments}
            quoted_chapters = {chapters[p.segment_id] for p in check.after}
            if not {candidate.chapters[0].chapter_id, candidate.chapters[-1].chapter_id} <= quoted_chapters:
                raise AppError("Intro-/Outro-Prüfung benötigt Textbelege aus dem ersten und letzten Kapitel.",
                               code="invalid_polish_evidence", status="blocked")
    validate_demanding_passages(review, candidate)
    spoken = next(c for c in review.checks if c.criterion == "spoken_language")
    unresolved = unresolved_referents(review)
    if spoken.verdict == "pass" and unresolved:
        # The review named a referent it could not trace; that is the density failure it
        # was asked to report, so it becomes an issue instead of a rejected review.
        return ["spoken_language: " + "; ".join(
            f"{passage.segment_id}: «{referent.expression}» wird an keiner früheren Stelle aufgelöst"
            for passage, referent in unresolved)]
    return []


def compare_dialogue(brief, entry, original, candidate, invoke, *, series_context=None, prerequisite_context=None,
                     follow_up=None, rule=TERMINOLOGY):
    """The before/after review as a single call, so an eval can run it without a polishing pass.

    ``follow_up`` (``follow_up_payload``) makes it the comparison after a repair: the previous round's failing points
    with what became of each, and the candidate segments changed since that round. ``rule`` is the project's
    terminology rule (``editorial.terminology``); the eval keeps the general one."""
    return invoke(
        rule + CONTINUITY + EPISODE_FRAMING + LISTENABILITY +
        instructions("dialogue_polish_review") + "\n" +
        json.dumps({"brief": brief, "host_roles": HOST_ROLES,
                    "episode": entry.model_dump(), "series_context": series_context,
                    "prerequisite_context": prerequisite_context or [], **(follow_up or {}),
                    "original": original.model_dump(), "candidate": candidate.model_dump(),
                    "dialogue_shape": dialogue_shape(candidate)}, ensure_ascii=False),
        DialoguePolishReview, POLISH_REVIEW_VERSION)


def changed_segments(before, after):
    """Segment IDs whose speaker, text or references a repair changed, or that it added (as in script_pipeline)."""
    earlier = {s.segment_id: (s.speaker_id, s.text, s.knowledge_refs) for s in before.segments}
    return [s.segment_id for s in after.segments if earlier.get(s.segment_id) != (s.speaker_id, s.text, s.knowledge_refs)]


def comparison_points(review, original, candidate):
    """A comparison's failing points as ``{criterion, issue, segment_ids}``, in the order the repair has always got them:
    the referents the review could not resolve, then its failing checks, each with the candidate segments it quotes."""
    density = validate_polish_review(review, original, candidate)
    segments = list(dict.fromkeys(passage.segment_id for passage, _ in unresolved_referents(review)))
    return ([{"criterion": "spoken_language", "issue": issue, "segment_ids": segments} for issue in density] +
            [{"criterion": check.criterion, "issue": f"{check.criterion}: {check.reason}",
              "segment_ids": list(dict.fromkeys(passage.segment_id for passage in check.after))}
             for check in review.checks if check.verdict == "fail"])


def scoped_points(points, scope):
    """The points that block and the notes, as ``(blocking, notes)``.

    A first comparison (no ``scope``) blocks on every point. From the second on, a point blocks only when it repeats a
    criterion the previous round sent to repair, quotes a segment changed since that round, or quotes nothing it could
    be placed by; a new point on text the previous round passed is a note. Each round's fresh review raised new role
    or framing points on unchanged text and stopped the run (finding of 2026-10-02). A note from earlier stays one while
    its segments are unchanged.

    Meaning and completeness block in every round, wherever they point: they compare the candidate with the checked
    draft, which stays the script when the repairs fail. A step the first polish lost, found in the second round on
    text the repair had not touched, became a note, and the lossy polish was published as passed (2026-10-02 review)."""
    if not scope:
        return list(points), []
    repaired = {row["criterion"] for row in scope["previous"] if row["blocking"]} | FIDELITY_CRITERIA
    changed = set(scope["changed"])
    blocking, notes = [], []
    for point in points:
        placed = set(point["segment_ids"])
        (blocking if point["criterion"] in repaired or not placed or placed & changed else notes).append(point)
    kept = [{key: row[key] for key in ("criterion", "issue", "segment_ids")} for row in scope["previous"]
            if not row["blocking"] and row["segment_ids"] and not set(row["segment_ids"]) & changed]
    return blocking, [*notes, *(row for row in kept if row not in notes)]


def follow_up_payload(scope):
    if not scope:
        return None
    return {"previous_checks": [{"criterion": row["criterion"], "point": row["issue"], "segment_ids": row["segment_ids"],
                                 "outcome": "repaired" if row["blocking"] else "noted"} for row in scope["previous"]],
            "changed_segments": scope["changed"]}


def polish_dialogue(config, entry, original, design, invoke, work: Path, validate, *,
                    series_context=None, prerequisite_context=None, style_notes=""):
    payload = {"brief": {"language": config.language, "audience": config.audience_level,
                          "host_names": getattr(config, "host_names", None),
                          "prior_knowledge": config.prior_knowledge, "depth": config.depth_request,
                          "style_notes": style_notes},
               "host_roles": HOST_ROLES, "episode": entry.model_dump(), "series_context": series_context,
               "prerequisite_context": prerequisite_context or [],
               "teaching_design": design.model_dump() if design is not None else None,
               "original": original.model_dump()}
    # Topic-neutral terminology, with the machine-learning names only for such a topic (2026-10-02).
    rule = terminology(config.language, getattr(config, "topic", ""), getattr(config, "central_question", ""))
    prompt = (
        rule + CONTINUITY + EPISODE_FRAMING + LISTENABILITY +
        instructions("dialogue_polish") + "\n" +
        json.dumps(payload, ensure_ascii=False))
    signature = digest({"version": POLISH_PROMPT_VERSION, "prompt": prompt})
    checkpoint = work / "checkpoint.json"
    # ``scope``: from the first repair after a comparison on, the points of the last comparison (``previous``, each
    # marked blocking or not) and the segments changed since (``changed``). Saved with the repaired candidate, before
    # the next comparison, so a resume asks that comparison the same question.
    candidate, review, repairs, scope = None, None, 0, None
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved.get("input_hash") == signature:
            candidate = EpisodeScript.model_validate(saved["candidate"])
            review = DialoguePolishReview.model_validate(saved["review"]) if saved["review"] else None
            repairs, scope = saved["repairs"], saved.get("scope")

    def save():
        write_json(checkpoint, {"input_hash": signature, "candidate": candidate.model_dump(),
                               "review": review.model_dump() if review else None, "repairs": repairs,
                               **({"scope": scope} if scope else {})})

    if candidate is None:
        candidate = invoke(prompt, EpisodeScript, POLISH_PROMPT_VERSION)
        save()
    kept_draft = False

    def well_formed(answer, candidate):
        validate_polish_review(answer, original, candidate)

    def checked(candidate):
        # compare_dialogue stays one call for the eval; here a malformed comparison is asked again.
        return lambda prompt, schema, version: corrected_call(invoke, prompt, schema, version,
                                                             lambda answer: well_formed(answer, candidate))

    while True:
        errors = validate(candidate, entry)
        if review is not None:
            try:
                well_formed(review, candidate)
            except AppError:
                # Saved before its check ran: compared again instead of stopping every resume here.
                review = None
        if not errors and review is None:
            review = compare_dialogue(payload["brief"], entry, original, candidate, checked(candidate),
                                      series_context=series_context,
                                      prerequisite_context=payload["prerequisite_context"],
                                      follow_up=follow_up_payload(scope), rule=rule)
            save()
        blocking, notes = scoped_points(comparison_points(review, original, candidate) if review is not None else [], scope)
        issues = errors + [point["issue"] for point in blocking]
        if not issues:
            if notes:
                write_json(work / "accepted_notes.json", [point["issue"] for point in notes])
            break
        write_json(work / "issues.json", issues)
        if repairs >= 2:
            # Both repairs spent and only the spoken language still faulted, never meaning, completeness, roles or
            # framing: the candidate stands with its points on record, and the script review reads the whole dialogue
            # again. Each repair had left a new wording detail (Asimov ep_007, 2026-09-29: an inserted sentence that
            # repeated the next one), so those points no longer stop the run.
            failing = {point["criterion"] for point in blocking}
            if not errors and failing <= {"spoken_language"}:
                write_json(work / "accepted_notes.json", [*issues, *(point["issue"] for point in notes)])
                break
            # Anything else, a verdict or a deterministic defect such as a polish below its length: the checked draft
            # stays the script, so the loss never reaches publication and the run goes on (Asimov ep_012, 2026-09-29:
            # a reasoning step lost in both repairs). The review stage judges that draft in full, its framing and roles
            # included. Until 2026-10-02 a deterministic defect never reached this fallback and roles or framing
            # stopped the run here.
            if not validate(original, entry):
                write_json(work / "kept_draft.json", issues)
                candidate, kept_draft = original, True
                break
            raise AppError(f"Dialogüberarbeitung benötigt Korrektur: {work / 'issues.json'}",
                           code="dialogue_polish_failed", status="blocked")
        repaired = invoke(prompt + "\n" + instructions("dialogue_polish_repair") + "\n" + json.dumps({
                              "candidate": candidate.model_dump(), "issues": issues}, ensure_ascii=False),
                          EpisodeScript, POLISH_REPAIR_VERSION)
        changed = changed_segments(candidate, repaired)
        if review is not None:
            scope = {"previous": [{**point, "blocking": True} for point in blocking] +
                                 [{**point, "blocking": False} for point in notes], "changed": changed}
        elif scope:
            # A round stopped by a deterministic defect had no comparison: its changes add to those of the round before.
            scope = {**scope, "changed": list(dict.fromkeys([*scope["changed"], *changed]))}
        candidate = repaired
        repairs += 1
        review = None
        save()
    # A draft kept after a deterministic defect has no comparison of the last candidate: its review is null.
    compared = review.model_dump() if review is not None else None
    write_json(work / "script.json", candidate.model_dump())
    write_json(work / "review.json", compared)
    write_json(work / "result.json", {"version": POLISH_VERSION, "prompt_version": POLISH_PROMPT_VERSION,
        "status": "kept_draft" if kept_draft else "passed", "host_roles": HOST_ROLES,
        "original_digest": digest(original.model_dump()), "polished_digest": digest(candidate.model_dump()),
        "repairs": repairs, "review": compared, "human_reviewed": False})
    return candidate, [work / name for name in ("script.json", "review.json", "result.json", "checkpoint.json")]
