"""A separate spoken-dialogue pass with a before/after fidelity and role review."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from .prompts import instructions
from .errors import AppError
from .editorial import TERMINOLOGY, EPISODE_FRAMING
from .models import Contract, EpisodeScript, NonEmpty
from .storage import digest, write_json
from .teaching import Passage

POLISH_VERSION = "dialogue_polish.v1"
# Keep the run input contract stable; the prompt version and full prompt bind new
# checkpoints. Existing runs retain their approved inputs and historical verdicts.
POLISH_PROMPT_VERSION = "dialogue_polish.v2-framing"
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


class DialoguePolishReview(Contract):
    checks: list[PolishCheck]
    limitations: list[str]


def validate_polish_review(review, original, candidate):
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


def polish_dialogue(config, entry, original, design, invoke, work: Path, validate, *, series_context=None):
    payload = {"brief": {"language": config.language, "audience": config.audience_level,
                          "prior_knowledge": config.prior_knowledge, "depth": config.depth_request},
               "host_roles": HOST_ROLES, "episode": entry.model_dump(), "series_context": series_context,
               "teaching_design": design.model_dump() if design is not None else None,
               "original": original.model_dump()}
    prompt = (
        TERMINOLOGY + EPISODE_FRAMING +
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
            review = invoke(
                TERMINOLOGY + EPISODE_FRAMING +
                instructions("dialogue_polish_review") + "\n" +
                json.dumps({"brief": payload["brief"], "host_roles": HOST_ROLES,
                            "episode": payload["episode"], "series_context": series_context,
                            "original": original.model_dump(), "candidate": candidate.model_dump()}, ensure_ascii=False),
                DialoguePolishReview, "dialogue_polish_review.v2-framing")
            save()
        if review is not None:
            validate_polish_review(review, original, candidate)
        issues = errors + ([f"{c.criterion}: {c.reason}" for c in review.checks if c.verdict == "fail"] if review else [])
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
