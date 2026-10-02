"""The expression layer of a Gemini recording: a few inline audio tags per episode, never in the script text.

Gemini 3.8 Flash TTS performs vocal events written into its transcript in angle brackets (``<laugh>``, ``<sigh>``,
``<breath>``, ``<short pause>``), and OpenRouter passes them through; a written style direction ("Sag es
fröhlich:") is spoken aloud instead, so only tags are used (listen test, 2026-09-29). One text-model call per
episode places them; the check makes sure that, without its tags, every tagged segment is exactly the text
that would be spoken anyway, so the layer can add expression but never change a word.
"""
from __future__ import annotations

import json
import re

from pydantic import Field

from .errors import AppError
from .models import Contract, Identifier, NonEmpty
from .prompts import instructions
from .research_patches import corrected_call

# The record format of expression.json; saved readings carry it, so it stays while the prompt changes.
EXPRESSION_VERSION = "audio_expression.v1"
# The prompt's own tag (prompts/audio_expression.txt). v2, 2026-10-02: never a <long pause> at a segment's start.
EXPRESSION_PROMPT_VERSION = "audio_expression.v2"
# Google's documented vocal events for Gemini 3.8 Flash TTS (English names, also in German text), kept to those
# that fit a factual two-host podcast; screams, sobs, growls, sneezes and the like are left out. The first four
# passed the listen test of 2026-09-29, the others are documented and heard in scripts/gemini-tags-test.py.
ALLOWED_TAGS = ("<short pause>", "<long pause>", "<breath>", "<exhales>", "<sigh>", "<phew>",
                "<laugh>", "<chuckle>", "<giggle>", "<gasp>", "<tsk>", "<throat-clearing>")
MAX_TAGS_PER_SEGMENT = 2
TAG = re.compile(r"<[^<>\n]{1,40}>")
# All 44 <long pause> tags of the 29 Sep recordings opened their segment, where assembly already pauses (900 ms at
# a chapter start), and four of them left 3.5 to 7.3 s of dead air. The tag may stand inside a segment only.
OPENING_LONG_PAUSE = re.compile(r"\A\s*<long pause>\s*")


class TaggedSegment(Contract):
    segment_id: Identifier
    text: NonEmpty


class ExpressionPlan(Contract):
    segments: list[TaggedSegment] = Field(default_factory=list)


def untagged(text):
    return " ".join(TAG.sub(" ", text).split())


def episode_tag_limit(count):
    """Tags one episode may carry: sparse, about one for every four segments."""
    return max(3, count // 4)


def without_opening_pause(plan):
    """``plan`` with a <long pause> at a segment's start removed, and a segment left without a tag dropped. A
    deterministic fix, so a model answer that breaks only this rule costs no correction call."""
    rows = []
    for row in plan.segments:
        text = OPENING_LONG_PAUSE.sub("", row.text, count=1)
        if TAG.search(text):
            rows.append(TaggedSegment(segment_id=row.segment_id, text=text))
    return ExpressionPlan(segments=rows)


def expression_defects(plan, spoken):
    """``spoken`` maps each segment id to the text that would be spoken without the layer."""
    errors, seen, total = [], set(), 0
    for row in plan.segments:
        if row.segment_id not in spoken:
            errors.append(f"{row.segment_id}: unknown segment id.")
            continue
        if row.segment_id in seen:
            errors.append(f"{row.segment_id}: listed twice.")
        seen.add(row.segment_id)
        tags = TAG.findall(row.text)
        if not tags:
            errors.append(f"{row.segment_id}: carries no tag; leave such segments out.")
        unknown = sorted(set(tags) - set(ALLOWED_TAGS))
        if unknown:
            errors.append(f"{row.segment_id}: only {', '.join(ALLOWED_TAGS)} are allowed, not {', '.join(unknown)}.")
        if len(tags) > MAX_TAGS_PER_SEGMENT:
            errors.append(f"{row.segment_id}: at most {MAX_TAGS_PER_SEGMENT} tags per segment.")
        if untagged(row.text) != " ".join(spoken[row.segment_id].split()):
            errors.append(f"{row.segment_id}: the words changed; without its tags the text must be exactly the given one.")
        if OPENING_LONG_PAUSE.match(row.text):
            errors.append(f"{row.segment_id}: a <long pause> never opens a segment; the recording already pauses there.")
        for match in TAG.finditer(row.text):
            before = row.text[match.start() - 1] if match.start() else " "
            after = row.text[match.end()] if match.end() < len(row.text) else " "
            if before.isalnum() or after.isalnum():
                errors.append(f"{row.segment_id}: a tag stands between words, never inside one.")
                break
        total += len(tags)
    limit = episode_tag_limit(len(spoken))
    if total > limit:
        errors.append(f"At most {limit} tags in this episode; use them only where they help most.")
    return errors


def plan_expression(invoke, script, spoken, *, language, labels):
    """The tagged text of the segments that get a tag, keyed by segment id.

    ``invoke(prompt, schema, version)`` is one text-model call. An answer the check still rejects after its
    corrections leaves the episode without tags: expression is a finish, so it never stops a recording."""
    limit = episode_tag_limit(len(script.segments))
    prompt = instructions("audio_expression") + "\n" + json.dumps({
        "language": language, "allowed_tags": list(ALLOWED_TAGS), "max_tags_per_segment": MAX_TAGS_PER_SEGMENT,
        "max_tags_in_episode": limit, "hosts": labels,
        "segments": [{"segment_id": s.segment_id, "speaker": labels.get(s.speaker_id, s.speaker_id),
                      "text": spoken[s.segment_id]} for s in script.segments]}, ensure_ascii=False)

    def check(plan):
        errors = expression_defects(without_opening_pause(plan), spoken)
        if errors:
            raise AppError(" ".join(errors), code="invalid_expression", status="blocked")

    try:
        plan = corrected_call(invoke, prompt, ExpressionPlan, EXPRESSION_PROMPT_VERSION, check)
    except AppError as exc:
        if exc.code != "invalid_expression":
            raise
        return {}, str(exc)
    return {row.segment_id: row.text for row in without_opening_pause(plan).segments}, ""
