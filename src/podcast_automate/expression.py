"""The expression layer of a Gemini recording: a few inline audio tags per episode, never in the script text.

Gemini 3.8 Flash TTS performs vocal events written into its transcript in angle brackets (``<laugh>``, ``<sigh>``,
``<breath>``, ``<short pause>``), and OpenRouter passes them through; a written style direction ("Sag es
fröhlich:") is spoken aloud instead, so only tags are used (listen test, 2026-09-29). One text-model call per
episode places them; the check makes sure that, without its tags, every tagged segment is exactly the text
that would be spoken anyway, so the layer can add expression but never change a word.

A recording through Google's own API (google_speech) speaks both hosts in one request, and there the other host's
short reactions go between pipes into a turn (``That's the lexical gap. |mhm| The meaning matches``); the listening
round of 2026-10-06 kept them. Only such a recording gets them (``backchannels``).
"""
from __future__ import annotations

import json
import re

from pydantic import Field

from .audio import PAUSE_TAGS
from .errors import AppError
from .models import Contract, Identifier, NonEmpty
from .prompts import instructions
from .research_patches import corrected_call

# The record format of expression.json; saved readings carry it, so it stays while the prompt changes. A record placed
# with listener reactions says so ("backchannels": true), so a reading is reused only for the route it was placed for.
EXPRESSION_VERSION = "audio_expression.v1"
# The prompt's own tag (prompts/audio_expression.txt). v2, 2026-10-02: never a <long pause> at a segment's start.
# v3, 2026-10-07: pauses on purpose and counted apart (episode_pause_limit), where the listener catches their breath.
EXPRESSION_PROMPT_VERSION = "audio_expression.v3-pauses"
# With the reactions of the other host (prompts/audio_expression_backchannels.txt appended), 2026-10-06; v4 with the
# pauses of v3.
BACKCHANNEL_PROMPT_VERSION = "audio_expression.v4-pauses"
# Google's documented vocal events for Gemini 3.8 Flash TTS (English names, also in German text), kept to those
# that fit a factual two-host podcast; screams, sobs, growls, sneezes and the like are left out. The first four
# passed the listen test of 2026-09-29, the others are documented and heard in scripts/gemini-tags-test.py.
ALLOWED_TAGS = ("<short pause>", "<long pause>", "<breath>", "<exhales>", "<sigh>", "<phew>",
                "<laugh>", "<chuckle>", "<giggle>", "<gasp>", "<tsk>", "<throat-clearing>")
MAX_TAGS_PER_SEGMENT = 2
TAG = re.compile(r"<[^<>\n]{1,40}>")
# The other host's reactions, spoken in their voice while this one talks; short sounds and words only.
BACKCHANNELS = {"en-US": ("|mhm|", "|hmm|", "|right|", "|oh|", "|okay|", "|yeah|"),
                "de-DE": ("|mhm|", "|hm|", "|aha|", "|ja|", "|okay|", "|oh|")}
BACKCHANNEL = re.compile(r"\|[^|<>\n]{1,20}\|")
# A reaction needs a turn long enough to react inside: at least this many words, one reaction at most.
MIN_BACKCHANNEL_WORDS = 30
# All 44 <long pause> tags of the 29 Sep recordings opened their segment, where assembly already pauses (900 ms at
# a chapter start), and four of them left 3.5 to 7.3 s of dead air. The tag may stand inside a segment only.
OPENING_LONG_PAUSE = re.compile(r"\A\s*<long pause>\s*")


class TaggedSegment(Contract):
    segment_id: Identifier
    text: NonEmpty


class ExpressionPlan(Contract):
    segments: list[TaggedSegment] = Field(default_factory=list)


def untagged(text):
    return " ".join(BACKCHANNEL.sub(" ", TAG.sub(" ", text)).split())


def episode_tag_limit(count):
    """Tags one episode may carry besides its pauses: sparse, about one for every four segments."""
    return max(3, count // 4)


# The pause tags, counted apart from the other tags since 2026-10-07: listeners of the first series found „der stetige
# Strom von Information“ left no moment to catch their breath, and a Google recording of Ontologies ep_001 had 0.3
# pauses of a second or more per minute, its longest stretch without any pause 90 s, with 3 pause tags in 34 minutes.
PAUSE_TAG_SET = frozenset(PAUSE_TAGS)


def episode_pause_limit(count):
    """Pause tags one episode may carry, a ceiling and never a target: a third of its segments. Where they go is the
    content's call, never a schedule (the user: „immer organisch bleiben, nicht algorithmisch“)."""
    return max(4, count // 3)


def episode_backchannel_limit(count):
    """Reactions one episode may carry, counted apart from the tags: about one for every four segments."""
    return max(2, count // 4)


def without_opening_pause(plan):
    """``plan`` with a <long pause> at a segment's start removed, and a segment left without a tag or reaction dropped.
    A deterministic fix, so a model answer that breaks only this rule costs no correction call."""
    rows = []
    for row in plan.segments:
        text = OPENING_LONG_PAUSE.sub("", row.text, count=1)
        if TAG.search(text) or BACKCHANNEL.search(text):
            rows.append(TaggedSegment(segment_id=row.segment_id, text=text))
    return ExpressionPlan(segments=rows)


def expression_defects(plan, spoken, backchannels=()):
    """``spoken`` maps each segment id to the text that would be spoken without the layer; ``backchannels`` are the
    reactions this recording may use, none for one that records each segment on its own."""
    errors, seen, total, pauses, reactions = [], set(), 0, 0, 0
    for row in plan.segments:
        if row.segment_id not in spoken:
            errors.append(f"{row.segment_id}: unknown segment id.")
            continue
        if row.segment_id in seen:
            errors.append(f"{row.segment_id}: listed twice.")
        seen.add(row.segment_id)
        tags, heard = TAG.findall(row.text), BACKCHANNEL.findall(row.text)
        if not tags and not heard:
            errors.append(f"{row.segment_id}: carries no tag; leave such segments out.")
        unknown = sorted(set(tags) - set(ALLOWED_TAGS))
        if unknown:
            errors.append(f"{row.segment_id}: only {', '.join(ALLOWED_TAGS)} are allowed, not {', '.join(unknown)}.")
        if len(tags) > MAX_TAGS_PER_SEGMENT:
            errors.append(f"{row.segment_id}: at most {MAX_TAGS_PER_SEGMENT} tags per segment.")
        if heard and not backchannels:
            errors.append(f"{row.segment_id}: this recording has no listener reactions; remove {', '.join(heard)}.")
        elif heard:
            strange = sorted(set(heard) - set(backchannels))
            if strange:
                errors.append(f"{row.segment_id}: only {', '.join(backchannels)} are allowed as reactions, "
                              f"not {', '.join(strange)}.")
            if len(heard) > 1:
                errors.append(f"{row.segment_id}: at most one reaction per segment.")
            if len(spoken[row.segment_id].split()) < MIN_BACKCHANNEL_WORDS:
                errors.append(f"{row.segment_id}: a reaction only inside a turn of {MIN_BACKCHANNEL_WORDS} words or more.")
            stripped = TAG.sub(" ", row.text).strip()
            if stripped.startswith("|") or stripped.endswith("|"):
                errors.append(f"{row.segment_id}: a reaction stands inside the turn, never at its start or end.")
        if untagged(row.text) != " ".join(spoken[row.segment_id].split()):
            errors.append(f"{row.segment_id}: the words changed; without its tags the text must be exactly the given one.")
        if OPENING_LONG_PAUSE.match(row.text):
            errors.append(f"{row.segment_id}: a <long pause> never opens a segment; the recording already pauses there.")
        for match in [*TAG.finditer(row.text), *BACKCHANNEL.finditer(row.text)]:
            before = row.text[match.start() - 1] if match.start() else " "
            after = row.text[match.end()] if match.end() < len(row.text) else " "
            if before.isalnum() or after.isalnum():
                errors.append(f"{row.segment_id}: a tag stands between words, never inside one.")
                break
        total += sum(tag not in PAUSE_TAG_SET for tag in tags)
        pauses += sum(tag in PAUSE_TAG_SET for tag in tags)
        reactions += len(heard)
    limit = episode_tag_limit(len(spoken))
    if total > limit:
        errors.append(f"At most {limit} tags besides pauses in this episode; use them only where they help most.")
    if pauses > episode_pause_limit(len(spoken)):
        errors.append(f"At most {episode_pause_limit(len(spoken))} pauses in this episode.")
    if backchannels and reactions > episode_backchannel_limit(len(spoken)):
        errors.append(f"At most {episode_backchannel_limit(len(spoken))} reactions in this episode.")
    return errors


def plan_expression(invoke, script, spoken, *, language, labels, backchannels=()):
    """The tagged text of the segments that get a tag, keyed by segment id.

    ``invoke(prompt, schema, version)`` is one text-model call. An answer the check still rejects after its
    corrections leaves the episode without tags: expression is a finish, so it never stops a recording.
    ``backchannels`` are the other host's reactions the recording can speak (BACKCHANNELS), empty for one that
    records each segment on its own."""
    limit = episode_tag_limit(len(script.segments))
    text = instructions("audio_expression")
    data = {"language": language, "allowed_tags": list(ALLOWED_TAGS), "max_tags_per_segment": MAX_TAGS_PER_SEGMENT,
            "max_tags_in_episode": limit, "max_pauses_in_episode": episode_pause_limit(len(script.segments)),
            "hosts": labels}
    if backchannels:
        text += "\n" + instructions("audio_expression_backchannels")
        data.update(allowed_backchannels=list(backchannels), min_words_for_backchannel=MIN_BACKCHANNEL_WORDS,
                    max_backchannels_in_episode=episode_backchannel_limit(len(script.segments)))
    data["segments"] = [{"segment_id": s.segment_id, "speaker": labels.get(s.speaker_id, s.speaker_id),
                         "text": spoken[s.segment_id]} for s in script.segments]
    prompt = text + "\n" + json.dumps(data, ensure_ascii=False)

    def check(plan):
        errors = expression_defects(without_opening_pause(plan), spoken, backchannels)
        if errors:
            raise AppError(" ".join(errors), code="invalid_expression", status="blocked")

    version = BACKCHANNEL_PROMPT_VERSION if backchannels else EXPRESSION_PROMPT_VERSION
    try:
        plan = corrected_call(invoke, prompt, ExpressionPlan, version, check)
    except AppError as exc:
        if exc.code != "invalid_expression":
            raise
        return {}, str(exc)
    return {row.segment_id: row.text for row in without_opening_pause(plan).segments}, ""
