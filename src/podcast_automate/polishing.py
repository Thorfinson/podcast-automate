"""A separate spoken-dialogue pass with a before/after fidelity and role review."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .prompts import instructions
from .errors import AppError
from .editorial import CONTINUITY, TERMINOLOGY, EPISODE_FRAMING
from .models import Contract, EpisodeScript, Identifier, NonEmpty
from .storage import digest, write_json
from .teaching import Passage

POLISH_VERSION = "dialogue_polish.v1"
# Keep the run input contract stable; the prompt version and full prompt bind new
# checkpoints. Existing runs retain their approved inputs and historical verdicts.
POLISH_PROMPT_VERSION = "dialogue_polish.v3-audit-notes"
POLISH_REVIEW_VERSION = "dialogue_polish_review.v3-density-notes"
DEMANDING_PASSAGES = 3
HOST_ROLES = {
    "host_a": "The expert: calm, precise and analytical. Develop mechanisms and relevant details, "
              "explain why each step follows and acknowledge uncertainty. Respond to the partner's actual objection.",
    "host_b": "The curious, thoughtful conversation partner: voice questions that arise while listening, "
              "test assumptions and connect details to the larger significance. Bring a reasonable alternative "
              "or deduction rather than ask for the next definition. Do not act unintelligent or merely praise the expert.",
}
POLISH_CRITERIA = ("meaning", "completeness", "speaker_roles", "spoken_language", "episode_framing")


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


def compare_dialogue(brief, entry, original, candidate, invoke, *, series_context=None, prerequisite_context=None):
    """The before/after review as a single call, so an eval can run it without a polishing pass."""
    return invoke(
        TERMINOLOGY + CONTINUITY + EPISODE_FRAMING +
        instructions("dialogue_polish_review") + "\n" +
        json.dumps({"brief": brief, "host_roles": HOST_ROLES,
                    "episode": entry.model_dump(), "series_context": series_context,
                    "prerequisite_context": prerequisite_context or [],
                    "original": original.model_dump(), "candidate": candidate.model_dump()}, ensure_ascii=False),
        DialoguePolishReview, POLISH_REVIEW_VERSION)


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
    prompt = (
        TERMINOLOGY + CONTINUITY + EPISODE_FRAMING +
        instructions("dialogue_polish") + "\n" +
        json.dumps(payload, ensure_ascii=False))
    signature = digest({"version": POLISH_PROMPT_VERSION, "prompt": prompt})
    checkpoint = work / "checkpoint.json"
    candidate, review, repairs = None, None, 0
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved.get("input_hash") == signature:
            candidate = EpisodeScript.model_validate(saved["candidate"])
            review = DialoguePolishReview.model_validate(saved["review"]) if saved["review"] else None
            repairs = saved["repairs"]

    def save():
        write_json(checkpoint, {"input_hash": signature, "candidate": candidate.model_dump(),
                               "review": review.model_dump() if review else None, "repairs": repairs})

    if candidate is None:
        candidate = invoke(prompt, EpisodeScript, POLISH_PROMPT_VERSION)
        save()
    while True:
        errors = validate(candidate, entry)
        if not errors and review is None:
            review = compare_dialogue(payload["brief"], entry, original, candidate, invoke,
                                      series_context=series_context,
                                      prerequisite_context=payload["prerequisite_context"])
            save()
        density = validate_polish_review(review, original, candidate) if review is not None else []
        issues = errors + density + (
            [f"{c.criterion}: {c.reason}" for c in review.checks if c.verdict == "fail"] if review else [])
        if not issues:
            break
        write_json(work / "issues.json", issues)
        if repairs >= 2:
            raise AppError(f"Dialogüberarbeitung benötigt Korrektur: {work / 'issues.json'}",
                           code="dialogue_polish_failed", status="blocked")
        candidate = invoke(prompt + "\n" + instructions("dialogue_polish_repair") + "\n" + json.dumps({
                               "candidate": candidate.model_dump(), "issues": issues}, ensure_ascii=False),
                           EpisodeScript, "dialogue_polish_repair.v1")
        repairs += 1
        review = None
        save()
    write_json(work / "script.json", candidate.model_dump())
    write_json(work / "review.json", review.model_dump())
    write_json(work / "result.json", {"version": POLISH_VERSION, "prompt_version": POLISH_PROMPT_VERSION,
        "status": "passed", "host_roles": HOST_ROLES,
        "original_digest": digest(original.model_dump()), "polished_digest": digest(candidate.model_dump()),
        "repairs": repairs, "review": review.model_dump(), "human_reviewed": False})
    return candidate, [work / name for name in ("script.json", "review.json", "result.json", "checkpoint.json")]
